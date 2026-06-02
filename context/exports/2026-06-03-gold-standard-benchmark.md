---
date: 2026-06-03
type: benchmark
corpus: GSC+ (GSC-2024 refresh, Groza et al.)
---

# Phenoscribe on a published gold standard (GSC+), vs FastHPOCR

## What this answers

Robinson issue #7 / Robinson Test Q3: the only prior benchmark was vs OntoGPT. The field reports on GSC+ against FastHPOCR, PhenoTagger, Doc2HPO, ClinPhen, NCBO/OBO Annotator and the Monarch tagger. This puts Phenoscribe on that corpus and runs FastHPOCR head-to-head on the same documents.

## What actually ran here vs what is cited

- **Ran:** Phenoscribe retrieval (ChromaDB) on all 228 GSC+ documents; FastHPOCR on the same 228 documents; Phenoscribe full two-call pipeline on a sample of 6 documents.
- **Cited (not re-run here):** published GSC+ F1 for FastHPOCR, PhenoTagger, Doc2HPO, ClinPhen, NCBO/OBO Annotator, Monarch (Groza 2024, Bioinformatics, Table 1). Fenominal is the Java port of FastHPOCR and is not run separately here.
- **BioCreative VIII Task 3:** now public (github.com/Ian-Campbell-Lab/Clinical-Genetics-Training-Data). It is a different task — span normalisation of dysmorphology physical-exam observations, not free-text concept recognition over abstracts — so it is not directly comparable to the GSC+ document-level metric. Documented, not run.

## Corpus and versions

- GSC+ / GSC-2024: 228 abstracts, 1823 distinct gold HP codes total. Mention-level annotations, scored here at document level (presence/absence of a concept ID), matching the FastHPOCR paper's primary metric.
- HPO release for scoring graph walks (hpo-toolkit): 2026-02-16.
- ChromaDB: shared seeded index, ~19k terms. Retrieval k=5.
- FastHPOCR v0.1.4 indexed from the same hp.obo as ChromaDB.

## Results — exact document-level HP-ID match (primary metric)

Scored on 228 documents (1823 gold codes). TP/FP/FN are micro-summed over documents.

| System | Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|
| FastHPOCR (this run) | 0.955 | 0.664 | 0.783 | 1210 | 57 | 613 |
| Phenoscribe retrieval top-1 (gold mentions, k=5) | 0.433 | 0.467 | 0.449 | 851 | 1116 | 972 |
| Phenoscribe full pipeline (sample n=6, claude-sonnet-4-6) | 0.588 | 0.185 | 0.282 | 10 | 7 | 44 |

## Results — lenient match (predicted within 2 HPO hops of a gold code)

Reported separately. The FastHPOCR paper uses exact IDs; lenient credit is shown so near-misses (parent/child/sibling) are visible, never folded into the exact number.

| System | Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|
| FastHPOCR (this run) | 0.973 | 0.719 | 0.827 | 1233 | 34 | 482 |
| Phenoscribe retrieval top-1 (k=5) | 0.718 | 0.805 | 0.759 | 1412 | 555 | 343 |
| Phenoscribe full pipeline (sample n=6) | 0.765 | 0.265 | 0.394 | 13 | 4 | 36 |

## Retriever ceiling (no LLM)

On 2910 gold mention strings fed straight into ChromaDB:

- top-1 exact-ID accuracy: **44.0%**
- recall@5 (gold ID anywhere in the shortlist): **73.0%**

recall@5 is the ceiling for the LLM judge: it can only return a code retrieval surfaced. The gap between top-1 and recall@5 is the room the judge has to improve on raw nearest-neighbour.

The full-pipeline recall (0.185) sits well below even the retriever's top-1 recall (0.467). The bottleneck is symptom extraction (LLM call 1): on dense scientific abstracts it returns only a handful of findings per document (e.g. 3 symptoms on a doc with many gold codes), because its prompt is tuned to pull patient complaints out of a French interview, not to exhaustively tag an English abstract. The retriever and judge are not the limiting stage here.

## Published GSC+ numbers (cited, document-level F1)

From Groza et al. 2024 (Bioinformatics, FastHPOCR), Table 1, GSC+ aligned to the 2019-11 HPO release. Exact concept-ID match.

| System | Precision | Recall | F1 |
|---|---|---|---|
| FastHPOCR | 0.82 | 0.71 | 0.76 |
| PhenoTagger | 0.77 | 0.67 | 0.72 |
| OBO Annotator | 0.80 | 0.56 | 0.66 |
| Monarch | 0.75 | 0.60 | 0.67 |
| ClinPhen | 0.63 | 0.65 | 0.64 |
| Doc2HPO | 0.80 | 0.49 | 0.61 |
| NCBO Annotator | 0.66 | 0.49 | 0.56 |

These rows are not re-runs; they are the published figures, included so the Phenoscribe and FastHPOCR rows above sit in the field's context. Our FastHPOCR row is a fresh run on a newer HPO release and the GSC-2024 corpus refresh, so it will not match the 0.76 cell exactly.

## Honest read

On this corpus the 30MB offline FastHPOCR dictionary scores F1=0.783 (P=0.955, R=0.664) end-to-end. Phenoscribe's retriever, handed the gold mention strings, gets top-1 exact accuracy 44.0% and recall@5 73.0%. The full sampled pipeline scores F1=0.282 (P=0.588, R=0.185) on n=6 documents — too small to rank against the published matrix, a directional signal only. Phenoscribe was built for spontaneous French speech, where a patient says "je suis vidé" and means fatigue; GSC+ is English scientific abstracts written in near-ontology language, which is exactly where a morphological dictionary like FastHPOCR is strongest. Read these numbers as Phenoscribe playing an away game on the dictionary's home ground, not as a verdict on the clinical-interview task it targets.

## Method

1. Load GSC+ (GSC-2024). For each abstract collect the set of gold HP IDs from its annotation file.
2. FastHPOCR: index hp.obo once, annotate each abstract, collect HP IDs. End-to-end.
3. Phenoscribe retrieval: feed each gold mention string into ChromaDB (top-k), take top-1 as the predicted code; also record whether the gold code was anywhere in the shortlist (recall@k).
4. Phenoscribe full pipeline (sampled): run extract_symptoms (LLM call 1) then match_hpo (ChromaDB + LLM judge, call 2) on the raw abstract.
5. Score document-level micro P/R/F1, exact and lenient.

## Reproduce

```
pip install FastHPOCR pronto
# download GSC-2024:
#   github.com/tudorgroza/code-for-papers/tree/main/gsc-2024
PYTHONPATH=$PWD/src python scripts/benchmark_gsc.py \
    --gsc-dir .tmp/GSC_2024 \
    --fasthpocr-index .tmp/fasthpocr_index/hp.index \
    --chroma-path <shared chroma_db> --full-sample 6 \
    --provider anthropic --model claude-sonnet-4-6
```

## Robinson Test

Moves Q3 ("benchmarked against the field's references on a published gold standard?"): Phenoscribe is now scored on GSC+ with FastHPOCR run head-to-head on the same documents, and placed against the published GSC+ matrix.