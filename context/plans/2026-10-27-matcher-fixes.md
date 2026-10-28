# Matcher fixes: search misses, denied findings, combined findings

Date: 2026-10-27. Status: draft, for Codex review.

## Why

The newest run (150 patients) had 333 findings to review: 299 with no code, 34
with a weak code. A check by hand against `data/hpo/hp.obo` (release 2026-02-16)
sorted the 299:

| Cause | Findings | Example |
|---|---|---|
| HPO has no good term | 147 | "sick leave", "lack of improvement", "urinary problems" |
| Search misses the right term (not in the top 5) | 105 | "shortness of breath" -> Dyspnea at rank 15 |
| Right term in the top 5, the judge says no match | 47 | denied "headache" -> Headache at rank 1 |

Of the 105 search misses, 53 rank 6 to 30 and 52 are not in the top 30.
Of the 47 refusals, 38 are denied findings and most of the other 9 are combined
findings ("nausea and diarrhea").

The user accepted the hand codes. They are now in the 3 result files (column
`Code Source` = "Reviewed by hand 2026-10-27"); backups are in
`~/Documents/Phenoscribe results/backup/`. The term -> code list is in the
scratchpad script `apply_codes.py` (`CODES`, 100 terms).

The 34 weak codes are mostly correct ("confusion" -> Confusion). They stay out
of scope.

## Step 0: a regression set (no patient data)

- Save `CODES` as `tests/fixtures/review_codes_2026-10-27.json`:
  `{clinical_term, expected: [hpo_id], status: "Present"|"Absent", cause}`.
  It holds English clinical terms only. No verbatim, no patient ID.
- Add `scripts/validation/search_recall.py`: for each term, the rank of the
  expected code in `search_hpo(term, k=30)`. Report recall@5 and recall@10.
- Baseline today: recall@5 = 47 of 152 rows (only the 47 "judge says no" rows).
- This set measures agreement with one reviewer (Tarik + Claude), not accuracy.
  See the "no gold standard" decision.

## Fix 1: the search finds the right term

Cause: the index holds one vector for each term. The vector mixes the name, all
EXACT/NARROW synonyms and the definition (`build_enriched_text`). A layperson
synonym ("shortness of breath" on Dyspnea) loses its weight in that mix.

Changes, in this order. Measure recall after each one; stop when the gain stops.

1. **Exact name lookup before the vector search.** Fold the clinical term
   (casefold, strip, hyphen = space, simple plural "-s") and look it up in a
   dict of HPO names and EXACT synonyms (from `parse_obo`). A hit goes to rank 1
   of the shortlist with distance 0.0. The vector hits follow, without the
   duplicate. Cheap, deterministic, no re-seed. Covers "shortness of breath",
   "diaphoresis", "increased appetite", "anomia", "restless sleep".
2. **One vector for each name and each EXACT/NARROW synonym** (plus the current
   enriched document). Query 4k docs, group by HPO id, keep the best distance,
   return the top k terms. Needs a re-seed: bump an `index_schema` value in the
   collection metadata and make `index_ready()` check it, so an old index gets
   rebuilt by `phenoscribe setup`. Check the seed time and the index size.
3. **Shortlist of 10 for the judge** (`k` 5 -> 10). Only if steps 1-2 leave many
   codes at rank 6-10. Check the judge prompt length and that the judge still
   picks well (replay of Marc's 8 rows: `scripts/validation/replay_marc_review.py`).

`WEAK_MATCH_DISTANCE` stays 0.6. An exact hit has distance 0.0, so it never
gets the weak flag.

Not in scope: a new embedding model (SapBERT exists as an option; measure it
on the regression set only if steps 1-2 fail).

## Fix 2: the judge codes a denied finding

Cause: the judge prompt shows the patient's words ("je n'ai pas mal à la tête").
The judge reads the denial and returns `match: false`. The app records the
status (Absent) on its own, so the judge must code the finding itself.

Changes in `match_hpo.py`:
- `JUDGE_SYSTEM_PROMPT`: one rule. "The patient can deny the finding. The
  status is recorded separately. Code the finding itself. Never refuse a term
  because the patient denies it."
- The user prompt adds a line `Status: denied by the patient` when `negated`.

Tests: the prompt has the rule; a negated symptom's user prompt has the status
line. Live check (needs the API key, ask first): replay the 38 denied rows from
the regression set through the judge; expect most to get the expected code.

## Fix 3: one finding for each symptom

Cause: the extractor sometimes returns "nausea and diarrhea" as one finding.
No HPO term covers both, so the judge refuses.

Changes in `extract_symptoms.py`:
- `SYSTEM_PROMPT` rule: "One symptom for each finding. Split 'nausée et
  diarrhée' into two findings with the same patient_verbatim and mentions."
- No code-side split: "and" also occurs inside real terms ("Nausea and
  vomiting" is one HPO term).

Tests: the prompt has the rule. Live check: run the extractor on 2 clean
transcripts that had a combined finding; expect separate findings.

## Also: a credit error must stop the file

During the credit outage (2026-10-26 23:49-00:05), 48 judge calls failed with
`429 insufficient_quota`. `match_hpo` caught the error, left the finding
uncoded and the file counted as done. Change: an exception from `llm_call` in
the judge step propagates (llm_call already retries once), so the file fails
and "Find HPO codes" can run it again. A judge answer that does not parse stays
"no good match", as today.

Test: a fake `llm_call` that raises -> `match_hpo` raises.

## Order and checks

1. Step 0, then Fix 1.1 and measure.
2. Fix 2, Fix 3, credit-error fix (small, independent).
3. Fix 1.2 (re-seed), measure; Fix 1.3 only if needed.
4. Full tests, ruff (no new errors), Codex code review, merge to local main.
5. Ask before a live run with the key. Re-code the 150 patients only when the
   user asks: the hand codes in the result files would be replaced.

## Codex review (2026-10-27, gpt-6-luna, high) and changes

| # | Sev | Finding | Change to the plan |
|---|---|---|---|
| 1 | High | `search_hpo()` has no OBO path and no synonym map. | Build the exact-name map once from `parse_obo` (cached, keyed by the obo path from config) and pass it to `search_hpo`. A name shared by several terms keeps all their IDs. |
| 2 | High | Distance 0.0 on an exact hit turns off the weak-shortlist flag. | Mark the candidate `exact=True` and keep its real vector distance. A folded name with 2+ HPO IDs gets `needs_review`. The weak flag uses the vector distance, as today. |
| 3 | Medium | Grouping per-synonym docs can give fewer than k terms. | Query 4k docs (more if needed), group by HPO id, return k unique IDs. Test it. |
| 4 | High | The regression set holds known failures only, so it is circular and measures no precision. | Record `status` and `cause` for each term. Add a second sample: 50 rows the matcher coded with no review flag in the same run; check before/after that their codes do not change for the worse (false-match check). Report both numbers. |
| 5 | High | "fall" maps to Recurrent falls. | Rejected: in release 2026-02-16, HP:0002527 is "Falls" (no "recurrent" in name or definition). The one "fall" row is Absent. |
| 6 | Medium | A prompt rule alone does not split reliably. | Add 6 hand-written test phrases (3 combined, 3 single HPO terms such as "nausea and vomiting") to the live check; measure the split rate. Keep "Nausea and vomiting" whole. |
| 7 | Medium | `test_match_hpo_judge_exception_flags_review` expects the old behaviour. | Change the test to expect the exception. Add a pipeline test: the file fails and a second run codes it. |

## Risks

- An exact lookup can match a term with a different sense ("concentration" in
  HPO is a lab level). The lookup uses names and EXACT synonyms only, and the
  judge still decides.
- A re-seed changes all shortlists, so earlier results are not comparable row by
  row. Record the `index_schema` in the Provenance sheet.
- Fix 2 can make the judge code a denied finding with a too-specific child term.
  The existing rule against unsupported child terms applies.

## Results (2026-10-27, built)

Search recall on `tests/fixtures/review_codes_2026-10-27.json`
(`scripts/validation/search_recall.py`):

| Search | Review @5 | Review @10 | Control @5 | Control @10 |
|---|---|---|---|---|
| Before (vector only, schema 1) | 32% | 45% | 100% | 100% |
| + exact lookup (1.1) | 51% | 59% | 100% | 100% |
| + one document per synonym (1.2) | 72% | 74% | 88% | 98% |

Control misses at @5 are partly old codes that were wrong (a denied "fever" had
"Inappropriate absence of fever"; the new search puts Fever first). So k = 10 (1.3).
The schema 2 index has 59,717 documents and takes 17.5 min to build.

Also built on the user's request: `interview_details()` and the Excel columns
Patient / Interview / Date (151 files = 116 patients; 17 with interview + follow-up).
Codex code review: 1 finding (old workbook header), fixed.
