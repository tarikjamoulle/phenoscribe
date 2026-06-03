# Phenoscribe validation baseline (pilot)

Date: 2026-06-03
Robinson issue #5 (patient-ID join blocking the F1) and Robinson Test Q2 (real
measurement) and Q10 (single-annotator ground truth).

## Headline

First real term-level F1 for Phenoscribe, measured on the one pilot patient we
can score today.

| Metric | Strict (exact HPO ID) | Partial credit (≤2 hops) |
|---|---|---|
| Precision | 30.4% | 47.8% |
| Recall | 46.7% | 46.7% |
| F1 | **36.8%** | 47.2% |

n = 1 patient (MGA.467). This is a PILOT subset, not the full 1003-row cohort.
We only hold 4 cached transcripts here (038, 451, 454, 467), and of those only
MGA.467 has a row in `CRS__under20_35_HPO_corr.xlsx`. The audio for the rest of
the cohort is not in this environment.

## Why the F1 was blocked for a month

The pipeline derived the patient_id from the filename stem ("467"). The ground
truth keys the same patient as "MGA.467". The join produced zero overlap, so
every prediction looked like an extra and every GT code looked missed. Fixed by
a config-driven prefix (`patient.id_prefix: "MGA."`) prepended to the stem in
the CLI. The cached transcript is still looked up by bare stem, so
`--skip-transcription` keeps working.

A second blocker surfaced once the join worked: the GT cell for MGA.467 uses the
`Term/HP:0001279[verbatim]` delimiter style, while the prevalence parser only
reads the parenthesised `Term (HP:0001279)` form. The cohort GT mixes at least
three styles between patients. The validation scorer now falls back to a raw
`HP:#######` scan per patient, so mixed-delimiter rows score.

## What was measured

- Pipeline: `phenoscribe process` with `--skip-transcription`, provider
  `anthropic`, model `claude-sonnet-4-6`.
- Predicted codes for MGA.467: 23. Ground-truth codes: 15.
- Exact matches (7): HP:0000020, HP:0000622, HP:0001260, HP:0001279,
  HP:0002315, HP:0002321, HP:0003401.
- Missed (8, in GT, not predicted): HP:0000240, HP:0000738, HP:0001250,
  HP:0001649, HP:0002171, HP:0002924, HP:0006842, HP:0007340.
- Extra (16 predicted not in GT, e.g. HP:0001097, HP:0002027, HP:0002875).
  Some are plausible re-readings of the same transcript, not all are errors;
  strict scoring counts them all against precision.

### Strict vs partial credit

Strict = exact HPO ID match, document-level (TP if the gold HPO ID appears at
least once in the prediction). This matches the convention in HPO
concept-recognition benchmarks (Groza et al. 2024, GPT phenotype CR; FastHPOCR).
No hierarchy expansion.

Partial credit is the existing hierarchy-aware score: a prediction within 2
is_a hops of a GT code (parent, child, sibling, grandparent, uncle/nephew)
counts toward precision at 0.75 (1 hop) or 0.5 (2 hops). Recall counts exact
hits only in both views, so recall is identical (46.7%).

Report both. Strict is the number to quote against published CR systems.
Partial credit shows how many misses are near misses worth a second look.

## Reproduce

From the worktree `/Users/tarikjamoulle/projects/phenoscribe-worktrees/05-ground-truth-f1`:

```bash
# 1. Make the cached transcripts visible to --skip-transcription and feed them as inputs.
mkdir -p .tmp/output/transcripts .tmp/inputs
for p in 038 451 454 467; do
  cp /Users/tarikjamoulle/projects/hpo_identifier/output/transcripts/$p.txt .tmp/output/transcripts/$p.txt
  cp /Users/tarikjamoulle/projects/hpo_identifier/output/transcripts/$p.txt .tmp/inputs/$p.txt
done

# 2. Run the pipeline (config: provider=anthropic, model=claude-sonnet-4-6,
#    patient.id_prefix="MGA.", chroma_db -> shared seeded index). See .tmp/config.yaml.
export ANTHROPIC_API_KEY=$(grep -m1 ANTHROPIC_API_KEY /Users/tarikjamoulle/projects/hpo_identifier/setup.sh | cut -d'"' -f2)
PYTHONPATH=$PWD/src /Users/tarikjamoulle/projects/hpo_identifier/.venv/bin/python \
  -m phenoscribe.cli process .tmp/inputs --config .tmp/config.yaml --skip-transcription

# 3. Restrict the ground truth to the pilot patients present (only MGA.467 here),
#    and restrict predictions to MGA.467 so precision is not diluted by patients
#    that have no GT row in this workbook.

# 4. Score.
PYTHONPATH=$PWD/src /Users/tarikjamoulle/projects/hpo_identifier/.venv/bin/python \
  scripts/validate.py --ground-truth .tmp/gt_pilot.xlsx --pipeline-output .tmp/pred_467.xlsx
```

The LLM is stochastic; exact counts can shift by a code or two between runs. The
prefix fix and the GT parsing fix are deterministic and covered by tests
(`tests/test_patient_join.py`, `tests/test_validate.py`).

## Caveat: single-annotator ground truth (Q10)

The HPO code lists are Marc Jamoulle's manual coding. They are one expert's
reading of each interview. With a single annotator there is no inter-annotator
agreement (IAA) figure, so we cannot separate "the tool is wrong" from "two
coders would disagree here too." Several of MGA.467's 16 extras are defensible
codes for the same transcript (e.g. exertional dyspnea, palpitations), which is
exactly the kind of boundary a second coder would settle.

Recommendation: have a second clinician independently code a sample of the
cohort (start with the pilot patients), compute Cohen's kappa or pairwise term
F1 between the two coders, and treat that agreement number as the ceiling for
what the tool can be expected to reach. Until then, read this F1 as
"agreement with one annotator on one patient," not a gold-standard accuracy.

## Next

- Get audio/transcripts for more of the 35 GT patients in this workbook to lift
  n above 1; the join and scoring now work for all of them.
- Normalise the GT delimiter styles at the source, or keep the raw-code scan as
  the scoring path.
- Add the second-coder IAA pass before quoting any cohort-level F1.
