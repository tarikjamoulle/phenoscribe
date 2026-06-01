---
problem: "Ship Phenoscribe so non-technical users on Windows, macOS, or Linux can run it locally — patient data never leaves the machine"
date: 2026-05-30
status: planned
---

# Implementation Plan: Cross-platform Docker distribution with browser GUI

## Summary

Package Phenoscribe as a Docker image plus per-OS launcher scripts (`run.sh`, `run.bat`). The image bakes in code, dependencies, the public HPO ontology, and a pre-seeded ChromaDB so first-run is functional immediately. The Whisper model downloads on first run to a mounted cache volume (~3 GB, persisted). A Gradio web GUI runs inside the container; launchers open the user's browser to `http://localhost:7860`. The existing CLI stays available via a separate launcher for batch use.

Distribution via GHCR (free, no separate account, public). Launchers auto-pull the latest image on each launch so updates are transparent. Multi-arch image (linux/amd64 + linux/arm64) so Apple Silicon Macs run natively. A separate `:cuda` tag for users with NVIDIA GPUs (Windows via WSL2 backend or Linux with nvidia-container-toolkit).

Patient data, API keys, and the Whisper cache all live in host-mounted folders next to the launcher. Nothing patient-related ever enters the image.

## Tasks

### Task 1: Dockerfile and entrypoint (CPU image)

**Files:** `Dockerfile`, `.dockerignore`, `docker-entrypoint.sh`

- Base image: `python:3.11-slim` (CPU, multi-arch ready).
- Install system deps: `ffmpeg` (audio decoding), `curl`, `ca-certificates`.
- Copy `pyproject.toml` first, `pip install -e .` to leverage layer caching.
- Copy `src/`, `scripts/`, `config.yaml`.
- During build:
  - `curl -L https://purl.obolibrary.org/obo/hp.obo -o /app/data/hpo/hp.obo`
  - `python scripts/seed_hpo.py` — populates `/app/data/chroma_db/`
  - Pre-fetch the ChromaDB embedding model: `python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; DefaultEmbeddingFunction()"` (downloads `all-MiniLM-L6-v2` into the image so the matching step works offline)
- `.dockerignore`: exclude `data/`, `output/`, `.venv/`, `tests/fixtures/recordings/`, `.git/`, `*.db`, `setup.sh`, `.env`.
- `docker-entrypoint.sh`: dispatch — first arg `gui` → launch Gradio on `0.0.0.0:7860`; first arg `cli` → run `phenoscribe <rest>`; default (no args) → `gui`.

**Verify:**
```bash
docker build -t phenoscribe:test .
docker run --rm phenoscribe:test cli --help
docker run --rm phenoscribe:test python -c "import chromadb; c = chromadb.PersistentClient('/app/data/chroma_db'); print(c.get_collection('hpo_terms').count())"
docker images phenoscribe:test --format '{{.Size}}'
```
**Expect:** Build succeeds. CLI help prints. ChromaDB count is ~17000. Image size ≤ 3 GB.
**Depends on:** —

---

### Task 2: Minimal Gradio app (file listing only)

**Files:** `pyproject.toml` (add `gradio>=4.0`), `src/phenoscribe/gui.py`

- New script entry: `phenoscribe-gui = "phenoscribe.gui:main"`.
- Minimal Gradio `Blocks` app:
  - Header "Phenoscribe"
  - Refresh button that lists files in `/app/input` (filtered by `SUPPORTED_EXTENSIONS` from `cli.py`)
  - Display as a `gr.Dataframe` showing filename + size
- Bind to `0.0.0.0:7860`, no auth (localhost-only via Docker port forward).
- No pipeline wired yet — just proves the GUI loads and sees mounted files.

**Verify:**
```bash
docker build -t phenoscribe:test .
docker run --rm -d --name pheno-test -p 7860:7860 -v "$(pwd)/data/recordings:/app/input" phenoscribe:test gui
sleep 5
curl -s http://localhost:7860 | grep -q "Phenoscribe" && echo OK
docker stop pheno-test
```
Then open `http://localhost:7860` in a browser, click Refresh, confirm files from `data/recordings/` appear.
**Expect:** `OK` printed. Browser shows file list matching local folder contents.
**Depends on:** Task 1

---

### Task 3: Wire Gradio to the pipeline with options and live progress

**Files:** `src/phenoscribe/gui.py`

- Add UI controls:
  - File selection: `gr.CheckboxGroup` populated from `/app/input` scan
  - Checkboxes: `Transcribe audio` (default on; auto-disabled for `.txt` inputs), `Pseudonymize PII` (default on, warn if disabled), `Speaker diarization` (default off, disabled if `HF_TOKEN` env var missing — show a tooltip explaining why)
  - Dropdowns: `Language` (fr, en, …), `LLM provider/model` (read available from config + env presence of API keys)
  - `Run` button
- On click: iterate selected files, call `process_recording()` for each, push progress via `gr.Progress` (per-file step labels: Transcribing / Pseudonymizing / Extracting / Matching / Writing).
- Results panel: `gr.Dataframe` of (file, status, n_codes, error_msg if failed) + `gr.File` linking the output Excel.
- Errors caught per-file so one failure doesn't kill the batch (mirrors current CLI behavior).
- Honour `--skip-transcription` semantics: if "Transcribe audio" unchecked and a transcript exists in `/app/output/transcripts/`, use it; otherwise show a clear error.

**Verify:**
```bash
# put one short test file in data/recordings/ first — e.g. a 30s .txt or .wav
docker run --rm -d --name pheno-test \
  -p 7860:7860 \
  -v "$(pwd)/data/recordings:/app/input" \
  -v "$(pwd)/output:/app/output" \
  -v "$(pwd)/models:/root/.cache" \
  --env-file .env \
  phenoscribe:test gui
```
Open `http://localhost:7860`, select the test file, click Run, watch progress, confirm Excel downloads.
```bash
ls output/results.xlsx && docker stop pheno-test
```
**Expect:** Progress bar advances through 6 steps. Results row shows ≥1 HPO code. `output/results.xlsx` exists on host.
**Depends on:** Task 2

---

### Task 4: Multi-arch build (amd64 + arm64) for Apple Silicon support

**Files:** `Dockerfile` (audit for arch-specific deps), `Makefile` (or `scripts/build-multiarch.sh`)

- Confirm `pyproject.toml` deps have arm64 wheels: `torch`, `chromadb`, `pyannote.audio`, `faster-whisper`. (Most do post-2024; spot-check via `pip download --platform manylinux2014_aarch64 …` if uncertain.)
- Add a `Makefile` target:
  ```makefile
  build-multiarch:
  	docker buildx create --use --name phenoscribe-builder 2>/dev/null || true
  	docker buildx build --platform linux/amd64,linux/arm64 \
  	  -t ghcr.io/<owner>/phenoscribe:latest --push .
  ```
- Local sanity build (single arch, `--load` works):
  ```makefile
  build-local:
  	docker buildx build --platform linux/$(shell uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/') \
  	  -t phenoscribe:local --load .
  ```

**Verify (on Apple Silicon Mac):**
```bash
make build-local
docker run --rm phenoscribe:local python -c "import platform; print(platform.machine())"
```
**Expect:** Prints `aarch64` (native arm64 build, no Rosetta emulation warning).
**Depends on:** Task 1

---

### Task 5: CUDA variant image for NVIDIA GPU users

**Files:** `Dockerfile.cuda`

- Base: `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04`
- Install Python 3.11 + same system deps as CPU image.
- Same `pip install -e .` but force CUDA torch: `pip install torch --index-url https://download.pytorch.org/whl/cu124` before the editable install.
- Same build-time HPO seeding + embedding model pre-fetch.
- Same entrypoint.
- amd64-only (NVIDIA CUDA images don't ship arm64).

**Verify (on a Linux box with NVIDIA GPU and nvidia-container-toolkit):**
```bash
docker build -f Dockerfile.cuda -t phenoscribe:cuda-test .
docker run --rm --gpus all phenoscribe:cuda-test python -c "import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))"
```
**Expect:** Prints GPU name. No `CUDA not available` error.
**Depends on:** Task 1

If no GPU machine available locally, defer verification to CI (Task 9 builds it; smoke-test happens when a contributor with a GPU pulls and runs).

---

### Task 6: macOS / Linux launcher (`run.sh`)

**Files:** `run.sh`, `.env.example`

`run.sh` does, in order:
1. Refuse to run if `docker` not on PATH → print clear install link per OS detected.
2. Refuse if `.env` missing → copy `.env.example` to `.env` and tell user to fill it in.
3. Create local folders if missing: `input/`, `output/`, `models/`.
4. `docker pull ghcr.io/<owner>/phenoscribe:latest` (auto-update).
5. Detect GPU: `command -v nvidia-smi && nvidia-smi -L >/dev/null 2>&1` → set `GPU_FLAGS="--gpus all"` and use `:cuda` tag; otherwise empty flags and `:latest` tag.
6. `docker run --rm -d --name phenoscribe -p 7860:7860 $GPU_FLAGS -v "$(pwd)/input:/app/input" -v "$(pwd)/output:/app/output" -v "$(pwd)/models:/root/.cache" --env-file .env <image> gui`
7. Wait for port 7860 to respond (loop with timeout).
8. Open browser: `open` (macOS) or `xdg-open` (Linux).
9. `trap` cleanup: on Ctrl+C, `docker stop phenoscribe`.

`.env.example`:
```
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
HF_TOKEN=
```

**Verify (macOS):**
```bash
rm -rf input output models .env
./run.sh
# Expect: prompted to fill .env, run again
cp .env.example .env && echo "OPENAI_API_KEY=sk-..." >> .env
./run.sh
# Expect: pull progress, browser opens to localhost:7860 with GUI
# Drop a test file in ./input/, refresh in browser, see it listed
```
**Expect:** Browser opens automatically. GUI loads. Mounted folders correctly visible.
**Depends on:** Task 3, Task 9 (registry must be populated)

For pre-Task-9 testing, swap step 4's `pull` for a local `docker tag phenoscribe:test ghcr.io/<owner>/phenoscribe:latest`.

---

### Task 7: Windows launcher (`run.bat`)

**Files:** `run.bat`

Same logic as `run.sh`, Windows-native:
1. `where docker >nul 2>nul || (echo Install Docker Desktop: https://www.docker.com/products/docker-desktop & pause & exit /b 1)`
2. `if not exist .env ( copy .env.example .env & echo Edit .env then re-run. & pause & exit /b 1 )`
3. `if not exist input mkdir input` (same for output, models)
4. `docker pull ghcr.io/<owner>/phenoscribe:latest`
5. GPU detect: `where nvidia-smi >nul 2>nul && set GPU_FLAGS=--gpus all && set TAG=cuda || set TAG=latest`
6. `docker run --rm -d --name phenoscribe -p 7860:7860 %GPU_FLAGS% -v "%cd%\input:/app/input" -v "%cd%\output:/app/output" -v "%cd%\models:/root/.cache" --env-file .env ghcr.io/<owner>/phenoscribe:%TAG% gui`
7. Wait for port (PowerShell one-liner or `:loop / ping -n 2 / curl / goto`).
8. `start http://localhost:7860`
9. `pause` at end so the window stays open; on close, `docker stop phenoscribe`.

**Verify (Windows 10/11 with Docker Desktop installed):**
- Double-click `run.bat`. Console shows pull progress, then browser opens to GUI.
- Drag a folder of audio files onto `input/`, refresh in GUI, confirm listed.
- Close console window → confirm container stopped: `docker ps` shows nothing.

If no Windows machine handy, syntax-check via `cmd /c run.bat` in a Windows VM, or static review (the script is short).

**Depends on:** Task 3, Task 9

---

### Task 8: CLI passthrough launchers

**Files:** `run-cli.sh`, `run-cli.bat`

Thin wrappers that forward args to the CLI inside the container — for your batch / automation use, not the stakeholder. Same volume mounts, no port forward, no browser, no `-d` (foreground so logs stream to terminal).

Example `run-cli.sh`:
```bash
#!/bin/bash
docker run --rm -it \
  -v "$(pwd)/input:/app/input" \
  -v "$(pwd)/output:/app/output" \
  -v "$(pwd)/models:/root/.cache" \
  --env-file .env \
  ghcr.io/<owner>/phenoscribe:latest cli "$@"
```

**Verify:**
```bash
./run-cli.sh process /app/input --output /app/output/results.xlsx
```
**Expect:** Existing CLI behavior, output appears in `./output/`.
**Depends on:** Task 6

---

### Task 9: GitHub Actions — multi-arch build + push to GHCR

**Files:** `.github/workflows/docker.yml`

Trigger: push to `main`, push of a `v*` tag, manual `workflow_dispatch`.

Two jobs:
1. **`build-cpu`** — `docker/setup-qemu-action` + `docker/setup-buildx-action` + `docker/login-action` (GHCR via `GITHUB_TOKEN`) + `docker/build-push-action` with `platforms: linux/amd64,linux/arm64`, tags `latest` and `v<ref>` if tag push.
2. **`build-cuda`** — same but `file: Dockerfile.cuda`, `platforms: linux/amd64`, tags `cuda` and `cuda-v<ref>`.

Cache via `cache-from: type=gha` / `cache-to: type=gha,mode=max` so subsequent builds aren't full rebuilds.

**Verify:**
```bash
git commit --allow-empty -m "ci: test docker build"
git push origin main
gh run watch
```
After it completes:
```bash
docker pull ghcr.io/<owner>/phenoscribe:latest
docker run --rm ghcr.io/<owner>/phenoscribe:latest cli --help
```
**Expect:** Workflow green. Image pullable. CLI works.
**Depends on:** Task 1, Task 4, Task 5

---

### Task 10: User-facing install docs (per OS)

**Files:** `INSTALL.md` (top-level, linked from `README.md`)

Sections:
- **What you need (all OS):** ~5 GB free, internet for first run, OpenAI/Anthropic API key, optionally HF_TOKEN for diarization.
- **Windows:** install Docker Desktop (link); enable WSL2 backend (link); download `run.bat` + `.env.example`; fill `.env`; double-click `run.bat`. Screenshot of GUI.
- **macOS:** install Docker Desktop (link); download `run.sh` + `.env.example`; `chmod +x run.sh`; fill `.env`; `./run.sh`. Note: M1/M2/M3/M4 supported natively; no GPU acceleration on Mac.
- **Linux:** install Docker Engine (link); for NVIDIA, install nvidia-container-toolkit (link); same `./run.sh` flow.
- **Where files go:** `./input/` (you put audio here), `./output/` (Excel + transcripts appear here), `./models/` (Whisper cache, ~3 GB, do not delete).
- **Updating:** `run.bat`/`run.sh` auto-pulls on each launch. To force: `docker pull ghcr.io/<owner>/phenoscribe:latest`.
- **Troubleshooting:** port 7860 already in use; `.env` not picked up; first-run model download slow; common errors.

**Verify:** Hand the doc + launcher to one non-developer (the stakeholder or a colleague). Have them go from "fresh laptop" to "Excel output" without asking for help. Note every place they got stuck — fix the doc.

**Depends on:** Task 6, Task 7

---

### Task 11: End-to-end smoke test on a clean machine

**Verify (on a machine with no Python, no repo, no prior setup):**
1. Install Docker Desktop following the doc.
2. Download `run.bat` (or `run.sh`) and `.env.example` from GHCR / GitHub release.
3. Rename `.env.example` → `.env`, paste API key.
4. Put one test audio file in `./input/`.
5. Run launcher. Browser opens. Select file. Click Run. Watch progress.
6. Open `./output/results.xlsx`. Verify HPO codes present.

**Expect:** Time from "downloaded launcher" to "Excel open" under 15 minutes (most of it is image pull + Whisper model download on first run). Subsequent runs start in seconds.

**Depends on:** All previous tasks.

---

## Definition of Done

- [ ] CPU image builds clean, multi-arch (amd64 + arm64), pushed to GHCR via CI
- [ ] CUDA image builds clean (amd64), pushed to GHCR via CI
- [ ] Gradio GUI loads, lists mounted files, runs the full pipeline with progress feedback
- [ ] `run.sh` works on macOS (Apple Silicon and Intel) and Linux; auto-detects GPU
- [ ] `run.bat` works on Windows 10/11 with Docker Desktop; auto-detects GPU
- [ ] Auto-pull on launch verified — pushing a new image to GHCR reaches users on next launch
- [ ] `INSTALL.md` covers all three OS with screenshots
- [ ] One non-developer goes from zero to working Excel output following only the doc
- [ ] No patient data is ever inside the image (only in mounted `./input/` and `./output/`)
- [ ] API keys live only in `.env` on the host, passed via `--env-file`

## Out of scope (deferred)

- Authentication on the Gradio app — relies on `localhost`-only binding. If anyone ever wants to expose this on a network, revisit.
- Auto-updating launcher scripts themselves (only the image auto-updates; if `run.bat` syntax changes, users re-download).
- Signed Windows executables — Docker Desktop is the trust boundary; the `.bat` is plaintext and auditable.
- Offline-only mode — image is offline-capable for HPO matching, but transcription requires the model download on first run and LLM calls require internet. A fully air-gapped variant is a separate effort.
