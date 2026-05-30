---
problem: "Tighten HPO accuracy and add cohort outputs based on stakeholder feedback (Peter Robinson email + Jamoulle papers + Plovdiv poster)"
date: 2026-05-30
adr: "../exports/hpo-identifier-adr-2026-03-01.md"
---

# Implementation Plan: Stakeholder Feedback Round 1

## Summary

Four improvements driven by `docs/` materials supplied by stakeholders:

1. **HPO ID re-resolution** in `match_hpo.py` — eliminate the LLM-name-vs-LLM-ID mismatch class of error that Peter Robinson flagged ("LLMs are reasonably good at retrieving ontology term labels but are much worse at retrieving the term identifiers"). Today `_parse_judge_response` accepts the LLM's `(hpo_id, hpo_term)` pair as long as the ID is in the candidate set, but trusts the LLM's term string — so a swapped label still ships. Fix: after the ID passes the candidate check, overwrite the term with the canonical name from the candidates list.

2. **Adopt `hpo-toolkit`** for hierarchy walks in `validate.py` — replace the hand-rolled BFS over a `dict[str, list[str]]` parent map with `hpotk`'s tested graph and distance functions. Also lets the scorer credit "shared common ancestor" cases the current code misses (siblings under a near common parent currently score 0).

3. **`phenoscribe aggregate` command** — produce cohort-level symptom prevalence outputs that match what Marc puts in papers (Plovdiv poster Figure, children's paper Figure 1). Reads a results workbook, counts patients per HPO term, emits a CSV + a matplotlib bar chart.

4. **One-off ontoGPT benchmark** — run ontoGPT on the same pseudonymized transcripts, score against the 1003-row ground truth using our existing scorer, log the precision/recall/F1 delta vs. Phenoscribe. Output a one-page comparison. Informational only; not a permanent integration.

All four are independent enough to ship one at a time. Order below is by value-per-effort: 1 → 2 → 3 → 4.

---

## Tasks

### Task 1: HPO ID re-resolution in judge step

> **Shipped 2026-05-30** in PR #1 (merged as `4c1e586`). Verified: 6/6 unit tests pass; audit of 168 prior-run rows shows 0 (term, ID) mismatches; end-to-end smoke with mocked Peter-style drift confirms canonical name shipped + `label_corrected` log fires; live gpt-4o run over 3 transcripts (50 matches, 53 calls) produced 0 mismatches and 0 log hits — defensive guarantee with no happy-path behavior change.

**Files:** `src/phenoscribe/match_hpo.py`, `tests/test_match_hpo.py` (new)

The current `_parse_judge_response` (match_hpo.py:103-136) validates that `data["hpo_id"]` is in the candidate set but returns `data` directly — meaning the LLM's `hpo_term` string is shipped untouched. If the LLM writes `{"hpo_id": "HP:0002027", "hpo_term": "Fatigue"}` (term mislabeled but ID valid), the bad pair ends up in the Excel.

Changes:
- After the `data["hpo_id"] in candidate_ids` check passes, look up the matching candidate and overwrite `data["hpo_term"]` with `candidate["name"]`. The LLM's label is discarded.
- Apply the same canonicalization to the regex fallback branch (the `re.search(r"HP:\d{7}", text)` path already pulls `c["name"]`, so it's already correct — confirm with a test).
- Add a structured log line at INFO level when the LLM-provided label differed from the canonical one, so we can quantify how often Peter's concern actually fires in practice: `logger.info("label_corrected: llm=%r canonical=%r id=%s", ...)`.
- Add unit tests covering:
  - LLM returns matching `(id, term)` → unchanged.
  - LLM returns valid `id` with wrong `term` → term replaced, log fires.
  - LLM returns `id` not in candidates → falls through to regex / top-candidate fallback.
  - Regex fallback finds an ID in candidates → returns canonical name from candidate list.

**Verify:**
```bash
pytest tests/test_match_hpo.py -v
```

**Expect:** All four test cases pass. A grep of recent pipeline logs for `label_corrected:` reports how often the LLM mis-labels in real runs.

**Ship:**
- Re-run the pipeline on 3 existing recordings end-to-end with `--skip-transcription` (transcripts already cached).
- Spot-check `output/results.xlsx` for any rows where the term name changed vs. the prior commit's output.
- Commit: `Re-resolve HPO term name from candidate list (Peter Robinson feedback)`.

**Depends on:** none. Half a day.

---

### Task 2: Migrate hierarchy scoring to `hpo-toolkit`

> **Shipped 2026-05-30** in PR #2. Verified: 14/14 unit tests pass (6 from Task 1 + 8 new). Synthetic real-HPO comparison shows the sibling case (Chest pain ↔ Abdominal pain via shared parent Pain) now scores 0.5 where the old walker scored 0; exact / parent / unrelated cases unchanged. The new scorer also added detailed-format parsing as a side effect; the prior loader couldn't read the format the pipeline has produced since post-shipping (the old scorer saw 0 predicted codes on `output/results.xlsx` despite 168 rows). Real-data F1 comparison was blocked separately by a patient-ID format mismatch between pipeline output (`038`) and ground truth (`MGA.038`), flagged for follow-up.

**Files:** `src/phenoscribe/validate.py`, `pyproject.toml`, `tests/test_validate.py` (new)

The hand-rolled `get_ancestors` (validate.py:54-73) and `score_match` (validate.py:76-104) only walk strictly up or strictly down from each term — they miss the case where predicted and ground-truth are siblings sharing a near common ancestor. `hpo-toolkit` ships a `distance(a, b)` over the directed graph that handles this correctly.

Changes:
- Add `hpo-toolkit>=0.8.0` to `pyproject.toml` `dependencies`.
- Replace `parse_obo` + `build_hierarchy` usage in `validate.py` with:
  ```python
  import hpotk
  store = hpotk.configure_ontology_store()
  hpo = store.load_minimal_hpo()  # or load_hpo() if we want definitions
  ```
  Loaded once at the top of `validate()`, passed into a new `score_match(predicted, ground_truth, hpo)`.
- Rewrite `score_match` to use `hpo.graph.shortest_path_length(pred, gt)` (or equivalent — confirm exact method name on first run; if `distance()` is the API, use that). Map distance → score:
  ```python
  def score_distance(d: int | None) -> float:
      if d is None: return 0.0
      return {0: 1.0, 1: 0.75, 2: 0.5}.get(d, 0.0)
  ```
- Keep `validate.py`'s public surface (`validate()`, `print_report()`) unchanged so `scripts/validate.py` still works.
- Delete `parse_obo`, `build_hierarchy`, `build_enriched_text` from `hpo_index.py` only if no other caller uses them. They're still used by `scripts/seed_hpo.py` for embedding — so leave them, just stop using them in `validate.py`.
- Cache the loaded HPO graph on a module-level variable so repeat calls don't re-download/re-parse.
- Unit tests:
  - Exact match (Fatigue → Fatigue) scores 1.0.
  - Known parent (Pain → Abdominal pain, 1 hop) scores 0.75.
  - Two unrelated terms score 0.0.
  - Sibling terms with shared parent score correctly (this is the new case the old code missed — pick two HPO terms from the same parent and assert the score is what we want for distance=2).

**Verify:**
```bash
pip install -e .
pytest tests/test_validate.py -v
python scripts/validate.py --ground-truth context/CRS__under20_35_HPO_corr.xlsx --pipeline-output output/results.xlsx
```

**Expect:** Validation report still prints with the same shape. Aggregate F1 should be **stable or higher** than before — the sibling-credit change can only add score, not remove it. If F1 drops noticeably, something's wrong with the distance API mapping.

**Ship:**
- Save the pre-migration validation report (`output/validation_pre_hpotk.txt`) and post-migration (`output/validation_post_hpotk.txt`) for the diff.
- Update `context/architecture-notes.md` validation section to note we now use hpo-toolkit.
- Commit: `Use hpo-toolkit for hierarchy walks in validation scorer`.

**Depends on:** Task 1 (so the validation we run after has the label fix baked in). One day.

---

### Task 3: `phenoscribe aggregate` cohort command

**Files:** `src/phenoscribe/aggregate.py` (new), `src/phenoscribe/cli.py`, `tests/test_aggregate.py` (new), `pyproject.toml`

Both the Plovdiv poster ("Mapping the prevalence of Long COVID symptoms in 10 kids") and the children's paper Figure 1 ("Prevalence of Long COVID Symptoms in 10 Children Seen in General Practice") show the same chart: HPO terms on the y-axis, patient count on the x-axis, sorted descending. Today Phenoscribe outputs per-patient rows only — aggregating into a prevalence chart is manual work.

Changes:
- New module `aggregate.py` with:
  - `load_patient_codes(workbook_path) -> dict[patient_id, list[(hpo_id, hpo_term)]]`. Reuse / extract from `validate.py`'s `load_codes_from_excel` since the parsing logic is nearly identical (handle both semicolon and PURL formats); refactor that out of `validate.py` into `aggregate.py` and have `validate.py` import it.
  - `compute_prevalence(patient_codes) -> list[dict]` returning rows of `{hpo_id, hpo_term, n_patients, pct, patient_ids}` sorted by `n_patients` desc.
  - `write_prevalence_csv(rows, path)` → standard CSV.
  - `write_prevalence_chart(rows, path, top_n=20)` → matplotlib horizontal bar chart, top N terms. Matches the Plovdiv poster style: terms on y-axis, count on x-axis, bars sorted long-to-short top-to-bottom.
- Add `matplotlib>=3.7` to `pyproject.toml`.
- New CLI subcommand in `cli.py`:
  ```
  phenoscribe aggregate <results.xlsx> [--csv out.csv] [--chart out.png] [--top 20]
  ```
  Defaults: write to `output/prevalence.csv` and `output/prevalence.png`. Prints a summary table to stdout (top 10 terms).
- Unit tests:
  - 3 patients, 5 terms, one term shared by all 3 → that term has n=3 at top.
  - Empty workbook → empty prevalence with a clear message, not a crash.
  - PURL-format workbook → parses correctly (uses the shared loader).

**Verify:**
```bash
phenoscribe aggregate output/results.xlsx --csv /tmp/prev.csv --chart /tmp/prev.png --top 20
file /tmp/prev.png  # PNG image data
head /tmp/prev.csv
pytest tests/test_aggregate.py -v
```

**Expect:** CSV lists every distinct HPO term with patient counts. PNG is a horizontal bar chart of the top 20 terms. Visual inspection: top terms should be Fatigue / Post-exertional intolerance for the long COVID dataset (matches the papers).

**Ship:**
- Generate `output/prevalence.csv` and `output/prevalence.png` for the current dataset and attach to the next stakeholder update so Marc sees what the command produces.
- Add a one-paragraph section to `context/architecture-notes.md` describing the aggregate command.
- Commit: `Add phenoscribe aggregate command for cohort prevalence outputs`.

**Depends on:** Task 1 (so the cohort numbers reflect the corrected labels). One day.

---

### Task 4: ontoGPT benchmark (one-off, informational)

**Files:** `scripts/benchmark_ontogpt.py` (new), `context/exports/ontogpt-benchmark-2026-05-30.md` (new, the report itself)

Peter Robinson explicitly suggested ontoGPT (PMID 38383067). The point of this task is **not** to integrate it — it's to get a defensible number for "how does Phenoscribe compare to a well-known purpose-built tool on the same data."

Changes:
- New `scripts/benchmark_ontogpt.py`:
  - Pip-install ontogpt in an isolated venv (`uv venv .venv-ontogpt && uv pip install ontogpt` or document the manual command in the script header). Don't add it to the main `pyproject.toml` — we're not shipping it.
  - For each `output/pseudo/<patient_id>.txt`, run:
    ```bash
    ontogpt extract -i <pseudo>.txt -t human_phenotype -O json -o /tmp/ontogpt/<patient_id>.json
    ```
    using the same provider/model as Phenoscribe's config so the comparison is fair (configure ontogpt to use Anthropic Claude via its env vars; this matches our setup and is cheaper than GPT-4).
  - Parse each output JSON into the same `{patient_id: set[hpo_id]}` shape `validate.py` produces.
  - Pass through the same `score_match` from `validate.py` against the ground truth.
  - Emit a side-by-side report: per-patient and aggregate precision/recall/F1, plus rows-where-ontogpt-found-something-Phenoscribe-missed and vice-versa.
- The output report `context/exports/ontogpt-benchmark-2026-05-30.md`:
  - Headline numbers (Phenoscribe F1 vs. ontoGPT F1).
  - Cost (sum of tokens × price) for the run.
  - Top 5 cases where each tool wins.
  - One-sentence verdict: keep Phenoscribe as-is / steal idea X / consider switching.
- Sanity-check the language question early: run ontoGPT on **two** French pseudonymized transcripts first. If output IDs are clearly wrong / empty / English-only, stop and translate transcripts to English via an LLM pre-pass before running the full benchmark. Note the translation step in the report.

**Verify:**
```bash
python scripts/benchmark_ontogpt.py --sample 2          # smoke test, two files
python scripts/benchmark_ontogpt.py --full              # full run, all pseudo transcripts
cat context/exports/ontogpt-benchmark-2026-05-30.md
```

**Expect:** Sample run completes without errors and produces a parseable JSON per transcript. Full run yields a report with concrete numbers. Verdict line is honest, not generous.

**Ship:**
- Commit the script + the report: `Benchmark ontoGPT against Phenoscribe on ground truth`.
- Send the report markdown to Marc as a stakeholder update.
- If the verdict suggests adopting any ontoGPT technique, open a follow-up plan — don't change Phenoscribe inside this task.

**Depends on:** Task 2 (uses the updated scorer so both tools are judged on the same hierarchy logic). Half a day if French works out of the box, one day if translation is needed.

---

## Definition of Done

- [x] Task 1: judge step canonicalizes term names; tests pass; label_corrected log line exists; committed (PR #1, 2026-05-30).
- [x] Task 2: hpo-toolkit replaces hand-rolled hierarchy walk in `validate.py`; aggregate F1 stable-or-higher vs. pre-migration; committed (PR #2, 2026-05-30).
- [ ] Task 3: `phenoscribe aggregate` produces CSV + PNG matching the Plovdiv-poster style; sample outputs in `output/`; committed.
- [ ] Task 4: ontoGPT benchmark report exists in `context/exports/` with concrete F1 / cost / verdict; committed.
- [ ] When all four are shipped: move this plan to `context/shipped/`.
