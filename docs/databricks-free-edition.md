# Databricks Free Edition: 30-Minute Deploy Runbook

Get anime-lakehouse running on Databricks Community/Free Edition. End state:
`dbt build --target databricks` green, pipeline job visible in the workspace.

## Prerequisites

- A Databricks account (Free/Community Edition is fine)
- Databricks CLI v1.7+ installed locally (`databricks --version`)
- This repo cloned with the venv active

Install the CLI if needed:
```bash
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sudo sh
```

## 1. Create workspace (~5 min)

1. Go to https://community.cloud.databricks.com/login.html
2. Sign up with your email (no credit card required for Community Edition)
3. Once in, note your workspace URL from the browser bar:
   `https://community.cloud.databricks.com` (or `https://<prefix>.cloud.databricks.com`)

## 2. Authenticate (~2 min)

```bash
export DATABRICKS_HOST="https://community.cloud.databricks.com"
databricks auth login
```

This opens a browser for OAuth. After login, credentials are cached in
`~/.databrickscfg`.

Verify:
```bash
databricks auth env
# Should show DATABRICKS_HOST and DATABRICKS_TOKEN set
```

## 3. Validate the bundle (~1 min)

```bash
cd databricks/
databricks bundle validate
```

Expected output:
```
Name: anime-lakehouse
Target: dev
Workspace:
  Host: https://community.cloud.databricks.com
  User: your@email.com
  Path: /Users/your@email.com/.bundle/anime-lakehouse/dev

Found 0 errors
```

## 4. Deploy (~3 min)

```bash
databricks bundle deploy -t dev
```

This creates:
- A job named `[dev your@email] [anime-lakehouse] catalog refresh`
- Uploaded Python files in your workspace

## 5. Run dbt against Databricks (~10 min)

First, set the connection environment variables:

```bash
export DATABRICKS_HOST="https://community.cloud.databricks.com"
export DATABRICKS_HTTP_PATH="/sql/1.0/warehouses/<your-warehouse-id>"
```

To find your warehouse ID:
1. In the Databricks workspace → SQL Warehouses
2. Click your warehouse → Connection Details
3. Copy the HTTP Path

Then run dbt:
```bash
cd transform/
dbt build --target databricks --profiles-dir .
```

Expected: 7 models + 9 tests pass (same as local DuckDB, but on Delta tables).

## 6. Run the job (~5 min)

```bash
databricks bundle run -t dev catalog_refresh
```

Or from the UI: Jobs → find `[anime-lakehouse] catalog refresh` → Run Now.

The job:
1. Ingests 200 titles from AniList into `anime_lakehouse.bronze.anilist_media`
2. Runs `dbt build --target databricks` for the full warehouse

## 7. Verify and screenshot

Check the Databricks UI:
- [ ] Job run completed successfully (green)
- [ ] `anime_lakehouse.bronze.anilist_media` table exists with ~200 rows
- [ ] `anime_lakehouse.core.dim_title`, `dim_user`, `fact_playback`, `episode_dropoff` exist

Take a screenshot of the successful job run for the README.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `bundle validate` → auth error | Re-run `databricks auth login` |
| `dbt build` → connection refused | Check DATABRICKS_HTTP_PATH is set correctly |
| `dbt build` → schema not found | The bundle deploy creates the catalog/schema; run step 4 first |
| Community Edition → no SQL Warehouse | Community Edition has limited SQL features; use a starter/paid tier for full SQL Warehouse support, or run dbt via the job (step 6) which uses serverless compute |
| Job fails → module not found | The job runs in Databricks runtime; `requests` is pre-installed but verify the environment spec |

## Notes

- **Community Edition limitations**: no Unity Catalog, limited clusters, no SQL
  Warehouses. For the full experience (catalog governance, scheduled jobs,
  SQL analytics), use the Free Trial or a paid workspace.
- The bundle's `trigger.periodic` (daily) only runs on paid workspaces.
  On Community Edition, use manual runs via `databricks bundle run`.
- Local DuckDB remains the CI database — Databricks is the deploy surface,
  not the test surface.
