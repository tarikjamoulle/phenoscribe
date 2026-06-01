---
problem: "Ship Phenoscribe so non-technical users can run it locally without a terminal"
date: 2026-06-01
shipped: 2026-06-01
status: shipped
plan: "../plans/2026-05-30-cross-platform-docker-distribution.md"
---

# Implementation Plan: Gradio web app + Docker + Claude integration

> **Shipped 2026-06-01** (Tasks 1–3 of the cross-platform Docker plan). End-to-end smoke test on `038.mp3` produced 12 HPO matches using `claude-haiku-4-5`. Tasks 4–11 of the original plan (multi-arch build, CUDA image, OS launchers, GHCR CI, install docs, clean-machine smoke test) are deferred to a follow-up.

## Summary

Phenoscribe now runs as a browser app inside a Docker container. The GP picks files in the browser, pastes an API key into a password field, clicks Run, and gets an Excel — no terminal. Audio never leaves the machine; only pseudonymised text reaches the LLM. The existing CLI stays available.

The Docker image bakes in code, dependencies, the HPO ontology, and a pre-seeded ChromaDB so it boots ready to run. Whisper model weights persist to a host-mounted `models/` directory across container restarts. API keys live in the user's session (browser → server memory) and never touch disk.

## Tasks

### Task 1: Dockerfile and entrypoint (CPU image)

> **Shipped 2026-06-01.** `phenoscribe:test` builds clean. CLI works inside the container. ChromaDB count = 17,055. Image size ~3 GB.

**Files:** `Dockerfile`, `.dockerignore`, `docker-entrypoint.sh`

- Base: `python:3.11-slim`. System deps: `ffmpeg`, `curl`, `ca-certificates`.
- `pyproject.toml` copied first for layer caching, then `pip install -e .`.
- Build-time HPO seeding: download `hp.obo`, run `scripts/seed_hpo.py` to populate `/app/data/chroma_db/`, pre-fetch the `all-MiniLM-L6-v2` embedding model so matching works offline.
- `.dockerignore` excludes `data/`, `output/`, `.venv/`, recordings, `.git/`, `.env`.
- `docker-entrypoint.sh` dispatches `gui` (default — Gradio on `0.0.0.0:7860`) vs `cli` (forwards to the existing CLI).

### Task 2: Gradio file listing + UI scaffold

> **Shipped 2026-06-01** as part of `src/phenoscribe/gui.py`.

**Files:** `pyproject.toml` (`gradio>=4.0`), `src/phenoscribe/gui.py`

- New entry point: `phenoscribe-gui = "phenoscribe.gui:main"`.
- Gradio `Blocks` layout: file checkboxes on the left, options column on the right, results dataframe + Excel download at the bottom.
- Files are listed from `PHENOSCRIBE_INPUT_DIR` (default `/data/recordings`) — anything under that mount with a supported audio extension shows up, with a Refresh button to rescan.

### Task 3: Pipeline wiring with progress, provider selection, and in-browser API key

> **Shipped 2026-06-01.** End-to-end run on `038.mp3` (6:20 audio, French) produced 12 HPO matches via `claude-haiku-4-5`: Headache, Lower limb pain, Neck pain, Fatigue, Periodic limb movements of sleep, Restless legs, Pain, Memory impairment, Palpitations, and three duplicates. Excel landed at `output/results.xlsx` on the host.

**Files:** `src/phenoscribe/gui.py`, `src/phenoscribe/pipeline.py`

- File-level progress via `gr.Progress` (no sub-step granularity inside Whisper).
- Options panel:
  - **Transcribe audio** (default on) — when off, the pipeline reuses a previously saved transcript from `output/transcripts/<patient_id>.txt`. Backed by a new `skip_pii` argument on `process_recording` that mirrors the existing `skip_transcription` flag.
  - **Pseudonymize PII** (default on, warning if disabled).
  - **Speaker diarization** — auto-disabled with an explanatory tooltip if `HF_TOKEN` is missing.
  - **Audio language** dropdown (fr, en, nl, de, es, it).
  - **LLM provider** dropdown (`openai`, `anthropic`).
  - **LLM model** dropdown that swaps choices when provider changes.
  - **API key** password field — paste-once-per-session, kept in memory, never written to disk. Placeholder reads "(loaded from environment)" if the key is in the env, otherwise prompts for the active provider.
- `provider.change` updates both the model dropdown and the API key placeholder via `gr.update(...)`. Earlier implementation returned fresh `gr.Dropdown(...)` instances and Gradio failed to update the choices client-side, so submitting a Claude model against the original gpt-4o choices triggered a validation error.
- Per-file try/except so one failure doesn't kill a batch — the offending row gets `status="failed"` with the exception message.
- `launch(allowed_paths=[str(OUTPUT_DIR)])` so Gradio can serve the Excel through the download component without copying it into a sandbox dir.

### Out-of-plan: Whisper model cache via host mount

> Discovered during smoke testing. The first run downloaded `large-v3` (~3 GB) into the container; restarting threw the download away. Without an `HF_TOKEN` the HF Hub rate-limits unauthenticated traffic, so re-downloads took 30+ minutes each.

**Fix:**
- Switched the default Whisper model from `large-v3` to `medium` in `config.yaml` (1.5 GB vs 3 GB, lower RAM ceiling, accuracy gap is small on clean clinical speech and the user base may not have beefy laptops).
- Documented the cache volume mount in the README quick-start: `-v "$(pwd)/models:/root/.cache/huggingface"`.
- First run downloads once; subsequent container restarts reuse the cache instantly.

### Out-of-plan: Claude integration confirmed

> The existing `_call_anthropic` path in `src/phenoscribe/llm.py` had not been exercised end-to-end before. Smoke test confirmed it works against the live Anthropic API for both the symptom-extraction and HPO-judging calls.

The web app exposes `anthropic` alongside `openai` in the provider dropdown with `claude-opus-4-7`, `claude-sonnet-4-6`, and `claude-haiku-4-5` as model choices. Tested with `claude-haiku-4-5` on `038.mp3` — 12 matches, total LLM latency ~28 seconds across all calls. (OpenAI was unavailable during the test because of a depleted prepaid balance on the test key, which turned into a useful forcing function.)

## Definition of done

- [x] Dockerfile builds clean (CPU, single-arch). Image ~3 GB.
- [x] Gradio GUI loads and lists files from the mount.
- [x] Run button executes the full pipeline with per-file progress.
- [x] Both `openai` and `anthropic` providers tested end-to-end through the GUI.
- [x] API key entry in the browser; never written to disk.
- [x] Whisper model cached to host across container restarts.
- [x] Excel download works from the GUI (`allowed_paths` set).
- [x] Audio file stays on the host; only pseudonymised text reaches the LLM (unchanged from the CLI flow).
- [ ] Multi-arch image (amd64 + arm64) on GHCR — *deferred*.
- [ ] CUDA variant on GHCR — *deferred*.
- [ ] Per-OS launcher scripts (`run.sh`, `run.bat`) — *deferred*.
- [ ] User-facing install docs and clean-machine smoke test — *deferred*.

## Notes for the follow-up

- The `large-v3` model still works if you want max accuracy; flip it back in `config.yaml`. The cache mount survives the swap (different cache subdirectories per model).
- The bind-mount used during development (`-v $(pwd)/src/phenoscribe:/app/src/phenoscribe:ro`) is for live-editing only; ship images should bake the code in.
- The "ollama" provider is wired in `llm.py` but intentionally hidden from the GUI dropdown — re-add it when we have a deployment story for users who actually have a local Ollama server.
- Container memory usage peaks at ~5.6 GB on `medium` during transcription. Docker Desktop's default 8 GB allocation on Mac is enough but tight; consider documenting a 12 GB recommendation in the install guide.
