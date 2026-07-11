#!/usr/bin/env bash
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
FAIL=0

ok() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }

echo "=== ci/test_gate.sh ==="
echo ""

# --- Test 1: robot mode produces valid NDJSON ---
echo "test: --robot produces valid NDJSON"
ROBOT_OUT=$("${REPO_ROOT}/ci/gate.sh" --robot --trigger test 2>/dev/null)
INVALID=$(echo "$ROBOT_OUT" | while IFS= read -r line; do
    echo "$line" | "${REPO_ROOT}/.venv/bin/python" -c "import json,sys; json.loads(sys.stdin.read())" 2>/dev/null || echo "BAD"
done | grep -c "BAD" || true)
if [[ "$INVALID" -eq 0 ]]; then
    ok "all NDJSON lines parse"
else
    fail "$INVALID lines failed JSON parse"
fi

# --- Test 2: event order ---
echo "test: event order is run_start, gate_start/end pairs, run_end"
EVENTS=$(echo "$ROBOT_OUT" | "${REPO_ROOT}/.venv/bin/python" -c "
import json, sys
events = [json.loads(l)['event'] for l in sys.stdin if l.strip()]
print(' '.join(events))
")
FIRST=$(echo "$EVENTS" | awk '{print $1}')
LAST=$(echo "$EVENTS" | awk '{print $NF}')
if [[ "$FIRST" == "run_start" && "$LAST" == "run_end" ]]; then
    ok "run_start first, run_end last"
else
    fail "expected run_start...run_end, got $FIRST...$LAST"
fi

# --- Test 3: gate_start/gate_end pairs match ---
echo "test: each gate has matching start/end"
echo "$ROBOT_OUT" | "${REPO_ROOT}/.venv/bin/python" -c "
import json, sys
lines = [json.loads(l) for l in sys.stdin if l.strip()]
starts = [e['gate'] for e in lines if e['event'] == 'gate_start']
ends = [e['gate'] for e in lines if e['event'] == 'gate_end']
assert starts == ends, f'mismatch: starts={starts} ends={ends}'
" > /dev/null 2>&1 && ok "gate start/end pairs match" || fail "gate start/end mismatch"

# --- Test 4: run record written ---
echo "test: run record written to data/labboard/runs/"
RUN_ID=$(echo "$ROBOT_OUT" | head -1 | "${REPO_ROOT}/.venv/bin/python" -c "import json,sys; print(json.loads(sys.stdin.read())['run_id'])")
RECORD="${REPO_ROOT}/data/labboard/runs/${RUN_ID}.json"
if [[ -f "$RECORD" ]]; then
    ok "run record exists: ${RUN_ID}.json"
else
    fail "run record not found for ${RUN_ID}"
fi

# --- Test 5: run record is valid JSON with required fields ---
echo "test: run record has required fields"
"${REPO_ROOT}/.venv/bin/python" -c "
import json, sys
with open('$RECORD') as f:
    r = json.load(f)
required = {'run_id', 'started_at', 'finished_at', 'status', 'trigger', 'gates'}
missing = required - set(r.keys())
assert not missing, f'missing fields: {missing}'
assert isinstance(r['gates'], list) and len(r['gates']) > 0
for g in r['gates']:
    assert 'gate' in g and 'status' in g and 'duration_s' in g and 'tail' in g
print('ok')
" > /dev/null 2>&1 && ok "run record schema valid" || fail "run record schema invalid"

# --- Test 6: --gate flag runs single gate ---
echo "test: --gate pytest runs only pytest"
SINGLE_OUT=$("${REPO_ROOT}/ci/gate.sh" --robot --gate pytest 2>/dev/null)
GATE_COUNT=$(echo "$SINGLE_OUT" | "${REPO_ROOT}/.venv/bin/python" -c "
import json, sys
gates = set()
for l in sys.stdin:
    e = json.loads(l)
    if 'gate' in e: gates.add(e['gate'])
print(len(gates))
")
if [[ "$GATE_COUNT" -eq 1 ]]; then
    ok "--gate runs single gate only"
else
    fail "--gate ran $GATE_COUNT gates instead of 1"
fi

# --- Test 7: exit code 0 on pass ---
echo "test: exit code 0 when all gates pass"
"${REPO_ROOT}/ci/gate.sh" --robot --gate pytest > /dev/null 2>&1
if [[ $? -eq 0 ]]; then
    ok "exit 0 on pass"
else
    fail "nonzero exit on passing run"
fi

# --- Test 8: eval_gate skips gracefully ---
echo "test: eval_gate skips when predictions absent"
EVAL_OUT=$("${REPO_ROOT}/ci/gate.sh" --robot --gate eval_gate 2>/dev/null)
EVAL_STATUS=$(echo "$EVAL_OUT" | "${REPO_ROOT}/.venv/bin/python" -c "
import json, sys
for l in sys.stdin:
    e = json.loads(l)
    if e.get('event') == 'gate_end':
        print(e['status'])
        break
")
if [[ "$EVAL_STATUS" == "skip" ]]; then
    ok "eval_gate skips correctly"
else
    fail "eval_gate status=$EVAL_STATUS, expected skip"
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
exit 0
