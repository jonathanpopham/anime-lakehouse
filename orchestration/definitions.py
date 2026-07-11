"""Dagster definitions: the full lineage as software-defined assets.

    anilist_catalog -> playback_events -> dbt models (staging + marts)

Run the UI with:  dagster dev -f orchestration/definitions.py
Materializing `all` executes ingest -> simulate -> dbt build in dependency
order; the dbt models appear individually in the asset graph via dagster-dbt.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dagster import AssetExecutionContext, Definitions, asset
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

REPO_ROOT = Path(__file__).parent.parent
dbt_project = DbtProject(project_dir=REPO_ROOT / "transform", profiles_dir=REPO_ROOT / "transform")
dbt_project.prepare_if_dev()


@asset(group_name="bronze")
def anilist_catalog(context: AssetExecutionContext) -> None:
    """Top-popularity TV anime from the AniList public API, landed as parquet."""
    subprocess.run(
        [sys.executable, "-m", "anime_lakehouse.ingest.anilist", "--pages", "4"],
        cwd=REPO_ROOT, check=True,
    )


@asset(deps=[anilist_catalog], group_name="bronze")
def playback_events(context: AssetExecutionContext) -> None:
    """Simulated Mux-style QoE events over the ingested catalog (seeded, deterministic)."""
    subprocess.run(
        [sys.executable, "-m", "anime_lakehouse.ingest.simulate_playback"],
        cwd=REPO_ROOT, check=True,
    )


@dbt_assets(manifest=dbt_project.manifest_path)
def warehouse_models(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(["build"], context=context).stream()


defs = Definitions(
    assets=[anilist_catalog, playback_events, warehouse_models],
    resources={"dbt": DbtCliResource(project_dir=dbt_project)},
)
