"""Score the mood-enrichment pipeline against the golden set.

Two layers of judgment, reported separately:
  1. Exact-set metrics vs golden labels — per-tag precision/recall/F1 plus
     Jaccard overlap per title. Deterministic, no LLM involved.
  2. Trace health — parse-failure rate, empty-tag rate, token cost per title.

Exit code is the regression gate: nonzero if macro-F1 drops below --min-f1 or
parse failures exceed --max-parse-fail. Wire this into CI so a prompt or model
change cannot silently degrade enrichment.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import duckdb

MOOD_TAGS = ["dark", "cozy", "hype", "melancholy", "absurd"]


def load_golden(path: Path) -> dict[int, set[str]]:
    golden = {}
    with path.open() as f:
        for line in f:
            row = json.loads(line)
            golden[row["media_id"]] = set(row["mood_tags"])
    return golden


def load_predictions(path: Path) -> dict[int, dict]:
    rows = duckdb.connect().execute(f"SELECT * FROM read_parquet('{path}')").to_arrow_table().to_pylist()
    return {r["media_id"]: r for r in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=Path("evals/golden/title_moods_golden.jsonl"))
    parser.add_argument("--predictions", type=Path, default=Path("data/enriched/title_moods.parquet"))
    parser.add_argument("--min-f1", type=float, default=0.6)
    parser.add_argument("--max-parse-fail", type=float, default=0.05)
    args = parser.parse_args()

    golden = load_golden(args.golden)
    preds = load_predictions(args.predictions)
    scored = {mid: preds[mid] for mid in golden if mid in preds}
    if not scored:
        raise SystemExit("no overlap between golden set and predictions — run enrichment on golden titles first")

    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    jaccards, parse_fails, total_tokens = [], 0, 0

    for mid, pred in scored.items():
        want, got = golden[mid], set(pred["mood_tags"] or [])
        if not got and pred.get("raw_response"):
            parse_fails += 1
        total_tokens += (pred.get("input_tokens") or 0) + (pred.get("output_tokens") or 0)
        union = want | got
        jaccards.append(len(want & got) / len(union) if union else 1.0)
        for tag in MOOD_TAGS:
            if tag in want and tag in got:
                tp[tag] += 1
            elif tag in got:
                fp[tag] += 1
            elif tag in want:
                fn[tag] += 1

    print(f"scored {len(scored)}/{len(golden)} golden titles\n")
    print(f"{'tag':<12} {'prec':>6} {'rec':>6} {'f1':>6}")
    f1s = []
    for tag in MOOD_TAGS:
        p = tp[tag] / (tp[tag] + fp[tag]) if tp[tag] + fp[tag] else 0.0
        r = tp[tag] / (tp[tag] + fn[tag]) if tp[tag] + fn[tag] else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        f1s.append(f1)
        print(f"{tag:<12} {p:>6.2f} {r:>6.2f} {f1:>6.2f}")

    macro_f1 = sum(f1s) / len(f1s)
    parse_fail_rate = parse_fails / len(scored)
    print(f"\nmacro-F1:        {macro_f1:.3f}  (gate: >= {args.min_f1})")
    print(f"mean Jaccard:    {sum(jaccards)/len(jaccards):.3f}")
    print(f"parse failures:  {parse_fail_rate:.1%}  (gate: <= {args.max_parse_fail:.0%})")
    print(f"tokens/title:    {total_tokens // max(len(scored), 1)}")

    if macro_f1 < args.min_f1 or parse_fail_rate > args.max_parse_fail:
        print("\nGATE FAILED", file=sys.stderr)
        sys.exit(1)
    print("\ngate passed")


if __name__ == "__main__":
    main()
