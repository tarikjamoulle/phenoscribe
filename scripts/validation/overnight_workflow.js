export const meta = {
  name: 'french-path-validation',
  description: 'Reference-free + Marc-agreement validation of the 26-recording run, then a mock Robinson review',
  phases: [
    { title: 'Measure', detail: 'reference-free battery (A) + negation + recall@5, in parallel' },
    { title: 'Adjudicate', detail: 'classify Marc disagreements (i/ii/iii) — agreement, not accuracy' },
    { title: 'Synthesize', detail: 'write the french-path-validation export' },
    { title: 'Review', detail: 'fresh web-grounded mock Robinson reviews the outputs' },
  ],
}

const PY = '/Users/tarikjamoulle/projects/hpo_identifier/.venv/bin/python'
const ROOT = '/Users/tarikjamoulle/projects/hpo_identifier'

// Honesty constraints handed to every agent so the framing never drifts.
const FRAMING = `
GROUND RULES (from context/plans/2026-06-04-scientific-validation-plan.md):
- Marc Jamoulle's manual codes are NOT a gold standard. Source A (reference-free
  checks) speaks to correctness; source B (overlap with Marc) is AGREEMENT only,
  never "accuracy" or "% correct".
- The French path is what a GP runs. Only 7 of 26 recordings have a Marc row:
  061, 402, 412, 450, 460, 466, 467.
- n is small and the LLM is stochastic. Report counts and intervals, not bare
  percentages to 3 sig figs. State limitations inline.
- This is a SIMULATED Robinson review used as a pre-mortem; keep "simulated" on it.
- Use ${PY} for python (the venv). Work read-only; do not modify src/ or re-run
  the pipeline. cwd is ${ROOT}.
`

phase('Measure')

// Three independent measurements, in parallel (barrier — synthesis needs all).
const measured = await parallel([
  () => agent(
    `${FRAMING}\nRun the reference-free battery and interpret it.\n` +
    `1. Run: ${PY} scripts/validation/measure_reffree.py --results output/results.xlsx ` +
    `--out output/validation/reffree.json --disagreements output/validation/marc_disagreements.json\n` +
    `2. Read output/validation/reffree.json. For EACH check A1..A6 and B, write a short paragraph: ` +
    `what it tests, the number, PASS/FAIL, and what it means against the matching Robinson remark ` +
    `(A1=Q1 release/validity, A2=dedup plan, A3=Q7 true-path, A4=judge hygiene, A5/negation=Q5, A6=Q9 IC, B=Q2/Q10).\n` +
    `3. Call out A2 (dedup) and any FAIL explicitly — those are real defects, not to be smoothed over.\n` +
    `Return your written interpretation as text. The JSON on disk is the source of truth.`,
    { label: 'measure:reffree', phase: 'Measure' }),

  () => agent(
    `${FRAMING}\nNegation evidence on the FRENCH path (Robinson Q5 — "je n'ai pas de fièvre").\n` +
    `Denied symptoms must NOT get a present HPO code.\n` +
    `1. grep the 26 transcripts in output/transcripts/ for French denial constructions ` +
    `(n'ai pas, pas de, plus de, jamais, aucun, non plus, ni ...). Collect ~15-25 concrete denied-symptom mentions.\n` +
    `2. For each, open the matching patient's rows in output/results.xlsx (read it with ${PY} + openpyxl) ` +
    `and determine whether that symptom was coded Present (a failure), Absent (correct), or absent from output.\n` +
    `3. Write a markdown table to output/validation/negation_evidence.md: transcript | denied phrase | ` +
    `coded as | verdict. Tally how many denials were correctly NOT minted as present.\n` +
    `Return a 1-paragraph summary with the tally. Be honest about any leak.`,
    { label: 'measure:negation', phase: 'Measure' }),

  () => agent(
    `${FRAMING}\nRetrieval ceiling (recall@5) on the FRENCH path — the cap on the whole pipeline.\n` +
    `1. Inspect scripts/eval_recall.py to see how it computes recall@k against the Chroma index ` +
    `(data/chroma_db). Read its --help.\n` +
    `2. The honest French-path question: when the extractor's French clinical_term/verbatim is fed to the ` +
    `retriever, is the code the judge ultimately picked actually in the top-5? Use the present codes in ` +
    `output/results.xlsx and their verbatims. If eval_recall.py can be pointed at these, run it; otherwise ` +
    `compute recall@5 directly: for a sample of present (verbatim, final_code) pairs, query Chroma and check ` +
    `whether final_code is in the top-5. Use ${PY}.\n` +
    `3. Write output/validation/recall_at5.md with the method, n, and recall@5. Flag clearly that this is ` +
    `the French path (not English oracle labels) and note the sample size.\n` +
    `Return a 1-paragraph summary. If you cannot compute it reliably, say so plainly rather than inventing a number.`,
    { label: 'measure:recall', phase: 'Measure' }),
]).then(rs => rs.filter(Boolean))

phase('Adjudicate')

const adjudication = await agent(
  `${FRAMING}\nAdjudicate the Phenoscribe-vs-Marc disagreements (source B). This is the deliverable, not the raw overlap.\n` +
  `1. Read output/validation/marc_disagreements.json (per patient: agree / pheno_only / marc_only HP codes).\n` +
  `2. For context, read the patient transcript (output/transcripts/MGA.<id>.txt) and the patient's rows ` +
  `(with verbatims) in output/results.xlsx.\n` +
  `3. For EACH pheno_only and marc_only code, look up the HP term name (${PY} + hpotk, or the term shown in ` +
  `results.xlsx) and classify the disagreement as exactly one of:\n` +
  `   (i)  Phenoscribe error — coded something the transcript does not support.\n` +
  `   (ii) Defensible Phenoscribe code Marc did not list — supported by the transcript.\n` +
  `   (iii) Marc code that looks outdated/incorrect or unsupported by this transcript.\n` +
  `   Marc's codes are NOT assumed correct (the project rule). Where you genuinely cannot tell, mark (?) and say why.\n` +
  `4. Write output/validation/marc_adjudication.md: per patient, a table code | term | side | class (i/ii/iii/?) | ` +
  `one-line reason. Then a tally of i/ii/iii across all 7 patients.\n` +
  `Tone: explain the difference, do NOT grade Marc. Return a short summary with the tally.`,
  { label: 'adjudicate:marc', phase: 'Adjudicate' })

phase('Synthesize')

const exportPath = 'context/exports/2026-06-04-french-path-validation.md'
await agent(
  `${FRAMING}\nWrite the validation export to ${exportPath}.\n` +
  `Sources to read and combine:\n` +
  `- output/validation/reffree.json (the objective battery A1..A6 + raw B counts)\n` +
  `- output/validation/negation_evidence.md\n` +
  `- output/validation/recall_at5.md\n` +
  `- output/validation/marc_adjudication.md\n` +
  `Structure:\n` +
  `  front-matter (date 2026-06-04, type: validation, reviews the two mock Robinson exports).\n` +
  `  1. Headline — what this run is (26 French recordings, the first multi-patient French-path evidence), ` +
  `and the A/B framing (reference-free + agreement, NO gold standard).\n` +
  `  2. Source A — reference-free battery: one subsection per A1..A6 with the number, PASS/FAIL, the Robinson ` +
  `remark it answers. Lead with PASSES (A1 validity, A3 closure, A6 IC) and state FAILS plainly (A2 dedup).\n` +
  `  3. Source B — agreement with Marc: the per-patient table, the micro agreement P/R/F1 LABELLED "agreement, ` +
  `not accuracy", and the adjudication tally (i/ii/iii). Interpret what the (ii) and (iii) counts mean.\n` +
  `  4. Negation + recall@5 findings.\n` +
  `  5. Honest limitations: n=7, stochastic, single annotator so no kappa, recall ceiling, the dedup defect, ` +
  `the PII subword-offset leak (4/13 files — loïse/gynéco/adjacent-entity), known clinic-name under-redaction.\n` +
  `  6. Reproduce: the exact measure_reffree.py command.\n` +
  `  7. Updated Robinson Test scorecard (Q1,Q2,Q5,Q7,Q8,Q9,Q10) re-scored on THIS French data, honest verdicts.\n` +
  `Apply the user's writing style: short direct sentences, no "not X but Y" contrastive framing, no filler.\n` +
  `Return the final path and a 3-line summary of the headline numbers.`,
  { label: 'synthesize:export', phase: 'Synthesize' })

phase('Review')

const reviewPath = 'context/exports/2026-06-04-robinson-review-round3.md'
await agent(
  `You are simulating Peter N. Robinson (HPO PI, Monarch) reviewing Phenoscribe, ROUND 3. This is a SIMULATED ` +
  `pre-mortem — Robinson has not seen this repo; keep "simulated" on the provenance.\n` +
  `Before reading the repo, ground your standard in primary sources: WebSearch/WebFetch the HPO true-path / ` +
  `annotation-propagation rule, Resnik/Lin IC similarity, and HPO concept-recognition F1 conventions (Groza/FastHPOCR). ` +
  `Then judge.\n` +
  `Read, in this order:\n` +
  `- The two prior reviews: context/exports/2026-06-03-mock-robinson-review.md and ` +
  `context/exports/2026-06-03-robinson-review-round2.md (your own earlier objections + the Robinson Test).\n` +
  `- The new evidence: ${exportPath} and everything in output/validation/.\n` +
  `- Spot-check the RAW outputs yourself, do not trust the summary: open output/results.xlsx (${PY} + openpyxl), ` +
  `confirm A2 dedup really is duplicated, confirm the Ancestor Closure sheet, confirm Present/Absent. ` +
  `Open 2-3 output/pseudo/*.txt and confirm the subword-offset PII leak (grep for a placeholder followed by letters).\n` +
  `Write ${reviewPath} in your voice. Cover:\n` +
  `  - Which round-1/round-2 objections this run actually answers WITH FRENCH-PATH EVIDENCE (be specific, cite the number).\n` +
  `  - The no-gold-standard reframe: is reporting reference-free + agreement (not accuracy) the honest move, or evasion?\n` +
  `  - What is still unanswered or newly exposed (dedup defect, PII leak, n=7, recall ceiling, no second annotator).\n` +
  `  - A re-scored Robinson Test table with your round-3 verdict per question.\n` +
  `  - Closing: would you now let this near a cohort, or still hand-code + triage?\n` +
  `Be adversarial and specific. Credit what is real; do not be talked into a number the evidence does not support.\n` +
  `Return the final path and your one-paragraph verdict.`,
  { label: 'review:robinson-round3', phase: 'Review' })

return {
  done: true,
  measured: measured.length,
  exports: [exportPath, reviewPath],
  validation_dir: 'output/validation/',
}
