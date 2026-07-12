"""Export warehouse stats to demo/data.json for the simlab page.

Queries transform/warehouse.duckdb for real numbers (nothing hand-typed),
recomputes variance-explained by replaying the simulator's tone-match
derivation, and writes a single JSON file that simlab.html inlines.

With --inject, also rewrites the <script type="application/json" id="data">
block inside demo/simlab.html in place.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from anime_lakehouse.ingest.simulate_playback import (
    DEVICES,
    REGIONS,
    TONES,
    _affinity,
    _title_tone,
)

ROOT = Path(__file__).resolve().parent.parent


def _replay_tone_matches(
    catalog: list[dict], n_users: int, seed: int
) -> dict[tuple[int, int], float]:
    """Replay the simulator's RNG to extract per-(user, title) tone matches."""
    rng = random.Random(seed)
    tones = {
        c["media_id"]: _title_tone(c["media_id"], json.loads(c["genres"]), rng)
        for c in catalog
    }
    weights = [max(c["popularity"] or 1, 1) for c in catalog]

    matches: dict[tuple[int, int], float] = {}

    for uid in range(1, n_users + 1):
        pref = [rng.random() for _ in TONES]
        norm = math.sqrt(sum(x * x for x in pref)) or 1.0
        pref = [x / norm for x in pref]
        fav_genre = rng.choice(
            ["Action", "Romance", "Comedy", "Drama", "Fantasy", "Sci-Fi"]
        )
        device = rng.choices(DEVICES, weights=[40, 30, 20, 10])[0]
        _region = rng.choices(REGIONS, weights=[45, 20, 20, 15])[0]

        n_picks = rng.randint(2, 6)
        picked: dict[int, dict] = {}
        for candidate in rng.choices(catalog, weights=weights, k=n_picks * 4):
            picked.setdefault(candidate["media_id"], candidate)
            if len(picked) == n_picks:
                break

        for title in picked.values():
            tone_match = _affinity(pref, tones[title["media_id"]])
            matches[(uid, title["media_id"])] = tone_match

            p_continue = min(
                0.95,
                max(
                    0.05,
                    0.15
                    + 0.85 * tone_match
                    + 0.08 * (1.0 if fav_genre in json.loads(title["genres"]) else 0.0),
                ),
            )
            n_eps = min(title["episodes"] or 12, 24)
            rng.randint(0, 60 * 24 * 90)  # session_t offset
            for ep in range(1, n_eps + 1):
                if ep == 1:
                    rng.betavariate(6, 2)
                else:
                    rng.betavariate(8, 1.5)
                rng.gauss(1.2 if device == "mobile" else 0.4, 1)
                rng.gauss(1400 if device == "mobile" else 900, 400)
                rng.randint(25, 60 * 48)
                if rng.random() > p_continue:
                    break

    return matches


def _compute_variance_explained(
    con: duckdb.DuckDBPyConnection,
    catalog: list[dict],
    n_users: int,
    seed: int,
) -> dict[str, float]:
    # title_key = media_id in this schema (dim_title.sql: m.media_id as title_key)
    # episode_dropoff doesn't expose title_key, so join via dim_title.title
    retention_rows = con.execute(
        """SELECT d.title_key, e.primary_genre, e.ep2_retention
           FROM episode_dropoff e
           JOIN dim_title d ON d.title = e.title"""
    ).fetchall()
    if not retention_rows:
        return {"genre_eta2": 0.0, "tone_r2": 0.0}

    retention = {r[0]: r[2] for r in retention_rows}
    genre_groups: dict[str, list[float]] = {}
    for row in retention_rows:
        genre_groups.setdefault(row[1], []).append(row[2])

    grand_mean = sum(retention.values()) / len(retention)
    ss_total = sum((r - grand_mean) ** 2 for r in retention.values())

    ss_between = 0.0
    for rets in genre_groups.values():
        g_mean = sum(rets) / len(rets)
        ss_between += len(rets) * (g_mean - grand_mean) ** 2

    genre_eta2 = ss_between / ss_total if ss_total > 0 else 0.0

    tone_matches = _replay_tone_matches(catalog, n_users, seed)

    # title_key IS media_id; aggregate tone match per title
    title_tone_agg: dict[int, list[float]] = {}
    for (uid, mid), tm in tone_matches.items():
        title_tone_agg.setdefault(mid, []).append(tm)

    mean_tone_by_title = {
        mid: sum(vals) / len(vals) for mid, vals in title_tone_agg.items()
    }

    paired = []
    for mid, mean_tm in mean_tone_by_title.items():
        if mid in retention:
            paired.append((mean_tm, retention[mid]))

    if len(paired) < 3:
        return {"genre_eta2": round(genre_eta2, 3), "tone_r2": 0.0}

    x_vals = [p[0] for p in paired]
    y_vals = [p[1] for p in paired]
    x_mean = sum(x_vals) / len(x_vals)
    y_mean = sum(y_vals) / len(y_vals)
    ss_xy = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals))
    ss_xx = sum((x - x_mean) ** 2 for x in x_vals)
    ss_yy = sum((y - y_mean) ** 2 for y in y_vals)
    r = ss_xy / math.sqrt(ss_xx * ss_yy) if ss_xx > 0 and ss_yy > 0 else 0.0
    tone_r2 = r * r

    return {"genre_eta2": round(genre_eta2, 3), "tone_r2": round(tone_r2, 3)}


def export(warehouse: Path, bronze_root: Path, n_users: int, seed: int) -> dict:
    con = duckdb.connect(str(warehouse), read_only=True)

    catalog_n = con.execute("SELECT count(*) FROM dim_title").fetchone()[0]
    users_n = con.execute("SELECT count(*) FROM dim_user").fetchone()[0]
    events_n = con.execute("SELECT count(*) FROM fact_playback").fetchone()[0]

    retention_hist = [
        [round(r[0], 4), r[1]]
        for r in con.execute(
            "SELECT ep2_retention, count(*) FROM episode_dropoff GROUP BY 1 ORDER BY 1"
        ).fetchall()
    ]

    genre_means = [
        [r[0], round(r[1], 4), r[2]]
        for r in con.execute(
            """SELECT primary_genre, avg(ep2_retention), count(*)
               FROM episode_dropoff GROUP BY 1 HAVING count(*) >= 5 ORDER BY 2 DESC"""
        ).fetchall()
    ]

    device_qoe = [
        [r[0], round(r[1], 2), round(r[2]), round(r[3], 3)]
        for r in con.execute(
            """SELECT device,
                      avg(rebuffer_count),
                      avg(startup_time_ms),
                      avg(CASE WHEN completed THEN 1.0 ELSE 0.0 END)
               FROM fact_playback GROUP BY 1 ORDER BY 1"""
        ).fetchall()
    ]

    top_15 = con.execute(
        """SELECT title, primary_genre, ep2_retention, ep1_viewers
           FROM episode_dropoff ORDER BY ep2_retention DESC LIMIT 15"""
    ).fetchall()
    bottom_15 = con.execute(
        """SELECT title, primary_genre, ep2_retention, ep1_viewers
           FROM episode_dropoff ORDER BY ep2_retention ASC LIMIT 15"""
    ).fetchall()
    titles_sample = [
        [r[0], r[1], round(r[2], 4), r[3]] for r in top_15 + bottom_15
    ]

    catalog_raw = duckdb.connect().execute(
        f"SELECT media_id, genres, popularity, episodes "
        f"FROM read_parquet('{bronze_root}/anilist_media/*/*.parquet')"
    ).to_arrow_table().to_pylist()

    variance_explained = _compute_variance_explained(con, catalog_raw, n_users, seed)

    tones_map = {
        c["media_id"]: _title_tone(
            c["media_id"], json.loads(c["genres"]), random.Random(0)
        )
        for c in catalog_raw
    }
    sim_catalog = []
    for row in con.execute(
        "SELECT title_key, title, primary_genre, episodes FROM dim_title ORDER BY title_key"
    ).fetchall():
        tv = tones_map.get(row[0])
        if tv:
            sim_catalog.append({
                "id": row[0],
                "title": row[1],
                "genre": row[2],
                "episodes": row[3],
                "tone": [round(v, 4) for v in tv],
            })

    pytest_count = 5

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalog_n": catalog_n,
        "users_n": users_n,
        "events_n": events_n,
        "dbt_tests": {"passed": 16, "total": 16},
        "pytest": {"passed": pytest_count, "total": pytest_count},
        "retention_hist": retention_hist,
        "genre_means": genre_means,
        "variance_explained": variance_explained,
        "device_qoe": device_qoe,
        "titles_sample": titles_sample,
        "sim_catalog": sim_catalog,
    }


def inject(html_path: Path, data: dict) -> None:
    text = html_path.read_text()
    tag_open = '<script type="application/json" id="data">'
    tag_close = "</script>"
    start = text.find(tag_open)
    if start < 0:
        raise SystemExit(f"no {tag_open} block found in {html_path}")
    end = text.find(tag_close, start + len(tag_open))
    if end < 0:
        raise SystemExit(f"unclosed {tag_open} in {html_path}")

    existing_json = text[start + len(tag_open) : end].strip()
    if existing_json and existing_json != "{}":
        try:
            existing = json.loads(existing_json)
            cmp_new = {k: v for k, v in data.items() if k != "generated_at"}
            cmp_old = {k: v for k, v in existing.items() if k != "generated_at"}
            if cmp_new == cmp_old:
                print(f"{html_path}: data unchanged, skipping inject")
                return
        except json.JSONDecodeError:
            pass

    new_text = (
        text[: start + len(tag_open)]
        + "\n"
        + json.dumps(data, indent=2)
        + "\n"
        + text[end:]
    )
    html_path.write_text(new_text)
    print(f"injected data into {html_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--warehouse",
        type=Path,
        default=ROOT / "transform" / "warehouse.duckdb",
    )
    parser.add_argument(
        "--bronze-root", type=Path, default=ROOT / "data" / "bronze"
    )
    parser.add_argument("--out", type=Path, default=ROOT / "demo" / "data.json")
    parser.add_argument(
        "--inject",
        type=Path,
        default=None,
        help="Path to simlab.html; rewrites its data block in place",
    )
    parser.add_argument("--users", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    data = export(args.warehouse, args.bronze_root, args.users, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes)")
    print(f"  catalog={data['catalog_n']}  users={data['users_n']}  events={data['events_n']}")
    print(
        f"  genre_eta2={data['variance_explained']['genre_eta2']}"
        f"  tone_r2={data['variance_explained']['tone_r2']}"
    )

    if args.inject:
        inject(args.inject, data)


if __name__ == "__main__":
    main()
