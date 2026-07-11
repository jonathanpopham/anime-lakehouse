"""Ingest anime catalog metadata from the AniList public GraphQL API into the bronze layer.

Bronze contract: raw API responses land as-is (one row per media item, nested
fields preserved as JSON strings) plus ingestion metadata. No cleaning here —
that's silver's job.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import duckdb
import requests

ANILIST_URL = "https://graphql.anilist.co"

QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage }
    media(type: ANIME, sort: POPULARITY_DESC, format_in: [TV, TV_SHORT]) {
      id
      title { romaji english }
      genres
      tags { name rank }
      description
      averageScore
      popularity
      episodes
      season
      seasonYear
      format
      status
    }
  }
}
"""


def fetch_pages(pages: int, per_page: int = 50, sleep_s: float = 0.8) -> list[dict]:
    """Fetch top-popularity TV anime, politely (AniList allows ~90 req/min)."""
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
        time.sleep(sleep_s)
    return rows


def land_bronze(rows: list[dict], bronze_root: Path) -> Path:
    """Write one parquet file per ingestion run, partitioned by ingest date."""
    ingested_at = dt.datetime.now(dt.timezone.utc)
    out_dir = bronze_root / "anilist_media" / f"ingest_date={ingested_at:%Y-%m-%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"media_{ingested_at:%H%M%S}.parquet"

    flat = [
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
            "_ingested_at": ingested_at.isoformat(),
            "_source": "anilist_graphql",
        }
        for r in rows
    ]

    con = duckdb.connect()
    # json round-trip keeps parquet schema inference simple and explicit
    tmp = out_dir / "_tmp.jsonl"
    with tmp.open("w") as f:
        for row in flat:
            f.write(json.dumps(row) + "\n")
    con.execute(
        f"COPY (SELECT * FROM read_json_auto('{tmp}')) TO '{out_path}' (FORMAT PARQUET)"
    )
    tmp.unlink()
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=4, help="pages of 50 titles to fetch")
    parser.add_argument("--bronze-root", type=Path, default=Path("data/bronze"))
    args = parser.parse_args()

    rows = fetch_pages(args.pages)
    out = land_bronze(rows, args.bronze_root)
    print(f"landed {len(rows)} media rows -> {out}")


if __name__ == "__main__":
    main()
