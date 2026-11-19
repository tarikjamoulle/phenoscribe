# Changelog

All notable changes to Phenoscribe are in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the version numbers follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-18

First public release.

### Added

- Transcription of recorded doctor-patient interviews with Whisper, on the computer. A `.txt` or `.docx` transcript skips this step.
- Removal of personal details on the computer: privacy models for French, Dutch and English, a multilingual model, and regex for dates, phones, e-mails and Belgian national numbers.
- A leak check that reads the clean text again and flags a first name or a town that stays in it.
- A review page where a person checks the doubtful files before any text goes to an LLM.
- Symptom extraction with an LLM (OpenAI, Anthropic or a local Ollama model), and a choice of HPO code from a local search index of the HPO terms.
- An Excel results file: one row for each finding, a "Mentions" sheet, a "Personal details" sheet with counts, and a "Provenance" sheet that names the HPO release and the models.
- ICPC-3 and ICPC-2 codes for each HPO code that has a mapping.
- The commands `process`, `clean`, `setup`, `status`, `aggregate` and `validate`.
- A start file for macOS, a release ZIP, and a Docker image.

### Changed for users of a pre-release copy

- `phenoscribe process --output` takes a folder. Each run writes a new `results_YYYY-MM-DD_HHMM.xlsx` in it.
- The flags `--retry-failed` and `--skip-transcription` are removed.
- The config keys `output.path`, `paths.jobs_db` and `pii.model`, and the section `diarization`, are removed. An old `config.yaml` with one of them stops with a message that names the key.
- `phenoscribe setup` builds the search index again one time.
- The default `patient.id_prefix` is empty, so `001.mp3` gets the patient ID `001`.
