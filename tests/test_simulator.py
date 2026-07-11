import datetime as dt
import json

from anime_lakehouse.ingest.simulate_playback import TONES, _title_tone, simulate
import random

CATALOG = [
    {"media_id": 1, "genres": json.dumps(["Action", "Sci-Fi"]), "popularity": 900000, "episodes": 26},
    {"media_id": 2, "genres": json.dumps(["Slice of Life", "Comedy"]), "popularity": 500000, "episodes": 12},
    {"media_id": 3, "genres": json.dumps(["Horror", "Psychological"]), "popularity": 300000, "episodes": 24},
]
START = dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc)


def test_deterministic_given_seed():
    a = simulate(CATALOG, n_users=50, seed=7, start=START)
    b = simulate(CATALOG, n_users=50, seed=7, start=START)
    assert a == b


def test_different_seed_differs():
    a = simulate(CATALOG, n_users=50, seed=7, start=START)
    b = simulate(CATALOG, n_users=50, seed=8, start=START)
    assert a != b


def test_event_shape_and_bounds():
    users, events = simulate(CATALOG, n_users=100, seed=1, start=START)
    assert len(users) == 100
    assert events, "expected at least one playback event"
    for e in events:
        assert 0 < e["watch_pct"] <= 1
        assert e["episode"] >= 1
        assert e["rebuffer_count"] >= 0
        assert e["media_id"] in {c["media_id"] for c in CATALOG}
    # event_id is the dedupe key downstream
    ids = [e["event_id"] for e in events]
    assert len(ids) == len(set(ids))


def test_tone_vector_is_unit_norm_and_deterministic():
    rng = random.Random(0)
    t1 = _title_tone(1, ["Horror"], rng)
    t2 = _title_tone(1, ["Horror"], rng)
    assert t1 == t2
    assert abs(sum(x * x for x in t1) - 1.0) < 1e-9
    assert len(t1) == len(TONES)


def test_planted_signal_dropoff_varies_by_title():
    """Episode-2 retention must differ meaningfully across titles — the whole
    point of the planted tone signal. If retention is flat, the analysis
    story downstream is dead."""
    _, events = simulate(CATALOG, n_users=2000, seed=42, start=START)
    retention = {}
    for c in CATALOG:
        ep1 = {e["user_id"] for e in events if e["media_id"] == c["media_id"] and e["episode"] == 1}
        ep2 = {e["user_id"] for e in events if e["media_id"] == c["media_id"] and e["episode"] == 2}
        if ep1:
            retention[c["media_id"]] = len(ep2) / len(ep1)
    assert retention
    assert max(retention.values()) - min(retention.values()) > 0.02
