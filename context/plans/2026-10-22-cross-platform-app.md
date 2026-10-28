# Plan: Phenoscribe for Marc on Windows, Mac and Linux

Date: 2026-10-22. Status: accepted (ZIP + `uv` is the main path).

## Summary

Ship Phenoscribe as a ZIP with one double-click start file for each OS. The start file
installs `uv` (a single-file Python manager, per-user install) and opens the web app in
the browser. Docker stays a supported second path, built from the same `uv.lock`.

Revised 2026-10-22 after a Codex review (gpt-6-luna, high): pin Python 3.12, keep Docker
and make it use `uv.lock`, test installs first, and drop two claims that were too strong
(no admin rights, key stored securely).

Users: researchers and clinicians who own their laptops and have admin rights. No IT
department controls the machines. Admin rights make Docker possible, but the ZIP stays
the main path because it has fewer steps and uses the Mac GPU.

## Decision: how to package

| Option | Verdict |
|---|---|
| Docker + prebuilt image | Second path. Strong environment control, but Marc must install Docker Desktop and WSL 2 (first WSL setup can need admin rights) and pull a 10 GB image. No GPU on Mac, so transcription is slow. Good for Linux and for a fallback if the ZIP fails on a machine. |
| One `.exe` / `.app` (PyInstaller) | No. Torch + CTranslate2 bundles are 3 GB+ and fragile, and antivirus tools often flag them. A separate build is necessary for each OS. |
| Hosted web server | No. Patient audio leaves Marc's laptop, and the server costs money to run. |
| **ZIP + start script + `uv`** | **Yes.** It runs one code path on all 3 OSes, installs per user (no admin rights on a normal laptop), uses mlx on Apple Silicon, and the lock file (`uv.lock`) has Windows x64 wheels for Python 3.10–3.13. |

## Why the ZIP and not a container

- Speed: Docker on a Mac runs a Linux virtual machine with no access to the Apple GPU.
  Whisper then runs on the CPU. Outside Docker it uses mlx on the GPU (about 4.5x faster
  on an M1 Pro, see `config.yaml`). On Windows both paths use the CPU.
- Isolation: `uv` gives enough. It downloads its own Python 3.12, puts all packages in
  the app's own `.venv`, and installs the exact `uv.lock` versions (`--frozen`). It does
  not touch the system Python. The app needs no system programs (PyAV bundles `ffmpeg`).
- The gap: Windows and Mac get different builds of the same packages, so a bug can show
  on one OS only. The CI install-and-test matrix (step 0) covers this.
- Cost of Docker for Marc: Docker Desktop + WSL 2 install and a 10 GB image, for no speed
  gain on Windows.

## What Marc does

1. Download `Phenoscribe.zip` from the GitHub release page, and extract it.
2. Double-click `Start Phenoscribe.bat` (Windows) or `Start Phenoscribe.command` (Mac).
3. One time only: the window downloads Python, the packages and the models (about 4 GB, 10–20 min).
4. The browser opens on the app. He pastes the API key one time. The browser remembers it.
5. He drags audio files onto the page, clicks **Start**, and downloads the Excel.

To update: download the new ZIP. The results live in `Documents/Phenoscribe`, outside the
app folder, so replacing the folder loses nothing.

## Work, in order

Each step is one small PR with tests. Steps 1–3 also fix the Docker image.

### 0. Pin Python and prove the install on clean machines

- Add `.python-version` with `3.12`, and set `requires-python = ">=3.10,<3.14"`. The lock
  has no torch wheel for Python 3.14, and `uv` can otherwise pick it.
- Add a CI job on `windows-latest`, `macos-latest` and `ubuntu-latest`: `uv sync --frozen`,
  then `uv run pytest`, then `phenoscribe setup --no-models` (step 2), then a smoke run on
  a short French `.txt` file with the LLM stubbed. Windows ARM is out of scope.
- Change the Dockerfile to install from `uv.lock` (`uv sync --frozen`). Today it uses
  `pip install -e .`, so the image does not get the pinned versions.

### 1. Choose the transcription backend automatically

- Problem: `config.yaml` says `backend: "mlx"`. Mlx exists only on Apple Silicon, so
  transcription fails on Windows, Linux and in Docker.
- Change: add `backend: "auto"` and make it the default in `config.yaml`. In
  `transcribe.py`, "auto" means mlx when `mlx_whisper` imports AND the `ffmpeg` command
  exists (`shutil.which`), else faster-whisper (PyAV bundles its own decoder).
- Test: "auto" resolves to faster-whisper when the import fails or `ffmpeg` is missing.

### 2. Make first-run setup one command: `phenoscribe setup`

- Problem: `data/` is not in git. A fresh copy has no `hp.obo` and no ChromaDB index.
  The Whisper and PII models download in the middle of the first run.
- Change: a new `setup` subcommand in `cli.py`. It downloads the pinned `hp.obo`, seeds
  the index (reuse `scripts/seed_hpo.py` logic, moved into `hpo_index.py`), and
  pre-downloads the Whisper and PII models. It skips each part that already exists,
  so it is safe to run at every start. It prints one plain line for each part
  ("Downloading the speech model (3 GB, one time only)...").
- `--no-models` skips the Whisper and PII downloads (for CI).
- The Dockerfile calls the same command. One setup path for all installs.
- Test: a second run does nothing (all parts present).

### 3. Move the batch run out of the UI: `run_batch()` in `pipeline.py`

- Problem: `gui.py` holds the file loop, the patient ID rule, the mapping file and the
  output path. The UI and the logic mix (single responsibility).
- Change: `run_batch(files, config, out_dir, on_progress) -> BatchResult`. It:
  - writes a new Excel for each run: `results_YYYY-MM-DD_HHMM.xlsx`. This removes the
    duplicate rows from reruns and the Windows "file is open in Excel" save error.
  - gives the patient ID: `MGA.<stem>` when the file stem is only digits (Marc's
    `467.mp3`), else the current `pt-<hash>`. Names in filenames stay out of the Excel.
  - reports progress for each step ("2/5 · 467.mp3 · removing personal details").
- `gui.py` then only builds the page and calls `run_batch()`.
- Test: two runs give two files; `467.mp3` gives `MGA.467`; `jean-martin.mp3` gives `pt-…`.

### 4. Make the page simple

Changes in `gui.py` only:

- File choice: an upload box (`gr.File(file_count="multiple")`) for drag and drop. Drop
  the folder list, the "Refresh" button and `PHENOSCRIBE_INPUT_DIR`. Gradio keeps the
  original filename of an upload, so the patient ID rule of step 3 still works.
- API key: one box, with a "Remember this key in this browser" tick box (on by default).
  It uses `gr.BrowserState` (browser local storage). This is plain storage in the browser
  profile, so the page says so. The app writes no key to disk. A "Test key" button makes
  one small call and says "Key works" or "Key rejected".
- Provider: one small choice (OpenAI / Anthropic), pre-selected from the key prefix.
  The "Test key" call confirms it. Remove the model menu; the model comes from `config.yaml`.
- Plain labels:
  - "Language of the recordings"
  - "I already have the transcripts (skip transcription)", in a closed "Advanced" panel
  - Remove the diarization box (it needs a token Marc does not have).
- Results: show the coded rows in a table on the page (term, HPO code, Present/Absent,
  REVIEW), plus the download button. `run_batch()` must return the rows for this; today
  the page gets only a status and a count for each file.
- Errors: one plain line for each file ("Key rejected", "Audio format not supported",
  "No internet"). The full trace goes to the log file.
- Launch with `inbrowser=True` and `server_name="127.0.0.1"` so only Marc's laptop
  can open the page.

### 5. Add the start scripts

- `Start Phenoscribe.bat` (Windows) and `Start Phenoscribe.command` (Mac/Linux, also
  works as `sh`). About 10 lines each:
  1. Go to the script's own folder (`cd /d "%~dp0"`, `cd "$(dirname "$0")"`).
  2. Install `uv` with the official one-line installer if `uv` is missing. On Windows the
     new `uv` is not on PATH in the same window, so call it by its full path
     (`%USERPROFILE%\.local\bin\uv.exe`).
  3. `uv run --frozen phenoscribe setup`
  4. `uv run --frozen phenoscribe-gui`
  5. On an error, keep the window open (`pause` / `read`) so the user can read it.
- Set `PHENOSCRIBE_OUTPUT_DIR` to `Documents/Phenoscribe`.
- Commit the `.command` file with the executable bit (`git update-index --chmod=+x`).
- The start scripts need no `ffmpeg` logic: step 1 ("auto") handles a Mac without it.

### 6. Trim the install

- Move `pyannote.audio` to an optional extra (`phenoscribe[diarize]`). It is large,
  off by default, and needs a Hugging Face token.
- Linux: plain `torch` from PyPI pulls CUDA wheels (about 2.5 GB). Accept this for now.
  Linux users are technical and can use Docker.

### 7. Release and docs

- A GitHub Action builds `Phenoscribe.zip` (repo files + start scripts) on each tag.
- Rewrite `INSTALL.md` as one page with screenshots: download, double-click, paste key,
  drop files. Include the one-time OS warning:
  - Windows SmartScreen: "More info" then "Run anyway".
  - Mac: System Settings → Privacy & Security → "Open Anyway".
  - Users own their laptops and have admin rights, so these prompts can always be passed.
- Tell the user to extract the ZIP to a short path (for example `C:\Phenoscribe`), to
  avoid the Windows path-length limit in the package folders.
- Keep a short Docker section at the end, for Linux servers. Remove the input folder
  mount from the `docker run` command (files now come through the upload box).

## Out of scope

- Code signing (costs money and time; the one-time warning is acceptable for one user).
- An installer, a tray icon, auto-update.
- Job resume in the UI (`jobs.db` stays a CLI feature).
- Search recall work (separate plan).

## Risks

| Risk | Check |
|---|---|
| A package has no wheel for Marc's Windows Python | Test steps 1–5 on a clean Windows VM before the release. |
| Whisper large-v3 on a laptop CPU is slow | Measure one 20-min recording on the VM. If too slow, default Windows to `large-v3-turbo`. |
| Antivirus flags the `uv` installer or the start file | Users own their laptops and have admin rights (confirmed 2026-10-22), so they can allow it. `INSTALL.md` says how. |
| First download is larger than "4 GB" | Measure the real total (Python, packages, Whisper ~3 GB, PII, HPO index) on the VM and put that number in `INSTALL.md`. |

## Done when

On a clean Windows 11 VM, a person who follows only `INSTALL.md` gets an Excel for one
recording, with no terminal command typed. The same check passes on a Mac.
