---
problem: "Automate HPO phenotype coding from long COVID patient interviews"
date: 2026-03-01
adr: "hpo-identifier-adr-2026-03-01.md"
---

# Implementation Plan: HPO Identifier Pipeline

## Summary

Build a Python CLI pipeline that processes patient interview audio into structured HPO phenotype codes. The pipeline runs six sequential stages: local transcription (faster-whisper), local PII pseudonymization (OpenMed), LLM symptom extraction, HPO vector search (ChromaDB), LLM HPO judging, and Excel output. All PII handling stays local; LLM calls only see pseudonymized text.

## Tasks

### Task 1: Project scaffolding and dependencies

**Files:** `pyproject.toml`, `src/hpo_identifier/__init__.py`, `src/hpo_identifier/config.py`, `config.yaml`

Set up Python project with uv/pip:
- Create `pyproject.toml` with dependencies: `faster-whisper`, `transformers` (for OpenMed), `chromadb`, `openpyxl`, `openai`, `anthropic`, `pyyaml`
- Create `config.yaml` with sections for: LLM provider/model, audio settings, output paths, ChromaDB path
- Create thin config loader that reads YAML and exposes typed settings
- Create basic project structure under `src/hpo_identifier/`

**Verify:** `pip install -e . && python -c "from hpo_identifier.config import load_config; load_config('config.yaml')"`
**Expect:** Install succeeds, config loads without error.

---

### Task 2: Download and seed HPO ontology into ChromaDB

**Files:** `src/hpo_identifier/hpo_index.py`, `scripts/seed_hpo.py`

- Download HPO ontology OBO file (https://hpo.jax.org/data/ontology)
- Parse OBO file: extract term ID, name, definition, synonyms, `is_a` parents
- For each of ~17K terms, build enriched text: `"{name}. Synonyms: {synonyms}. Definition: {definition}"`
- Embed using ChromaDB's default embedding function (all-MiniLM-L6-v2)
- Store in persistent ChromaDB collection with metadata: `hpo_id`, `name`, `parent_ids`
- Build HPO hierarchy graph (dict of id → parent_ids) for validation scoring later
- Expose `search_hpo(clinical_term: str, k: int = 5) -> list[dict]` function

**Verify:** `python scripts/seed_hpo.py && python -c "from hpo_identifier.hpo_index import search_hpo; results = search_hpo('abdominal pain'); print(results[0]); assert 'HP:' in results[0]['hpo_id']"`
**Expect:** Seeding completes, search returns top-5 HPO candidates with IDs, "Abdominal pain (HP:0002027)" is in top results.

---

### Task 3: Transcription module (faster-whisper + text input)

**Files:** `src/hpo_identifier/transcribe.py`

- Install/configure faster-whisper with a French-tuned model (e.g., `bofenghuang/whisper-large-v3-french` or fallback to `large-v3`)
- Create `transcribe(audio_path: str) -> str` function
- Accept common audio formats (.wav, .mp3, .m4a, .ogg)
- **Also accept pre-existing text files (.txt)** — if input is a text file, skip transcription and read the text directly
- Return full transcript as plain text
- Log transcription duration and audio file info (or "text input" for .txt files)

**Verify:** `python -c "from hpo_identifier.transcribe import transcribe; text = transcribe('tests/fixtures/sample.wav'); print(text[:200]); assert len(text) > 50"`
**Expect:** Transcription returns non-empty French text from a sample audio file. Text files are passed through directly.
**Depends on:** Task 1

---

### Task 4: PII pseudonymization module (OpenMed)

**Files:** `src/hpo_identifier/pii.py`

- Load OpenMed French PII model from HuggingFace
- Create `pseudonymize(text: str) -> tuple[str, dict]` function
  - Detect PII entities with type labels (PERSON, DATE, LOCATION, ORGANIZATION, etc.)
  - Replace each entity with numbered pseudonym per type: `PERSON_1`, `PERSON_2`, `DATE_1`, etc.
  - Maintain consistency: same entity always gets same pseudonym throughout text
  - Return pseudonymized text + mapping table (kept local)
- Non-identifying context preserved: "grandmother", "school", "work" stay as-is

**Verify:** `python -c "from hpo_identifier.pii import pseudonymize; text, mapping = pseudonymize('Le Dr. Martin m\\'a vu le 15 mars 2023 à Bruxelles.'); print(text); print(mapping); assert 'Martin' not in text; assert 'PERSON_1' in text"`
**Expect:** PII replaced with numbered pseudonyms, mapping table contains original values.
**Depends on:** Task 1

---

### Task 5: LLM abstraction layer

**Files:** `src/hpo_identifier/llm.py`

- Create thin LLM client that reads provider/model from config
- Support three providers: `openai`, `anthropic`, `ollama`
- Single interface: `llm_call(system_prompt: str, user_prompt: str) -> str`
- Each provider uses its native SDK (openai, anthropic) or HTTP for Ollama
- API key read from environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`)
- Basic retry logic (1 retry on transient errors)

**Verify:** `python -c "from hpo_identifier.llm import llm_call; response = llm_call('You are a test.', 'Say hello.'); print(response); assert len(response) > 0"`
**Expect:** LLM responds successfully using configured provider.
**Depends on:** Task 1

---

### Task 6: Symptom extraction prompt (LLM Call 1)

**Files:** `src/hpo_identifier/extract_symptoms.py`

- Create `extract_symptoms(transcript: str) -> list[dict]` function
- Prompt instructs LLM to:
  - Read French transcript
  - Extract every symptom/complaint mentioned
  - For each: `patient_verbatim` (original French), `clinical_term` (English medical concept), `context` (temporal/severity)
  - Output structured JSON array
- Parse LLM JSON response with error handling
- Return list of `{"patient_verbatim": str, "clinical_term": str, "context": str}`

**Verify:** `python -c "from hpo_identifier.extract_symptoms import extract_symptoms; results = extract_symptoms('Le patient dit qu\\'il a mal au ventre et qu\\'il est fatigué.'); print(results); assert any('abdominal' in r['clinical_term'].lower() for r in results)"`
**Expect:** Extracts at least 2 symptoms (abdominal pain, fatigue) with English clinical terms and French verbatim.
**Depends on:** Task 5

---

### Task 7: HPO matching module (ChromaDB search + LLM Call 2)

**Files:** `src/hpo_identifier/match_hpo.py`

- Create `match_hpo(symptoms: list[dict]) -> list[dict]` function
- For each symptom:
  1. Search ChromaDB with `clinical_term` → get top-5 HPO candidates
  2. Send to LLM: "Given this clinical concept: '{clinical_term}', which of these HPO terms is the best match? {candidates}. Return the HPO ID and term name."
  3. Parse LLM response → extract selected HPO ID and term
- Return enriched list: `{"hpo_term": str, "hpo_id": str, "patient_verbatim": str, "clinical_term": str}`

**Verify:** `python -c "from hpo_identifier.match_hpo import match_hpo; results = match_hpo([{'clinical_term': 'abdominal pain', 'patient_verbatim': 'mal au ventre', 'context': ''}]); print(results); assert results[0]['hpo_id'].startswith('HP:')"`
**Expect:** Returns HPO match with valid HP: code for "abdominal pain".
**Depends on:** Task 2, Task 5

---

### Task 8: Excel output module (dual format)

**Files:** `src/hpo_identifier/output.py`

- Create `write_excel(patient_id: str, matches: list[dict], output_path: str, format: str = "semicolon")` function
- **Format A — Semicolon (default):** One row per patient, all matches as semicolon-separated triplets: `HPO Term (HP:code) [patient verbatim]; ...`
  - Columns: `Patient_ID`, `observation_source_value`
- **Format B — PURL:** One row per HPO term per patient, with PURL-style code links
  - Columns: `CASE ID`, `HPO TERM`, `HPO Code Purl`, `Verbatim`
  - PURL format: `http://purl.obolibrary.org/obo/HP_XXXXXXX` (underscore, not colon)
- Output format selectable via config file
- Support appending multiple patients to same workbook

**Verify:** `python -c "from hpo_identifier.output import write_excel; write_excel('TEST.001', [{'hpo_term': 'Fatigue', 'hpo_id': 'HP:0012378', 'patient_verbatim': 'fatigue'}], '/tmp/test_output.xlsx'); import openpyxl; wb = openpyxl.load_workbook('/tmp/test_output.xlsx'); print(wb.active['B2'].value); assert 'HP:0012378' in wb.active['B2'].value"`
**Expect:** Excel file created with correctly formatted HPO triplets matching GP's chosen format.
**Depends on:** Task 1

---

### Task 9: SQLite job tracker

**Files:** `src/hpo_identifier/jobs.py`

- Create SQLite database with schema: `jobs (id INTEGER PRIMARY KEY, audio_file TEXT, patient_id TEXT, status TEXT, step_failed TEXT, error_msg TEXT, retries INTEGER DEFAULT 0, created_at TIMESTAMP, updated_at TIMESTAMP)`
- Functions: `create_job()`, `update_job()`, `get_pending_jobs()`, `get_failed_jobs()`
- Status flow: `pending → processing → completed | failed`

**Verify:** `python -c "from hpo_identifier.jobs import create_job, update_job, get_pending_jobs; import os; os.remove('/tmp/test_jobs.db') if os.path.exists('/tmp/test_jobs.db') else None; create_job('/tmp/test_jobs.db', 'test.wav', 'TEST.001'); jobs = get_pending_jobs('/tmp/test_jobs.db'); print(jobs); assert len(jobs) == 1"`
**Expect:** Job created and retrievable with pending status.
**Depends on:** Task 1

---

### Task 10: Pipeline orchestrator

**Files:** `src/hpo_identifier/pipeline.py`, `src/hpo_identifier/cli.py`

- Create `process_recording(audio_path: str, patient_id: str) -> list[dict]` that chains all steps:
  1. Transcribe audio → raw text
  2. Pseudonymize → clean text + mapping
  3. Extract symptoms → symptom list
  4. Match HPO → matched triplets
  5. Write Excel output
  6. Update job status
- Create CLI entry point: `python -m hpo_identifier process <input_dir> --output results.xlsx`
  - Scan directory for audio files (.wav, .mp3, .m4a, .ogg) and text files (.txt)
  - Create jobs in SQLite for each
  - Process sequentially
  - Retry failed jobs once
  - Print summary (completed/failed counts)

**Verify:** `python -m hpo_identifier process tests/fixtures/ --output /tmp/test_results.xlsx`
**Expect:** Pipeline processes test audio file(s), produces Excel output, SQLite shows completed status.
**Depends on:** Task 3, Task 4, Task 6, Task 7, Task 8, Task 9

---

### Task 11: Validation scorer

**Files:** `src/hpo_identifier/validate.py`, `scripts/validate.py`

- Load ground truth Excel (CRS__under20_35_HPO_corr.xlsx)
- Parse existing format: extract HPO codes per patient
- Load pipeline output Excel
- For each patient, compare pipeline HPO codes against ground truth:
  - Exact match = 1.0
  - Parent/child (1 hop in HPO hierarchy) = 0.75
  - Grandparent/grandchild (2 hops) = 0.5
  - Wrong/beyond 2 hops = 0
- Calculate precision, recall, and weighted F1 per patient and aggregate
- Print report with per-patient scores and overall summary

**Verify:** `python scripts/validate.py --ground-truth CRS__under20_35_HPO_corr.xlsx --pipeline-output /tmp/test_results.xlsx`
**Expect:** Prints validation report with per-patient and aggregate scores.
**Depends on:** Task 2, Task 10

---

## Definition of Done

- [ ] All 11 tasks verified
- [ ] Pipeline processes at least 1 real audio recording end-to-end
- [ ] Excel output matches GP's existing format
- [ ] PII pseudonymization confirmed (no real names in LLM-sent text)
- [ ] Validation scorer runs against ground truth with meaningful scores
- [ ] Config file allows swapping LLM provider without code changes
