# Phenoscribe

Automated HPO phenotype coding from patient interviews. Built for a GP doing long-COVID research who was coding ~150 recordings manually.

> **Never used a terminal?** Follow **[INSTALL.md](INSTALL.md)** — a step-by-step guide from "I just got a laptop" to "I have an Excel". Allow about 45 minutes the first time.
>
> A double-click launcher for Mac and Windows is planned (see `context/plans/`).

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

## Two ways to run

### Requirements

- **Docker Desktop** (Mac / Windows) or **Docker Engine** (Linux). Free download at <https://www.docker.com/products/docker-desktop/>.
- An **API key** from OpenAI or Anthropic. About $0.01 per recording.
- **~8 GB free disk space** (most of it is the Whisper voice-recognition model).
- For the CLI: **Python 3.11+** and `uv` (or `pip`).

Both ways use the same folder layout next to the project. Create them once:

```bash
mkdir -p data/recordings output
```

| Folder | What goes in it | Who writes it |
|---|---|---|
| `data/recordings/` | **Your audio files.** Drop `.mp3`, `.wav`, `.m4a`, `.ogg` (or pre-made `.txt` transcripts) here. | You |
| `output/` | **The results.** Excel at `output/results.xlsx`, transcripts at `output/transcripts/<name>.txt`, pseudonymised text at `output/pseudo/<name>.txt`, and `output/filename_mapping.json` mapping each hashed `pt-…` id back to your original filename. | Phenoscribe |

The Whisper transcription model and the HPO ontology are baked into the Docker image at build time, so the first run doesn't trigger a multi-GB download mid-transcription.

### Web app (Docker — recommended for non-developers)

First time only — build the image (20–30 min, mostly downloading Whisper + Torch):

```bash
docker build -t phenoscribe .
```

Every time — start the app:

```bash
docker run --rm -p 127.0.0.1:7860:7860 \
  -e PHENOSCRIBE_INPUT_DIR=/data/recordings \
  -e PHENOSCRIBE_OUTPUT_DIR=/data/output \
  -v "$(pwd)/data/recordings:/data/recordings:ro" \
  -v "$(pwd)/output:/data/output" \
  phenoscribe
```

The `127.0.0.1:` prefix on `-p` keeps the published port reachable only from this machine — nobody else on your network can browse to it.

Then:

1. Open <http://localhost:7860> in your browser.
2. The files you put in `data/recordings/` show up as checkboxes. Tick the ones you want to process. (Added new files after the app started? Click **Refresh file list**.)
3. Pick your LLM provider, paste your API key in the password box, choose the language.
4. Click **Run**. Progress bar shows which file is being processed.
5. When it finishes, open `output/results.xlsx` on your machine. There's also a download button in the browser. `output/filename_mapping.json` tells you which original filename each `pt-…` id came from.

### CLI

```bash
uv pip install -e .

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
`hp/releases/2026-02-16`, the Dockerfile downloads the matching `v2026-02-16`
tag, and the seeded ChromaDB index is built from that obo. Startup verifies the
`data-version:` header of the on-disk obo against `hpo.release` and fails loudly
on a mismatch, so build host and run host can't disagree. Every results workbook
carries the release on a `Provenance` sheet, so any output is self-describing.

Retired codes resolve forward: an obsolete term with `replaced_by:`, or a merged
id carried as `alt_id:`, maps to its active id when loading ground truth and when
matching. On Marc Jamoulle's ground truth, 27 of 332 distinct codes were retired
in this release; 24 now resolve to an active id instead of dropping out of search.

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

Ground truth: 1003 manually coded rows. Scoring is hierarchy-aware via [hpo-toolkit](https://pypi.org/project/hpo-toolkit/): exact match = 1.0, one hop = 0.75, two hops = 0.5. Sibling matches via a shared parent get credit; the earlier hand-rolled walker scored those at zero.

Run `phenoscribe validate <results.xlsx> <ground_truth.xlsx>` to score a batch.

## Where to read more

- **`context/architecture-notes.md`** — live design reference. The first thing to read if you want to understand or modify the pipeline.
- **`context/exports/`** — formal documents shared with stakeholders (ADR, client report, ontoGPT benchmark).
- **`context/shipped/`** — completed implementation plans, dated chronologically.
- **`context/plans/`** — work currently in flight.

## License

Apache 2.0
