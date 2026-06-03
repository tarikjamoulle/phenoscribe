---
problem: "Harden Phenoscribe against the mock Peter Robinson peer review before it goes near a clinical cohort"
date: 2026-06-03
shipped: 2026-06-03
status: shipped
review: "../exports/2026-06-03-mock-robinson-review.md"
related:
  - ../exports/2026-06-03-validation-baseline.md
  - ../exports/2026-06-03-embedding-recall.md
  - ../exports/2026-06-03-gold-standard-benchmark.md
  - ../architecture-notes.md
merged_to: main
merge_head: 7af4227
---

# Shipped: response to the mock Robinson review

All ten issues from `context/exports/2026-06-03-mock-robinson-review.md` were
implemented across ten PRs (#6–#15), each built in its own worktree, reviewed by
a Robinson-persona agent, and merged into `main` on 2026-06-03. Final state:
144 tests passing, 2 skipped (live-API smoke tests), ChromaDB re-seeded to 19,389
terms on `hp/releases/2026-02-16`.

The work was done as a pre-mortem. The review is simulated — Robinson never saw
the repo. Where a fix is real and measured, that is stated. Where the number is a
small-n pilot or the gap is still open, that is stated too. No "more codes =
better" claims.

## What landed, by issue

| # | Issue | PR | Branch | Result |
|---|-------|----|--------|--------|
| 1 | Ontology not pinned; the two shipped copies disagree; `replaced_by` not followed | [#7](https://github.com/tarikjamoulle/phenoscribe/pull/7) | `01-pin-ontology` | Pinned in `config.yaml`; startup guard fails on mismatch; `replaced_by`/`alt_id` chains followed; release stamped in every workbook |
| 2 | PII step claims OpenMed (97.97% F1) but loads a general 4-label NER | [#8](https://github.com/tarikjamoulle/phenoscribe/pull/8) | `02-honest-pii` | False claim removed from docs; shipped a real French PII NER + offline fallback + drift test |
| 3 | Ignores the polyhierarchy / true-path rule | [#6](https://github.com/tarikjamoulle/phenoscribe/pull/6) | `03-ancestor-propagation` | Optional ancestor-closure sheet via `hpotk`; fixed-point test |
| 4 | Hop-count similarity, walked undirected | [#10](https://github.com/tarikjamoulle/phenoscribe/pull/10) | `04-resnik-ic-similarity` | Resnik/Lin IC from `phenotype.hpoa`; directional precision/recall split |
| 5 | No F1 after a month (the `038` vs `MGA.038` join) | [#11](https://github.com/tarikjamoulle/phenoscribe/pull/11) | `05-ground-truth-f1` | Join fixed; first real term-level F1 reported |
| 6 | No negation, no frequency/onset/severity, no subontology awareness | [#9](https://github.com/tarikjamoulle/phenoscribe/pull/9) | `06-negation-modifiers` | `negated` + modifier fields; absent findings kept and marked, not coded as present |
| 7 | Only benchmarked against OntoGPT | [#15](https://github.com/tarikjamoulle/phenoscribe/pull/15) | `07-gold-benchmarks` | GSC+ (228 docs) + FastHPOCR head-to-head |
| 8 | Default general-domain embeddings; recall@k never measured | [#13](https://github.com/tarikjamoulle/phenoscribe/pull/13) | `08-biomed-embeddings-recall` | SapBERT option + recall@5/@10 measured |
| 9 | Synonym scope discarded | [#14](https://github.com/tarikjamoulle/phenoscribe/pull/14) | `09-synonym-scope` | Parse + filter to EXACT/NARROW |
| 10 | Silent judge fallback; unpinned deps; no LICENSE/CITATION; format sprawl | [#12](https://github.com/tarikjamoulle/phenoscribe/pull/12) | `10-judge-confidence-hygiene` | `needs_review` + confidence surfaced; deps pinned; LICENSE + CITATION.cff added |

## Detail

### 1 — Ontology pinned end-to-end (#7)
`hpo.release` pinned in `config.yaml` (`hp/releases/2026-02-16`). `check_obo_version`
runs at the top of every pipeline pass and raises `HpoVersionMismatch` if the
on-disk obo header, the config, and the index disagree. `parse_obo` now reads
`replaced_by:`, `consider:`, and `alt_id:`; `build_obsolete_map` + `resolve_obsolete`
map a retired id to its active replacement so an obsolete ground-truth code is
scored against its replacement instead of silently dropped. Every output workbook
carries a Provenance sheet with the release. The ChromaDB collection stamps
`hpo_release` into its metadata.
Files: `hpo_index.py`, `config.py`, `pipeline.py`, `output.py`, `validate.py`,
`scripts/seed_hpo.py`, `Dockerfile`, `tests/test_hpo_version.py`.
Caveat: no CI workflow exists, so "pinned in CI" (Q1) is enforced at runtime, not
on push.

### 2 — Honest PII (#8)
The "OpenMed, French medical NER, 97.97% F1" claim was false; the code loaded
`Jean-Baptiste/camembert-ner`. The claim is removed from `README.md` and
`architecture-notes.md`, which now explain that the 97.97% figure had no source.
Shipped a real French PII NER as the default (`Anonym-IA/V2-camembert-ner-pii-french`,
MIT) with a documented offline fallback, configurable via `pii:` in `config.yaml`,
plus a test that fails if the model's label set drifts.
Files: `pii.py`, `config.py`, `pipeline.py`, `README.md`, `architecture-notes.md`,
`pyproject.toml`, `tests/test_pii.py`.

### 3 — True-path / ancestor propagation (#6)
New `ontology.py` builds the `is_a` graph with `hpotk` and computes the ancestor
closure of the predicted terms. With `output.propagate_ancestors: true`, the
workbook gets an "Ancestor Closure" sheet (leaf terms plus all `is_a` ancestors,
with provenance). The human-facing leaf list is unchanged by default. Tested to a
fixed point (Episodic ataxia → its ancestors up to the root).
Files: `ontology.py`, `output.py`, `pipeline.py`, `cli.py`, `config.py`,
`tests/test_ontology.py`.

### 4 — Resnik IC similarity, directional errors (#10)
New `semantic_similarity.py` computes information content from disease annotations
(`phenotype.hpoa`) and scores term similarity by the IC of the most informative
common ancestor (Resnik 1995, Lin 1998). A near-root prediction like
`HP:0000118` now scores ~0 against a specific truth instead of 0.75. Errors are
classified by direction: predicting an ancestor of truth (non-specific, a recall
problem) is separated from predicting a descendant (over-specific, a precision
problem). The validation report includes the IC distribution of predicted terms.
Files: `semantic_similarity.py`, `validate.py`, `tests/test_validate.py`.

### 5 — First real F1 (#11)
The patient-ID join (`467` vs `MGA.467`) is fixed with a config-driven prefix
(`patient.id_prefix`). The scorer also falls back to a raw `HP:#######` scan so
the cohort's mixed delimiter styles still parse. First measured term-level F1:

| Metric | Strict (exact ID) | Partial (≤2 hops) |
|---|---|---|
| Precision | 30.4% | 47.8% |
| Recall | 46.7% | 46.7% |
| F1 | **36.8%** | 47.2% |

Caveat: **n = 1 patient** (MGA.467) — the only cached transcript with a matching
ground-truth row. This is a pilot, not a cohort number.
Files: `validate.py`, `cli.py`, `pipeline.py`, `config.py`,
`tests/test_patient_join.py`. See `../exports/2026-06-03-validation-baseline.md`.

### 6 — Negation, frequency, onset, severity (#9)
Extraction schema gains `negated`, `frequency`, `onset`, `severity`. Negated
findings are kept and marked Absent rather than coded as present
("je n'ai pas de fièvre" → fever marked absent). New `modifiers.py` maps
frequency/onset/severity text to the correct HPO subontology leaves. The detailed
output gains Present/Absent and modifier columns; the semicolon format splits
present vs excluded.
Files: `extract_symptoms.py`, `match_hpo.py`, `modifiers.py`, `output.py`,
`aggregate.py`, `tests/test_negation.py`, `tests/test_modifiers.py`,
`tests/test_extract_symptoms.py`.

### 7 — Gold-standard benchmark (#15)
Ran Phenoscribe and FastHPOCR head-to-head on all 228 GSC+ documents
(exact document-level HP-ID match, the FastHPOCR paper's primary metric):

| System | Precision | Recall | F1 |
|---|---|---|---|
| FastHPOCR (this run) | 0.955 | 0.664 | **0.783** |
| Phenoscribe retrieval top-1 (k=5) | 0.433 | 0.467 | 0.449 |
| Phenoscribe full pipeline (sample n=6) | 0.588 | 0.185 | 0.282 |

The dictionary wins on this English corpus, reported plainly. The bottleneck is
extraction, not retrieval. BioCreative VIII Task 3 is a different task (span
normalisation) and is documented but not run.
Files: `scripts/benchmark_gsc.py`, `tests/test_benchmark_gsc.py`,
`../exports/2026-06-03-gold-standard-benchmark.md`.

### 8 — Biomedical embeddings + recall@k (#13)
Added a SapBERT embedding option and measured the retrieval ceiling (no LLM),
407 label/code pairs on the same gold set:

| Index | recall@5 | recall@10 |
|---|---|---|
| default (all-MiniLM-L6-v2) | 0.359 | 0.398 |
| sapbert (PubMedBERT-fulltext) | 0.413 | 0.450 |

SapBERT lifts recall@5 by +15% relative. The embedding model is configurable
(`hpo_index.embedding_model`) and stamped into the collection metadata.
Caveat: this eval used English labels; the French retrieval path is unmeasured,
and the cross-lingual SapBERT variant is the next candidate.
Files: `embeddings.py`, `hpo_index.py`, `match_hpo.py`, `config.py`,
`scripts/build_sapbert_index.py`, `scripts/eval_recall.py`,
`tests/test_embeddings.py`, `../exports/2026-06-03-embedding-recall.md`.

### 9 — Synonym scope (#14)
The synonym parser now captures the OBO scope and `build_enriched_text` embeds
only EXACT and NARROW synonyms by default, dropping BROAD and RELATED so chatty
synonyms stop diluting a term's vector. Configurable to restore the old behaviour.
Files: `hpo_index.py`, `tests/test_hpo_index.py`.
Caveat: measured gains were small on this gold set.

### 10 — Judge hygiene + repo hygiene (#12)
The silent "top candidate" fallback is gone. Every degraded path (judge failure,
weak shortlist) sets `needs_review` and a confidence score, surfaced in the
output (an amber row + Confidence column in detailed, a `{REVIEW}` marker inline
in semicolon). Dependencies pinned with upper bounds. Added `LICENSE` (Apache-2.0)
and `CITATION.cff`. A healthy-control fixture + test scaffold was added for the
false-positive question.
Files: `match_hpo.py`, `output.py`, `pyproject.toml`, `LICENSE`, `CITATION.cff`,
`tests/test_match_hpo.py`, `tests/test_output.py`, `tests/test_healthy_control.py`.

## The Robinson Test — scorecard

| Q | Question | Status |
|---|----------|--------|
| 1 | Which HPO release? Printed, pinned, `replaced_by` followed | **Yes** at runtime (#7). CI enforcement still missing. |
| 2 | Term-level F1 on a published gold standard | **Yes** — GSC+ retrieval F1 0.449, full pipeline 0.282 (n=6); private pilot 36.8% (n=1) (#15, #11) |
| 3 | Performance vs Fenominal / FastHPOCR / Doc2HPO | **Yes** — FastHPOCR 0.783 vs Phenoscribe 0.449 on GSC+; others cited (#15) |
| 4 | recall@5 / recall@10 before the judge | **Yes** — 0.359/0.398 default, 0.413/0.450 SapBERT (#13) |
| 5 | Negation — test for "je n'ai pas de fièvre" | **Yes** — `tests/test_negation.py` (#9) |
| 6 | Frequency / onset / severity schema | **Yes** — schema + HPO subontology mapping (#9) |
| 7 | Ancestor closure for one patient | **Yes** — closure sheet (#6) |
| 8 | False-positive rate on a healthy control | **Partial** — fixture + test scaffold shipped (#12); the actual FP number needs a live run with an API key |
| 9 | IC distribution of predicted terms | **Yes** — in the validation report (#10) |
| 10 | Inter-annotator agreement on the ground truth | **Open** — needs a second coder; not addressable in code |

## Still open (honest)

- **Scale.** Only 4 transcripts are cached locally, so the private F1 is n=1 and
  the full-pipeline GSC+ number is n=6. The single biggest unlock is more
  transcripts.
- **Second annotator (Q10).** One coder is one opinion. IAA (Cohen's kappa or
  pairwise term F1) is the ceiling the tool should be measured against, and it
  does not exist yet.
- **Healthy-control FP rate (Q8).** Scaffold only; the number is pending a live run.
- **French embedding path (#8/#13).** SapBERT was measured on English labels;
  the cross-lingual variant on French queries is unmeasured.
- **No CI.** "Pinned in CI" (Q1) is unenforced until a GitHub Actions workflow runs
  the version guard + tests.
- **The dictionary still beats us** on English GSC+ (0.783 vs 0.449). The gap is
  in extraction, not retrieval.

## Integration notes

Merged locally with real merge commits in order #7, #12, #6, #10, #11, #9, #14,
#13, #8, #15, running the full suite after each. Conflicts (all additive) resolved
in `output.py`, `validate.py`, `match_hpo.py`, `hpo_index.py`, `config.py`,
`config.yaml`, `pipeline.py`, `.gitignore`. Two integration bugs were caught only
by testing after each merge: a dropped `from pathlib import Path` import, and test
stubs for `search_hpo` that predated the `embedding_model` kwarg. Also fixed the
CITATION.cff repo slug and deduped `.gitignore`. Rollback tag
`pre-integration-2026-06-03`.
