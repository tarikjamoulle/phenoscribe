---
problem: "Close the round-3 review flags on the French-path validation, and make the shipped artifact match the fixed code"
date: 2026-06-04
shipped: 2026-06-04
status: shipped
review: "../exports/2026-06-04-robinson-review-round3.md"
final_review: "../exports/2026-06-04-robinson-review-round4.md"
plan: "../plans/2026-06-04-scientific-validation-plan.md"
related:
  - 2026-06-03-robinson-round2-response.md
  - ../exports/2026-06-04-french-path-validation.md
workflow:
  name: round3-fix
  run_id: wf_0ca3e047-19b
  agents: 14
  tokens: 469241
merged_to: main
rollback_tag: pre-round3-2026-06-04
---

# Shipped: round-3 fixes (`round3`)

The round-3 mock review found four real defects and one unmeasured number on the
26-recording French run. Fixed with a multi-agent workflow (`round3-fix`,
`wf_0ca3e047-19b`): four need-to-know agents in isolated worktrees, a fresh
reviewer per fix, merge to main in conflict order, re-measure, then a round-4
mock Robinson check. All four fixes verified `integrated`; suite 175 passed / 1
skipped (was 162). Rollback tag `pre-round3-2026-06-04`.

## What landed

| Flag | Fix | Verified |
|------|-----|----------|
| PII leaks name fragments (`Héloïse`→`PERSON_1loïse`, 10/26 files) | Corrected subword→char span mapping in `pii.py` | 0/26 files leak (grep on `pseudo_round3/`) |
| Duplicate `(patient,code)` rows pollute the closure (55 pairs) | `dedup_by_code` in `output.py`, one row per code; closure reads the deduped set | A2 PASS; MGA.038 *Lower limb pain* 5×→1× |
| `clinical_term` not persisted → recall unmeasurable | Threaded through extract→pipeline→`output.py` as a workbook column | Column present; populated on future runs |
| Harness misreports A4 (0 vs real 78) and drops MGA.460 (n=6) | `measure_reffree.py`: parse string flags + accept `HPO:` prefix | A4 = 78/698; B concordance n=7 |
| French recall@5 unknown | Re-extract `clinical_term`, query the live index | recall@5 ≈ 0.91 (n=477 mentions) |

## Artifact regeneration

The fixes are merged, but the shipped `output/results.xlsx` was written before
the dedup merge. `scripts/validation/dedup_rewrite.py` rewrites it through the
merged code path (no pipeline re-run): present rows 610→533, absent preserved,
closure rebuilt clean. Old workbook kept at `output/results.pre-dedup.xlsx`. The
`Clinical Term` column is present but blank in this run — it was never stored, so
it only fills on a future `phenoscribe process` run.

## Still open

- Second annotator / Cohen's κ — out of scope (no second clinician).
- A full `phenoscribe process` re-run to populate `clinical_term` in the workbook.

Round-4 verdict: still hand-code + triage, but the tool no longer fabricates
codes, drowns in near-root terms, mints denied symptoms, or leaks names.
