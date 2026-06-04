---
date: 2026-06-04
status: draft
owner: tarik
reviews:
  - ../exports/2026-06-03-mock-robinson-review.md
  - ../exports/2026-06-03-robinson-review-round2.md
related:
  - ../exports/2026-06-03-validation-baseline.md
  - ../exports/2026-06-03-gold-standard-benchmark.md
  - ../plans/2026-06-02-dedup-and-regression-cases.md
  - ../shipped/2026-06-03-robinson-round2-response.md
---

# Plan: scientific validation of the French path against the Robinson reviews

## Why this plan exists

Both mock reviews land the same closing blow: the fixes are real code, but the
numbers describe a tool we are not shipping. Round 2, section E and item 2 of the
closing:

> *"Measure the thing you ship. recall@k and F1 on the French path. A real
> healthy-control run with an API key. A second annotator and a κ."*

Everything quoted to date was measured in English (GSC+, recall@k on oracle
labels) or at n=1 (the MGA.467 pilot). We have now run `phenoscribe process` on
26 real French recordings with a live API key. This is the first time we can put
a number on the French path. This plan turns that run into evidence, layer by
layer, each mapped to a specific review remark.

## There is no gold standard — this reshapes everything

Decision (2026-06-04, Tarik): **Marc's manual codes are not a gold standard.**
They are one expert's earlier reading, and Marc himself says some are not
correct. This is Robinson's own Q10 ("one coder is not a gold standard, it is one
opinion") and the project rule that comparisons must *explain* differences, not
grade Marc.

Consequence: **F1 against Marc's codes does not measure accuracy.** It measures
agreement between two fallible coders. So "% correct against gold standard"
cannot be the headline. Evidence comes from three sources instead, in priority
order:

- **A — Reference-free checks.** Objectively right or wrong with no reference at
  all. This is the *primary* evidence (Layer 4c–4e, plus code validity, negation,
  retrieval ceiling). Nobody can dispute the reference because there isn't one.
- **B — Concordance with Marc, as agreement + adjudication.** Overlap reported as
  inter-coder agreement, never accuracy. Every disagreement is classified:
  (i) real Phenoscribe error, (ii) defensible Phenoscribe code Marc didn't list,
  (iii) Marc code that is outdated/wrong. Adjudication is the deliverable, not the
  raw overlap percentage.

**Source C (a consensus second-annotator gold standard) is dropped** (Tarik,
2026-06-04). No second clinician is in reach, so we make no accuracy claim at all
and lean entirely on A (correctness, no reference needed) and B (agreement,
adjudicated). This is the honest ceiling of what is measurable now.

## What "evidence" means here

A claim counts only if:

1. It is measured on the **French interview path** a GP runs (`phenoscribe
   process` on audio), not an English proxy.
2. It is **reproducible from the repo** — one command, pinned inputs, checked-in
   inputs.
3. Its **status is labelled**: reference-free (A), agreement-with-Marc (B), or
   consensus-scored (C). A and C may speak to correctness; B speaks only to
   agreement. n, stochasticity, and the small sample are stated, not buried.

## The achievable sample

Cross-referencing the 26 recordings against `data/ground_truth/CRS__under20_35_HPO_corr.xlsx`:

- GT workbook: 35 patients.
- Recordings we just processed: 26.
- **Overlap (audio + a GT row): 7** — `061, 402, 412, 450, 460, 466, 467`.
- 19 recordings have no GT row; 28 GT patients have no audio here.

So the scorable French-path sample is **n=7**, up from n=1 in the baseline. The
other 19 transcripts are still useful for the dedup, IC-distribution, and
healthy-control checks (which need no GT), and for spot-checking PII.

This is the headline honesty point: n=7 is a 7× improvement and the first
multi-patient French F1, and it is still too small to rank against the published
GSC+ matrix. We report it as "agreement with one annotator on seven patients",
not "accuracy".

---

## Layer-by-layer test design

Four layers, in pipeline order. Each names the review remark it answers, the
metric, and the artifact it produces.

### Layer 0 — Reproducibility harness

**Answers:** round 2 §C (no CI, "passes on my machine"), §E (not reproducible).

- Wrap the scoring in one command: `phenoscribe validate output/results.xlsx
  data/ground_truth/CRS__under20_35_HPO_corr.xlsx`. It already exists (round-2
  `ic-validate` fix); confirm it restricts to the 7 GT-matched patients so
  precision is not diluted by the 19 ungraded ones.
- Check the validate path applies the `MGA.` prefix join and the raw `HP:#######`
  delimiter fallback (the two fixes from the baseline doc).
- Output: a committed `context/exports/2026-06-04-french-path-validation.md` with
  the exact command and the seed/config used.

### Layer 1 — Transcript fidelity (WER)

**Answers:** Robinson Test Q-implicit (the input to everything); not a numbered
remark, but the foundation of every code downstream.

- We have **no reference transcripts** — Marc coded from audio, not from text. So
  WER cannot be computed against truth without manual reference creation.
- Proposed: hand-correct **3 transcripts** (one short, one median, one long —
  e.g. 061, 412, 434) to make a reference, compute WER with `jiwer`. This is the
  cheapest defensible transcript number.
- Honest framing: WER on 3 self-made references is a sanity check on the mlx
  large-v3 French output, not a benchmark. It bounds how much of any downstream
  miss is "the model never heard the word".
- **Needs:** ~1–2 h of manual correction (Marc or Tarik). Flag as optional for
  round 3.

### Layer 2 — Pseudonymisation (PII recall / precision)

**Answers:** round 1 §2 (PII model honesty), the GDPR claim in the README.

Two sub-tests:

1. **Span-level recall/precision on a sample.** Manually annotate PII spans
   (names, places, dates, phones, IDs) in ~5 transcripts, compare to what
   `output/pseudo/*.txt` redacted. Report recall (caught / should-have-caught)
   and precision (correct / redacted). This is the number the README's "spot-check
   the first batch" hand-waves.
2. **Regression: the subword-offset bug.** Scoped across the run (2026-06-04) —
   present in **4 of 13** pseudo files, four shapes from one root cause (mapping
   NER subword spans back to character offsets in `pii.py`):
   - `PERSON_1loïse` (×2, MGA.038) ← *Héloïse* — **genuine privacy leak**, half a
     real name survives.
   - `PERSON_1ynéco` (×2, MGA.061) ← *gynéco* — a clinical word false-flagged as a
     person and mangled (over-redaction + signal loss).
   - `LOCATION_1LOCATION_2` (MGA.408) ← two adjacent places colliding.
   - `ORGANIZATION_1L` (MGA.404) ← `l'` article fragment leaking.

   Add a failing test in `tests/test_pii.py` covering a multi-subword accented
   name and two adjacent entities, then fix the offset mapping. The `loïse` case
   is a privacy defect; the others are correctness defects. One fix addresses all
   four.

Known-limitation misses (clinic names like *Marie-Curie*) get logged, not fixed —
the README already documents them.

**Needs:** ~1 h manual PII annotation on 5 transcripts.

### Layer 3 — Symptom extraction (negation + over-extraction)

**Answers:** round 1 §6, round 2 Q5 (negation — "the one that would worry me"),
round 1 §5 (the "everything-is-fatigue" over-extraction problem).

1. **Negation, on real data.** Grep the 26 transcripts for denial constructions
   (`n'ai pas`, `pas de`, `jamais`, `aucun`). For each, confirm the symptom
   either is absent from `present_codes()` or carries `hpo_id=None`. The round-2
   fix claims denied symptoms short-circuit before search — this is the first
   test of that claim on spontaneous French, not the unit fixture.
2. **Over-extraction signal.** Count extracted symptoms per patient vs GT codes
   per patient for the 7 scorable ones. A large symptoms≫GT ratio with low
   precision is the "3.9× more codes is not winning" failure. This falls out of
   the Layer-4 precision number; report them together.

### Layer 4 — HPO codes

**Answers:** Robinson Test Q2, Q7, Q8, Q9; round 1 §3, §4; the dedup plan.

Reordered after the no-gold-standard decision: the **reference-free checks
(4c–4e) are now the primary deliverable**; the Marc comparison (4a–4b) is
demoted to source B (agreement, not accuracy).

#### 4a. Concordance with Marc — agreement, NOT accuracy (source B)

- Compute overlap on the 7 matched patients: per patient, codes-in-both,
  Phenoscribe-only, Marc-only. Report as **inter-coder agreement**, never "% correct".
- Definition (for the overlap, not as accuracy): exact HP ID, patient-level.
  Report **per-patient distribution** (median + range), not a single micro number —
  n=7 means one patient swings it, and there is no truth to pool toward.
- **Adjudication is the actual deliverable.** For each disagreement classify:
  (i) Phenoscribe error, (ii) defensible Phenoscribe code Marc didn't list,
  (iii) Marc code outdated/wrong. The baseline already noted several MGA.467
  "extras" are defensible (exertional dyspnea, palpitations) — that is category
  (ii), and it is exactly why the raw overlap is not an accuracy number.
- No three-significant-figure F1. If a single agreement figure is shown at all,
  it carries its interval and the label "agreement with one annotator".

#### 4b. Precision vs recall split, directed (round 1 §4)

- Keep precision and recall separate; never fold into one "accuracy".
- Partial credit, if shown at all, uses **directed Lin IC over the MICA** (the
  round-2 metrics-repro primitive), not undirected hop-count. Parent-of-truth
  (non-specific, recall miss) and child-of-truth (fabricated specificity,
  precision miss) are reported as different errors.
- Read against Marc this is still agreement, not accuracy — it characterises
  *how* the two coders differ, feeding the 4a adjudication.

#### 4c. Dedup — no duplicate (patient, code) rows (dedup plan D1) — source A

- Assert `output/results.xlsx` has exactly one row per (patient_id, HP code).
  The 2026-06-02 audit found *Lower limb pain* twice for 038 and *Impaired
  executive functioning* three times. Confirm the dedup-always pass shipped and
  fires; if not, this is a code task before scoring (duplicates inflate every
  per-row count).

#### 4d. Graph / ancestor closure correctness (Q7, round 1 §3)

- Confirm `results.xlsx` carries the **Ancestor Closure** sheet (now on by
  default per round-2 truepath).
- For one patient, verify the closure is the exact `is_a` ancestor set of the
  leaf codes (compare against `hpotk get_ancestors` over the pinned OBO).
- Verify **negated findings are excluded** from the closure (the round-2 reviewer
  flagged this as an open reconciliation — test it, don't assume).

#### 4e. IC distribution of predicted terms (Q9)

- Run the IC distribution `phenoscribe validate` prints. If the predictions
  cluster at low-IC roots (*Phenotypic abnormality*, *Abnormality of the nervous
  system*), the tool is a syntactic exercise. Report median/mean/max IC over the
  7 patients' predicted terms.

#### 4f. Healthy-control false-positive rate (Q8)

- The current test stubs the extractor to return `[]` — it tests plumbing, not
  behaviour (round-2 Q8 = No). We have no labelled healthy control in the 26.
- Proposed: take one transcript with few/no real findings (402 had 0 PII and is
  mostly function questions) OR record/obtain a no-finding interview, run it, and
  count minted present codes. Near-zero is the pass.
- **Needs:** ideally a real healthy-control recording from Marc; interim use the
  lowest-finding transcript and label the limitation.

---

## Honesty constraints (carried into every artifact)

These are not caveats to bury; round 2 §D says the moment "simulated" drops off,
this gets read aloud at a meeting.

- **Single annotator (Q10).** Marc's codes are one expert's reading. No κ is
  possible until a second clinician double-codes a subset. Every F1 is "agreement
  with one annotator", and that agreement is the ceiling, not the accuracy.
- **n=7, stochastic.** Report counts and intervals, not bare percentages. State
  the model and that re-runs shift counts by a code or two.
- **The reviewer is invented.** This plan hardens against expected objections. It
  is not external review. Keep "simulated" on the provenance until a human who is
  not the author reads the code.

## Deliverables

1. `context/exports/2026-06-04-french-path-validation.md` — **reference-free
   battery first** (code validity, dedup, ancestor-closure correctness, IC
   distribution, retrieval recall@5), then the Marc concordance as agreement +
   adjudication table (not accuracy), with the one reproduce command.
2. `tests/test_pii.py` — failing test + fix for the subword-offset bug (covers
   the `loïse` leak and the adjacent-entity collision).
3. A negation evidence table over the 26 transcripts (denied phrase → coded or
   not).
4. An adjudication worksheet for the 7 matched patients: every Phenoscribe-only
   and Marc-only code tagged (i)/(ii)/(iii). This is the seed of source C.
5. Updated Robinson Test scorecard — re-scored on French data, with Q2/Q10
   reframed as "agreement, no gold standard yet" rather than claimed accuracy.

## Open items that need Marc or a second clinician (not code)

- A second annotator on ≥5 of the 7 patients → Cohen's κ / pairwise term F1 (Q10).
- A genuine healthy-control recording (Q8).
- Audio for more of the 28 unmatched GT patients to lift n above 7.
- 1–2 h to hand-correct 3 reference transcripts for the WER sanity check.

## Sequence

1. Wait for the run to finish; confirm 26/26 transcripts + results.xlsx.
2. Layer 0 + 4a–4e (no new data needed) → the headline export. **Do first.**
3. Layer 4c dedup + Layer 2 partial-token fix (code tasks).
4. Layer 3 negation table.
5. Layer 1 WER + Layer 4f healthy control (need manual input) → flag for round 3.
