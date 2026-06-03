---
problem: "Product owner audit of Phenoscribe — measure what works, cut scope creep, unblock the F1 number"
date: 2026-06-02
status: open
adr: "../exports/2026-03-01-adr-hpo-pipeline.md"
related_export: "../exports/2026-05-30-ontogpt-benchmark.md"
---

# Product Owner Review: Phenoscribe (2026-06-02)

## Summary

Phenoscribe's core architecture is sound. The hybrid "vector shortlist + LLM judge" pattern directly answers Peter Robinson's documented critique that LLMs hallucinate ontology codes, and the ontoGPT benchmark backs it up (3.9× more codes recovered on three transcripts).

Three issues drag on the product today:

1. **One leaked secret on disk** (`setup.sh` contains live OpenAI and Anthropic API keys in plaintext — gitignored, still must be rotated).
2. **Unmeasured accuracy.** The patient-ID format mismatch (`038` vs `MGA.038`) has blocked the ground-truth F1 number against the 1003 manually coded rows since at least 2026-05-30. The whole project's success criterion is unmeasured.
3. **Scope creep.** Three output formats live in parallel (`detailed`, `semicolon`, `purl`), an optional diarization path drags pyannote, a one-shot ontoGPT benchmark sits in `scripts/` with three documented monkey-patches, and a `seed_hpo.py` script keeps an OBO parser alive that the rest of the codebase has moved off of.

This plan rotates the keys, unblocks the F1 number, and deletes the dead branches. Once shipped, the codebase shrinks meaningfully and the product gets its first real validation number.

## Findings — what to keep, what to cut

### Keep

- **Two-LLM-call structure** — extraction separate from judging. Justified by Peter Robinson's caveat and the ontoGPT benchmark.
- **Local PII pseudonymisation.** The GDPR story is the product's moat.
- **SQLite job tracker.** Minimal infra that earns its keep the first time a batch fails halfway.
- **`phenoscribe aggregate`.** Produces the artefact stakeholders actually use (Plovdiv-poster style chart).
- **`context/` discipline.** Dated ADRs, shipped plans, exports. Rare in one-person tools.
- **`hpo-toolkit` for hierarchy walks.** Outsources ontology semantics to the people who own HPO.

### Cut or defer

- **Two of three output formats.** Default is `semicolon`; if Marc doesn't use PURL and detailed, delete them. ~80 lines across `output.py`, `aggregate.py`, `validate.py` evaporate.
- **Diarization.** Off by default, drags pyannote + HF_TOKEN setup, not required by the output. Confirm the GP isn't using it and remove.
- **`hpo_index.parse_obo` / `build_hierarchy`.** Only `scripts/seed_hpo.py` still calls them. ChromaDB seeding can be driven by `hpotk` instead, then the OBO parser dies.
- **`scripts/benchmark_ontogpt.py`.** One-shot; verdict landed. Move to `context/exports/` as an attachment or delete.
- **CLI-level retry loop.** Duplicates `jobs.retries` tracking. Pick one place.
- **`output/results.xlsx.stale-march8`.** Stray artefact in `output/`.

---

## Tasks

Ordered by value-per-effort: 1 (must) → 2 (unblocks measurement) → 3–5 (simplification) → 6 (cleanup).

### Task 1: Rotate leaked API keys and switch to `.env.example`

**Files:** `setup.sh` (delete), `.env.example` (new), `context/architecture-notes.md`, `README` if added

`setup.sh` contains live `OPENAI_API_KEY` and `CLAUDE_API_KEY` values in plaintext. The file is gitignored, but the secrets sit on disk in the working tree and have been there since at least 2026-06-01.

Changes:
- Revoke both keys at the provider consoles.
- Issue new keys, store in `~/.config/phenoscribe/env` (or 1Password / pass / OS keychain).
- Replace `setup.sh` with `.env.example` containing the variable names only, no values. Document `source ~/.config/phenoscribe/env` (or equivalent) in the README.
- Audit `git log --all -p -- setup.sh` to confirm the keys were never committed. If they were, additionally rewrite history or accept the leak and rotate (rotation is the only real fix anyway).
- Audit any other `~/.bash_history`, shell rc files, or shared machines.

**Verify:**
```bash
grep -RE "sk-(proj|ant)-[A-Za-z0-9_\-]{20,}" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=.venv-ontogpt
git log --all -p -- setup.sh | grep -E "sk-(proj|ant)-"
```

**Expect:** No matches. Both commands silent.

**Ship:** Confirm new keys work with one `process` smoke run. Commit: `Rotate API keys; replace setup.sh with .env.example`.

**Depends on:** none. One hour, mostly waiting for key rotation.

---

### Task 2: Fix the patient-ID join so F1 against the 1003-row ground truth becomes computable

**Files:** `src/phenoscribe/cli.py` (or wherever `patient_id` is derived), `scripts/validate.py`, `tests/test_validate.py`

`cli.py:99` derives `patient_id` from the filename stem (`038.mp3` → `038`). Ground truth uses `MGA.038`. The 2026-05-30 ontoGPT benchmark report explicitly notes: *"Ground-truth F1 was not computed because the patient-ID format mismatch between pipeline output (`038`) and the GP's manual codes (`MGA.038`) still blocks joining."* That comment has been load-bearing for at least a month.

Options (pick whichever Marc finds least surprising):

1. **Normalise at read time.** Strip a `MGA.` prefix from ground-truth IDs in `load_codes_from_excel`, or strip everything before the last `.` digits. Cheap, but hides the convention.
2. **Rename input files.** Rename recordings to `MGA.038.mp3` so the natural stem matches. Cleanest, requires a one-time file rename.
3. **Config-driven prefix.** Add `patient_id_prefix: "MGA."` to `config.yaml`; prepend on write. Most flexible, smallest blast radius.

Recommend option 3 — preserves the recording filenames, makes the convention explicit, and the config already exists.

Add the option to `Config`, prepend it in `cli.py` when calling `create_job` and `process_recording`, and add a `test_validate.py` case asserting that the pipeline output joins against the existing ground-truth fixture.

Once the join works, run the scorer end-to-end and **commit the resulting `validation_2026-06-02.txt` to `context/exports/`.** That number is the product's first defensible accuracy claim.

**Verify:**
```bash
phenoscribe process data/recordings/ --skip-transcription   # uses cached transcripts
python scripts/validate.py --ground-truth data/ground_truth/CRS__under20_35_HPO_corr.xlsx --pipeline-output output/results.xlsx
```

**Expect:** Validation report shows non-zero `patients_evaluated` and a real F1. If F1 is below 0.4, that's still a real number to react to.

**Ship:** Commit the validation report under `context/exports/2026-06-02-validation-baseline.md`. Note in `architecture-notes.md` that this is the first measured F1.

**Depends on:** Task 1. One day.

---

### Task 3: Collapse output formats to one

**Files:** `src/phenoscribe/output.py`, `src/phenoscribe/aggregate.py`, `src/phenoscribe/validate.py`, `config.yaml`, tests

Confirm with Marc which format he actually opens. Default is `semicolon`. If detailed and PURL aren't read by anyone, delete them.

Changes:
- Pick the surviving format (likely `detailed` — it's the easiest to filter/sort in Excel and `aggregate` reads it natively).
- Remove the other two from `HEADERS`, `_write_*_format`, and any branch in `output.py`.
- Drop the `format` field from `OutputConfig` (or keep it for one release as a deprecation warning).
- Simplify `aggregate.load_patient_codes` to one parsing branch.
- Drop the now-unused `_SEMICOLON_TRIPLET` and `_PURL_TO_HP` regexes if their branches die.
- Update tests to the surviving format only.

**Verify:**
```bash
pytest tests/ -v
phenoscribe process data/recordings/ --skip-transcription
phenoscribe aggregate output/results.xlsx
```

**Expect:** All tests pass. Aggregate still produces the Plovdiv chart unchanged.

**Ship:** Commit: `Drop dead output formats; keep detailed only`.

**Depends on:** confirmation from Marc on which format to keep. Half a day.

---

### Task 4: Decide diarization's fate

**Files:** `src/phenoscribe/diarize.py`, `src/phenoscribe/pipeline.py`, `config.yaml`, `pyproject.toml`

Diarization is behind `diarization.enabled = false`. It requires HF_TOKEN, drags `pyannote.audio>=3.1.0`, and adds a Step 1b in the pipeline. If the GP isn't using it for analysis, it's dead weight.

Decision: ask Marc whether speaker-labeled transcripts are useful for his review. If no:
- Delete `diarize.py`, the diarization branch in `pipeline.py`, the `DiarizationConfig` dataclass, and the `pyannote.audio` dependency.
- Update `architecture-notes.md` to drop the optional Step 1b.

If yes, keep but document setup more clearly in the README.

**Verify:** If removed, `python -c "import pyannote"` fails; `pytest tests/` still passes.

**Ship:** Commit: `Remove diarization` (or `Document diarization HF_TOKEN setup`).

**Depends on:** Marc's input. Half a day if removing, less if keeping.

---

### Task 5: Migrate `seed_hpo.py` to `hpo-toolkit`, delete `parse_obo`

**Files:** `scripts/seed_hpo.py`, `src/phenoscribe/hpo_index.py`

`scripts/seed_hpo.py` is now the only caller of `hpo_index.parse_obo`, `build_enriched_text`, `build_hierarchy`. `validate.py` already moved to `hpo-toolkit`. Finishing the migration removes the OBO parser, the OBO file in `data/hpo/hp.obo`, and the `hpo_obo` config path.

Changes:
- Rewrite `seed_hpo.py` to iterate terms from `hpotk` (`hpo.terms`), pulling `name`, `definition`, `synonyms` from each `hpotk.MinimalTerm` (or `hpotk.Term` if minimal doesn't expose synonyms — confirm at first run).
- Delete `parse_obo`, `build_enriched_text`, `build_hierarchy` from `hpo_index.py`. Keep `search_hpo` and `seed_chromadb`'s public signature.
- Drop `hpo_obo` from `PathsConfig` and `config.yaml`.
- Add a test that `seed_chromadb` produces a ChromaDB collection of the expected size (~17K HP terms).

**Verify:**
```bash
python scripts/seed_hpo.py
python -c "from phenoscribe.hpo_index import search_hpo; print(search_hpo('headache'))"
pytest tests/ -v
```

**Expect:** ChromaDB seeded with ~17K terms, `search_hpo` returns sensible top-5 for "headache".

**Ship:** Commit: `Drive HPO indexing from hpo-toolkit; delete OBO parser`.

**Depends on:** none, but cleanest after Task 3 so the codebase is already thinning. Half a day.

---

### Task 6: Cleanup pass

**Files:** `src/phenoscribe/cli.py`, `output/`

Small items:

- Remove the CLI-level retry loop in `_cmd_process` — `jobs.retries` already tracks attempts; collapse to one retry policy in `pipeline.process_recording`.
- Delete `output/results.xlsx.stale-march8`.
- If `status` subcommand is unused, fold its contents into `process --status` or delete.
- Move `scripts/benchmark_ontogpt.py` to `context/exports/2026-05-30-benchmark-ontogpt-script.py` (it's a one-shot artefact attached to the report, not a maintained script).

**Verify:** `pytest tests/`, smoke run of `process`.

**Ship:** Commit: `Cleanup: dedupe retry loop, drop stale artefacts`.

**Depends on:** Tasks 3–5. Half a day.

---

## Investigations (do these before deciding the scope cuts)

These are open questions whose answers determine whether some tasks above should be cut, expanded, or merged. They're cheap to answer — mostly conversations with Marc.

- **Which Excel format does the GP actually open?** (Drives Task 3.)
- **Has Marc ever turned diarization on?** (Drives Task 4.)
- **What's the expected cohort growth?** If it stays at ~150, the current `load_workbook`+`save` per patient is fine. If it grows to thousands, batch the write.
- **What does the failure distribution in `jobs.db` look like?** If most failures are transcription, the retry loop is right. If they're at LLM-extraction, the retry policy needs to back off API rate limits.

---

## Definition of Done

- [ ] Task 1: keys rotated, `setup.sh` gone, `.env.example` documented, no matching secret strings on disk.
- [ ] Task 2: first F1 number computed against the 1003-row ground truth, committed under `context/exports/2026-06-02-validation-baseline.md`.
- [ ] Task 3: one output format remains; `output.py` is materially smaller.
- [ ] Task 4: diarization either deleted or has a one-paragraph setup guide in the README.
- [ ] Task 5: `hpo_index.parse_obo` is gone; `seed_hpo.py` runs via `hpo-toolkit`.
- [ ] Task 6: stale `output/` artefacts removed; retry loop collapsed.
- [ ] When all six shipped: move this plan to `context/shipped/2026-06-02-product-owner-review.md` with shipped-date frontmatter.
