# Phenoscribe

Automated HPO phenotype coding from patient interviews. Built for long-COVID research in
general practice, where about 150 recordings were coded by hand.

> **Never used a terminal?** Follow **[INSTALL.md](INSTALL.md)**: download the ZIP, double-click the start file, paste your key, drop your files.

## What it does

A recorded doctor–patient conversation goes in. An Excel file of standard HPO codes comes
out. Six steps:

1. **Transcribe** the recording with Whisper large-v3, on the laptop.
   - mlx on the GPU of an Apple Silicon Mac, faster-whisper on the CPU elsewhere (`transcription.backend: auto`).
   - The audio file never leaves the machine.
   - A `.txt` or `.docx` transcript skips this step.
2. **Remove personal details** on the laptop, then let a person check them.
   - Privacy models for each language (French, Dutch, English) plus regex find names, places, dates, phones, e-mails and national IDs.
   - Each value gets a stable placeholder at every place it appears: "Dr. Martin" → "PERSON_1", "Bruxelles" → "LOCATION_1".
   - The web app approves each file with no blocking warning sign (`review.blocking_warnings`). A blocking sign is a word after a title, a first name or town left in the clean text, or a low-score removal when only one model ran.
   - The user checks the doubtful files: untick wrong removals, add missed names, keep dates, ages, gender or job titles, approve (`review.py`).
   - Only approved clean text goes to the LLM.
   - This step is also a tool on its own: clean texts plus a counts report, with no LLM.
   - See [Personal details](#personal-details) for the models, the numbers and what the removal misses.
3. **Extract symptoms** with an LLM. Each complaint becomes a clinical English label plus the patient's own words.
   - The prompts have rules and worked examples for French and English interviews (`transcription.language`, or the language menu of the page).
   - Dutch, German, Spanish and Italian run the same rules with no examples. These languages are not validated.
   - French is the benchmarked language. For English, only unit tests and one live test on a synthetic interview (`tests/test_english.py`) exist. No English benchmark number exists.
4. **Shortlist ten HPO candidates** for each symptom from a local ChromaDB index of about 19,400 HPO terms.
5. **Pick the best code** from that shortlist with an LLM. The term name comes again from the official ontology, so the LLM cannot put a wrong name on a valid code.
6. **Write the Excel file**.
   - By default one row for each finding: term, code, present/absent, modifiers, the patient's words, review flag, first and last mention as a % of the interview, number of mentions.
   - A "Mentions" sheet has one row for each place a finding comes up, in interview order (`timeline.py`). It gives the position % (0 = start, 100 = end, spoken text only), the time from the `[mm:ss]` markers, the mention number and the order of first appearance.
   - Whisper transcripts have a `[mm:ss]` marker before each segment.
   - `output.format: semicolon` gives one row for each patient: `Term (HP:code) [patient verbatim]; …`.

`phenoscribe aggregate` gives a cohort summary: the count of patients for each HPO term, plus a horizontal bar chart.

## Privacy

- The audio and the placeholder table never leave the machine.
- Only approved clean text goes to the LLM provider. A person checked it, or the page approved it because it shows no blocking warning sign. The report records which.
- The CLI sends unchecked clean text only with `--no-review`.
- The removal can miss details (see [Personal details](#personal-details)).
- The raw transcripts and the review files (`review/`, with the real names) stay on the machine.
- Designed under the GDPR and Belgian health-data law.

## Personal details

The removal of personal details is automatic and can miss a name. Phenoscribe shows you the files that it doubts. Open a file in the review if you want to check it.

- **What goes to the cloud provider** (OpenAI or Anthropic): the clean text only, with each removed value replaced by a placeholder (`PERSON_1`, `LOCATION_1`). The audio, the raw transcript and the placeholder table stay on the computer.
- **Languages**: French, Dutch and English each have their own model plus a multilingual model. German, Spanish and Italian get the multilingual model only. The language menu of the page picks the privacy models (`pii.models` in `config.yaml`).
- **Two layers**, both on the computer: the privacy models (all spans of all models of the language are removed), and regex for dates, phones, e-mails and Belgian national numbers in every language.
- **The leak check**: after the removal, the app reads the clean text again. A capitalized Belgian first name or town inside a sentence ("Claire", "Namur"), a capital after a removed name ("PERSON_1 Peeters") or after a family-name particle ("Van den Broeck") gives the warning sign "Possibly left". Lower-case words ("peau rose", "geel", "spa") and disease eponyms ("maladie de Crohn") give no sign.

### Numbers

Measured on 2026-11-18 with `scripts/eval_pii.py` on **5 synthetic interviews for each language** (`tests/fixtures/pii`, written and frozen before any model ran). The script counts what the pipeline removes: each found value at every place in the text. Five interviews is a small check: a recall of 1.0 here does not mean that every name is found in real interviews. Some rules were set after a first run on the same interviews (title words stay in the text, Belgian fixed-line phones, merged spans), so the numbers are a little optimistic.

| Language | Models | Recall (all details) | Recall (names) | Words removed by mistake | Clean files sent to a person |
|---|---|---|---|---|---|
| French | CamemBERT PII + GLiNER | 0.905 (38/42) | 0.944 | "clinique", "hôpital", "rue", "pharmacie", 2 house numbers, and 2 disease eponyms ("Crohn", "Raynaud") | 0 of 2 |
| Dutch, with DEDUCE | GLiNER + DEDUCE | 1.0 (49/49) | 1.0 | "de", "van", "in", "de", and "geel" (yellow), "Spa" (the water) | 0 of 5 |
| Dutch, no DEDUCE | GLiNER | 0.939 (46/49) | 0.905 | "in", "de", and "geel" (yellow) | 2 of 2 |
| English | Stanford de-identifier + GLiNER | 1.0 (47/47) | 1.0 | "university", "Marolles", "Hello", "Tuesday", 1 house number, and "rose" (a colour) | 0 of 5 |

- Dutch has less protection without the optional DEDUCE install: GLiNER alone missed 2 first names and 1 street. Each of the 3 files with a miss stops: one by the leak check, two by "Unsure about", which blocks when only one model ran.
- French misses: a first name that is also a word ("Rose"), two towns ("Rochefort", "Jambes") and one street. Each of the 3 files with a miss gets a blocking sign and stops.
- A value is removed at every place, in any case. So the town "Geel" also removes "geel" (yellow), and the name "Rose" also removes "rose" (a colour).
- "Clean files sent to a person": files with every detail removed that still get a blocking sign, so they wait for a person on the page. With one model (Dutch, no DEDUCE), the sign was "Unsure about" (a low-score removal).
- A model alone gives less: French CamemBERT PII 0.643, GLiNER 0.857; Dutch GLiNER 0.939, DEDUCE 0.694; English Stanford 0.936, GLiNER 0.936. The full output is in `benchmarks/pii/pii_eval_2026-11-18.json`.

### Which files wait for a person

The page and batch mode (`--no-review`) use the same rule (`review.blocking_warnings`).

| Warning sign | Meaning | Page and batch mode |
|---|---|---|
| Possibly left (leak check) | A name or a town can be in the clean text | Stops |
| Possibly missed (a word after a title) | A name can be in the clean text | Stops |
| Unsure about (low score), two models ran | The value is removed already, and a second model checked the text | Continues |
| Unsure about (low score), one model ran | No second model checked the text (Dutch without DEDUCE, German, Spanish, Italian) | Stops |
| No personal detail found | Nothing to remove | Continues |

A stopped file gets a clear message and does not go to the cloud. A file with "Possibly left" or "Possibly missed" goes to a cloud provider only after a person approved it on the page (`pipeline.code_review`). "Approve all the rest" skips it.

### Models and licences

| Model | Licence | Published test |
|---|---|---|
| [`Anonym-IA/V2-camembert-ner-pii-french`](https://huggingface.co/Anonym-IA/V2-camembert-ner-pii-french) | MIT | None. The model card gives a self-reported micro-F1 of 0.933. It is trained mostly on legal documents. |
| [`urchade/gliner_multi_pii-v1`](https://huggingface.co/urchade/gliner_multi_pii-v1) | Apache-2.0 | The GLiNER method has a paper. This PII version has no published test. |
| [`StanfordAIMI/stanford-deidentifier-base`](https://huggingface.co/StanfordAIMI/stanford-deidentifier-base) | MIT | JAMIA 2022, on US radiology reports and clinical notes. |
| [DEDUCE](https://github.com/vmenger/deduce) (Dutch rules, optional) | GPL-3.0 | 2018 paper on Dutch clinical notes (recall 0.916). No number for version 3. |
| [`Jean-Baptiste/camembert-ner`](https://huggingface.co/Jean-Baptiste/camembert-ner) (French fallback, offline) | MIT | WikiNER F1 0.89 (model card). |
| First names and towns of the leak check: [Statbel](https://statbel.fgov.be/en/open-data/first-names-total-population-municipality-16), 2026 | CC BY 4.0 | Source and changes in the first line of each file in `src/phenoscribe/resources/`. |

No model was trained for Phenoscribe. No model has a test on Belgian interview transcripts: the synthetic test set above is the only check.

**DEDUCE** (Dutch rules, UMC Utrecht) has a GPL-3.0 licence (`LICENSE.md` on GitHub; PyPI says LGPL-3.0), so it is never a default dependency of this MIT package. It is an optional install: `uv sync --extra dutch-rules`. The Dutch map names it, and it runs only when it is installed. Without it, Dutch gets GLiNER only (see the numbers above).

**Not used**: the OpenMed Dutch PII models are Apache-2.0, but their training data ([`ai4privacy/pii-masking-400k`](https://huggingface.co/datasets/ai4privacy/pii-masking-400k)) is for academic, non-commercial use only. On the Dutch test set, OpenMed alone gave 0.837 and GLiNER + OpenMed 1.0.

**Open point, French model**: the model has the MIT licence, but its training data ([`TypicaAI/pii-masking-60k_fr`](https://huggingface.co/datasets/TypicaAI/pii-masking-60k_fr)) has no licence tag (checked on 2026-11-18). Phenoscribe does not ship the model: `phenoscribe setup` downloads it from Hugging Face. We keep it because it adds recall: French with GLiNER alone gives 0.857, and with both models 0.905. If you need a clear licence chain, remove it from `pii.models.fr` in `config.yaml`.

## Ways to run

### Requirements

- An **API key** from OpenAI or Anthropic. About $0.01 per recording.
- **About 12 GB free disk space** (the Whisper speech model, 3 GB, and the privacy models, 2.1 GB).
- Windows x64, Linux, or an Apple Silicon Mac. `uv.lock` has no torch wheel for Intel Macs. The Windows start file is not tested on Windows yet.
- For the CLI: `uv`. It installs Python 3.12 (`.python-version`) and the exact versions in `uv.lock`.

### Web app (start file, recommended)

Double-click `Start Phenoscribe.bat` (Windows) or `Start Phenoscribe.command` (Mac; `sh` on Linux).
The script installs `uv` 0.6.17 if it is missing, runs `phenoscribe setup`, and opens the page on
<http://127.0.0.1:7860>. When port 7860 is busy, the page takes the next free port; set `GRADIO_SERVER_PORT` to choose one. Drop files or choose a folder, choose a tool, then click **Start**. The
app decides the work for each file (`plan_batch` in `batch.py`):

- Audio is transcribed.
- A `.txt` or `.docx` is read as it is.
- A saved transcript of the same audio bytes is used again
  (`transcripts/by-audio/<sha256>.txt`, keyed by the bytes, backend, model and language).

The run has two stages with a review between them. `prepare_batch` transcribes and finds the
personal details, and saves `review/<patient_id>.json`. The user checks and approves each file.
Then `code_batch` codes the approved files, or `finish_clean` makes a ZIP of the clean texts
and a `personal_details_*.xlsx` report.

Each coding run writes a new `results_YYYY-MM-DD_HHMM.xlsx` with a "Personal details" sheet
(counts only) to the folder `PHENOSCRIBE_OUTPUT_DIR`. The start files set it to
`Documents/Phenoscribe results`; without it, the page uses
`output` in the current folder. The same folder holds
`transcripts/`, `review/`, `clean/`, `phenoscribe.log`, and `filename_mapping.json` (each
hashed `pt-…` ID back to its filename). A file named only by a
number (`001.mp3`) gets that number as its ID, with the prefix `patient.id_prefix` in front.

`phenoscribe setup` downloads the pinned `hp.obo`, builds the ChromaDB index, and
downloads the Whisper model and the privacy models. It skips each part that is present, so it
can run at every start. The transcription backend `auto` picks mlx on Apple Silicon when `ffmpeg`
exists, else faster-whisper on the CPU. A new Mac has no `ffmpeg` (`brew install ffmpeg`),
so it uses the CPU path, about 4x slower, until `ffmpeg` is installed.

### CLI

```bash
uv sync --frozen && uv run phenoscribe setup

# Code every audio file and transcript in data/recordings/ into a new
# output/results_YYYY-MM-DD_HHMM.xlsx. Each file waits for a review in the page
# first; --no-review sends the clean text to the LLM with no person to check it.
uv run phenoscribe process data/recordings/ --output output --no-review

# Remove personal details only (no LLM): clean texts, a report and a ZIP in output/
uv run phenoscribe clean data/recordings/ --output output

# Build a cohort summary (CSV + bar chart) from an existing results file
uv run phenoscribe aggregate output/results_2026-11-18_1405.xlsx --top 30

# Count the reviews of the output folder (needs review, approved, done)
# and name the files that wait
uv run phenoscribe status --output output
```

`process` and `clean` read the files of the folder with the same rules as the page (`plan_batch`). They print each skipped file with the reason.

`process` prints one line for each file: done with the number of codes, waits for a review, or failed with the error. `clean` prints the removed details of each file, or the error.

The exit code of `process` and `clean` is 1 when a file failed. A file that waits for a review (or, for `clean`, stays out of the ZIP because of a blocking warning sign) is no failure, so the exit code stays 0.

The console shows the progress lines and the warnings. Add `--verbose` to see each step of the log too.

A second run writes a new results file. A saved transcript of the same audio is used again, so a second run with another LLM provider or model does not run Whisper again.

## Configuration

`config.yaml` controls the LLM provider, Whisper model, language, output format, and paths. A missing file gives the same values as the shipped file. An unknown key or a value outside its choices stops the app with one line that names it, for example `config.yaml: unknown key llm.provder; known keys: …`.

Three LLM providers:

- `openai` (default)
- `anthropic`
- `ollama` (a local model, for offline use; CLI only)

Each provider takes any of its chat models: set `llm.model`.

API keys come from the environment (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) or from the key box on the web page. With **Remember this key in this browser** ticked, the browser keeps the key in its local storage. The app never writes a key to disk. The web page offers OpenAI and Anthropic and takes the model from `config.yaml`.

### HPO release

One HPO release is used everywhere. `hpo.release` in `config.yaml` names it
(`hp/releases/2026-02-16`).

- `phenoscribe setup` downloads the matching file (tag `v2026-02-16`) and builds the index from it.
- At start, the app compares the `data-version:` line of `hp.obo` with `hpo.release`. When they differ, the app stops with an error.
- Every results workbook names the release on its `Provenance` sheet.

An obsolete HPO code moves to its active code. This applies to a term with `replaced_by:`
and to a merged code kept as `alt_id:`. It applies when the app reads a ground truth and when
it matches. In one clinician's manual code lists, 27 of 332 different codes were obsolete in
this release. 24 of them move to an active code.

### Embedding model

The search index turns each HPO term into a vector with an encoder.
`hpo_index.embedding_model` in `config.yaml` picks the encoder:

- `default`: ChromaDB's built-in all-MiniLM-L6-v2.
- `sapbert`: SapBERT, a biomedical encoder.

The index stores the name of its encoder. The two encoders give different vectors, so an index
works only with its own encoder. When `config.yaml` names another encoder, the search stops
with the error `EmbeddingModelMismatch`.

To use SapBERT:

1. Set `hpo_index.embedding_model: sapbert` in `config.yaml`.
2. Run `phenoscribe setup`. It builds the index again with the encoder of `config.yaml`.

## Project layout

```
src/phenoscribe/          Pipeline source
  __init__.py             Package marker and __version__ (from pyproject.toml)
  transcribe.py           Step 1: Whisper (mlx or faster-whisper, backend auto), or read a .txt or .docx
  speakers.py             Who says each line: doctor or patient (an LLM pass)
  pii.py                  Step 2: privacy models for each language + regex
  review.py               Step 2: the review (untick, add, keep types, approve), warning signs, clean text
  extract_symptoms.py     Step 3: LLM symptom extraction
  modifiers.py            Frequency, onset and severity to HPO codes
  embeddings.py           Encoders of the search index (default, SapBERT)
  hpo_index.py            Step 4: HPO parser, ChromaDB index and search
  match_hpo.py            Step 5: LLM choice of the code + canonical name
  findings.py             One finding: its keys, its Excel row key, its status
  ontology.py             HPO graph and ancestor closure (true-path rule)
  icpc.py                 ICPC-3 and ICPC-2 codes for an HPO code
  timeline.py             Where each finding comes up: position %, time, order
  output.py               Step 6: the results workbook (three formats, Mentions, Personal details)
  aggregate.py            Cohort counts + bar chart
  semantic_similarity.py  Information content and Lin/Resnik similarity
  validate.py             Score a results file against a ground truth
  text.py                 Text helpers shared by the steps (fold, timestamps, titles)
  llm.py                  One call interface for OpenAI, Anthropic and Ollama
  config.py               config.yaml loader
  pipeline.py             The steps for one file: read_transcript, find_details, code_review
  batch.py                Many files: plan_batch, run_batch, prepare_batch, code_batch, finish_clean
  naming.py               Patient IDs, interview details, transcript cache and results file names
  cli.py                  argparse CLI (process, clean, setup, status, aggregate, validate)
  gui/                    Gradio web app: theme.py, views.py (HTML), handlers.py (events), app.py (page)

Start Phenoscribe.bat     Double-click start file (Windows)
Start Phenoscribe.command Double-click start file (Mac; `sh` on Linux)
scripts/                  Benchmarks, data builds, CI smoke run (see scripts/README.md)
benchmarks/               Checksums and measured outputs
data/                     HPO ontology, ChromaDB, recordings (gitignored)
output/                   CLI output: Excel, transcripts, reviews, clean text
                          (the web app writes to Documents/Phenoscribe results)
tests/                    Unit + integration tests
```

## Validation

`phenoscribe validate <results.xlsx> <ground_truth.xlsx>` scores a batch against manual codes.
It prints:

- the precision, recall and F1 for each term, strict and with partial credit;
- the information content (IC) of the predicted terms, to show if general terms near the root dominate.

The partial credit uses the information content of the most informative common ancestor:
Resnik (1995) and Lin (1998), built from `phenotype.hpoa` with
[hpo-toolkit](https://pypi.org/project/hpo-toolkit/). Both come from the HPO release that
`hpo.release` in `config.yaml` pins, the same release as the search index. An exact ID match scores 1.0. A
prediction near the root scores about 0 against a specific truth, because the shared ancestor
carries almost no information. The strict F1 counts exact HPO ID matches only, as HPO
concept-recognition benchmarks do.

The IC scores are for validation only. The pipeline picks a code from the ChromaDB shortlist
with the LLM (steps 4 and 5). It does not rank on IC.

## Benchmarks

- `scripts/benchmark_gsc.py`: Phenoscribe on the GSC+ gold standard (Groza et al.), with
  FastHPOCR on the same 228 documents and the published GSC+ table for context. It gives the
  document-level precision, recall and F1, exact and near-hit, and the recall@k of the search.
  `scripts/fetch_benchmark_data.py` downloads the corpus and `phenotype.hpoa`.
  It checks `phenotype.hpoa` against `benchmarks/CHECKSUMS.txt`. No digest is recorded
  for the GSC+ corpus yet, so the script warns that this download is not verified.
- `scripts/dev/benchmark_ontogpt.py`: Phenoscribe and OntoGPT on the same clean
  French transcripts. It needs private data.

`scripts/README.md` lists every script.

## Development

```bash
uv sync
uv run phenoscribe setup --no-models          # hp.obo and the search index for the tests
uv run python -m pytest -m "not network and not live"
uv run ruff check
```

The test markers (`pyproject.toml`):

- `network`: the test downloads a model or data. The validation tests download the HPO
  release and its IC table once, to `~/.hpo-toolkit` and `~/.cache/phenoscribe`; they count
  as `network` until these caches exist.
- `live`: the test calls a paid LLM API. It skips without `ANTHROPIC_API_KEY` or
  `OPENAI_API_KEY`. Run them with `-m live`.
- `needs_hpo_data`: the test reads `data/hpo/hp.obo` or `data/chroma_db`. It skips until
  `uv run phenoscribe setup --no-models` has run.

`uv run python -m pytest --cov` adds a coverage table. `CONTRIBUTING.md` has the rules for
a prompt or privacy change.

## License

The Phenoscribe code is under the MIT licence. See `LICENSE`.

Third-party parts keep their own licences:

- **Human Phenotype Ontology.** Phenoscribe downloads the HPO release that `config.yaml` names. Every results workbook gives that release. Use of the HPO follows the [HPO licence](https://human-phenotype-ontology.github.io/license.html). Cite: Gargano MA et al. The Human Phenotype Ontology in 2024: phenotypes around the world. *Nucleic Acids Res.* 2024;52(D1):D1333-D1346.
- **ICPC-3.** WONCA owns the copyright of ICPC-3, which is available under CC BY-ND. Phenoscribe contains ICPC codes only, with no ICPC titles.
- **HPO to ICPC-3 table** (`src/phenoscribe/resources/hpo_icpc.csv`). The rows marked `hand` are the hand mapping by M. Jamoulle and K. van Boven of the HPO terms of Deer et al. (2021) to ICPC-3, published here with their agreement. The rows marked `draft` are ours and wait for a clinical review. The ICPC-2 codes come from the official ICPC-3 browser.
- **hpo-toolkit** is GPL-3. Phenoscribe imports it and does not include it. The release ZIP contains only Phenoscribe's own code; `uv` installs the libraries on the user's computer.

## Releasing

1. Set the new version in `pyproject.toml` (`project.version`) and in `CITATION.cff`
   (`version`, `date-released`), and add its entry to `CHANGELOG.md`.
2. Push a version tag: `git tag v0.2.0 && git push origin v0.2.0`.

The release job (`.github/workflows/release.yml`) runs the tests, stops when the tag differs
from `pyproject.toml` or `CITATION.cff`, and attaches `Phenoscribe.zip` to a GitHub release.
INSTALL.md points users to that download. The ZIP leaves out `tests/`, `benchmarks/`,
`scripts/dev/` and `.github/` (`export-ignore` in `.gitattributes`).
