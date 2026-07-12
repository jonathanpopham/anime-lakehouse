# Databricks Free Edition: deploy runbook

End state: the bundle deployed, the ingest job green, and `dbt build --target
databricks` building the warehouse as Delta tables governed by Unity Catalog.

Budget about 45 minutes for the first run, most of it waiting on the workspace.

> **Free Edition, not Community Edition.** The legacy Community Edition was
> retired in 2025 and never supported Unity Catalog. Free Edition is
> serverless-only and includes Unity Catalog, which is what this project
> targets. Sign up at <https://www.databricks.com/learn/free-edition>, not at
> `community.cloud.databricks.com`.

## Prerequisites

- Databricks CLI v1.7 or newer (`databricks --version`). Already installed at
  `~/.local/bin/databricks` if installed via the script below.
- This repo cloned, venv active, `uv pip install -e ".[databricks]"` run.

## 1. Create the workspace

1. Sign up at <https://www.databricks.com/learn/free-edition>. No credit card.
2. Pick a cloud when prompted; any is fine, the pipeline is cloud-agnostic.
3. When the workspace opens, copy the URL from the address bar. It looks like
   `https://dbc-a1b2c3d4-e5f6.cloud.databricks.com`. That is your host.

## 2. Authenticate

```bash
export DATABRICKS_HOST="https://dbc-XXXXXXX.cloud.databricks.com"   # your URL
databricks auth login --host "$DATABRICKS_HOST"
```

A browser opens for OAuth. Credentials cache in `~/.databrickscfg`.

Verify:

```bash
databricks auth env          # host + token present
databricks current-user me   # your email
```

## 3. Validate the bundle

```bash
cd databricks/
databricks bundle validate
```

Expect `Found 0 errors` plus your host, user, and bundle path. An auth error
here means step 2 did not complete; re-run `databricks auth login`.

## 4. Create the catalog

Free Edition gives you Unity Catalog but not necessarily a catalog named
`anime_lakehouse`. Create it once, in a workspace SQL editor or notebook:

```sql
CREATE CATALOG IF NOT EXISTS anime_lakehouse;
CREATE SCHEMA  IF NOT EXISTS anime_lakehouse.bronze;
CREATE SCHEMA  IF NOT EXISTS anime_lakehouse.core;
```

To use a different catalog name, override it at deploy time:
`databricks bundle deploy -t dev --var="catalog=my_catalog"`, and set the same
name in `transform/profiles.yml` under the `databricks` target.

## 5. Deploy and run the ingest job

```bash
databricks bundle deploy -t dev
databricks bundle run -t dev catalog_refresh
```

The job runs `databricks/jobs/ingest_media.py` on serverless compute: it pulls
200 titles from the AniList API and MERGE-upserts them into
`anime_lakehouse.bronze.anilist_media` as a Delta table. Reruns are idempotent, so
running it twice is safe and is worth doing once to prove the MERGE.

## 6. Build the warehouse with dbt

Find the SQL warehouse HTTP path: workspace sidebar → SQL Warehouses → click
the warehouse → Connection details → copy **HTTP path**.

```bash
export DATABRICKS_HOST="https://dbc-XXXXXXX.cloud.databricks.com"
export DATABRICKS_HTTP_PATH="/sql/1.0/warehouses/xxxxxxxxxxxx"
cd transform/
dbt build --target databricks --profiles-dir .
```

Expect the same model and test counts as local DuckDB, now against Delta:
staging views plus `dim_title`, `dim_user`, `fact_playback`, `episode_dropoff`,
with the key and referential-integrity tests passing.

## 7. Verify

In the workspace, under Catalog → `anime_lakehouse`:

- [ ] `bronze.anilist_media` exists, roughly 200 rows
- [ ] `core.dim_title`, `core.dim_user`, `core.fact_playback`,
      `core.episode_dropoff` exist
- [ ] Jobs → `[anime-lakehouse] catalog refresh` shows a green run
- [ ] Run `SELECT * FROM anime_lakehouse.core.episode_dropoff ORDER BY ep2_retention DESC LIMIT 10`

Screenshot the green job run and the catalog tree for the README.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `bundle validate` reports an auth error | `databricks auth login --host "$DATABRICKS_HOST"` did not complete |
| `dbt build` cannot connect | `DATABRICKS_HTTP_PATH` is unset or wrong; recopy it from Connection details |
| `dbt build` says schema not found | Run the `CREATE CATALOG` / `CREATE SCHEMA` statements in step 4 |
| Job fails on a missing module | The job spec declares `requests`; confirm the `environments` block in `databricks.yml` survived deploy |
| Quota or capacity error | Free Edition has per-account serverless quotas; wait and rerun, or trim `--pages` on the ingest job |

## Notes

- Free Edition is serverless-only. The bundle's daily `trigger.periodic` is
  accepted but scheduled triggers are a paid-workspace feature; run the job
  manually with `databricks bundle run`.
- DuckDB stays the CI database. Databricks is the deploy surface, not the test
  surface, which is why `ci/gate.sh` needs no secrets.
