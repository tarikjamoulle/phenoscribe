---
date: 2026-05-30
type: benchmark
plan: ../plans/stakeholder-feedback-plan-2026-05-30.md
---

# ontoGPT vs Phenoscribe — benchmark

## Verdict

**Sample is small (3 transcripts).** Treat the numbers as a directional signal, not a settled benchmark.

On these 3 pseudonymised long-COVID transcripts, Phenoscribe surfaces an average of 19.3 HPO codes per patient; ontoGPT surfaces 5.0 (~3.9× fewer). When ontoGPT does find something, its codes line up with Phenoscribe most of the time — 73% of ontoGPT's codes match Phenoscribe exactly. ontoGPT contributes essentially nothing Phenoscribe missed (1 of 15 codes are unique to ontoGPT across the cohort).

The recall gap is the dominant signal even at this scale. Phenoscribe finds 41 codes ontoGPT misses entirely — and on the toughest transcript (1 of 3), ontoGPT returns **zero** phenotypes. Phenoscribe's bounded ChromaDB shortlist plus a second-pass LLM judge is what closes that gap — exactly the discipline Peter Robinson recommended.

**Recommendation: keep Phenoscribe; do not adopt ontoGPT as-is.** Setup friction is high (three install workarounds, see below), French input requires a translation pre-pass, and recall is much weaker on this sample. Worth revisiting if ontoGPT ships a working out-of-the-box HPO template with a non-English annotator — and worth re-running with a larger cohort before treating the recommendation as final.

## Setup notes (real install friction)

Three concrete problems hit during install:

1. **Pydantic schema rejects ontoGPT's own output.** `ontogpt extract -t human_phenotype` crashes on every input with `extra_forbidden` for `original_spans`. The `HumanPhenotypeSet` model sets `extra='forbid'` but the SPIRES engine emits the field. Worked around by patching the generated template file (see `scripts/benchmark_ontogpt.py` header).
2. **Default install silently drops HPO grounding.** Without warming the oaklib `sqlite:obo:hp` adapter (lazy download), ontoGPT returns `AUTO:phrase%20text` IDs instead of `HP:codes` and ships them under `named_entities` with no warning.
3. **English-only HPO synonyms.** French input produces `AUTO:` IDs even with HPO cached, because oaklib only matches English labels and synonyms. We solved this by translating French → English with `phenoscribe.llm.llm_call(model="gpt-4o")` before handing the text to ontoGPT.

## Method

For each pseudonymised transcript in `output/pseudo/`:

1. Translate French → English via gpt-4o using a clinical-faithful system prompt that preserves the pseudonymisation tokens.
2. Run `ontogpt extract -t human_phenotype -m gpt-4o -O json` on the English text.
3. Collect the set of grounded `HP:` IDs from `named_entities`.
4. Compare against Phenoscribe's HP IDs for the same patient (from `output/results.xlsx`).
5. Classify each predicted code as **exact overlap**, **close** (≤2 hops in HPO via `hpo-toolkit`), or **unique** to that tool.

Ground-truth F1 was not computed because the patient-ID format mismatch between pipeline output (`038`) and the GP's manual codes (`MGA.038`) still blocks joining. Comparing the two tools head-to-head on the same input is a useful intermediate signal.

## Per-patient

| Patient | Phenoscribe codes | ontoGPT codes | Exact overlap | Phenoscribe close | ontoGPT close | Phenoscribe unique | ontoGPT unique |
|---|---|---|---|---|---|---|---|
| 038 | 10 | 5 | 3 | 1 | 1 | 6 | 1 |
| 451 | 35 | 10 | 8 | 5 | 2 | 22 | 0 |
| 454 | 13 | 0 | 0 | 0 | 0 | 13 | 0 |
| **total** | **58** | **15** | **11** | **6** | **3** | **41** | **1** |

## Sample per-patient detail

### 038

- Exact overlap (3): ['HP:0002315', 'HP:0002354', 'HP:0012378']
- Phenoscribe only, no close ontoGPT match (6): ['HP:0009020', 'HP:0012452', 'HP:0012514', 'HP:0030833', 'HP:6000627', 'HP:6000707']
- ontoGPT only, no close Phenoscribe match (1): ['HP:0002360']

### 451

- Exact overlap (8): ['HP:0000360', 'HP:0002172', 'HP:0002354', 'HP:0004324', 'HP:0012378', 'HP:0033630', 'HP:0100749', 'HP:0100832']
- Phenoscribe only, no close ontoGPT match (22): ['HP:0000010', 'HP:0000019', 'HP:0000739', 'HP:0001962', 'HP:0002321', 'HP:0002360', 'HP:0002591', 'HP:0005059', 'HP:0005957', 'HP:0006688', 'HP:0011534', 'HP:0025297', 'HP:0030219', 'HP:0030391', 'HP:0030757', 'HP:0031987', 'HP:0033360', 'HP:0034997', 'HP:0100639', 'HP:4000033', 'HP:4000064', 'HP:5200243']
- ontoGPT only, no close Phenoscribe match (0): —

### 454

- Exact overlap (0): —
- Phenoscribe only, no close ontoGPT match (13): ['HP:0001257', 'HP:0001324', 'HP:0002018', 'HP:0003323', 'HP:0003394', 'HP:0003473', 'HP:0003546', 'HP:0003552', 'HP:0003750', 'HP:0007021', 'HP:0008969', 'HP:0009020', 'HP:0012378']
- ontoGPT only, no close Phenoscribe match (0): —
