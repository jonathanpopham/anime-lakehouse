#!/usr/bin/env bash
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TRIGGER="cli"
ROBOT=0
SINGLE_GATE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --robot) ROBOT=1; shift ;;
        --trigger) TRIGGER="$2"; shift 2 ;;
        --gate) SINGLE_GATE="$2"; shift 2 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(head -c 4 /dev/urandom | xxd -p | cut -c1-4)"
RUN_DIR="${REPO_ROOT}/data/labboard/runs"
mkdir -p "$RUN_DIR"

GATES=(pytest determinism dbt_build eval_gate)
if [[ -n "$SINGLE_GATE" ]]; then
    GATES=("$SINGLE_GATE")
fi

VENV="${REPO_ROOT}/.venv/bin"
if [[ ! -x "${VENV}/python" ]]; then
    echo "ERROR: .venv not found — run: uv venv --python 3.12 && uv pip install -e '.[dev]'" >&2
    exit 1
fi
PATH="${VENV}:${PATH}"
export PATH

upper() { echo "$1" | tr '[:lower:]' '[:upper:]'; }
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
json_str() { python -c "import json,sys; print(json.dumps(sys.argv[1]))" "$1"; }

emit() {
    [[ $ROBOT -eq 1 ]] && echo "$1"
    return 0
}

human() {
    [[ $ROBOT -eq 0 ]] && echo "$@"
    return 0
}

elapsed() {
    python -c "
from datetime import datetime
s=datetime.fromisoformat('$1'.replace('Z','+00:00'))
e=datetime.fromisoformat('$2'.replace('Z','+00:00'))
print(f'{(e-s).total_seconds():.2f}')
"
}

stream_file() {
    local gate="$1" file="$2"
    while IFS= read -r line; do
        local jline
        jline=$(json_str "$line")
        emit "{\"event\":\"log\",\"run_id\":\"${RUN_ID}\",\"gate\":\"${gate}\",\"line\":${jline},\"ts\":\"$(ts)\"}"
        human "  $line"
    done < "$file"
}

RUN_STATUS="pass"
STARTED_AT="$(ts)"
GATE_JSON_FILE=$(mktemp)
: > "$GATE_JSON_FILE"

emit "{\"event\":\"run_start\",\"run_id\":\"${RUN_ID}\",\"ts\":\"${STARTED_AT}\",\"gates\":[$(printf '"%s",' "${GATES[@]}" | sed 's/,$//')]}"
human "=== gate run ${RUN_ID} (trigger: ${TRIGGER}) ==="
human ""

run_gate() {
    local gate="$1"
    local gate_start gate_end status="" duration_s
    gate_start="$(ts)"
    emit "{\"event\":\"gate_start\",\"run_id\":\"${RUN_ID}\",\"gate\":\"${gate}\",\"ts\":\"${gate_start}\"}"
    human "--- ${gate} ---"

    local cmd_exit=0
    local tmpout
    tmpout=$(mktemp)

    case "$gate" in
        pytest)
            (cd "$REPO_ROOT" && python -m pytest -q 2>&1) > "$tmpout" || cmd_exit=$?
            stream_file "$gate" "$tmpout"
            ;;
        determinism)
            local tmp1 tmp2
            tmp1=$(mktemp -d)
            tmp2=$(mktemp -d)
            # determinism gate needs the catalog present in the temp bronze root
            cp -r "${REPO_ROOT}/data/bronze/anilist_media" "$tmp1/anilist_media"
            cp -r "${REPO_ROOT}/data/bronze/anilist_media" "$tmp2/anilist_media"
            (
                cd "$REPO_ROOT"
                python -m anime_lakehouse.ingest.simulate_playback --seed 42 --bronze-root "$tmp1" 2>&1
                python -m anime_lakehouse.ingest.simulate_playback --seed 42 --bronze-root "$tmp2" 2>&1
            ) > "$tmpout" || cmd_exit=$?
            stream_file "$gate" "$tmpout"
            if ! diff -r "$tmp1" "$tmp2" > /dev/null 2>&1; then
                cmd_exit=1
                local msg="FAIL: parquet outputs differ between identical seed runs"
                echo "$msg" >> "$tmpout"
                emit "{\"event\":\"log\",\"run_id\":\"${RUN_ID}\",\"gate\":\"${gate}\",\"line\":$(json_str "$msg"),\"ts\":\"$(ts)\"}"
                human "  $msg"
            fi
            rm -rf "$tmp1" "$tmp2"
            ;;
        dbt_build)
            local dbt_cmd="dbt build --profiles-dir ."
            if [[ -f "${REPO_ROOT}/data/enriched/title_moods.parquet" ]]; then
                dbt_cmd="dbt build --profiles-dir . --vars {enriched: true}"
            fi
            (cd "${REPO_ROOT}/transform" && eval "$dbt_cmd" 2>&1) > "$tmpout" || cmd_exit=$?
            stream_file "$gate" "$tmpout"
            ;;
        eval_gate)
            if [[ ! -f "${REPO_ROOT}/data/enriched/title_moods.parquet" ]]; then
                status="skip"
                local reason="data/enriched/title_moods.parquet absent"
                emit "{\"event\":\"log\",\"run_id\":\"${RUN_ID}\",\"gate\":\"${gate}\",\"line\":$(json_str "skip: $reason"),\"ts\":\"$(ts)\"}"
                human "  skip: $reason"
                gate_end="$(ts)"
                duration_s=$(elapsed "$gate_start" "$gate_end")
                emit "{\"event\":\"gate_end\",\"run_id\":\"${RUN_ID}\",\"gate\":\"${gate}\",\"status\":\"skip\",\"reason\":\"${reason}\",\"duration_s\":${duration_s},\"ts\":\"${gate_end}\"}"
                human "  -> SKIP (${duration_s}s)"
                human ""
                echo "{\"gate\":\"${gate}\",\"status\":\"skip\",\"duration_s\":${duration_s},\"tail\":[]}" >> "$GATE_JSON_FILE"
                rm -f "$tmpout"
                return
            fi
            (cd "$REPO_ROOT" && python evals/run_eval.py 2>&1) > "$tmpout" || cmd_exit=$?
            stream_file "$gate" "$tmpout"
            ;;
        *)
            human "  unknown gate: ${gate}"
            cmd_exit=2
            ;;
    esac

    if [[ -z "$status" ]]; then
        if [[ $cmd_exit -eq 0 ]]; then
            status="pass"
        else
            status="fail"
            RUN_STATUS="fail"
        fi
    fi

    gate_end="$(ts)"
    duration_s=$(elapsed "$gate_start" "$gate_end")
    emit "{\"event\":\"gate_end\",\"run_id\":\"${RUN_ID}\",\"gate\":\"${gate}\",\"status\":\"${status}\",\"duration_s\":${duration_s},\"ts\":\"${gate_end}\"}"
    human "  -> $(upper "$status") (${duration_s}s)"
    human ""

    local tail_json
    tail_json=$(tail -50 "$tmpout" | python -c 'import json,sys; print(json.dumps([l.rstrip() for l in sys.stdin.readlines()]))')
    echo "{\"gate\":\"${gate}\",\"status\":\"${status}\",\"duration_s\":${duration_s},\"tail\":${tail_json}}" >> "$GATE_JSON_FILE"
    rm -f "$tmpout"
}

for gate in "${GATES[@]}"; do
    run_gate "$gate"
done

FINISHED_AT="$(ts)"
RUN_DURATION=$(elapsed "$STARTED_AT" "$FINISHED_AT")

emit "{\"event\":\"run_end\",\"run_id\":\"${RUN_ID}\",\"status\":\"${RUN_STATUS}\",\"duration_s\":${RUN_DURATION},\"ts\":\"${FINISHED_AT}\"}"

# Write run record (§3.2)
GATES_ARRAY=$(paste -sd',' "$GATE_JSON_FILE")
RUN_RECORD="{\"run_id\":\"${RUN_ID}\",\"started_at\":\"${STARTED_AT}\",\"finished_at\":\"${FINISHED_AT}\",\"status\":\"${RUN_STATUS}\",\"trigger\":\"${TRIGGER}\",\"gates\":[${GATES_ARRAY}]}"
echo "$RUN_RECORD" | python -m json.tool > "${RUN_DIR}/${RUN_ID}.json"
rm -f "$GATE_JSON_FILE"

human "=== $(upper "$RUN_STATUS") (${RUN_DURATION}s) — record: data/labboard/runs/${RUN_ID}.json ==="

if [[ "$RUN_STATUS" == "fail" ]]; then
    exit 1
fi
exit 0
