---
date: 2026-06-02
status: draft
owner: tarik
---

# Dedup pass + HPO regression cases

## Why

Two findings from the 2026-06-02 email-coding comparison need follow-up:

1. **Phenoscribe produces multiple rows per (patient, HPO code)** when the source restates the same symptom — see `output/test_mail_results.xlsx` (Impaired executive functioning appears three times) and `output/results.xlsx` (Lower limb pain twice for 038). The original design assumed batches of recordings per patient, where preserving each verbatim makes sense. In practice **each transcript is one document for one patient**, so the natural unit is one row per (patient, code). The duplication is noise — and it pushes any per-row downstream count (prevalence, scoring, Excel readability) off by however many times a concept was restated.

2. **HP:0033750 misfire on the email comparison.** The pipeline assigned *Reduced functional residual capacity* (a lung-volume term) to a verbatim about cognitive endurance. The candidate shortlist surfaced respiratory terms; the LLM judge picked the least-bad. Without a regression case, the next embedding tweak or prompt change could silently make this worse without anyone noticing.

## Decisions to make

### D1. Dedup default

**Proposed:** dedup-always. Drop the "single-document outputs only" qualifier — every Phenoscribe run today processes one document per patient, so the dedup pass is the same code path either way.

**Open question:** what happens to the multiple verbatims when (patient, code) collapses to one row? Three options:

| Option | Behaviour | Pros | Cons |
|---|---|---|---|
| A. Keep longest verbatim | Pick the verbatim with most characters | Simplest; preserves richest example | Drops other evidence; "longest" ≠ "most representative" |
| B. Concatenate verbatims | Join all verbatims with `; ` in one cell | Preserves all evidence | Cells get long; harder to read at a glance |
| C. Keep first verbatim | Pick the first occurrence by transcript order | Simplest; preserves temporal anchor | Drops follow-up evidence |

**Recommendation:** **B (concatenate)** for the `detailed` output format, **A (longest)** for the `semicolon` and `purl` formats. The `detailed` format already exists for evidence-preservation; the others are summaries. Match each format's existing intent.

### D2. Regression-case storage and execution

**Proposed:** a JSON fixture file (`tests/fixtures/hpo_regression_cases.json`) listing known-tricky verbatims, with the assertions to run. A pytest module loads the fixture and exercises `match_hpo()` per case.

Example fixture entry:

```json
{
  "id": "longcovid-functional-endurance-not-lung-volume",
  "verbatim": "incapacité à maintenir un niveau de fonctionnement constant sur la durée",
  "clinical_term": "Sustained functional endurance",
  "forbidden_codes": ["HP:0033750"],
  "expected_one_of": ["HP:0030973", "HP:0012378"],
  "source": "context/exports/2026-06-02-email-coding-audit.md",
  "notes": "HP:0033750 is a lung-volume term; words 'functional' and 'capacity' embed close to respiratory terminology"
}
```

**Open questions:**

- **Cost.** Each case fires the LLM judge (one paid API call). With ~10 cases × two providers, every test run costs cents. Should we (a) cache LLM responses to disk and replay in CI, or (b) mark the test as `@pytest.mark.regression` and only run on-demand?
  - **Recommendation:** (b) — `@pytest.mark.regression`, opt-in. Add a Makefile/CLI alias `make regression` that runs them, then call out a periodic manual run (e.g. before any release to Marc).
- **Provider drift.** A case that passes on `claude-sonnet-4-6` might fail on `gpt-4o-mini`. Should fixtures encode the model used, or expect any-provider correctness?
  - **Recommendation:** Each case records the provider/model it was first observed under. The test runs against the recorded config. If a case fails on the recorded config, that's a regression. If we want cross-provider coverage, that's a separate matrix.

### D3. Seed cases

At minimum, seed with:
- The HP:0033750 case from the email comparison.
- Any new misfires the 038 comparison agent surfaces (output expected within this session; doc at `context/exports/2026-06-02-038-coding-comparison.md`).
- A short list of clean-pass cases: pick 3–5 Phenoscribe rows that *are* correct on the email or 038 (e.g. HP:0033051 for executive function on the cognitive verbatim) as canaries — they catch regressions in the *good* path, not just the bad path.

## Implementation tasks

### Task 1 — Dedup pass in `aggregate_results` / output writer

- Add a `dedup_by_code` helper in `src/phenoscribe/aggregate.py` (or `src/phenoscribe/output.py`) that takes a list of match dicts and returns one dict per (patient_id, hpo_code), with verbatims combined per the rule chosen in D1.
- Wire it into the output writer path (`write_excel`) before rows are emitted.
- Honour the output format: `detailed` → concatenate verbatims; `semicolon`/`purl` → keep longest.
- No CLI flag — this becomes the default.
- Unit test: feed a synthetic match list with duplicates, assert one row per code with the expected verbatim handling.

### Task 2 — Regression fixture + test

- Create `tests/fixtures/hpo_regression_cases.json` with the seed cases (see D3).
- Create `tests/test_hpo_regression.py`:
  - Loads the fixture.
  - For each case, calls `match_hpo()` with the single symptom and the case's recorded provider/model.
  - Asserts `forbidden_codes` are absent and at least one of `expected_one_of` is present.
  - Marked `@pytest.mark.regression` so default `pytest` runs skip it.
- Document in the project README how to run regression (one short paragraph), and what the cost is.

### Task 3 — Wire up the seed cases

- After the 038 comparison doc is written, scan it for any misfires and add them as fixture entries.
- Add the 3–5 canary cases for the good path.

### Task 4 — Update architecture-notes

- Note the dedup behaviour in `context/architecture-notes.md` so future readers know the per-row count semantics.
- Note the regression-case mechanism + how to add new cases.

## Acceptance

- `phenoscribe process` on `data/emails_inputs/` produces a Phenoscribe Excel with no duplicate (patient, code) rows.
- `pytest -m regression` runs the seed cases and passes against the current pipeline.
- A new entry in the regression fixture is a 5-line edit, not a code change.

## Out of scope

- Cross-provider regression matrix.
- Caching LLM responses for replay-in-CI (revisit if cost or run time become annoying).
- Reworking the embedding model for HPO terms (the HP:0033750 misfire is downstream evidence that embeddings could be tuned, but tuning embeddings is a much bigger project — regression-case tracking is the cheap immediate move).
