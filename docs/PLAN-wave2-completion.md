# anime-lakehouse Wave 2: completion, coordination, audit

Status: ACTIVE. Integrator: operator session.
Date: 2026-07-11. Bead prefix: aml. Predecessor: docs/PLAN-wave1-flywheel.md.

## 1. Scope

Wave 1 built the platform surfaces (gate lattice, labboard, simlab, Databricks
readiness) via a 3-agent worktree swarm. Wave 2 finishes the run: regression
goldens, the labeling tool, integration under an auditable protocol, a
review-only audit round, a compliance audit of closed beads, an operations log
documenting the swarm run itself, and a wave-3 ideation round.

## 2. Doctrines adopted mid-run (normative from this point)

**D8. Data purity in outward pages.** simlab and any future public page present
data and visualizers only: metric names, units, counts, distributions,
controls, raw-data table views, downloadable JSON. No narrative headlines, no
interpretive captions, no em dashes in copy. Provenance strips state facts
(row counts, seed, source, computation names). The reader draws conclusions.

**D9. Process as product.** The techniques used to build this repo are part of
the deliverable. Therefore: plan documents and .beads are committed; every
commit references its bead ID where one applies; each agent lane merges via a
no-fast-forward merge commit naming the agent, its beads, and the independent
re-verification result; docs/OPERATIONS-LOG.md records dispatches,
interventions with evidence, and gate results. The history must support this
audit query: for any line of code, which bead demanded it, which agent wrote
it, which gate verified it.

**D10. Distrust green.** The integrator re-runs every gate independently
before closing any bead. Agent self-reports are hypotheses.

## 3. Work packages

### WP-D: regression goldens (agent ci, in flight)
Bead aml-golden-regression-o41. scripts/freeze_goldens.py writes
tests/golden/*.golden.csv from the seeded warehouse; --check exits nonzero on
drift with a diff; gate 5 "goldens" added to ci/gate.sh with skip-if-absent.
Rationale: the determinism gate proves same-seed reproducibility; goldens
prove transform stability. A dbt model edit that changes numbers fails CI
even when schema tests still pass.

### WP-E: golden-set labeling tool (agent labboard, in flight)
Bead aml-golden-labeling-helper-a9e. /label route: top-50 titles by
popularity, synopsis only, fixed tag vocabulary, 1 to 3 tags enforced, writes
evals/golden/title_moods_golden.jsonl. Second-pass mode with hidden labels
writes .pass2.jsonl; /label/report shows agreement counts and percentages as
data. Unblocks the live enrichment eval (aml-enrichment-live-run-it8).

### WP-F: simlab data-purity correction (agent simlab, in flight)
Bead aml-simlab-html-8p9 under D8. Charts, tables, sliders, download button,
provenance strip, formula shown as code. All narrative copy removed.

### Integration protocol (integrator)
Bead aml-integrate-ef1, then aml-compliance-audit-sa8.
1. Each lane completes wave 1 plus wave 2 in its worktree.
2. Merge order: ci, labboard, simlab. Command shape:
   git merge --no-ff ntm/anime-lakehouse/cc_N -m "merge(<lane>): beads <ids>; gates re-verified: <result>"
3. Integrator-owned files reconciled at merge: pyproject testpaths, .gitignore,
   README.
4. Independent verification after each merge: bash ci/gate.sh, python -m
   pytest -q, plus lane-specific checks from the bead acceptance criteria.
5. Worktrees and branches removed after merge; traceability lives in merge
   commits and the operations log.
6. Compliance pass: every closed bead re-checked against its acceptance
   criteria with commands run fresh. False closes reopen with evidence.

### Audit round (aml-audit-round-fsm, after integration)
Review-only swarm round on integrated main using the codebase-audit method.
Domains: correctness and security (server.py subprocess and SSE handling,
gate.sh quoting and exit paths), UX against plan quality bars, docs
de-slopification under D8. Findings tagged CRITICAL, HIGH, MEDIUM, LOW and
filed as beads. Each finding is fixed or explicitly accepted; no silent
closes.

### Ideation round (aml-idea-wizard-w3-hnr, after audit)
idea-wizard pass on the audited repo produces scored wave-3 candidates;
survivors become beads. Candidate seeds: streaming ingestion, dbt semantic
layer, Dagster sensors and schedules, eval-set expansion, labboard run-diff
view, cost dashboards.

### Operations log (aml-ops-log-w3g, integrator, last)
docs/OPERATIONS-LOG.md assembled from session evidence: bead graph snapshot,
dispatch table (agent, domain, beads, worktree, branch), tick log with
interventions (including the caught lane-collision near miss and the mid-run
data-purity correction), merge records, gate results, compliance table.

## 4. Dependency graph (wave 2)

```
aml-golden-regression-o41 (ci)      \
aml-golden-labeling-helper-a9e (lb)  } -> aml-integrate-ef1 -> aml-compliance-audit-sa8 -> aml-ops-log-w3g
aml-simlab-html-8p9 (simlab)        /            |
                                                  +-> aml-audit-round-fsm -> aml-idea-wizard-w3-hnr
                                                  +-> aml-simlab-publish-b7v -> aml-readme-commit-5vo
backlog: aml-enrichment-live-run-it8 (needs labeling + key), aml-dbx-deploy-eaz (needs workspace)
```

## 5. Acceptance for the wave

1. gate.sh exits 0 on integrated main with gates 1 through 5 present,
   run by the integrator.
2. Full pytest green including labboard and labeling smoke tests.
3. Goldens: check green on main; documented red on a scratch mutation.
4. simlab passes D8 review: no narrative copy, tables and download present.
5. Merge commits and bead references make the history auditable end to end.
6. Compliance table shows zero unresolved false closes.
7. Audit findings all resolved or accepted with reasons.
8. br dep cycles empty; beads synced and committed.
