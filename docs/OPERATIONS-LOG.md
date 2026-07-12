# Operations log: the swarm run

Date: 2026-07-11. Integrator: operator session. Agents: 4 (3 implementers, 1 reviewer).
Method: plan in markdown, decompose to a bead graph, dispatch parallel agents in
isolated worktrees on disjoint file sets, cross-review before merge, integrate
under distrust-green, audit after.

This document records what happened, including the mistakes. It is part of the
deliverable: the technique is as much the product as the pipeline is.

## 1. Work graph

22 beads, prefix `aml`, zero dependency cycles. Planning and bead creation
preceded all implementation code.

| wave | beads |
|------|-------|
| 1 | gate.sh, CI workflow + runner setup, CI runbook, bundle validate, Databricks runbook, labboard server, labboard UI, labboard tests, simlab export, simlab page |
| 2 | golden regression + gate 5, labeling tool, integrate, compliance audit, audit round, publish, README, operations log |
| backlog | golden-set labeling by hand, live enrichment run, Databricks deploy, idea-wizard wave 3 |

Work order was selected with `bvr --robot-triage` (PageRank and betweenness over
the dependency graph), not by intuition. Dispatches went to the highest-scoring
unblocked beads.

## 2. Dispatch table

| agent | pane | worktree branch | domain (owned files) | beads |
|-------|------|-----------------|----------------------|-------|
| ci | 1 | `ntm/anime-lakehouse/cc_1` | `ci/**`, `.github/**`, `databricks/**`, 2 runbooks | gate.sh, workflow, runbooks, bundle, goldens |
| labboard | 2 | `ntm/anime-lakehouse/cc_2` | `labboard/**`, `evals/golden/**` | server, UI, tests, labeling tool |
| simlab | 3 | `ntm/anime-lakehouse/cc_3` | `demo/**`, `scripts/export_demo_data.py` | export, simulation page |
| reviewer | 4 | (read-only, no worktree) | none | cross-review of all three branches |

Every marching order carried: worktree path, files owned, files forbidden,
acceptance gates, and the instruction to report honestly, because a found bug is
a win rather than a failure to hide.

Contracts in `docs/PLAN-wave1-flywheel.md` section 3 were frozen before dispatch:
the NDJSON event stream, the run-record schema, the gate order, the labboard HTTP
surface, and the data-export shape. This is what let the labboard agent build
against `gate.sh` while the CI agent was still writing it. The fake-gate fixture
is the contract test.

## 3. Interventions

| # | observation | action | evidence it landed |
|---|-------------|--------|--------------------|
| 1 | Panes 1 and 2 had unsubmitted prompts staged that would have had them work each other's beads | Cleared the input lines, dispatched disjoint wave-2 beads instead | Lane commits stayed inside their declared domains |
| 2 | simlab's first draft carried narrative headlines and interpretive captions | Mid-build course correction: data-purity rules (doctrine D8), narrative removed, tables and raw-JSON download added | Commit `237a90a`; reviewer and integrator greps both confirm zero em dashes, zero narrative strings |
| 3 | Reviewer report scrolled out of the pane buffer before it could be read | Instructed the reviewer to persist the full report to a file | Report captured, all seven findings actionable |
| 4 | Reviewer pane had a stray "merge these branches" instruction staged in its input | Operator override: merging is the integrator's job, reviewer stays read-only | Reviewer touched no files |

Ground truth for every intervention came from pane tails, `git log` in the
worktrees, and bead state. No agent self-report was accepted as evidence.

## 4. Cross-review before merge

The reviewer audited all three branches against the frozen contracts before any
merge. Findings: 0 critical, 2 high, 2 medium, 3 low.

| severity | lane | defect |
|----------|------|--------|
| HIGH | ci | `eval` on a constructed dbt command string. The `--vars` YAML literal word-splits, so the enriched build path breaks |
| HIGH | labboard | Path traversal: `run_id` from the query string was used to build a file path without validation |
| MEDIUM | labboard | `stderr=STDOUT` mixed non-JSON lines into the NDJSON stream, which would break the UI parser mid-run |
| MEDIUM | labboard | No length or format validation on `run_id` |
| LOW | ci | `xxd` dependency (ships with vim, absent on minimal Linux) |
| LOW | ci | dbt `--vars` quoting fragility |
| LOW | labboard | Non-JSON lines polluting the live NDJSON file |

Both HIGH findings were fixed on the originating branch before merge (`f385396`,
`44e7ff2`). The simlab lane was clean.

## 5. Integration

Merge order: ci, labboard, simlab. Each merge is a no-fast-forward commit naming
the agent, its beads, and the review outcome.

```
f813135  merge(ci): gate lattice, CI workflow, runbooks, goldens
311b868  merge(labboard): SSE server, instrument-panel UI, smoke tests, labeling tool
61ac212  merge(simlab): data export + self-contained simulation page
```

Integrator-owned files reconciled at merge: `pyproject.toml` (pytest discovery),
`.gitignore`, `README.md`.

## 6. Independent verification (distrust-green)

Every gate was re-run by the integrator. Agent-reported results were treated as
hypotheses.

| check | result |
|-------|--------|
| `ci/gate.sh` | exit 0. pytest pass, determinism pass, dbt_build pass (16 of 16), eval_gate skip (no predictions yet), goldens pass |
| `ci/gate.sh --robot` | every NDJSON line parses with `jq`; run record written to disk |
| `pytest` | 21 passed |
| `ci/test_gate.sh` | 8 passed |
| goldens, negative test | mutation applied to `episode_dropoff.sql` in a throwaway copy: check exits 1 with a printed diff |
| labboard | server live: `/` 200, `/api/runs` lists the real run, `/label` 200 |
| simlab export | idempotent: second `--inject` run is a no-op |
| `databricks bundle validate` | schema resolves; full validation requires workspace auth, which the runbook sequences |

**Defect found during verification:** `gate.sh` wrote its run record to a
directory it did not create, so on a fresh checkout the write failed while the
run still reported success and exited 0. A gate that lies about its own artifact
is exactly the class of bug this protocol exists to catch. Fixed before the beads
were closed.

## 7. Audit round

The swarm was flipped to review-only mode on integrated main, split by domain.

| domain | findings |
|--------|----------|
| docs and CI config | 1 high, 3 medium, 14 low |
| UX and accessibility | 3 high, 5 medium, 4 low |
| correctness and security | 0 high, 0 medium, 1 low |

The security auditor independently re-verified both pre-merge HIGH fixes: the
enriched-vars branch of `gate.sh` executes dbt correctly (16 of 16 models), and
seven traversal payloads against the live labboard server all return 400 while a
valid run id returns 200.

Highest-value audit findings, fixed by the integrator:

- The CI runbook documented a manual "Run workflow" button that could not exist,
  because the workflow had no `workflow_dispatch` trigger. Added.
- `labboard/index.html` carried an em dash in its title, violating the same D8
  doctrine the operator had imposed on the simlab agent. Fixed.
- README deploy commands disagreed with the bundle and the runbook. README
  rewritten.

Remaining medium findings (contrast pairs in the labboard dark theme, focus-visible
styles on the labeling pages) are filed for wave 3 rather than silently closed.

## 8. Outcome

| metric | value |
|--------|-------|
| beads closed with evidence | 16 |
| false closes found by the compliance audit | 0 |
| review findings caught before merge | 7 |
| audit findings after merge | 31 (1 high fixed, 1 high fixed, rest triaged) |
| defects the integrator found that all agents missed | 1 (run-record directory) |
| lost merges | 0 |
| gates on every change | 5 |
