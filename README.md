# anime-lakehouse

The data foundation for a streaming R&D lab, end to end: a medallion lakehouse
over a real anime catalog, a dimensional warehouse built with dbt, an LLM
enrichment pipeline with trace-based evals, and one analytics question the
whole stack exists to answer:

> **What predicts episode-2 drop-off — and do LLM-derived mood tags predict it
> better than genre?**

## Architecture

```
AniList API ──► bronze/anilist_media ─┐
                                      ├─► dbt (silver: stg_*) ─► gold star schema
QoE simulator ─► bronze/playback_events ┘        │                 dim_title
                                                 │                 dim_user
                 LLM mood enrichment ────────────┘                 fact_playback
                 (Claude + Langfuse traces)                        episode_dropoff
                        │
                 evals/run_eval.py  ◄── golden set + regression gate
```

- **Bronze** — raw landings, ingestion metadata attached, no cleaning.
  Locally: parquet under `data/bronze/`. On Databricks: Delta tables in
  Unity Catalog (`anime_lakehouse.bronze.*`), MERGE-upserted for idempotent reruns.
- **Silver** — validated, typed staging views (`stg_media`, `stg_playback`,
  `stg_users`). Nothing downstream reads bronze directly.
- **Gold** — a star schema at explicit grains (`dim_title`, `dim_user`,
  `fact_playback`) plus the `episode_dropoff` mart. dbt tests enforce keys and
  referential integrity.
- **Enrichment** — Claude classifies each synopsis into a fixed mood-tag
  vocabulary; every call traced to Langfuse; outputs join into `dim_title`.
- **Evals** — golden-set scoring with a CI regression gate ([evals/](evals/)).
  A prompt or model change that degrades tagging fails the build.

Two runtimes, one codebase: local development runs the entire stack on DuckDB
in seconds; the Databricks Asset Bundle ([databricks/](databricks/)) deploys
the same pipeline as serverless jobs writing Delta tables. Prototype fast,
promote deliberately — the lab workflow.

## Quickstart (local)

```bash
make setup                    # uv venv + deps
source .venv/bin/activate
make pipeline                 # ingest catalog -> simulate events -> dbt build
duckdb transform/warehouse.duckdb "select * from episode_dropoff limit 10"
```

With API keys (`ANTHROPIC_API_KEY`, optional Langfuse pair):

```bash
make enrich                   # LLM mood tags, traced
make warehouse-enriched       # rebuild gold with mood_tags joined in
make eval                     # score against golden set; nonzero exit on regression
```

Orchestrated view: `make dagster` and materialize the asset graph from the UI.

## Deploy to Databricks

```bash
databricks auth login --host https://<workspace>.cloud.databricks.com
cd databricks && databricks bundle deploy -t dev && databricks bundle run catalog_refresh
cd transform && dbt build --target databricks
```

## The playback data is simulated — on purpose

Real playback telemetry isn't public, so `simulate_playback.py` generates
Mux-style QoE events (watch %, rebuffers, startup time) with a **planted
causal signal**: continuation past episode 1 depends on a latent per-title
tone vector more than on genre. That makes the headline question falsifiable —
the analysis either recovers the planted truth or it doesn't — and turns the
dashboard into an eval of the enrichment pipeline rather than a chart demo.
Same seed, same output: the whole dataset is reproducible byte-for-byte.

## Layout

| Path | What |
|------|------|
| `src/anime_lakehouse/ingest/` | AniList ingestion + QoE event simulator |
| `src/anime_lakehouse/llm/` | mood-tag enrichment (Claude + Langfuse) |
| `transform/` | dbt project: staging views + gold star schema |
| `orchestration/` | Dagster asset definitions for the full lineage |
| `databricks/` | Asset Bundle: serverless jobs + Delta/Unity Catalog twin |
| `evals/` | golden set, scoring, CI regression gate |
