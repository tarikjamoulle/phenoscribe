---
type: mock-review
round: 4
date: 2026-06-04
reviewer: "Simulated Peter N. Robinson (Humboldt Professor, BIH @ Charité; HPO PI)"
disclaimer: "SIMULATED. Robinson has not seen this repository. This is a fourth-pass pre-mortem written after the round-3 fix PRs landed on main. It is a model rehearsing the objections his lab would raise, used to verify that the round-3 flags were actually closed. It is not external review. Keep the word 'simulated' on the provenance."
reviews:
  - ./2026-06-03-mock-robinson-review.md
  - ./2026-06-03-robinson-review-round2.md
  - ./2026-06-04-robinson-review-round3.md
verifying_flags_from: ./2026-06-04-robinson-review-round3.md
merges_checked:
  - 08cc781 "Merge PR: round3/clinical-term"
  - e0d8ad3 "Merge PR: round3/dedup"
  - 3c62663 "Merge PR: round3/pii-offset"
  - b8a0708 "Merge PR: round3/harness"
evidence:
  - output/validation/reffree_round3.json
  - output/validation/dedup_demo_round3.md
  - output/validation/pii_recheck_round3.md
  - output/validation/recall_at5_round3.md
  - output/pseudo_round3/*.txt
  - src/phenoscribe/pii.py
  - src/phenoscribe/output.py
  - output/results.xlsx (the shipped workbook)
provenance:
  hpo_release: hp/releases/2026-02-16
grounding:
  - "True-path rule: an explicit annotation entails every is_a ancestor, recursively; the closure IS the deliverable a phenotyper hands to Exomiser/LIRICAL (HPO docs; PHENOstruct/HEMDAG)."
  - "GDPR Art. 4(5) / Recital 26: pseudonymisation must remove attribution to a data subject; identifiability is judged on 'all means reasonably likely to be used.' A surviving name fragment is residual personal data (EDPB Guidelines 01/2025 on Pseudonymisation)."
  - "Concept-recognition convention: document-level P/R/F1; FastHPOCR 0.76 F1 on GSC+ (Groza et al., Bioinformatics 2024)."
---

# Mock Reviewer Report, Round 4 — *Phenoscribe*
*Written as if by P. N. Robinson after the round-3 fixes were merged. Same caveat as the last three rounds: this is a simulation, a pre-mortem, not a real review. The provenance keeps the word "simulated" and so do I.*

The brief this round is narrow and I like it that way: I named five flags in round 3, the team merged four PRs against them, and my only job is to check — with my own eyes, on the live path, not from the summaries — whether each is actually closed. I ran the harness myself. I opened the shipped workbook with openpyxl. I grepped the regenerated pseudo files. Two of the five are genuinely, cleanly fixed and I will say so without hedging. Two are fixed *in the code* but not *in the artifact a clinician opens*, which is a distinction the team's own demo files are honest about and which matters enormously for a clinical tool. One is improved but measured against the wrong input.

---

## Per round-3 flag: FIXED / PARTIAL / NOT-FIXED, with evidence I checked personally

### Flag 1 — `pii.py` offset leak (round 3: "10 of 26 files leak, undercounted as 4"). **FIXED.**
This is the one I most wanted closed and it is closed. I read the fix: `_snap_to_word_boundaries` in `src/phenoscribe/pii.py` (merged in `3c62663` / `4e7f8e0`) grows each NER span left and right along `[0-9A-Za-zÀ-ÖØ-öø-ÿ]` to whole-word boundaries before replacement, so a subword span that covered only "Hé" of "Héloïse" now redacts the whole word. The logic is correct and the apostrophe/hyphen-as-boundary choice is sensible (`l'X` snaps to `X`, not to the article).

I did not trust the recheck doc — I re-ran the grep myself over `output/pseudo_round3/*.txt`:
- A placeholder-adjacent-to-letter scan (`(PERSON|LOCATION|ORGANIZATION|…)_\d+[A-Za-zÀ-ÿ]` and the mirror) returns **zero matches across all 26 files.**
- Every specific round-2 leak string is gone: `loïse`, `_1vid` (David), `_3ivaldi` (Vivaldi), `_1vel`, `_3ait`, and the over-redaction collisions `PERSON_1PERSON`, `LOCATION_1LOCATION`, `_2timulation` — **none survive** in `pseudo_round3/`.
- The named round-2 site in MGA.038 read `...avec PERSON_1loïse...`; in `pseudo_round3/MGA.038.txt` it reads `...avec PERSON_1...`. The tail is gone.

This is the fix done right, including re-reporting the honest count (0/26, down from 10/26). On GDPR grounds (Art. 4(5)/Recital 26 — a surviving name fragment is residual personal data, identifiability judged on "means reasonably likely"), the leak that made the old output a pseudonymisation failure no longer fires. Credited in full.

**Caveat I must record:** this is fixed in `output/pseudo_round3/`. The round-3 *recall* harness (next flag) still read the **old, leaky** `output/pseudo/` directory. So the codebase pseudonymises correctly now; one downstream measurement script was still pointed at the pre-fix files. That is a wiring problem, not a leak — but it means "the fixed pseudonymiser" and "the recall measurement" did not run on the same bytes.

### Flag 2 — n=6-not-7 GT parse (round 3: harness silently dropped one annotated patient). **FIXED.**
I ran `scripts/validation/measure_reffree.py` against the live workbook. It printed `B vs Marc: AGREEMENT n=7`. The JSON it wrote (`reffree_round3.json`) lists seven matched patients — MGA.061, .402, .412, .450, .460, .466, **.467** — where round 2/3 scored six. I read why: `load_gt` now joins *all* non-empty cells of each row (`joined = " ".join(...)`) before regex-extracting `HPO?:\d{7}`, and `_normalise_codes` accepts both `HP:` and `HPO:` prefixes (Marc writes both). MGA.467's codes lived in a column the old parser didn't read; they are now caught. n=7 is the honest count of patients with parseable Marc codes. Fixed, and for the right reason.

### Flag 3 — A4 measurement contradiction (round 3: reffree.json said 0 needs-review / conf-min 0, workbook had 78 REVIEW / 70 conf-0). **FIXED.**
The harness now reports the truth. Live run prints `A4 hygiene: 78 needs-review of 698 rows`; `reffree_round3.json` records `needs_review_rows: 78`, `rows_total: 698`, `confidence_median: 0.9`, `confidence_min: 0.0`. I read the fix: `_is_truthy_flag` now treats the string `"REVIEW"` (and `yes/true/1/x`) as a flagged row instead of coercing a non-bool cell to `False`, and `_as_float` parses numeric *strings* like `"0.00"`. So the count reconciles with the workbook it cites: **non-zero needs-review, confirmed.** The validation file no longer contradicts the artifact it validates. This was the single thing I said a clinical tool cannot ship, and it is corrected.

### Flag 4 — dedup pollution (round 3: 55 dup `(patient,code)` pairs in the primary sheet, propagating into the closure). **PARTIAL — fixed in code, NOT in the shipped artifact.**
Here I have to be exact, because the team's own demo doc is exact and I credit that honesty. `dedup_by_code` exists in `src/phenoscribe/output.py` (merged `e0d8ad3`/`d989e2c`), keys on `(hpo_id, negated)`, is wired into `write_results` *before* both the flat sheet and `_write_closure_sheet`, and the `dedup_demo_round3.md` demonstrates it collapses 610→533 rows and eliminates all 55 pairs. The function is correct.

But I opened the **shipped `output/results.xlsx`** — the file a GP actually receives — with openpyxl, and:
- Primary `Sheet`: still **55 duplicated present `(patient,code)` pairs**. MGA.038 / HP:0012514 (*Lower limb pain*) still appears **five times.**
- `Ancestor Closure` sheet: still **55 dup pairs, 2032 rows**, MGA.038's *Lower limb pain* still repeated **five times** in the closure. The exact pollution I flagged in round 3, untouched.

The reason is stated plainly in `dedup_demo_round3.md`: the workbook was written at **01:30**; the dedup helper merged at **09:34**; "No pipeline re-run." The demo *applies the fix to the loaded rows in memory* and reports the after-numbers, but never rewrites the workbook. So the live artifact still pollutes the closure 5× on the worst case, and a downstream similarity engine fed this workbook still over-weights *Lower limb pain* fivefold. **The code change is real and correct; the deliverable is unchanged.** For a flag whose whole point was "the workbook a GP opens is not safe," shipping the fix without regenerating the workbook leaves the flag open on the path that matters. Half-day fix becomes a one-command fix: re-run `process`, or run the deduper over the existing workbook and rewrite it.

### Flag 5 — unmeasured French recall@5 (round 3: `clinical_term` not persisted; ceiling unknown, bracketed 0.17–0.996). **PARTIAL — now measured (~0.91), with a residual tautology the doc admits, and run on the wrong pseudo dir.**
This moved from "unknown" to a number with a confidence interval, which is real progress. `recall_at5_round3.md` reports **recall@5 = 0.912** (435/477, Wilson [0.883, 0.934]), recall@10 = 0.918. The method is the right shape: re-run extraction to recover `clinical_term`, query the same `search_hpo` the pipeline uses, hit@k = assigned code in top-k, per-patient spread shown (min 0.759), 135/612 unjoinable findings excluded rather than guessed. The persist-`clinical_term` PR (`08cc781`/`131ad76`) closes the root cause for *future* runs.

Three things keep this PARTIAL, two of which the doc states against itself (credit for candour):
1. **Residual tautology.** Where this run's `clinical_term` equals the production run's for the same verbatim, the hit is guaranteed by construction. The doc says 0.91 is "an *optimistic* estimate," not a clean held-out test, and cannot isolate the tautological share because the *production* run still didn't persist `clinical_term` (the PR fixes it going forward; this measurement is a replay). Correct framing.
2. **Join noise.** Most of the 42 misses are verbatim-join artifacts (`'fatigue' → Lower limb pain`), not retriever failures — so even the misses are partly bookkeeping. Honest, and it means the true number is fuzzier than the third decimal.
3. **Wrong input dir (my finding).** `eval_recall_french.py` hard-codes `PSEUDO_DIR = output/pseudo` and the doc explicitly says `pseudo_round3/` "does not exist" — but it does, it's where the *fixed* pseudonymiser wrote. So the recall was measured on the **old leaky transcripts**, not the ones the shipped pipeline now produces. The number is unlikely to move much (the leaked fragments are name tails, not clinical terms), but "measured on the French path the tool runs" should mean the *current* French path.

Net: the ceiling is no longer unknown. It sits around 0.91 recall@5 with an optimistic lean, materially above the 0.17 raw-verbatim floor. That is the most decision-relevant number in the project and it finally has a value. It is not yet a clean held-out measurement.

---

## Anything newly broken by the changes

Nothing regressed in behaviour — A1 (0 invalid/0 obsolete of 610), A3 (closure consistent, 0 negation leaks), A5 (88 absent, no Present/Absent collision) and A6 (median IC 4.77, 1.5% below IC 2) all still hold on the live run. The new fragility is an **artifact-vs-code skew** introduced by the way the fixes were verified:
- The dedup fix and the workbook disagree (workbook predates the fix).
- The pii fix wrote `pseudo_round3/`, but the recall harness read `pseudo/`.
- So three different vintages of output coexist in `output/` (01:30 workbook, 09:4x pseudo_round3, old pseudo). A reviewer who opens the wrong one draws the wrong conclusion. This is exactly the "harness must agree with the artifact" problem from round 3, displaced one level: the *measurements* are now self-consistent, but the *artifacts* are not all regenerated. One `make` target that regenerates workbook + pseudo + all validation in one pass would close this for good.

---

## The Robinson Test — re-scored, round 4

| Q | Question | Round-3 | Round-4 (my own read, post-merge) |
|---|----------|---------|-----------------------------------|
| 1 | HPO release pinned, printed, `replaced_by` followed | Yes | **Yes** — unchanged; 0 invalid/0 obsolete of 610. |
| 2 | Term-level F1 on a published gold standard | No | **No** — still only agreement-with-Marc (F1 0.272, n=**7** now). No accuracy number on the product. |
| 3 | vs Fenominal / FastHPOCR / Doc2HPO | Partial | **Partial (unchanged)** — no new comparator this round. |
| 4 | recall@5/@10 before the judge | No ("unknown") | **Partial** — now measured at ~0.91 recall@5 (Wilson [0.883,0.934]); optimistic, residual tautology, and run on the old pseudo dir. Moves up from "unknown." |
| 5 | Negation — "je n'ai pas de fièvre" | Pass (this run) | **Pass (unchanged)** — 88 absent, no collision. |
| 6 | Frequency / onset / severity schema | Partial | **Partial** — columns present; quality still unmeasured. |
| 7 | Ancestor closure for one patient | Partial | **Partial** — closure correct & negation-clean, but the *shipped* closure still inherits 55 dup pairs (5× on MGA.038) because the workbook predates the dedup fix. |
| 8 | False-positive rate on a healthy control | No | **No** — still no healthy-control recording. |
| 9 | IC distribution | Yes | **Yes** — median IC 4.77, 1.5% below IC 2. |
| 10 | Inter-annotator agreement | Open | **Open** — still one annotator, no κ. |
| — | judge hygiene (A4) | Regressed in reporting | **Fixed** — harness now reports 78 needs-review / 698 rows, reconciles with the workbook. |
| — | PII pseudonymisation | leak (10/26) | **Fixed** — 0/26 in `pseudo_round3`; verified by my own grep. |

Tally: two flags cleanly fixed (PII 10→0, A4 reconciled), one fixed (n=7), one moved unknown→partial (French recall ~0.91), one fixed-in-code-only (dedup — the shipped workbook is still polluted). Q1, Q5, Q9 remain clean; A4 and PII go from broken to fixed. Q2, Q8, Q10 remain unanswered and admitted.

---

## Closing — does the round-3 "still hand-code and triage" verdict move?

**It moves at the margin, in the same direction as last round, but not across the line — and it is held back by one specific, avoidable thing.**

Three of my five round-3 flags are genuinely closed and I verified each myself, not from the prose: the pseudonymiser no longer leaks name fragments (0/26, confirmed by grep — that retires a live GDPR-incident risk, the hardest blocker), the validation harness no longer lies about its own judge hygiene (78/698, reconciled), and it no longer drops an annotated patient (n=7). Those are real, and two of them removed reasons I would have refused the tool outright. The French retrieval ceiling, the single most decision-relevant number, finally has a value (~0.91 recall@5) instead of a 0.17–0.996 shrug — call that the fourth flag substantially addressed, with honest caveats about the residual tautology.

What stops the verdict crossing to "triage with under review" is the fifth flag, and it is the one that should have been the easiest: **the shipped workbook still duplicates findings 5× and bleeds them into the closure sheet**, because the dedup code was merged but the artifact was never regenerated. A clinician opening `output/results.xlsx` today gets the *un*fixed file. A tool whose output feeds Exomiser/LIRICAL cannot ship a closure that weights *Lower limb pain* fivefold, and the fix already exists — it just was not run. Regenerate the workbook (and point the recall harness at `pseudo_round3/`) so the artifacts match the code, and four of five flags are clean.

So: **still hand-code and triage** — but I am now one regenerated workbook away from saying "triage with every output under review." The judgement is the same as round 3 in words and better in substance: the blockers that remain are an un-rerun pipeline and a missing second annotator, not a tool that fabricates, drowns in near-root terms, mints denied symptoms, or leaks names. Tell Marc, again and with respect, to keep coding by hand and use this as a triage assistant whose output he checks — and tell the team that the gap between "fixed" and "shipped" is now literally one command.

— *(mock)* P. R.
