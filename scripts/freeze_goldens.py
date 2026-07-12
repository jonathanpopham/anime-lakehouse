"""Freeze golden CSV snapshots from the seeded warehouse.

Writes deterministic CSVs (sorted, stable column subset, 4dp float rounding)
that ci/gate.sh uses as a regression gate. In --check mode, compares the
current warehouse against the frozen goldens and exits 1 on any drift.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import io
import sys
from pathlib import Path

import duckdb

WAREHOUSE = Path("transform/warehouse.duckdb")
GOLDEN_DIR = Path("tests/golden")

GOLDENS = {
    "episode_dropoff": {
        "query": (
            "SELECT title, primary_genre, ep1_viewers, ep2_viewers, "
            "ROUND(ep2_retention, 4) AS ep2_retention "
            "FROM episode_dropoff ORDER BY title"
        ),
        "file": "episode_dropoff.golden.csv",
    },
    "dim_title_summary": {
        "query": (
            "SELECT title_key, title, primary_genre, average_score, "
            "popularity, episodes "
            "FROM dim_title ORDER BY title_key"
        ),
        "file": "dim_title_summary.golden.csv",
    },
}


def query_to_csv(con: duckdb.DuckDBPyConnection, query: str) -> str:
    result = con.execute(query)
    columns = [desc[0] for desc in result.description]
    rows = result.fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(
            format_value(v) for v in row
        )
    return buf.getvalue()


def format_value(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.4f}"
    return v


def freeze(golden_dir: Path) -> None:
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    golden_dir.mkdir(parents=True, exist_ok=True)

    for name, spec in GOLDENS.items():
        content = query_to_csv(con, spec["query"])
        out = golden_dir / spec["file"]
        out.write_text(content)
        rows = content.count("\n") - 1
        print(f"wrote {out} ({rows} rows)")


def check(golden_dir: Path) -> bool:
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    all_ok = True

    for name, spec in GOLDENS.items():
        golden_path = golden_dir / spec["file"]
        if not golden_path.exists():
            print(f"SKIP {name}: {golden_path} not found")
            continue

        expected = golden_path.read_text()
        actual = query_to_csv(con, spec["query"])

        if expected == actual:
            print(f"OK   {name}")
        else:
            all_ok = False
            print(f"FAIL {name}: drift detected")
            diff = difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=f"golden/{spec['file']}",
                tofile="warehouse (current)",
                n=3,
            )
            sys.stdout.writelines(diff)

    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare warehouse against frozen goldens; exit 1 on drift",
    )
    parser.add_argument(
        "--golden-dir",
        type=Path,
        default=GOLDEN_DIR,
        help="Directory containing golden CSV files",
    )
    args = parser.parse_args()

    if not WAREHOUSE.exists():
        print(f"ERROR: warehouse not found at {WAREHOUSE}", file=sys.stderr)
        sys.exit(1)

    if args.check:
        ok = check(args.golden_dir)
        sys.exit(0 if ok else 1)
    else:
        freeze(args.golden_dir)


if __name__ == "__main__":
    main()
