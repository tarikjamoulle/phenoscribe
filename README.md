# Phenoscribe

Automated HPO phenotype coding from patient interviews. Built for a GP doing long-COVID research who was coding ~150 recordings manually.

> **Never used a terminal?** Follow **[INSTALL.md](INSTALL.md)**: download the ZIP, double-click the start file, paste your key, drop your files.

## What it does

A recorded GP–patient conversation in, an Excel of standardized HPO codes out. Six steps:

1. **Transcribe** the recording with faster-whisper, on the laptop. The audio file never leaves the machine.
2. **Pseudonymise PII** locally with a French NER model plus regex. Names, places, dates, phones, emails and national IDs get stable placeholders: "Dr. Martin" → "PERSON_1", "Bruxelles" → "LOCATION_1". The mapping stays on the machine. Default model: [`Anonym-IA/V2-camembert-ner-pii-french`](https://huggingface.co/Anonym-IA/V2-camembert-ner-pii-french) (CamemBERT-base, MIT, validation micro-F1 0.9327). See [Privacy](#privacy) for what it catches and what it misses.
3. **Extract symptoms** with an LLM: each complaint becomes a clinical English label plus the patient's own French verbatim.
4. **Shortlist five HPO candidates** per symptom from a local ChromaDB index of ~17,000 HPO terms.
5. **Pick the best code** from that shortlist with an LLM. The term name is re-read from the official ontology so the AI can't mis-label a valid code (Peter Robinson's caveat).
6. **Write the Excel** in the GP's existing format: `Term (HP:code) [patient verbatim]; …`

Cohort summaries with `phenoscribe aggregate` — counts per HPO term plus a horizontal bar chart in the style of the Plovdiv poster.

## Privacy

- Audio and the mapping table never leave the machine.
- Only pseudonymised text is sent to the LLM provider.
- Designed under GDPR + Belgian health-data law.

### PII model: what it catches, what it misses

Step 2 runs entirely on the local machine. Two layers:

- **NER head** (configurable via `pii.model` in `config.yaml`). Default is
  [`Anonym-IA/V2-camembert-ner-pii-french`](https://huggingface.co/Anonym-IA/V2-camembert-ner-pii-french):
  CamemBERT-base (110M params, ~445 MB), MIT licence, fully local. It tags 39
  French PII entity types (names, street/city/postal, email, phone,
  social-security number, IBAN, dates, job titles, ...). Reported validation
  micro-F1 is 0.9327 (`best_metrics.json` on the model card). A general 4-label
  French NER, [`Jean-Baptiste/camembert-ner`](https://huggingface.co/Jean-Baptiste/camembert-ner)
  (PER/LOC/ORG/MISC, WikiNER F1 0.8914), ships as the offline `pii.fallback_model`
  and is loaded automatically if the default cannot be fetched.
- **Regex** for structured PII: dates, Belgian/French phone numbers, emails,
  Belgian national numbers. Deterministic, runs regardless of the NER head.

Known limitation. Any general or PII NER under-redacts rare proper nouns it
never saw in training: hospital and clinic names, drug eponyms, uncommon
surnames, and bare first names. On our pseudonymised sample transcripts the
PII model caught residual person names (e.g. "Madame de Simon") while leaving
drug and condition names (Zaldiar, Ivabradine, Paxlovid, "Covid long") in
place — which is what you want, since those carry clinical meaning. The
general fallback model flags those drug/condition tokens as MISC and would
over-redact them, and it also catches some first names the PII model misses.
Neither model is a complete de-identifier. Spot-check the first batch.

## Ways to run

### Requirements

- An **API key** from OpenAI or Anthropic. About $0.01 per recording.
- **About 10 GB free disk space** (most of it is the Whisper speech model).
- For the CLI: `uv`. It installs Python 3.12 (`.python-version`) and the exact versions in `uv.lock`.

### Web app (start file, recommended)

Double-click `Start Phenoscribe.bat` (Windows) or `Start Phenoscribe.command` (Mac; `sh` on Linux).
The script installs `uv` if it is missing, runs `phenoscribe setup`, and opens the page on
<http://127.0.0.1:7860>. Drop files or choose a folder, then click **Start coding**. The app
decides the work for each file (`plan_batch` in `pipeline.py`): audio is transcribed, a `.txt`
is coded as it is, and a saved transcript of the same audio bytes is reused
(`transcripts/by-audio/<sha256>.txt`, keyed by the bytes, backend, model and language).

Each run writes a new `results_YYYY-MM-DD_HHMM.xlsx` to `Documents/Phenoscribe`
(`PHENOSCRIBE_OUTPUT_DIR`), with `transcripts/`, `pseudo/`, `phenoscribe.log`, and
`filename_mapping.json` (each hashed `pt-…` ID back to its filename). A file named only by a
number (`467.mp3`) gets the ID `MGA.467`.

`phenoscribe setup` downloads the pinned `hp.obo`, builds the ChromaDB index, and
pre-downloads the Whisper and PII models. It skips each part that is present, so it is safe
at every start. The transcription backend `auto` picks mlx on Apple Silicon (when `ffmpeg`
exists), else faster-whisper.

### Docker (Linux servers)

```bash
docker build -t phenoscribe .     # runs phenoscribe setup inside the image
docker run --rm -p 127.0.0.1:7860:7860 \
  -e PHENOSCRIBE_OUTPUT_DIR=/data/output \
  -v "$(pwd)/output:/data/output" \
  phenoscribe
```

The `127.0.0.1:` prefix on `-p` keeps the published port reachable only from this machine.

### CLI

```bash
uv sync --frozen && uv run phenoscribe setup

# Process every audio file in data/recordings/, write to output/results.xlsx
phenoscribe process data/recordings/ --output output/results.xlsx

# Build a cohort summary (CSV + bar chart) from an existing results file
phenoscribe aggregate output/results.xlsx --top 30

# Show the status of the last batch
phenoscribe status
```

`--skip-transcription` reuses saved transcripts from `output/transcripts/`. Use it when you change the LLM provider or model and want to re-run just the matching step without redoing Whisper.

## Configuration

`config.yaml` controls the LLM provider, Whisper model, language, output format, and paths. Three LLM providers wired:

- `openai` (default — gpt-4o, gpt-4o-mini, gpt-4-turbo)
- `anthropic` (claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5)
- `ollama` (local Llama / Mistral — for offline use; CLI only)

API keys live in `.env` or in the web app's password box. Never written to disk by the GUI.

### HPO release

One pinned release drives everything: `hpo.release` in `config.yaml` is
`hp/releases/2026-02-16`, `phenoscribe setup` downloads the matching `v2026-02-16`
tag, and the seeded ChromaDB index is built from that obo. Startup verifies the
`data-version:` header of the on-disk obo against `hpo.release` and fails loudly
on a mismatch, so build host and run host can't disagree. Every results workbook
carries the release on a `Provenance` sheet, so any output is self-describing.

Retired codes resolve forward: an obsolete term with `replaced_by:`, or a merged
id carried as `alt_id:`, maps to its active id when loading ground truth and when
matching. On Marc Jamoulle's ground truth, 27 of 332 distinct codes were retired
in this release; 24 now resolve to an active id instead of dropping out of search.

### Embedding model

`hpo_index.embedding_model` in `config.yaml` selects the encoder for vector
search: `default` (ChromaDB's built-in all-MiniLM-L6-v2, what the shipped index
is seeded with) or `sapbert` (the biomedical encoder). The collection stores the
model it was seeded with on its metadata.

The index and the config must agree. Querying an all-MiniLM index with SapBERT
vectors (or vice versa) is a geometry mismatch: the two encoders put text in
different vector spaces, so the distances are meaningless and retrieval is
garbage. `search_hpo` compares the configured `embedding_model` against the
collection's stored metadata and raises `EmbeddingModelMismatch` when they
differ, rather than returning silently wrong codes.

So switching to SapBERT is a two-step change, not a one-line config edit:

1. Re-seed the index for the new encoder: `python scripts/build_sapbert_index.py`
   (downloads SapBERT and rebuilds the collection with `embedding_model='sapbert'`).
2. Set `hpo_index.embedding_model: sapbert` in `config.yaml`.

Do them in either order; the guard just refuses to run the pipeline until they
match.

## Project layout

```
src/phenoscribe/        Pipeline source
  transcribe.py         Step 1: faster-whisper
  pii.py                Step 2: local French NER + regex pseudonymisation
  extract_symptoms.py   Step 3: LLM symptom extraction
  hpo_index.py          Step 4: ChromaDB vector search
  match_hpo.py          Step 5: LLM judging + canonical name resolution
  output.py             Step 6: Excel writer (three formats)
  aggregate.py          Cohort-level prevalence + bar chart
  pipeline.py           Orchestrator
  cli.py                Typer CLI
  gui.py                Gradio web app
  diarize.py            Optional: pyannote speaker diarization (needs HF_TOKEN)

data/                   HPO ontology, ChromaDB, recordings (gitignored)
output/                 Excel, transcripts, pseudonymised text, filename mapping
context/                Architecture notes, plans, shipped docs, exports
tests/                  Unit + integration tests
```

## Validation

Ground truth: 1003 manually coded rows. Scoring uses information content (IC) of the most informative common ancestor — Resnik (1995) MICA and Lin (1998) normalisation — built from `phenotype.hpoa` via [hpo-toolkit](https://pypi.org/project/hpo-toolkit/). An exact ID match scores 1.0; a near-root prediction against a specific truth scores ~0 because the shared ancestor carries almost no information. The report also gives a strict document-level F1 (exact HPO ID match only, the convention in HPO concept-recognition benchmarks).

Run `phenoscribe validate <results.xlsx> <ground_truth.xlsx>` to score a batch. It prints the term-level (strict and partial-credit) precision/recall/F1 and the IC distribution of the predicted terms (the Q9 check: are predictions dominated by low-IC near-root terms?).

The IC machinery is used for validation only. Match-time ranking inside `process` selects from the ChromaDB cosine shortlist with the LLM judge (steps 4–5 above); it does not rank on IC. Routing the live ranker through IC would be an unmeasured behaviour change, so it is kept out of the pipeline until measured.

## Benchmarks

- `scripts/benchmark_ontogpt.py` — head-to-head vs OntoGPT on the pseudonymised French transcripts.
- `scripts/benchmark_gsc.py` — Phenoscribe on the GSC+ gold standard (Groza et al.), with FastHPOCR run on the same 228 documents and the published GSC+ matrix for context. Document-level precision/recall/F1, exact and lenient. On this English-abstract corpus FastHPOCR's offline dictionary wins (exact F1 0.78 vs Phenoscribe retrieval top-1 0.45); the retriever's recall@5 is 73%. See `context/exports/2026-06-03-gold-standard-benchmark.md` for the honest read.

## Where to read more

- **`context/architecture-notes.md`** — live design reference. The first thing to read if you want to understand or modify the pipeline.
- **`context/exports/`** — formal documents shared with stakeholders (ADR, client report, ontoGPT benchmark).
- **`context/shipped/`** — completed implementation plans, dated chronologically.
- **`context/plans/`** — work currently in flight.

## License

Apache 2.0
