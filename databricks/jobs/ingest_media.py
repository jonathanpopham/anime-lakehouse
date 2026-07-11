"""Databricks job: ingest the AniList catalog into a Unity Catalog Delta table.

The PySpark twin of src/anime_lakehouse/ingest/anilist.py — same API contract,
same bronze columns, but landing as a Delta table (bronze.anilist_media) with
MERGE upserts instead of parquet files, so reruns are idempotent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time

import requests
from pyspark.sql import SparkSession, functions as F

ANILIST_URL = "https://graphql.anilist.co"

QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage }
    media(type: ANIME, sort: POPULARITY_DESC, format_in: [TV, TV_SHORT]) {
      id title { romaji english } genres tags { name rank } description
      averageScore popularity episodes season seasonYear format status
    }
  }
}
"""


def fetch_pages(pages: int, per_page: int = 50) -> list[dict]:
    rows: list[dict] = []
    for page in range(1, pages + 1):
        resp = requests.post(
            ANILIST_URL,
            json={"query": QUERY, "variables": {"page": page, "perPage": per_page}},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()["data"]["Page"]
        rows.extend(payload["media"])
        if not payload["pageInfo"]["hasNextPage"]:
            break
        time.sleep(0.8)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="anime_lakehouse")
    parser.add_argument("--pages", type=int, default=4)
    args = parser.parse_args()

    spark = SparkSession.builder.getOrCreate()
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {args.catalog}.bronze")

    ingested_at = dt.datetime.now(dt.timezone.utc).isoformat()
    rows = [
        {
            "media_id": r["id"],
            "title_romaji": (r.get("title") or {}).get("romaji"),
            "title_english": (r.get("title") or {}).get("english"),
            "genres": json.dumps(r.get("genres") or []),
            "tags": json.dumps(r.get("tags") or []),
            "description_html": r.get("description"),
            "average_score": r.get("averageScore"),
            "popularity": r.get("popularity"),
            "episodes": r.get("episodes"),
            "season": r.get("season"),
            "season_year": r.get("seasonYear"),
            "format": r.get("format"),
            "status": r.get("status"),
            "_ingested_at": ingested_at,
            "_source": "anilist_graphql",
        }
        for r in fetch_pages(args.pages)
    ]
    incoming = spark.createDataFrame(rows).withColumn("_ingested_at", F.col("_ingested_at").cast("timestamp"))

    target = f"{args.catalog}.bronze.anilist_media"
    if not spark.catalog.tableExists(target):
        incoming.write.format("delta").saveAsTable(target)
    else:
        incoming.createOrReplaceTempView("incoming")
        spark.sql(f"""
            MERGE INTO {target} t USING incoming s ON t.media_id = s.media_id
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
        """)
    print(f"merged {len(rows)} rows into {target}")


if __name__ == "__main__":
    main()
