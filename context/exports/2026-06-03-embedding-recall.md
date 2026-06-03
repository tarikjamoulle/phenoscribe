# HPO retrieval recall: default vs SapBERT embeddings

Date: 2026-06-03
Robinson issue #8, Robinson Test Q4.

## The question

The shared ChromaDB index is seeded with ChromaDB's default ONNX function,
which is all-MiniLM-L6-v2: a general-domain sentence model in 384 dims. The
top-k shortlist it returns is the ceiling on the whole pipeline. The LLM judge
in stage 2 can only pick a code that retrieval already surfaced. If the gold
code is not in the top-k, no downstream prompt recovers it.

Recall@k for that shortlist had never been measured. This export measures it.

## What was compared

- **default** — all-MiniLM-L6-v2 (384d), the shared index, read-only.
- **sapbert** — cambridgeltl/SapBERT-from-PubMedBERT-fulltext (768d), built
  into a scratch index under `.tmp/chroma_sapbert`. SapBERT (Liu et al. 2021,
  NAACL) is self-aligned on UMLS synonym pairs, so surface forms of the same
  biomedical concept land close together. Embedding = [CLS] of the last layer,
  per the model card. Cosine space, same as the shared index.

Both indexes were built from the same HPO release (hp/releases/2026-02-16) and
contain the same 19,389 terms.

## Method

`scripts/eval_recall.py`, no LLM.

- Ground truth: `data/ground_truth/hop_list_terms.csv`, the per-term manual
  codes (Marc Jamoulle's work). Deduped to unique (label, HP code) pairs: 434
  pairs over 304 unique codes.
- 25 of those codes are obsolete or renamed in this HPO release and are absent
  from both indexes, so neither model can retrieve them. They are excluded from
  scoring rather than counted as misses. That leaves 407 pairs over 279 codes,
  the set present in both indexes.
- For each pair, query the index with the clinical label, take the top-10 ids,
  and check whether the gold code is in the top-5 / top-10.

## Results

| index                                  |   n | recall@5 | recall@10 |
|----------------------------------------|----:|---------:|----------:|
| default (all-MiniLM-L6-v2, 384d)       | 407 |    0.359 |     0.398 |
| sapbert (PubMedBERT-fulltext, 768d)    | 407 |    0.413 |     0.450 |

SapBERT moves recall@5 from 0.359 to 0.413 (+5.4 points, +15% relative) and
recall@10 from 0.398 to 0.450 (+5.2 points, +13% relative), on the same labels
and the same gold set.

## Reading the absolute numbers

Recall@5 around 0.4 looks low, and it is worth being honest about why. The
manual labels are short clinical concepts written to be read alongside the
patient verbatim and the interview context. Many do not match an HPO term by
surface text alone: "Discomfort", "Loss of the word", "Strange dreams",
"Brain fog". Pure vector retrieval on the bare label has no access to the
verbatim or the context that the stage-2 judge sees. So these numbers are a
floor on retrieval, not an estimate of end-to-end pipeline accuracy.

The comparison is still the right one for issue #8: holding everything else
fixed, a biomedical encoder pulls more gold codes into the shortlist than the
general-domain default. That raises the ceiling the rest of the pipeline runs
under.

## Recommendation

Ship SapBERT as a selectable option (`hpo_index.embedding_model: sapbert`) and
move the shared index to it after a re-seed. The retrieval ceiling goes up at
both k with no change to the judge.

Two caveats to weigh before making it the default:

- **French input.** The interviews are French; SapBERT-from-PubMedBERT is
  English. The eval here used English labels, so it does not test the French
  path. The cross-lingual `SapBERT-UMLS-2020AB-all-lang-from-XLMR-large` is the
  natural next candidate and should be measured on French queries before we
  commit. The selector is built to take more model keys.
- **Cost.** SapBERT is 768d and runs through transformers; seeding 19k terms
  took ~225s on CPU here versus the default's bundled ONNX model. Query latency
  is one forward pass per symptom. Acceptable for this workload, worth noting.

## Reproduce

    PYTHONPATH=src python scripts/build_sapbert_index.py   # writes .tmp/chroma_sapbert
    PYTHONPATH=src python scripts/eval_recall.py            # prints the table above

The build writes only to the gitignored `.tmp/` scratch dir. The shared index
is never modified.
