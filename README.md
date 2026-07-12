# anime-lakehouse

A medallion lakehouse, a dimensional warehouse, an LLM enrichment pipeline with
trace-based evals, and a gate lattice that runs all of it on every change.

Local development runs the entire stack on DuckDB in seconds. The same dbt
models and ingestion logic deploy to Databricks (Delta Lake, Unity Catalog,
serverless jobs) through an Asset Bundle.

```
AniList API ---> bronze/anilist_media ----+
                                          +--> dbt staging (silver) --> gold star schema
QoE simulator -> bronze/playback_events --+          |                    dim_title
                                                     |                    dim_user
                    LLM mood enrichment -------------+                    fact_playback
                    (Claude, Langfuse traces)                             episode_dropoff
                            |
                    evals/run_eval.py <--- golden set + regression gate
```

## Quickstart

```bash
make setup                    # uv venv + deps
source .venv/bin/activate
make pipeline                 # ingest catalog, simulate events, dbt build
duckdb transform/warehouse.duckdb "select * from episode_dropoff limit 10"
```

With an Anthropic key (Langfuse keys optional):

```bash
make enrich                   # LLM mood tags, one trace per title
make warehouse-enriched       # rebuild gold with mood_tags joined
make eval                     # score against the golden set
```

## Gate lattice

`ci/gate.sh` is the single entrypoint for every quality check. Five gates run in
fixed order, cheapest first. All gates run even after one fails, so a red run
shows every failure rather than the first.

| gate | checks | fails when |
|------|--------|------------|
| pytest | unit suite (21 tests) | logic regression |
| determinism | simulator run twice, seed 42, parquet byte-compared | reproducibility broken |
| dbt_build | staging + marts + 16 schema tests | model or contract failure |
| eval_gate | macro-F1, parse-fail rate, p95 latency, cost per title | enrichment quality regression |
| goldens | frozen mart outputs compared row by row | transform output drift |

```bash
bash ci/gate.sh                       # human output
bash ci/gate.sh --robot --trigger ci  # NDJSON event stream, one JSON object per line
bash ci/test_gate.sh                  # the gate harness own tests (8)
```

Every run writes a record to `data/labboard/runs/<run_id>.json`.

## labboard

A stdlib-only server (no framework, no dependencies) that runs the gate lattice
and streams it live over Server-Sent Events.

```bash
python labboard/server.py             # http://127.0.0.1:8377
```

Routes: `/` gate lattice with live log and run history, `/label` the golden-set
labeling tool (fixed tag vocabulary, 1 to 3 tags per title, two-pass mode),
`/label/report` per-title agreement counts.

## Deploy to Databricks

```bash
export DATABRICKS_HOST="https://<workspace>.cloud.databricks.com"
databricks auth login
cd databricks && databricks bundle deploy -t dev && databricks bundle run -t dev catalog_refresh
cd ../transform && dbt build --target databricks --profiles-dir .
```

Step by step, including Free Edition signup: `docs/databricks-free-edition.md`.
Self-hosted Linux CI runner: `docs/ci-linux-box.md`.

## Simulated playback, on purpose

Real playback telemetry is not public. `simulate_playback.py` generates
Mux-style QoE events (watch percentage, rebuffer count, startup time) with a
planted causal signal: continuation past episode 1 depends on a latent per-title
tone vector more than on genre. The signal is recoverable, so the analysis is
falsifiable rather than decorative, and the same seed produces byte-identical
output.

`demo/simlab.html` presents the resulting data with an in-browser port of the
continuation model whose parameters are adjustable.

## How this repo was built

Planned in markdown, decomposed into a dependency-checked bead graph, then
implemented by parallel agents in isolated git worktrees, each owning a disjoint
file set. Every branch was cross-reviewed before merge, merged with a
bead-referencing merge commit, and re-verified independently by the integrator.
`git log` answers, for any line: which bead demanded it, which agent wrote it,
which gate verified it.

- `docs/PLAN-wave1-flywheel.md`, `docs/PLAN-wave2-completion.md`: the plans and
  the frozen inter-agent contracts.
- `docs/OPERATIONS-LOG.md`: the run itself. Dispatches, interventions, review
  findings, merge records, gate results.
- `.beads/`: the work graph, with acceptance evidence in every close reason.

## Layout

| path | contents |
|------|----------|
| `src/anime_lakehouse/ingest/` | AniList ingestion, QoE event simulator |
| `src/anime_lakehouse/llm/` | mood-tag enrichment (Claude, Langfuse) |
| `transform/` | dbt project: staging views, gold star schema |
| `orchestration/` | Dagster asset definitions |
| `databricks/` | Asset Bundle: serverless jobs, PySpark ingest, Delta MERGE |
| `evals/` | golden set, scoring, regression gate |
| `ci/` | gate lattice, runner setup |
| `labboard/` | live gate and eval UI, labeling tool |
| `demo/` | data export, simulation page |
| `tests/golden/` | frozen mart outputs |
