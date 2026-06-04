---
type: validation
date: 2026-06-04
status: draft
owner: tarik
simulated: true
reviews:
  - ./2026-06-03-mock-robinson-review.md
  - ./2026-06-03-robinson-review-round2.md
related:
  - ../plans/2026-06-04-scientific-validation-plan.md
sources:
  - output/validation/reffree.json
  - output/validation/negation_evidence.md
  - output/validation/recall_at5.md
  - output/validation/marc_adjudication.md
provenance:
  hpo_release: hp/releases/2026-02-16
  results: output/results.xlsx
---

# French-path validation — Phenoscribe

This is a pre-mortem against two **simulated** Robinson reviews (round 1 and
round 2). Robinson has not seen this repository. Keep "simulated" on it.

## 1. Headline

We ran `phenoscribe process` on 26 real French recordings with a live API key.
This is the first French-path evidence on more than one patient. Every number
in the round-1 and round-2 reviews was measured in English (GSC+, recall@k on
oracle labels) or at n=1 (the MGA.467 pilot). The French interview path is the
only path a GP runs. Here it is, on 26 recordings, measured directly.

Evidence comes in two sources, and the distinction is load-bearing:

- **Source A — reference-free checks.** Properties that are right or wrong with
  no reference at all: code validity, ancestor closure, IC distribution,
  negation handling. These speak to correctness. Nobody can dispute the
  reference because there isn't one.
- **Source B — agreement with Marc.** Overlap with Marc Jamoulle's manual code
  list, reported as inter-coder agreement and adjudicated case by case. Marc's
  codes are not a gold standard. He says some are not correct, and a
  `marc_only` code is held to the same transcript-support test as a `pheno_only`
  one. Source B is agreement, never accuracy, never "% correct".

There is no source C. No second clinician is in reach, so no κ and no accuracy
claim. A (correctness) and B (agreement) are the honest ceiling of what is
measurable now.

Three headline numbers:

- Code validity passes clean: 610 present codes, 260 distinct, **0 invalid, 0
  obsolete**. Ancestor closure holds on all 26 patients. IC median 4.77, only
  1.5% of scored terms below IC 2 — the output is specific, not a wall of roots.
- Negation: **0 genuine leaks** across 31 examined denied-symptom mentions. No
  denied symptom was minted as a present HPO code in this run.
- Agreement with Marc (n=6 scorable patients, micro): precision 0.246, recall
  0.315, F1 0.276 — **agreement, not accuracy**. Adjudication of the 152
  disagreements: 10 real Phenoscribe errors, 93 defensible either side, 36 Marc
  codes the transcript alone can't support, 13 genuine ties.

## 2. Source A — reference-free battery

One subsection per check. Passes first.

### A1 — Code validity (PASS)

Answers round 1 §1 and the README's quote of Robinson on the
"LLM-mints-an-HP-code-that-doesn't-exist" failure mode.

- present codes: 610; distinct: 260
- invalid codes: **0**
- obsolete codes: **0**
- **PASS**

Every code the pipeline emitted exists in the pinned release
(`hp/releases/2026-02-16`) and none is obsolete. The judge-over-a-shortlist
design holds: no fabricated identifiers.

### A3 — Ancestor closure (PASS)

Answers round 1 §3 and round 2 A (true-path rule). Checks that, where ancestor
propagation is computed, the closure is internally consistent and no negated
finding leaks into it.

- checked patients: **26 / 26**
- patients mismatched: **0**
- negated terms leaked into closure: **0**
- **PASS**

Closure is correct where it runs. The honest caveat from round 2 stands and is
repeated in section 5: propagation is off by default in the shipped `process`
run, so the default workbook is still a flat leaf list.

### A6 — IC distribution (PASS)

Answers round 1 §9 and Q9 ("if you are mostly predicting *Phenotypic
abnormality*, you have built a syntactic exercise").

- terms scored: 543
- median IC: **4.77**, mean: 5.19
- min: 1.33, max: 9.47
- fraction below IC 2: **0.015**

The output is specific. Only 1.5% of scored terms sit near the root. The
"everything-is-fatigue" / near-root failure mode does not show up in the IC
profile of this run.

### A4 — Judge hygiene (PASS)

Answers round 1 §10 (silent fallback) and round 2's credit for the
`needs_review` flag.

- rows total: 698
- needs-review rows: **0**
- confidence median: 0.90; min: 0.00

No row was forced through on a silent fallback in this run. One row sits at
confidence 0.00; with `needs_review` at 0 it was not flagged, which is worth a
look (see limitations).

### A5 — Negation surfaced (PASS)

Answers round 1 §6 and round 2 Q5. Confirms the pipeline emits explicit Absent
rows rather than dropping denials silently.

- present rows: 610
- explicit **Absent** rows: **88**
- patients with at least one Absent row: 19 / 26

Denials are surfaced as Absent rows, across most of the cohort. The harder
question — does a denied symptom still get a Present code minted? — is section 4.

### A2 — Deduplication (FAIL)

Stated plainly: this one fails.

- duplicated (patient, code) pairs: **55**
- worst case: MGA.038 carries `HP:0012514` **5 times**; MGA.419 carries
  `HP:0012378` 5 times
- **FAIL**

The same HP code is emitted multiple times for the same patient when the symptom
is mentioned more than once in the interview. 55 such pairs across the cohort.
This inflates row counts and pollutes any per-patient code set that isn't
deduped before use. It is a real defect in the shipped output. The downstream
agreement counts in source B were computed on deduped sets, so this does not
distort section 3, but the raw workbook a GP opens carries the duplicates.

## 3. Source B — agreement with Marc

Agreement with one annotator. Not accuracy. Marc's codes are not a gold
standard, and every disagreeing code — `marc_only` or `pheno_only` — is held to
the transcript.

Seven of 26 recordings carry a Marc row (`061, 402, 412, 450, 460, 466, 467`).
MGA.460 has a Marc row but no listed disagreements, so it is not adjudicated.
Six patients are scored.

### Per-patient overlap

| Patient | pred n | Marc n | agree | pheno-only | marc-only | Jaccard |
|---------|-------:|-------:|------:|-----------:|----------:|--------:|
| MGA.061 | 8 | 11 | 2 | 6 | 9 | 0.118 |
| MGA.402 | 13 | 16 | 5 | 8 | 11 | 0.208 |
| MGA.412 | 18 | 32 | 7 | 11 | 25 | 0.163 |
| MGA.450 | 32 | 13 | 6 | 26 | 7 | 0.154 |
| MGA.466 | 22 | 5 | 3 | 19 | 2 | 0.125 |
| MGA.467 | 25 | 15 | 6 | 19 | 9 | 0.176 |

### Micro agreement (label: agreement, not accuracy)

Pooled over the 6 patients:

- agree (tp): 29
- pheno-only (fp): 89
- marc-only (fn): 63
- **precision vs Marc: 0.246**
- **recall vs Marc: 0.315**
- **F1 vs Marc: 0.276**

These are agreement figures between two fallible coders, labelled exactly that
way in the source JSON: *"AGREEMENT with one annotator, NOT accuracy"*. They are
not an accuracy rate and must not be quoted as "% correct". n is 6, the model is
stochastic, and there is no second annotator, so no κ. Treat these as a picture
of where two readings of the same conversation diverge.

### Adjudication tally (152 disagreements)

Each disagreeing code classed once:

| Class | Meaning | Count |
|-------|---------|------:|
| (i)   | Phenoscribe error — not supported by transcript | 10 |
| (ii)  | Defensible, transcript-supported (either side)   | 93 |
| (iii) | Marc code outdated / unsupported by *this* transcript | 36 |
| (?)   | Undecidable from transcript alone | 13 |
| **Total** | | **152** |

What the counts mean:

- **(ii) = 93.** Most of the disagreement is substance the exact-code overlap
  metric missed. A large share is Marc and Phenoscribe coding the same complaint
  at different granularity — headache vs migraine, fatigue vs chronic fatigue,
  slurred speech vs dysarthria, limb weakness vs lower-limb weakness. The micro
  F1 of 0.276 understates how often the two readings actually agree on what the
  patient said. They agree on the finding and split on the code.
- **(iii) = 36.** These are Marc codes the French transcript does not support:
  lab values (transaminases, factor VIII, aldosterone), imaging/pathology
  findings (gliosis, encephalocele, cerebral hypoplasia), and chart-level
  inferences. This is not "Marc got it wrong". It is a scope difference between
  everything in a patient's record and what one conversation contains. A
  transcript-only system cannot and should not mint these.
- **(i) = 10.** The actionable Phenoscribe errors. Almost all are near-miss
  mappings: numb tongue coded as *tongue pain*, "leans left" coded as *horizontal
  sacrum*, reading loss coded as *dyslexia*, exertional breathlessness coded as
  the *rest* subtype, adult inability to cry coded as infantile *weak cry*, and
  one non-phenotype modifier (*Severe*, HP:0012828) emitted as a standalone code.
  Tighten mapping so a real symptom doesn't snap to a structurally adjacent term,
  and stop emitting modifier terms as codes.
- **(?) = 13.** Honest ties: broad parent terms, subtype calls the transcript
  can't settle (anomic aphasia, axonal neuropathy, seizure vs jerk), and a few
  vague Phenoscribe maps.

## 4. Negation and recall@5

### Negation (source A, French path)

Method: grepped the 26 transcripts for French denial constructions (`n'ai pas`,
`pas de`, `plus de`, `jamais`, `aucun`, `ni … ni`, `non plus`), read each in
context to confirm a genuine symptom denial, then inspected the patient's rows.

- denied-symptom mentions examined: **31** (covering ~40 HPO terms)
- correctly not minted as Present (Absent row, or absent from output): **29 / 31**
- the 2 "Present" rows are both correct on inspection: MGA.408 is a denial the
  patient immediately reverses ("Pas d'acouphènes? **Si.**"), and MGA.419's "pas
  de souffle" is the patient affirming exertional breathlessness
- **genuine leaks: 0**

Cross-check: 88 explicit Absent rows across the cohort, and no (patient, code)
pair is coded both Present and Absent.

This is the round-2 worry — "*je n'ai pas de fièvre* still produces an HPO code
for fever" — tested on the actual French run. In this run it does not. The
denial does not leak into a Present code. The sample is purposive, not
exhaustive, and the model is stochastic; this is evidence for this run, not a
guarantee.

### Recall@5 (source A — retrieval ceiling)

The judge can only pick a code the retriever shortlisted, so retrieval recall is
the hard ceiling on the whole pipeline. The exact French-path ceiling is **not
measurable from this run's artifacts**: the extractor's `clinical_term` query
string is not persisted anywhere (not in `results.xlsx`, the run log, or
`output/pseudo`). Two bounds bracket it.

| Bound | query string | recall@5 | Wilson 95% CI |
|-------|--------------|---------:|---------------|
| Lower | raw French verbatim | 0.170 (103/607) | [0.142, 0.202] |
| Upper | resolved English HPO term | 0.996 (259/260) | [0.979, 0.999] |

The lower bound queries the raw conversational French quote against an
English-label, English-centric index (all-MiniLM-L6-v2) — a cross-lingual
mismatch, which is exactly why the pipeline normalizes to a `clinical_term`
before retrieving. The upper bound is near-tautological: the query is the
resolved name of the code being tested for. The true French-path ceiling sits
between these and depends entirely on the quality of the extractor's
`clinical_term`. To measure it for real, persist `clinical_term` per row and
re-run against (clinical_term, final_code) pairs.

## 5. Honest limitations

- **n = 7** recordings have a Marc row; 6 are scored. Two dozen integer events.
  Report counts and Wilson intervals, not bare percentages to three sig figs.
- **Stochastic model.** A re-run could differ by a code or two. Every count here
  describes this run.
- **One annotator, so no κ.** Source C is dropped. There is no accuracy claim
  anywhere in this document. Source B is agreement only.
- **Retrieval recall ceiling is unmeasured on the French path.** The
  `clinical_term` query string isn't persisted, so the real ceiling sits
  somewhere between 0.170 and 0.996 and can't be pinned from these artifacts.
- **Dedup defect (A2).** 55 duplicated (patient, code) pairs in the shipped
  workbook. The raw output a GP opens carries repeats.
- **PII subword-offset leak — 4 of 13 pseudo files.** Four shapes, one root
  cause (mapping NER subword spans back to character offsets in `pii.py`):
  - `PERSON_1loïse` (×2, MGA.038) ← *Héloïse* — a **genuine privacy leak**, half
    a real name survives redaction.
  - `PERSON_1ynéco` (×2, MGA.061) ← *gynéco* — a clinical word false-flagged as a
    person and mangled (over-redaction + signal loss).
  - `LOCATION_1LOCATION_2` (MGA.408) ← two adjacent places colliding.
  - `ORGANIZATION_1L` (MGA.404) ← an `l'` article fragment leaking.
  The `loïse` case is a privacy defect; the other three are correctness defects.
- **Known clinic-name under-redaction.** Clinic names like *Marie-Curie* are
  logged, not fixed. The README documents this limitation.
- **Ancestor propagation off by default.** A3 passes where it runs, but the
  shipped `process` run emits a flat leaf list.
- **Simulated review.** This is a pre-mortem against a model rehearsing
  Robinson's objections. No human external reviewer has seen the code. The word
  "review" stays qualified with "simulated".

## 6. Reproduce

The reference-free battery and the raw Marc concordance counts come from one
read-only command:

```
.venv/bin/python scripts/validation/measure_reffree.py \
    --results output/results.xlsx \
    --gt data/ground_truth/CRS__under20_35_HPO_corr.xlsx \
    --obo data/hpo/hp.obo \
    --out output/validation/reffree.json \
    --disagreements output/validation/marc_disagreements.json
```

Exit code is always 0 — this is measurement, not a gate. Findings are in
`output/validation/reffree.json`. The negation, recall@5, and adjudication
markdown alongside it were produced by manual reading of the transcripts and the
workbook, as documented in each file's Method section.

## 7. The Robinson Test — re-scored on this French data

Re-scored against the 26-recording French run. Only the questions this run
speaks to (Q1, Q2, Q5, Q7, Q8, Q9, Q10) are listed. Honest verdicts.

| Q | Question | Round-2 score | This French run |
|---|----------|---------------|-----------------|
| 1 | HPO release pinned, printed, `replaced_by` followed | Yes | **Yes** — run pinned to `hp/releases/2026-02-16`; A1 finds 0 invalid, 0 obsolete codes. |
| 2 | Term-level F1 on a published gold standard | No | **Still no** — this run has agreement-with-Marc (F1 0.276, n=6), which is agreement, not accuracy, on a non-gold reference. No accuracy number on the product. |
| 5 | Negation — "je n'ai pas de fièvre" | No | **Improved to a pass on this run** — 31 denials examined, 0 genuine leaks, 88 Absent rows, no Present/Absent collision. Purposive sample, stochastic model. |
| 7 | Ancestor closure for one patient | Partial | **Partial** — A3 passes on all 26 where closure runs; still off by default in the shipped run. |
| 8 | False-positive rate on a healthy control | No | **No** — no healthy-control recording in this cohort. The negation result bounds one FP mode (denied symptoms), not the general FP rate. |
| 9 | IC distribution of predicted terms | No (for the product) | **Yes (for this run)** — A6 measured on the codes the pipeline emitted: median IC 4.77, 1.5% below IC 2. Specific output, on the product, on the French path. |
| 10 | Inter-annotator agreement | Open | **Open** — still one annotator (Marc). No κ. Source B is reported as agreement, never accuracy. |

Net: Q1 and Q9 are clean passes on the French product. Q5 passes on this run as
a purposive sample. Q7 remains partial (off by default). Q2, Q8, Q10 stay
unanswered, and we say so. The dedup FAIL (A2) and the PII subword leak are open
defects to fix before round 3.
