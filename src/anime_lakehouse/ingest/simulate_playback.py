"""Simulate Mux-style playback QoE events against the ingested catalog.

Stands in for a production playback event stream so the warehouse, dashboards,
and drop-off analysis run on realistic data. The generator plants a known
causal signal on purpose: each title carries a latent *tone* vector and each
user a tone preference, and continuation past episode 1 depends on tone match
more strongly than on genre match. The gold-layer analysis question — "do
LLM-derived mood tags predict episode-2 drop-off better than genre?" — has a
recoverable ground truth, which is what makes it usable as an eval for the
enrichment pipeline rather than a chart demo.

Determinism: same --seed, same catalog -> byte-identical output.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import random
from pathlib import Path

import duckdb

TONES = ["dark", "cozy", "hype", "melancholy", "absurd"]
DEVICES = ["tv", "mobile", "web", "console"]
REGIONS = ["NA", "LATAM", "EU", "APAC"]


def _title_tone(media_id: int, genres: list[str], rng: random.Random) -> list[float]:
    """Deterministic latent tone vector per title, loosely correlated with genre."""
    h = hashlib.sha256(f"tone:{media_id}".encode()).digest()
    base = [h[i] / 255.0 for i in range(len(TONES))]
    # nudge tones toward genre stereotypes so the signal is plausible, not random
    nudges = {
        "Horror": ("dark", 0.35), "Thriller": ("dark", 0.25), "Psychological": ("melancholy", 0.3),
        "Slice of Life": ("cozy", 0.4), "Comedy": ("absurd", 0.3), "Action": ("hype", 0.35),
        "Sports": ("hype", 0.3), "Drama": ("melancholy", 0.25), "Romance": ("cozy", 0.2),
    }
    for g in genres:
        if g in nudges:
            tone, w = nudges[g]
            base[TONES.index(tone)] = min(1.0, base[TONES.index(tone)] + w)
    norm = math.sqrt(sum(x * x for x in base)) or 1.0
    return [x / norm for x in base]


def _affinity(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def simulate(catalog: list[dict], n_users: int, seed: int, start: dt.datetime) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    tones = {c["media_id"]: _title_tone(c["media_id"], json.loads(c["genres"]), rng) for c in catalog}
    weights = [max(c["popularity"] or 1, 1) for c in catalog]

    users, events = [], []
    for uid in range(1, n_users + 1):
        pref = [rng.random() for _ in TONES]
        norm = math.sqrt(sum(x * x for x in pref)) or 1.0
        pref = [x / norm for x in pref]
        fav_genre = rng.choice(["Action", "Romance", "Comedy", "Drama", "Fantasy", "Sci-Fi"])
        device = rng.choices(DEVICES, weights=[40, 30, 20, 10])[0]
        region = rng.choices(REGIONS, weights=[45, 20, 20, 15])[0]
        users.append({
            "user_id": uid,
            "signup_date": (start - dt.timedelta(days=rng.randint(1, 900))).date().isoformat(),
            "primary_device": device,
            "region": region,
            "favorite_genre": fav_genre,
        })

        # draw distinct titles per user (choices samples with replacement, and a
        # duplicate title would collide on event_id, the downstream dedupe key)
        n_picks = rng.randint(2, 6)
        picked: dict[int, dict] = {}
        for candidate in rng.choices(catalog, weights=weights, k=n_picks * 4):
            picked.setdefault(candidate["media_id"], candidate)
            if len(picked) == n_picks:
                break
        for title in picked.values():
            tone_match = _affinity(pref, tones[title["media_id"]])
            genre_match = 1.0 if fav_genre in json.loads(title["genres"]) else 0.0
            # tone dominates continuation; genre is a weak nudge (the planted truth)
            p_continue = min(0.95, max(0.05, 0.15 + 0.85 * tone_match + 0.08 * genre_match))

            n_eps = min(title["episodes"] or 12, 24)
            session_t = start + dt.timedelta(minutes=rng.randint(0, 60 * 24 * 90))
            for ep in range(1, n_eps + 1):
                watch_pct = min(1.0, max(0.02, rng.betavariate(6, 2) if ep == 1 else rng.betavariate(8, 1.5)))
                events.append({
                    "event_id": f"{uid}-{title['media_id']}-{ep}",
                    "user_id": uid,
                    "media_id": title["media_id"],
                    "episode": ep,
                    "started_at": session_t.isoformat(),
                    "watch_pct": round(watch_pct, 4),
                    "completed": watch_pct >= 0.9,
                    "rebuffer_count": max(0, int(rng.gauss(1.2 if device == "mobile" else 0.4, 1))),
                    "startup_time_ms": max(120, int(rng.gauss(1400 if device == "mobile" else 900, 400))),
                    "device": device,
                    "region": region,
                })
                session_t += dt.timedelta(minutes=rng.randint(25, 60 * 48))
                if rng.random() > p_continue:
                    break
    return users, events


def _write_parquet(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".jsonl")
    with tmp.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    duckdb.connect().execute(
        f"COPY (SELECT * FROM read_json_auto('{tmp}')) TO '{out_path}' (FORMAT PARQUET)"
    )
    tmp.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bronze-root", type=Path, default=Path("data/bronze"))
    args = parser.parse_args()

    con = duckdb.connect()
    catalog = con.execute(
        f"SELECT media_id, genres, popularity, episodes FROM read_parquet('{args.bronze_root}/anilist_media/*/*.parquet')"
    ).to_arrow_table().to_pylist()
    if not catalog:
        raise SystemExit("no catalog in bronze — run the anilist ingest first")

    start = dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc)
    users, events = simulate(catalog, args.users, args.seed, start)
    _write_parquet(users, args.bronze_root / "users" / "users.parquet")
    _write_parquet(events, args.bronze_root / "playback_events" / "events.parquet")
    print(f"simulated {len(users)} users, {len(events)} playback events")


if __name__ == "__main__":
    main()
