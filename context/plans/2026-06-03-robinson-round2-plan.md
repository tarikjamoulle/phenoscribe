---
type: plan
date: 2026-06-03
problem: "After-action on the round-1 Robinson response: what held up, what was theatre, and the ordered work to make the fixes load-bearing and the numbers honest."
reviews:
  - ../exports/2026-06-03-robinson-review-round2.md
  - ../shipped/2026-06-03-robinson-review-response.md
status: proposed
---

# After-action: the round-1 response, and the plan to round 2

The round-1 response shipped ten fixes overnight. A second-pass audit (four
agents, tracing the actual `cli.py process` control flow, the evidence files,
and the git/lockfile state) splits them into what is real and what is paper.

## What we did right

These are live in the default `phenoscribe process` run and answer the round-1
objection for real:

- **Ontology pinned end-to-end (#1).** `hp/releases/2026-02-16` in config,
  Dockerfile, the on-disk `hp.obo` header, and the Chroma metadata. A real
  `check_obo_version` guard fails the seed on mismatch. `replaced_by`/`alt_id`
  resolution runs at match time. This is the cleanest pass and it directly
  killed the "two copies disagree" complaint.
- **Honest PII (#2).** The fabricated "OpenMed 97.97%" claim is gone from docs.
  A real French PII NER is the default with a working offline fallback and a
  label-drift test. The remaining 0.9327 figure is at least an attributed
  model-card number, not an invention.
- **Judge hygiene (#10).** The silent "top candidate" fallback now sets
  `needs_review` + a confidence score on every degraded branch, surfaced as an
  amber row and a Confidence column. LICENSE and CITATION.cff exist; the CFF
  slug bug was fixed.
- **Synonym scope (#9).** EXACT/NARROW-only enrichment is the seed-time default
  and the shipped index was built with it.
- **The honesty of the evidence writing.** Small-N, the dictionary loss, and the
  English/French gap are all named in the exports. The caveats are good. The
  problem is the headlines, not the footnotes.

## What we missed

Audit verdicts, with the evidence:

| # | Claimed | Reality | Why it's not done |
|---|---------|---------|-------------------|
| 4 | Resnik IC similarity | **Inert** | `validate()` runs only from `scripts/` + tests; no `validate` CLI command. Match-time ranking still uses cosine + judge. IC scores nothing the tool emits. |
| 8 | SapBERT embeddings | **Inert** | On-disk Chroma metadata is `embedding_model='default'` (all-MiniLM). SapBERT index was never seeded. Setting the config flag without re-seeding queries a MiniLM index with SapBERT vectors. |
| 3 | True-path / ancestor closure | **Off by default** | `output.propagate_ancestors: false`. Default run emits the flat leaf list. |
| 6 | Negation stops coding | **Display-only** | `present_codes()`/`absent_codes()` defined + tested but called by nothing in `src/`. Negated symptoms still get an HP code; negation only flips a column to "Absent". |
| — | Deps pinned (reproducible) | **Lockfile is fiction** | `uv.lock` pins `torch==2.12.0`, `transformers==5.9.0` — versions that do not exist. `uv sync` fails on any other machine. |
| — | CI | **None** | No `.github/`. Guard + 146 tests run only on the author's laptop. 10 PRs merged to `main` in 15 minutes, no human review. |
| 2 | F1 on a gold standard | **n=1 / English** | Private F1 36.8% is one patient (7/15, 7/23). GSC+ is English, retrieval-only, oracle labels. The French production path has zero numbers. |
| 8/4 | Healthy-control FP | **Plumbing test** | `test_healthy_control.py` stubs the extractor to return `[]`. The real FP rate is unmeasured. |
| — | Metric bugs | **Two survive** | Undirected hop-count still scores GSC+ lenient + OntoGPT "≤2 hops"; recall@k denominator is per-mention (common synonyms dominate), not per-concept. |
| — | Reproducibility | **Broken** | GSC+ corpus in gitignored `.tmp/`; `phenotype.hpoa` downloaded at runtime, never vendored/checksummed. Headline benchmark can't be rerun from the repo. |
| — | Epistemics | **Self-grading** | AI wrote the review, the fixes, and the approving "Robinson". No external human has read the code. |

The pattern: round 1 answered the *review* (add a module, add a field, add a
flag) rather than the *behaviour* (what `process` does on a real recording).

## The plan — round 2, in dependency order

### Phase 0 — Make it runnable elsewhere (half a day, blocks everything)
1. **Fix the lockfile.** Re-resolve against the real index so `uv sync` succeeds
   on a clean machine. Correct the `pyproject.toml` comment that cites
   torch 2.12 / transformers 5.9. *Done when:* `uv sync && pytest` passes in a
   fresh clone / container.
2. **Add CI.** One GitHub Actions workflow: `uv sync`, `pytest`, and the
   `check_obo_version` guard, on push + PR. This alone would have caught #1.
   *Done when:* the badge is green on a clean runner, not a laptop.
3. **Fill the LICENSE copyright line** (`Copyright 2026 Tarik Jamoulle`).

### Phase 1 — Make the four inert fixes load-bearing (2–3 days)
4. **Negation actually excludes coding.** Wire `present_codes()`/`absent_codes()`
   into the pipeline so a denied symptom is recorded as Absent and *not* assigned
   a present HP code. *Done when:* the "je n'ai pas de fièvre" test asserts no
   present fever code, end to end through `process`, not a stub.
5. **True-path closure on by default.** Flip `propagate_ancestors: true`, or emit
   the closure as the canonical machine-readable output while keeping the leaf
   list for the human sheet. *Done when:* a default run yields the ancestor
   closure for the downstream consumer.
6. **IC ranking or honest removal.** Either route match-time selection through the
   Resnik IC score (add a `validate` subcommand at minimum so the IC distribution
   is producible from the CLI), or delete the claim that the tool uses IC.
   *Decision needed:* rank-time IC vs validation-only — pick one and make the
   docs match.
7. **Seed SapBERT or drop it.** Build and commit/seed the SapBERT index so the
   default (or a documented `--index sapbert`) actually queries SapBERT vectors,
   or remove the option until it's seeded. No config flag that silently mismatches
   the index geometry.

### Phase 2 — Measure the thing we ship (1–2 weeks, needs data + API key)
8. **French recall@k and F1.** Re-run `eval_recall.py` and the validation against
   the French labels / French retrieval path. This is the number that describes
   production; everything English is a proxy.
9. **Healthy-control FP rate, for real.** A live `process` run on the healthy
   fixture with an API key; report the count of phenotypes hallucinated from a
   well patient.
10. **More transcripts.** n=1 is the single biggest blocker. Even 10–20 matched
    transcripts move the private F1 from anecdote to pilot.
11. **Second annotator + κ.** One coder is one opinion. Get a second pass on a
    subset, report Cohen's κ or pairwise term-F1 as the ceiling the tool is
    measured against.

### Phase 3 — Reproducibility + metric correctness (2–3 days)
12. **Vendor or checksum the benchmark inputs.** A download script + checksum for
    the GSC+ corpus and `phenotype.hpoa`, so the headline benchmark reruns from
    the repo.
13. **Kill the surviving undirected hop-count** in the GSC+ lenient and OntoGPT
    scorers; replace with IC-based credit or drop lenient scoring.
14. **Report recall@k both ways** — per-mention and per-concept (macro) — since
    they diverge materially on this label set.

### The one that isn't code
15. **External read.** Send the repo to a human who is not the author —
    realistically someone in the HPO/Monarch orbit — before any external claim of
    "reviewed". Until then, every artifact keeps the "simulated / pre-mortem"
    label. The AI passes are a rehearsal of expected objections; they are not
    validation, and the provenance should never imply they are.

## Sequencing rationale

Phase 0 first because nothing downstream is trustworthy if the repo doesn't
build off the author's laptop — and the fake lockfile means it currently
doesn't. Phase 1 next because making the already-written fixes actually run is
cheap and converts four "paper" answers into real ones. Phase 2 is the long pole
(needs data, a second human, an API budget) and is where the only defensible
accuracy claim will come from. Phase 3 is cleanup that makes the numbers
reproducible and the metrics honest. Phase 15 gates any external-facing claim.
