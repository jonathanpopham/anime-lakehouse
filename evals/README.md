# Enrichment evals

The mood-tag pipeline is only useful if its outputs are trustworthy, so the
eval is part of the pipeline, not an afterthought.

## Golden set

`golden/title_moods_golden.jsonl` — one JSON object per line:

```json
{"media_id": 1, "title": "Cowboy Bebop", "mood_tags": ["melancholy", "hype"]}
```

Labeling protocol (target: 50 titles):

1. Pull the 50 most-watched titles from `dim_title` (popularity-ranked, so the
   eval weights what users actually see).
2. Label each title with 1–3 tags from the fixed vocabulary
   (`dark, cozy, hype, melancholy, absurd`) reading only the synopsis — the
   same input the model sees. If you can't decide from the synopsis alone,
   the model can't either; note the ambiguity instead of consulting outside
   knowledge.
3. Second pass a week later on a shuffled order; keep only labels that agree
   with the first pass, adjudicate the rest deliberately. Disagreement rate is
   the human ceiling — the model can't be expected to beat it.

## Running

```bash
python evals/run_eval.py            # score + regression gate
python evals/run_eval.py --min-f1 0.7   # tighten the gate
```

The gate exits nonzero on regression: wire it into CI so prompt and model
changes must clear it before merge.

## What trace-based means here

Scores come from the same artifacts production emits: predictions parquet plus
Langfuse traces carrying latency, retries, and token cost per title. When the
gate fails, the failing trace is one click away — the eval tells you *which*
titles regressed and the trace tells you *why*.
