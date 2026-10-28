---
date: 2026-06-04
status: parked — pick up in a later exploration session
owner: tarik
origin: arete brainstorm (GROUND + EXPLORE done; DECIDE/STRESS/SHIP pending)
related:
  - ../exports/2026-06-04-french-path-validation.md
  - ../shipped/2026-06-04-round3-fixes.md
  - ../../src/phenoscribe/match_hpo.py
---

# Plan: calibrated P(HPO code correct), to replace LLM self-confidence

**Parked deliverable.** This captures a brainstorm so a later session can resume
at DECIDE without re-deriving anything. It is NOT yet a build spec — the
architecture below is the leading candidate, not a final decision.

## Problem

Today each assigned HPO code carries the **LLM judge's self-reported confidence**
(`match_hpo.py:27`), with `needs_review` set below 0.5 and confidence capped at
0.4 when the retrieval shortlist is weak (`match_hpo.py:140–152`). LLMs are
poorly calibrated at judging their own correctness, so this number is a triage
signal wearing a probability's clothes. We want a **calibrated P(code correct)**:
codes stamped 0.8 should be acceptable ~80% of the time, defensible statistically.

## Decisions already made (grounding)

- **What P drives:** a *reported quality score* (shown per code) AND a *triage
  threshold* (which rows a clinician reviews). Not an auto-accept gate, not
  downstream weighting. So calibration matters (people read 0.8 as 80%), but the
  stakes are bounded.
- **Label budget:** ~50 binary-adjudicated `(verbatim, code) → accept/reject`
  pairs. This is a **spot-check, not a training set** — enough to *validate*
  calibration direction with wide CIs, not to *fit* a flexible calibrator.
  Implication: prefer methods that *construct* probabilities over methods that
  *fit* them. (Isotonic regression overfits at n=50; avoid.)
- **Compute:** a few extra LLM calls per symptom are acceptable (self-consistency
  is on the table). Local/GDPR constraint still holds — prefer local models where
  possible.
- **No gold standard.** "Correct" is operationalized as "a clinician would accept
  this code for this verbatim" (binary), judged per code against one quote — far
  cheaper and more defensible than re-coding interviews. See
  [[project_no_gold_standard]].

## The signals (candidate features for P)

| ID | Signal | Cost | Labels | Note |
|----|--------|------|--------|------|
| D1 | **Retrieval geometry**: top-1 cosine distance + **margin to candidate #2** + top-5 spread | free (already computed) | none | margin-to-#2 often beats absolute distance as a correctness cue |
| D2 | **Judge self-consistency**: run judge K=8–16× (temp>0 and/or shuffled shortlist); agreement frequency | K calls | none | agreement frequency *is* a constructed frequency; literature: calibrates reliably at N≥8 |
| D3 | **Multi-view agreement**: query retrieval with French `patient_verbatim` AND English `clinical_term` (+ optional back-translation); cross-view agreement | cheap | none | independent views catch different failures than temperature sampling |
| D4 | **Entailment back-check**: local NLI cross-encoder scores whether the verbatim entails the official **HPO term definition** | local fwd pass | none | meaning-grounded, not preference-grounded; better calibrated than verbalized confidence |
| D5 | **Second-best margin / adversarial judge**: judge reports P(runner-up is right) or argues for/against | 1 call | none | best-vs-second gap as a separation signal |

## Turning signals into a calibrated probability

- **D6 — Agreement-as-probability + Beta smoother** `[no fitting]`: take the
  D2/D3 agreement rate, smooth with a Beta(α,β) prior so 5/5 isn't a naked 1.0.
  Probability by construction; the 50 only validate it. Best fit for the label
  budget.
- **D7 — Venn-Abers predictor** `[uses the 50]`: combine signals into one score,
  wrap in Venn-Abers → **calibrated probability intervals with finite-sample
  validity**, minimal assumptions, widens gracefully at n=50. More defensible
  than isotonic. Conformal variant instead yields a coverage *set* ("code in the
  90% set") rather than a point P — likely less useful for a reported score.
- **Temperature scaling (1 param) with the invert-softmax trick** `[uses the 50]`:
  if we keep any LLM-verbalized probability as an input, don't re-softmax it —
  invert to logits first, then scale. A single scalar is stable at n=50.

## Leading candidate architecture (the DECIDE we did not finish)

Per assigned (verbatim → code):
1. **D2** judge agreement frequency (K≈8) — backbone.
2. **D1** retrieval margin (top1−top2) — free feature.
3. **D4** one local NLI cross-encoder pass (verbatim ↔ HPO definition) — feature.
4. Combine: `score = w·[agreement, margin, entailment]` (tiny logistic, or just
   agreement smoothed by D6 if we want zero fitting).
5. **Calibrate/certify** with Venn-Abers (or 1-param temperature) on the 50 pairs.
6. **Report** P with a 95% bootstrap CI; demote the old judge self-confidence to
   one input among several, not the headline.

Cost ≈ 2 extra LLM calls + 1 local cross-encoder pass per symptom.

## Validating calibration on ~50 labels (honestly)

- **SmoothECE** (kernel-smoothed, hyperparameter-free) or **Brier score**, each
  with **bootstrap 95% CIs** (resample the 50 ×1000). Avoid binned ECE at n=50.
- **Coarse reliability diagram** (3–4 bins) with bootstrapped bands to show *where*
  calibration breaks (expect high-P overconfidence).
- **External coherence check:** does P track the 7-patient Marc-agreement and the
  existing `needs_review` pattern? Weak corroboration, not proof.
- Headline framing: "spot-checked, directionally calibrated (n=50, wide CIs)",
  never "rigorously calibrated".

## Build order (when resumed)

1. Persist the raw signals the pipeline already has (cosine distances, margin,
   per-run judge picks) into the match record — cheap, unblocks everything.
2. Add D2 (K-sample judge agreement) behind a config flag; log agreement freq.
3. Add D4 (local NLI cross-encoder) as an optional scorer.
4. Build the ~50-pair adjudication set (binary accept/reject; you or Marc).
5. Fit Venn-Abers / temperature; produce reliability diagram + Brier ± CI.
6. Replace the reported confidence column with calibrated P + CI; re-derive the
   `needs_review` threshold from the calibrated curve, not a hardcoded 0.5.

## Open questions / gaps (for the DECIDE session)

- Point probability (Venn-Abers) vs coverage set (conformal) — the reported-score
  goal favors Venn-Abers; confirm.
- NLI cross-encoder calibration drift on French biomedical text (models trained on
  SNLI/MNLI, English news/fiction) — validate on the 50, maybe a French NLI model.
- Is agreement measuring *correctness* or just *model stability*? The 50 labels
  arbitrate this; if agreement ≠ correctness, lean harder on D1/D4.
- Whether to compute P in the interactive path or as an offline batch pass.

## Prior art (from the researcher brief, 2026-06-04)

- **Self-consistency / CISC** — agreement↔correctness; calibrates at N≥8.
  (arXiv 2403.09849, 2502.06233)
- **Conformal prediction / Venn-Abers** — finite-sample guarantees, no minimum
  set size, graceful at n=50. (arXiv 2405.01976, 2502.05676, 1211.0025; NER
  conformal arXiv 2601.16999)
- **Verbalized-confidence miscalibration + invert-softmax temperature scaling.**
  (arXiv 2410.06707, 2412.14737)
- **Retrieval margin as correctness feature.** (arXiv 2510.00137)
- **NLI cross-encoders for verification** (`cross-encoder/nli-deberta-v3-base`),
  better calibrated than verbalized confidence. (HF / sentence-transformers)
- **AutoPCR (2024)** — closest prior art: LLM phenotype concept recognition with
  two confidence tiers (τ₁=0.95 HIGH, τ₂=0.85 MEDIUM). (arXiv 2507.19315)
- **Clinical CR tools** (FastHPOCR/PhenoTagger/Doc2HPO) — mostly do NOT publish a
  confidence mechanism; our judge-based P would be more principled.
- **Small-n calibration metrics**: SmoothECE + bootstrap CIs, Brier over binned
  ECE. (arXiv 2309.12236; Apple ml-calibration)
