#!/usr/bin/env bash
set -euo pipefail

# Canned §3.1 NDJSON emitter for labboard tests.
# Emits a two-gate run (pytest pass, eval_gate skip) with small sleeps.
# Writes run record per §3.2 when --robot is passed.

TRIGGER="cli"
ROBOT=false
RUN_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --robot) ROBOT=true; shift ;;
        --trigger) TRIGGER="$2"; shift 2 ;;
        --gate) shift 2 ;;  # ignored in fixture
        *) shift ;;
    esac
done

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-fake"
NOW_UTC() { date -u +%Y-%m-%dT%H:%M:%S.000Z; }

emit() {
    if $ROBOT; then
        printf '%s\n' "$1"
    fi
}

STARTED_AT="$(NOW_UTC)"

emit "{\"event\":\"run_start\",\"run_id\":\"$RUN_ID\",\"ts\":\"$(NOW_UTC)\",\"gates\":[\"pytest\",\"eval_gate\"]}"

# Deliberate non-JSON noise on stdout to exercise the NDJSON line filter
if $ROBOT; then
    printf 'WARNING: this is not JSON and should be filtered out\n'
fi

# Gate 1: pytest — pass
emit "{\"event\":\"gate_start\",\"run_id\":\"$RUN_ID\",\"gate\":\"pytest\",\"ts\":\"$(NOW_UTC)\"}"
sleep 0.05
emit "{\"event\":\"log\",\"run_id\":\"$RUN_ID\",\"gate\":\"pytest\",\"line\":\"collecting ...\",\"ts\":\"$(NOW_UTC)\"}"
sleep 0.05
emit "{\"event\":\"log\",\"run_id\":\"$RUN_ID\",\"gate\":\"pytest\",\"line\":\"tests/test_ingest.py::test_fetch_page PASSED\",\"ts\":\"$(NOW_UTC)\"}"
sleep 0.05
emit "{\"event\":\"log\",\"run_id\":\"$RUN_ID\",\"gate\":\"pytest\",\"line\":\"tests/test_simulate.py::test_determinism PASSED\",\"ts\":\"$(NOW_UTC)\"}"
sleep 0.05
emit "{\"event\":\"log\",\"run_id\":\"$RUN_ID\",\"gate\":\"pytest\",\"line\":\"tests/test_warehouse.py::test_schema PASSED\",\"ts\":\"$(NOW_UTC)\"}"
sleep 0.05
emit "{\"event\":\"log\",\"run_id\":\"$RUN_ID\",\"gate\":\"pytest\",\"line\":\"5 passed in 0.17s\",\"ts\":\"$(NOW_UTC)\"}"
sleep 0.05
emit "{\"event\":\"gate_end\",\"run_id\":\"$RUN_ID\",\"gate\":\"pytest\",\"status\":\"pass\",\"duration_s\":0.35,\"ts\":\"$(NOW_UTC)\"}"

# Gate 2: eval_gate — skip (no predictions parquet)
emit "{\"event\":\"gate_start\",\"run_id\":\"$RUN_ID\",\"gate\":\"eval_gate\",\"ts\":\"$(NOW_UTC)\"}"
sleep 0.05
emit "{\"event\":\"log\",\"run_id\":\"$RUN_ID\",\"gate\":\"eval_gate\",\"line\":\"predictions parquet not found, skipping\",\"ts\":\"$(NOW_UTC)\"}"
sleep 0.05
emit "{\"event\":\"gate_end\",\"run_id\":\"$RUN_ID\",\"gate\":\"eval_gate\",\"status\":\"skip\",\"reason\":\"no predictions parquet\",\"duration_s\":0.1,\"ts\":\"$(NOW_UTC)\"}"

FINISHED_AT="$(NOW_UTC)"
emit "{\"event\":\"run_end\",\"run_id\":\"$RUN_ID\",\"status\":\"pass\",\"duration_s\":0.45,\"ts\":\"$FINISHED_AT\"}"

# Write run record (§3.2) if data dir exists
RUN_DIR="data/labboard/runs"
mkdir -p "$RUN_DIR"
cat > "$RUN_DIR/$RUN_ID.json" <<RECORD
{"run_id":"$RUN_ID","started_at":"$STARTED_AT","finished_at":"$FINISHED_AT","status":"pass","trigger":"$TRIGGER","gates":[{"gate":"pytest","status":"pass","duration_s":0.35,"tail":["5 passed in 0.17s"]},{"gate":"eval_gate","status":"skip","duration_s":0.1,"tail":["predictions parquet not found, skipping"]}]}
RECORD
