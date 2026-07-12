# anime-lakehouse — Wave 1: CI gates, labboard, simlab, Databricks readiness

Status: ACTIVE · Integrator: main session · Date: 2026-07-11
Bead prefix: `aml` · Execution: ultracode (3 agents, disjoint file sets, own worktrees)

## 1. Why this wave exists

The repo already proves the pipeline works (200 real AniList titles → bronze,
149k simulated QoE events, 16/16 dbt models+tests on DuckDB, 5/5 pytest,
eval harness with a regression gate). What it does NOT yet prove is that the
*reliability story is visible*: gates that run on every change, a UI where a
human watches evals and tests execute live, a shareable simulation page that
stands on its own, and a credible path onto Databricks itself.

Wave 1 turns "trust me, it's tested" into "watch it test itself."

Non-goals for this wave: live LLM enrichment runs (needs ANTHROPIC_API_KEY —
operator-only), Databricks workspace creation (needs an account), golden
set labeling (50 titles, human task; a labeling helper is backlog `aml-15`),
public GitHub publication.

## 2. Architecture decisions (with why)

**D1. One gate entrypoint: `ci/gate.sh`.** Every quality check runs through a
single ordered script with two output modes: human (default) and `--robot`
(NDJSON events on stdout, one JSON object per line). Why: CI, labboard, and a
human at a shell must observe *identical* semantics — three consumers, one
implementation. This is the repo's `verify.sh` in playbook terms.

**D2. labboard is stdlib-only Python.** `http.server.ThreadingHTTPServer`,
Server-Sent Events for live streaming, zero new dependencies. Why: the repo's
runtime deps stay honest (a repo that drags in FastAPI to serve one
page undermines the "prototype vs long-term judgment" story); SSE over
WebSockets because the traffic is strictly server→client log streaming and SSE
works through `http.server` without a framework.

**D3. Run records are the contract between CI and labboard.** `gate.sh`
*writes* `data/labboard/runs/<run_id>.json`; labboard only *reads* that
directory and streams live runs it spawns itself. Why: agents own disjoint
files (playbook rule); the data directory is runtime state (gitignored), so
neither agent edits the other's code.

**D4. simlab is one self-contained HTML file with baked data + an in-browser
simulator.** A build script exports real warehouse numbers to JSON; the page
inlines them and also ports the simulator's continuation rule to JS with a
seeded PRNG so sliders (tone weight / genre weight) re-run a 2,000-user
simulation live in the browser. Why: the artifact host blocks all external
requests (strict CSP), so self-contained is mandatory; the interactive
simulator makes the planted-signal methodology manipulable rather than merely
asserted, which is the point: the reader can move the parameters and watch the
variance decomposition change. Publication is integrator-only.

**D5. CI = GitHub Actions.** The committed workflow runs on GitHub-hosted
runners; a setup script + runbook also bring up a self-hosted runner as a
systemd service on a Linux box for those who want local execution. Why GH
Actions rather than
Gitea/Woodpecker: a green Actions badge is legible to any reader in a way a
homelab Woodpecker is not, and the workflow file stays boring and standard.
Note: the committed workflow runs on GitHub-hosted runners; self-hosting is
documented as an option but must not be enabled on a public repo, where
pull-request code would execute on your own hardware.

**D6. DuckDB stays the CI database.** Gates run against the local DuckDB
warehouse, not Databricks. Why: deterministic, free, seconds-fast, no secrets
in CI; the Databricks bundle is deploy surface, validated separately (D7).

**D7. Databricks readiness = bundle validates + a runbook an operator can
follow in under 30 minutes.** `databricks bundle validate` must pass locally (CLI
install documented); the runbook covers Free Edition signup → OAuth login →
bundle deploy → `dbt build --target databricks` → where screenshots go in the
README. Why: the only step that genuinely requires a human is account
creation; everything around it should be pre-chewed so the "runs on
Databricks" box gets ticked the same evening a workspace exists.

## 3. Contracts (normative — agents implement to these exactly)

### 3.1 NDJSON event stream (`gate.sh --robot`, stdout)

One JSON object per line. `ts` is ISO-8601 UTC.

```json
{"event":"run_start","run_id":"20260711T193000Z-a1b2","ts":"...","gates":["pytest","determinism","dbt_build","eval_gate"]}
{"event":"gate_start","run_id":"...","gate":"pytest","ts":"..."}
{"event":"log","run_id":"...","gate":"pytest","line":"5 passed in 0.17s","ts":"..."}
{"event":"gate_end","run_id":"...","gate":"pytest","status":"pass","duration_s":1.42,"ts":"..."}
{"event":"run_end","run_id":"...","status":"pass","duration_s":42.0,"ts":"..."}
```

`status` ∈ `pass|fail|skip`. A `skip` carries `"reason"` (e.g. eval_gate skips
when `data/enriched/title_moods.parquet` is absent). `log` lines are raw
subprocess output, unbuffered, ≤ 4 KiB per line.

### 3.2 Run record (`data/labboard/runs/<run_id>.json`, written by gate.sh)

```json
{"run_id":"20260711T193000Z-a1b2","started_at":"...","finished_at":"...",
 "status":"pass","trigger":"cli|labboard|ci",
 "gates":[{"gate":"pytest","status":"pass","duration_s":1.42,"tail":["last ≤50 log lines"]}]}
```

### 3.3 Gate order and semantics (gate.sh)

| # | gate id | command (venv-relative) | fail means |
|---|---------|--------------------------|------------|
| 1 | `pytest` | `python -m pytest -q` | unit regression |
| 2 | `determinism` | run simulator twice with `--seed 42` into temp dirs, byte-compare parquet | reproducibility broken |
| 3 | `dbt_build` | `dbt build --profiles-dir .` in `transform/` (add `--vars '{enriched: true}'` iff enriched parquet exists) | warehouse or schema-test failure |
| 4 | `eval_gate` | `python evals/run_eval.py` iff predictions parquet exists, else skip | enrichment quality regression |

Order is fixed (cheap→expensive, unit→system). First `fail` sets run status
fail but remaining gates still run (a red run should show *all* reds, not the
first). Exit code: 0 iff no gate failed.

### 3.4 labboard HTTP surface (default `127.0.0.1:8377`)

- `GET /` → UI (single embedded HTML page)
- `GET /api/runs` → JSON list of run records, newest first (reads 3.2 dir)
- `POST /api/run` → spawn `ci/gate.sh --robot --trigger labboard`, return `{"run_id":...}`
- `GET /api/stream?run_id=X` → SSE; each NDJSON event (3.1) forwarded as one
  SSE `data:` frame; stream closes after `run_end`. Reconnect-safe: events for
  a live run are also appended to `data/labboard/live/<run_id>.ndjson` and
  replayed on connect.

Only one live run at a time; `POST /api/run` while running → 409.

### 3.5 simlab data export (`scripts/export_demo_data.py` → `demo/data.json`)

```json
{"generated_at":"...","catalog_n":200,"users_n":5000,"events_n":149223,
 "dbt_tests":{"passed":16,"total":16},"pytest":{"passed":5,"total":5},
 "retention_hist":[[0.625,1],[0.7,3], "..."],
 "genre_means":[["Action",0.8501,127],"... n>=5 only"],
 "variance_explained":{"genre_eta2":0.022,"tone_r2":0.511},
 "device_qoe":[["tv",0.33,904,0.343],"... [device,rebuf,startup_ms,completion]"],
 "titles_sample":[["Sword Art Online","Action",0.9301,186],"... top/bottom 15 by retention"]}
```

Real numbers only — the script queries `transform/warehouse.duckdb`; nothing
hand-typed. simlab.html inlines this JSON at build time via a
`<script type="application/json" id="data">` block (the export script also
re-renders the HTML's data block in place with `--inject`).

## 4. Work packages and file ownership (disjoint — enforced)

### WP-A: CI (agent `ci`) — owns `ci/**`, `.github/**`, `docs/ci-linux-box.md`, `docs/databricks-free-edition.md`, `databricks/**` edits
- `aml-2` `ci/gate.sh` per §3.1–3.3. Bash, `set -euo pipefail`, no bashisms
  beyond bash 3.2 (macOS) yet Linux-first. Also writes run record §3.2.
  `--trigger` flag; `--gate <id>` runs a single gate.
- `aml-3` `.github/workflows/ci.yml` (self-hosted linux; steps: checkout,
  `uv venv`+install, `ci/gate.sh --robot --trigger ci`; upload run record as
  artifact) + `ci/setup-runner.sh` (idempotent: download actions-runner,
  `./config.sh` from `$RUNNER_URL $RUNNER_TOKEN` env, systemd unit install,
  status check) — script must be safe to re-run and refuse to run as root.
- `aml-4` `docs/ci-linux-box.md`: exact commands for the Linux box (deps: git,
  uv, python3.12, node absent OK), runner registration walkthrough, how to
  watch a run from labboard on the box (`ssh -L 8377:...`).
- `aml-11` make `databricks bundle validate` pass (fix `databricks/databricks.yml`
  as needed; if the CLI can't be installed in-session, document exact expected
  output and mark the bead's close reason accordingly — honesty over green).
- `aml-12` `docs/databricks-free-edition.md` runbook per D7.

### WP-B: labboard (agent `labboard`) — owns `labboard/**`
- `aml-5` `labboard/server.py` per §3.4. Stdlib only. Subprocess management:
  spawn gate.sh, pump stdout NDJSON → live file + connected SSE clients;
  SIGTERM on server shutdown. Port/host flags. No shell=True.
- `aml-6` labboard UI embedded in `labboard/index.html` (served by server.py;
  keep as separate file read at startup). Dark-first instrument panel, both
  themes. Layout: gate lattice cards (idle/running/pass/fail/skip states with
  color + icon, never color alone), live log pane (monospace, autoscroll with
  pin), run history rail with status chips + duration. A "Run gates" button
  wired to `POST /api/run`, disabled while live. No external assets.
- `aml-7` `labboard/test_labboard.py`: pytest smoke — start server on a free
  port, POST /api/run with `GATE_SH=labboard/fixtures/fake_gate.sh` env
  override (fixture emits a canned §3.1 stream), assert SSE frames arrive in
  order and the run record lands. The env override exists precisely so tests
  never run the real 40s lattice.

### WP-C: simlab (agent `simlab`) — owns `demo/**`, `scripts/export_demo_data.py`
- `aml-8` `scripts/export_demo_data.py` per §3.5 (+ `--inject` mode).
- `aml-9` `demo/simlab.html` — self-contained, both themes, validated palette
  (blue #2a78d6/#3987e5, aqua #1baf7a/#199e70, red #e34948/#e66767; muted bar
  for the 2.2% null result). Sections: (1) hero thesis "Genre explains 2.2%.
  Mood explains 51%." with the two-bar variance chart; (2) retention histogram
  + flat genre bars side by side; (3) device QoE mini-multiples ("it's not the
  network"); (4) THE SIMULATOR: sliders tone-weight [0..1], genre-weight
  [0..0.3], users [500..5000]; mulberry32-seeded JS port of the continuation
  rule (`p_continue = clamp(0.15 + tone_w*match + genre_w*genre_match)`);
  live-redraws retention histogram + recomputed variance-explained bars;
  a "reset to repo defaults" chip; (5) pipeline diagram + gate lattice strip
  (16/16 dbt, 5/5 pytest, eval gate design); (6) honesty footer: simulated
  events, planted signal, byte-deterministic, AniList data is real. Charts:
  inline SVG built by JS from the JSON block; per-mark hover tooltips; direct
  labels (relief rule); no legend where single-series; reduced-motion respect.
- `aml-10` (integrator) publish via Artifact; verify hover, both themes,
  slider redraw; attach URL to bead close.

### Integration (me)
- `aml-13` merge worktrees, resolve conflicts, run `ci/gate.sh` independently,
  run labboard smoke test, export+inject simlab data fresh.
- `aml-14` README update (labboard screenshot slot, CI badge slot, simlab
  link), commit, close beads with evidence.

## 5. Dependency graph

```
aml-1 (plan+beads, done)
  ├─► aml-2 gate.sh ──► aml-3 workflow+runner ──► aml-4 ci runbook
  │        └─────────► aml-5 labboard server ──► aml-6 UI ──► aml-7 smoke
  ├─► aml-8 export ──► aml-9 simlab.html ──► aml-10 publish (integrator)
  ├─► aml-11 bundle validate ──► aml-12 databricks runbook
  └─► ... all ──► aml-13 integrate+verify ──► aml-14 README+commit
backlog: aml-15 golden-set labeling helper (labboard page), aml-16 enrichment
run + eval on real key, aml-17 Databricks deploy
```

`aml-5` depends on `aml-2` only through contract §3.1/3.2 (frozen above), so
labboard starts immediately against the fixture stream — the fake_gate fixture
is the contract test.

## 6. Acceptance gates for the wave

1. `ci/gate.sh` exits 0 on current main; `--robot` output validates against
   §3.1 (jq-parseable, event order sane).
2. `pytest` green including `labboard/test_labboard.py`.
3. labboard demo: `python labboard/server.py` → browser shows history, live
   run streams gate-by-gate, red run renders red (verify by temporarily
   breaking a unit test in a scratch copy — not committed).
4. `demo/simlab.html` opens file:// with zero console errors, zero network
   requests, works in both themes, sliders redraw <100ms at 2k users.
5. `databricks bundle validate` passes or its blocker is documented honestly.
6. Beads: all wave beads closed with evidence, `br dep cycles` empty,
   `br sync --flush-only` done, .beads committed.

## 7. Risks / notes

- SSE through `http.server` needs explicit `flush()` per frame and
  `Content-Type: text/event-stream` + no buffering; ThreadingHTTPServer is
  fine for a localhost tool (documented single-user).
- GH Actions self-hosted runner on a personal box: repo should be private
  until runner hardening reviewed (runners on public repos execute PR code —
  called out in the runbook in bold).
- The simlab JS simulator is a *simplified port* (no QoE fields, fixed
  episode counts) — the page must say so; it exists to make the methodology
  tangible, not to re-prove the Python numbers.
- Plan-review deviation: the playbook's 4× GPT-Pro review rounds were not run
  (not drivable from this session). Mitigation: contracts frozen in §3 are the
  highest-risk surface and were self-checked (self-containment, DAG acyclicity,
  justification sampling, steady-state on second pass). The plan can be pasted
  this plan into GPT Pro Extended Reasoning at leisure; integration happens
  through beads either way.
```
