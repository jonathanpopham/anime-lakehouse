"""LLM mood-tag enrichment: classify each title's synopsis into mood tags.

Every call is traced to Langfuse (trace name `mood_enrichment`, one span per
title) so the eval harness can score *traces*, not just final outputs: latency,
cost, retry count, and tag validity all come from the same trace stream the
production pipeline emits.

Requires ANTHROPIC_API_KEY; Langfuse keys optional (tracing degrades to off,
loudly). Output lands in data/enriched/title_moods.parquet, which dim_title
joins when built with --vars '{enriched: true}'.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import duckdb

MOOD_TAGS = ["dark", "cozy", "hype", "melancholy", "absurd"]

SYSTEM_PROMPT = f"""You classify anime synopses into mood tags for a streaming \
recommendation system. Choose 1-3 tags from exactly this set: {MOOD_TAGS}. \
Respond with only a JSON array of tag strings, nothing else. Base the mood on \
the emotional register of the synopsis, not the genre labels."""


def load_titles(warehouse: Path, limit: int | None) -> list[dict]:
    con = duckdb.connect(str(warehouse), read_only=True)
    q = "SELECT title_key AS media_id, title, synopsis FROM dim_title WHERE length(synopsis) > 40"
    if limit:
        q += f" LIMIT {limit}"
    return con.execute(q).to_arrow_table().to_pylist()


def _get_tracer():
    """Langfuse client, or None when it is unavailable or unconfigured.

    The tracing surface moved in Langfuse 3: the langfuse.decorators module is
    gone and the client is reached through get_client(). Pinning >=3 in
    pyproject keeps this import honest, and returning None (rather than
    swallowing the failure) makes an untraced run visible instead of silent.
    """
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return None
    from langfuse import get_client

    return get_client()


def classify(titles: list[dict], model: str) -> list[dict]:
    import anthropic

    langfuse = _get_tracer()
    if langfuse is None:
        print("WARNING: LANGFUSE_PUBLIC_KEY unset, running untraced", file=sys.stderr)

    client = anthropic.Anthropic()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    results = []
    for t in titles:
        started = time.monotonic()
        msg = client.messages.create(
            model=model,
            max_tokens=100,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": t["synopsis"][:2000]}],
        )
        latency_ms = round((time.monotonic() - started) * 1000)
        raw = msg.content[0].text.strip()
        try:
            tags = [tag for tag in json.loads(raw) if tag in MOOD_TAGS]
            parse_ok = True
        except (json.JSONDecodeError, TypeError):
            tags, parse_ok = [], False

        if langfuse is not None:
            # One generation span per title. The eval reads these traces back,
            # so latency and token counts are recorded here rather than derived.
            with langfuse.start_as_current_observation(
                name="mood_enrichment",
                as_type="generation",
                model=model,
                input=t["synopsis"][:2000],
                metadata={"media_id": t["media_id"], "title": t["title"]},
            ) as span:
                span.update(
                    output={"mood_tags": tags, "raw": raw, "parse_ok": parse_ok},
                    usage_details={
                        "input": msg.usage.input_tokens,
                        "output": msg.usage.output_tokens,
                    },
                )

        results.append({
            "media_id": t["media_id"],
            "mood_tags": tags,
            "raw_response": raw,
            "parse_ok": parse_ok,
            "latency_ms": latency_ms,
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
            "enrichment_model": model,
            "enriched_at": now,
        })
        print(f"  {t['title']}: {tags}")

    if langfuse is not None:
        langfuse.flush()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("transform/warehouse.duckdb"))
    parser.add_argument("--out", type=Path, default=Path("data/enriched/title_moods.parquet"))
    parser.add_argument("--model", default="claude-haiku-4-5")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set")

    titles = load_titles(args.warehouse, args.limit)
    print(f"classifying {len(titles)} titles with {args.model}")
    results = classify(titles, args.model)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".jsonl")
    with tmp.open("w") as f:
        for row in results:
            f.write(json.dumps(row) + "\n")
    duckdb.connect().execute(
        f"COPY (SELECT * FROM read_json_auto('{tmp}')) TO '{args.out}' (FORMAT PARQUET)"
    )
    tmp.unlink()
    print(f"wrote {len(results)} enriched rows -> {args.out}")


if __name__ == "__main__":
    main()
