---
type: mock-review
round: 3
date: 2026-06-04
reviewer: "Simulated Peter N. Robinson (Humboldt Professor, BIH @ Charité; HPO PI)"
disclaimer: "SIMULATED. Robinson has not seen this repository. This is a third-pass pre-mortem written after reading the French-path validation produced in response to rounds 1 and 2. It is a model rehearsing the objections his lab would likely raise, used to harden the tool. It is not external review. Keep the word 'simulated' on the provenance."
reviews:
  - ./2026-06-03-mock-robinson-review.md
  - ./2026-06-03-robinson-review-round2.md
response_reviewed: ./2026-06-04-french-path-validation.md
evidence:
  - output/validation/reffree.json
  - output/validation/negation_evidence.md
  - output/validation/recall_at5.md
  - output/validation/marc_adjudication.md
  - output/results.xlsx
  - output/pseudo/*.txt
provenance:
  hpo_release: hp/releases/2026-02-16
grounding:
  - "True-path rule: a term annotation entails all is_a ancestors (HPO docs; PHENOstruct/HEMDAG)."
  - "Resnik (1995): sim = IC of the most informative common ancestor. Lin (1998): normalised by IC of both terms."
  - "FastHPOCR (Groza et al., Bioinformatics 2024): 0.76 document-level F1 on GSC+, degrades on noisier clinical narratives."
---

# Mock Reviewer Report, Round 3 — *Phenoscribe*
*Written as if by P. N. Robinson after reading the French-path validation. Same caveat as the last two: this is a simulation, a pre-mortem, not a real review. The provenance keeps the word "simulated" and so do I.*

I will start where I always start: did the thing I asked about actually get measured, on the thing a GP actually runs? For two rounds the answer was no — every number was English (GSC+, recall@k on oracle labels) or n=1. This time someone ran `phenoscribe process` on 26 real French recordings with a live key and looked at the raw output instead of describing it. That is the first honest French-path evidence in this repository, and I want to credit it before I take it apart. I also did what the brief asked and opened the workbook myself rather than trusting the summary. Two of the things I found are better than the prose; one of them is worse, and the summary does not disclose it.

---

## 1. What this run actually answers — with French-path evidence

These are the objections that moved, and the specific numbers behind them. I verified each against `output/results.xlsx` with openpyxl, not against the markdown.

- **§1 / Q1 — fabricated codes, ontology pinning.** Genuinely closed. The `Provenance` sheet prints `hpo_release = hp/releases/2026-02-16`. Of **610** present codes (**260** distinct) I find **0 invalid and 0 obsolete** in the pinned release. The judge-over-a-shortlist design does what the README claimed: it does not mint HP IDs that don't exist. This was already the strongest part of the design; it now has French-path numbers under it.

- **§9 / Q9 — IC distribution, the "everything-is-fatigue" failure.** Closed, on the product, on the French path. Across **543** scored terms the median IC is **4.77**, mean 5.19, and only **1.5%** sit below IC 2. This is the question I care about most after fabrication, because it is the one that separates a phenotyper from a syntactic exercise, and the output is specific. A coder that mostly emitted *Phenotypic abnormality* and *Abnormality of the nervous system* would show a left-shifted IC mass. This one does not. Round 2 scored this "No (for the product)." It is now a Yes for this run.

- **§6 / Q5 — negation, "je n'ai pas de fièvre."** Materially improved, and this is the answer that would have worried me most. In round 2 the `negated` field flipped a column label but the code was still minted — a fever code for a denied fever. On this run I cannot find that leak. `negation_evidence.md` walks **31** denied-symptom mentions (~40 HPO terms); **29/31** are correctly handled as Absent or simply absent, and the **2** "Present" rows are both correct on inspection — MGA.408 is a denial the patient reverses ("Pas d'acouphènes? **Si.**") and MGA.419's "pas de souffle" is the patient *affirming* exertional breathlessness. The workbook confirms **88 explicit Absent rows** across 19 patients, and I checked: no `(patient, code)` pair is coded both Present and Absent. Denial no longer leaks into a Present code in this run. Caveat the document states and I endorse: the sample is purposive (grep + read), the model is stochastic, this is evidence for one run, not a guarantee.

- **§3 / Q7 — the true-path rule / ancestor closure.** Partly closed. The `Ancestor Closure` sheet is real: **610 leaf rows + 1422 ancestor rows = 2032**, with an `Origin` column tagging each and `Derived From` provenance. Closure is internally consistent on 26/26 patients and no negated term leaks into it. That is the true-path rule, correctly implemented. The reason it is only *partly* closed is in §3 below.

So: four objections move this round (Q1, Q9 clean; Q5 improved to a pass on this run; Q7 partial). That is real progress measured in the right language, and it is more than rounds 1 and 2 delivered combined.

## 2. The no-gold-standard reframe — honest move, or evasion?

This is the most important judgement call in the document, so I will be explicit. Splitting the evidence into **Source A — reference-free correctness** (validity, closure, IC, negation: properties that are right or wrong with no reference) and **Source B — agreement with Marc** (overlap reported as agreement between two fallible coders, never as accuracy) is the **honest move, and the correct one.** I have argued for years that hop-count masquerading as similarity, and "we found 3.9× more codes" masquerading as winning, are the two ways these tools lie to themselves. Reporting Source B as *agreement* — labelled in the JSON itself as `"AGREEMENT with one annotator, NOT accuracy"` — refuses both. When the two coders disagree the document does not assume Marc is right; it adjudicates 152 disagreements against the transcript and finds only **10** real Phenoscribe errors, **93** defensible-either-side (mostly granularity splits — headache vs migraine, fatigue vs chronic fatigue), and **36** Marc codes the conversation cannot support (lab values, imaging, chart-level inference). That last bucket is the right call: a transcript-only system *should not* mint a factor-VIII level, and saying so is not special pleading, it is scope discipline.

Where the reframe shades from honesty toward evasion is in what it lets the headline avoid. "Agreement, not accuracy" is true, and it is also a very comfortable place to stand when you have no second annotator and a recall ceiling you cannot measure. Two specific places:

- The micro figures (precision 0.246 / recall 0.315 / F1 0.276 vs Marc, n=6) are reported with the right label, but Jaccard against the *only* human code list in reach runs **0.118–0.208**. The adjudication argues much of that gap is granularity, and I believe a lot of it is. But "agreement is low and most of the disagreement is defensible" is a claim the data can only *partly* carry at n=6, and the reader should not walk away thinking the granularity story is established. It is plausible and partly evidenced. It is not measured.
- Calling Source A "correctness" is fair for validity and closure (those are theorems). It is weaker for IC and negation, which are correctness *of properties*, not correctness *of the codes*. A run can have a beautiful IC distribution and still code the wrong specific phenotype — see the 10 near-miss errors (numb tongue → *tongue pain*, "leans left" → *horizontal sacrum*). High median IC means the output is specific; it does not mean it is *right*. The document mostly respects this distinction. The headline blurs it.

Net: the reframe is honest, not evasive — but only because the limitations section is unusually candid. Strip the caveats and the same numbers become evasion. Keep them welded on.

## 3. Still unanswered, and newly exposed

### Newly exposed by my own read — A4 is wrong in the summary
This is the finding that earns the brief's instruction to open the file myself. `reffree.json` reports A4 (judge hygiene) as `needs_review_rows: 0` and `confidence_min: 0`, and the validation prose says "No row was forced through on a silent fallback in this run." **The workbook says otherwise.** The `Sheet` tab has **78 rows flagged `REVIEW`** in the Needs Review column and **70 rows at confidence 0.00** — Hernia, Dyslexia, Vertigo, *Horizontal sacrum*, *Weak cry*, and dozens more. So one of two things is true: the validation script read the wrong column (or a stale workbook), or the workbook was regenerated after measurement. Either way, **the A4 PASS is not supported by the file it cites.** This matters beyond the bookkeeping error: the judge *is* surfacing low-confidence rows (good — that is the round-1 fix working), and the validation accidentally erased the evidence of its own best behaviour while also publishing a count that doesn't reconcile. Fix the measurement, then re-report. A validation that disagrees with the artifact it validates is the one thing a clinical tool cannot ship.

### The dedup defect (A2) — confirmed, and it bleeds into the closure
Confirmed against the raw sheet: **55** duplicated `(patient, code)` present pairs. MGA.038 carries `HP:0012514` (*Lower limb pain*) **five times**, with five different verbatims ("j'ai mal dans le bas des jambes", "ça fait super mal dans les jambes", …). The document is honest that this is a FAIL. What it does not say: the duplicates **propagate into the Ancestor Closure sheet** — I see *Lower limb pain* repeated in MGA.038's closure rows — so the one sheet built to satisfy the true-path rule inherits the defect. A downstream similarity engine fed the raw workbook will see five copies of one finding and weight it 5×. The agreement counts in Source B were deduped, so §3 of their doc is safe; the workbook a GP opens is not. This is a half-day fix (collapse on `(patient, code)`, keep the highest-confidence verbatim) and it should land before anything else.

### The PII leak — real, and undercounted
The document admits a subword-offset leak in "4 of 13 pseudo files." Grepping `output/pseudo/` for a placeholder followed by letters, I find it in **10 of the 26 MGA files**: MGA.038, .061, .404, .408, .409, .411, .427, .429, .434, .460. Two distinct failure modes:
- **Genuine PII survival** — `PERSON_1loïse` (×2, *Héloïse* — half a real given name survives redaction), `PERSON_1vid` (*David*), `PERSON_3ivaldi` (*Vivaldi*), `PERSON_1vel`, `PERSON_3ait`. The NER found the entity and the offset-mapping back to character spans truncated the replacement, leaving the tail of the name in the "pseudonymised" text that then goes to the LLM and into the logs.
- **Over-redaction / collision** — `PERSON_1PERSON`, `LOCATION_1LOCATION`, `ORGANIZATION_1L` (an `l'` article fragment), `PERSON_2timulation` (a clinical word eaten).

The root cause is one bug in `pii.py` (subword span → character offset), as the document says. But "4 files, one of them a real leak" understates it to "10 files, at least four with surviving name fragments." For a tool making GDPR-relevant claims, the count is the claim. Re-run the grep, report 10, and fix the offset arithmetic before this touches a real recording. A pseudonymiser that leaks `loïse` is not a pseudonymiser.

### Still genuinely unanswered
- **n = 7 (6 scored).** Q2, Q8, Q10 untouched. Two dozen integer events. The document reports Wilson intervals instead of bare percentages — correct — but no interval rescues n=6 into a measurement. It is a picture, not a number.
- **The recall ceiling is still unmeasured on the French path.** The honest and slightly damning finding: the extractor's `clinical_term` — the actual query string — **is persisted nowhere**, so the true retrieval ceiling cannot be reconstructed. The two bounds (0.170 raw French verbatim, 0.996 English-term-of-the-answer) bracket *everything*. The lower bound shows a 17% recall@5 when you hand the English-centric all-MiniLM index a colloquial French quote; the upper bound is near-tautological. The ceiling on the whole pipeline sits somewhere in a 0.17–0.996 band, which is to say: unknown. This is the single most decision-relevant number for the tool and it is the one number the run cannot produce. Persist `clinical_term` and re-run; this is a one-line logging change.
- **No second annotator, no κ.** Source C is dropped, and the document says so. One coder remains one opinion. The 93 "defensible either side" disagreements are exactly where a second annotator would adjudicate; without one, the granularity story stays a hypothesis.
- **Ancestor propagation off by default.** A3 passes where it runs, but the shipped `process` workbook's primary `Sheet` is still the flat leaf list; the closure lives in a secondary sheet. For a tool whose output feeds Exomiser/LIRICAL/Phenomizer, the true-path closure is the deliverable, not an appendix. Default it on, or make the closure sheet the canonical one.

## 4. The Robinson Test — re-scored, round 3

| Q | Question | Round-2 | Round-3 verdict (French run, my own read of the workbook) |
|---|----------|---------|-----------------------------------------------------------|
| 1 | HPO release pinned, printed, `replaced_by` followed | Yes | **Yes** — Provenance sheet prints the release; 0 invalid, 0 obsolete across 610 codes. Clean. |
| 2 | Term-level F1 on a published gold standard | No | **No** — only agreement-with-Marc (F1 0.276, n=6), correctly labelled agreement not accuracy. No accuracy number on the product. |
| 3 | vs Fenominal / FastHPOCR / Doc2HPO | Partial | **Partial (unchanged)** — FastHPOCR was run in round 2 (and won, 0.78 vs 0.45 retriever); this French run adds no new comparator. |
| 4 | recall@5 / @10 before the judge | Partial | **No (for the French path)** — `clinical_term` not persisted; the real ceiling is unmeasurable, bracketed only by 0.170–0.996. Worse than "partial" because the honest answer is "unknown." |
| 5 | Negation — "je n'ai pas de fièvre" | No | **Pass on this run** — 31 denials examined, 0 genuine leaks, 88 Absent rows, no Present/Absent collision. Purposive sample, stochastic model. |
| 6 | Frequency / onset / severity schema | Partial | **Partial** — schema present (Frequency/Onset/Severity columns exist in the sheet); quality still unmeasured. |
| 7 | Ancestor closure for one patient | Partial | **Partial** — closure sheet correct on 26/26, but off by default and it inherits the dedup duplicates. |
| 8 | False-positive rate on a healthy control | No | **No** — no healthy-control recording. Negation bounds one FP mode, not the general rate. |
| 9 | IC distribution of predicted terms | No (product) | **Yes** — median IC 4.77, 1.5% below IC 2, on the codes the pipeline emitted. The output is specific. |
| 10 | Inter-annotator agreement | Open | **Open** — still one annotator, no κ. |
| — | (judge hygiene, A4) | (credited) | **Regressed in the reporting** — workbook shows 78 REVIEW / 70 conf-0 rows; reffree.json claims 0. The behaviour is fine; the measurement is wrong. |

Tally: Q1 and Q9 clean. Q5 passes on this run. Q3, Q6, Q7 partial. Q2, Q8, Q10 unanswered and admitted. Q4 is now honestly "unknown" rather than "partial." That is two clean French-path passes and one genuine behavioural win on negation — real movement — set against a recall ceiling nobody can pin, a dedup defect that pollutes the closure, a PII leak undercounted by more than half, and a validation file that contradicts its own workbook on A4.

## 5. Closing — cohort, or hand-code and triage?

Still hand-code and triage. But the gap is closing, and I want to be precise about why.

What changed my mind *at the margin*: the tool no longer fabricates codes, no longer drowns in near-root terms, and — the one I genuinely did not expect — no longer mints a code for a symptom the patient denied. Those are the three failure modes that make me refuse a phenotyper outright, and on this 26-recording French run, measured directly, none of them fires. The judge-over-a-shortlist instinct was right and the evidence now supports it in the language the tool actually runs in.

What still keeps it out of a cohort, in order: (1) you do not know your retrieval ceiling on the French path — it could be 0.2, it could be 0.7, and until you persist `clinical_term` you are flying blind on the single most important number; (2) the shipped workbook duplicates findings and that pollution reaches the closure sheet other tools would consume; (3) the pseudonymiser leaks real name fragments in 10 of 26 files, and a phenotyping tool that leaks `Héloïse` is a GDPR incident, not a triage assistant; (4) n=6 against one annotator is a picture, not a measurement; (5) your own validation file disagrees with your own workbook on judge hygiene, which means the measurement harness is not yet trustworthy — and the harness is the thing I have to trust before I trust the tool.

So: fix the `clinical_term` logging and measure the French ceiling; collapse the duplicates before they reach the closure; fix the offset bug in `pii.py` and re-report the leak count as 10; reconcile A4 against the actual workbook; and get a second annotator on even ten transcripts so "defensible either side" becomes a κ instead of an argument. Do those five and you have a tool I would let a clinician *triage with under review* — not yet code a cohort with unsupervised. Tell Marc, again and with respect, to keep coding by hand and to use this as a triage assistant whose every output he checks. The difference from round 2 is that I now believe the triage would save him time rather than cost it. That is progress. It is not a green light.

— *(mock)* P. R.
