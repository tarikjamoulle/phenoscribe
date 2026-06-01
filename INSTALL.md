# Installing Phenoscribe — a step-by-step guide

This guide is for people who have never used a command line. If you can follow a recipe, you can install Phenoscribe. Allow about 45 minutes the first time — most of it is waiting for downloads.

> A double-click launcher for Mac and Windows is on the roadmap. Until then, you'll need to type a few commands. The steps below tell you exactly what to type.

## 1. What you need

- A laptop running **Mac**, **Windows 10/11**, or **Linux**.
- About **10 GB of free disk space** for the Docker image (it bundles the Whisper voice-recognition model and the HPO ontology so the first run doesn't need to download them).
- An **internet connection** for the first run (later runs can be offline for transcription; the AI step still needs internet).
- An **API key** from OpenAI or Anthropic. This is what pays for the AI part. About $0.01 per recording. (See step 3.)

## 2. Install Docker Desktop

Docker is the program that runs Phenoscribe inside a sealed box on your machine, so it doesn't interfere with anything else you have installed.

### Mac

1. Go to <https://www.docker.com/products/docker-desktop/> and click **Download for Mac**. Pick the chip type — Apple Silicon (M1/M2/M3/M4) or Intel.
2. Open the downloaded `.dmg` file. Drag **Docker** into your **Applications** folder.
3. Open **Applications → Docker**. The first time, it will ask for your password and walk through a brief setup. Accept the defaults.
4. Wait until the Docker icon in the top menu bar stops animating — it should say "Docker Desktop is running".

### Windows

1. Go to <https://www.docker.com/products/docker-desktop/> and click **Download for Windows**.
2. Run the installer. When asked, leave both checkboxes ticked ("Use WSL 2 instead of Hyper-V" and "Add shortcut to desktop").
3. Restart your computer when prompted.
4. Open **Docker Desktop** from the Start Menu. Accept the agreement. Wait until the whale icon in the bottom-right tray stops animating.

### Linux

Follow the official guide for your distribution: <https://docs.docker.com/engine/install/>. Make sure you can run `docker` without `sudo` (the post-install steps cover this).

## 3. Get an API key

You only need **one** of OpenAI or Anthropic. Pick whichever you have an account with, or set up the cheaper one.

### Option A: OpenAI

1. Go to <https://platform.openai.com/signup> and create an account if you don't have one.
2. Add a payment method at <https://platform.openai.com/settings/organization/billing/overview>. **You also need to pre-pay some credit** ($5 is plenty for testing) — OpenAI no longer bills after usage.
3. Create an API key at <https://platform.openai.com/api-keys> → **+ Create new secret key**. Give it a name like "phenoscribe", click Create, and **copy the key now** (you can't see it again later). It starts with `sk-…`.

### Option B: Anthropic (Claude)

1. Go to <https://console.anthropic.com/> and create an account.
2. Add credit at **Settings → Billing**.
3. Create an API key at **Settings → API Keys** → **Create Key**. Copy it; it starts with `sk-ant-…`.

Keep the key in a password manager or a note where you can find it later.

## 4. Download Phenoscribe

The easiest way, no Git needed:

1. Go to <https://github.com/tarikjamoulle/phenoscribe>.
2. Click the green **Code** button → **Download ZIP**.
3. Open the downloaded `phenoscribe-main.zip` from your Downloads folder. On Mac it unzips automatically; on Windows right-click → Extract All.
4. Move the unzipped `phenoscribe-main` folder to somewhere easy to find — for example, your **Documents** folder.

## 5. Open the Terminal

This is the program where you'll type the Phenoscribe commands.

- **Mac:** Press `Cmd + Space`, type `Terminal`, press Enter.
- **Windows:** Press the Windows key, type `Command Prompt`, press Enter. (Or use PowerShell — either works.)
- **Linux:** Press `Ctrl + Alt + T`, or look for "Terminal" in your apps.

A new window will open with a blinking cursor. You're going to type a few commands here.

## 6. Move into the Phenoscribe folder

In the Terminal window, type the command below and press Enter. Replace the path if you put the folder somewhere else.

**Mac / Linux:**

```
cd ~/Documents/phenoscribe-main
```

**Windows:**

```
cd %USERPROFILE%\Documents\phenoscribe-main
```

To check you're in the right place, type `ls` (Mac/Linux) or `dir` (Windows) and press Enter. You should see file names like `Dockerfile`, `README.md`, `pyproject.toml`.

## 7. Build the Phenoscribe image (one time)

This builds the sealed box that runs Phenoscribe. **It takes 20–30 minutes the first time** because it downloads the Whisper voice-recognition model (~3 GB) and a lot of supporting software, all into the image. After this, you won't need to do it again unless you re-download a new version of Phenoscribe.

Type this command and press Enter:

```
docker build -t phenoscribe .
```

(Don't forget the dot at the end.) You'll see a lot of text scrolling — that's normal. When it finishes, you'll see something like `naming to docker.io/library/phenoscribe`. If you see an error, jump to the Troubleshooting section.

## 8. Create your folders

Phenoscribe needs two folders next to the project: one for your recordings, one for results.

Type this command and press Enter:

**Mac / Linux:**

```
mkdir -p data/recordings output
```

**Windows:**

```
mkdir data\recordings output
```

What each folder is for:

| Folder | What goes in it |
|---|---|
| `data/recordings/` | **Your audio files.** Drag your `.mp3`, `.wav`, `.m4a`, or `.ogg` recordings into this folder. |
| `output/` | **Your results.** Phenoscribe writes the Excel here, plus a `filename_mapping.json` that links each hashed `pt-…` id back to your original filename. You don't put anything in this folder yourself. |

The Whisper voice-recognition model is already inside the Docker image (that's why step 7 took a while), so there's no separate model folder to manage.

## 9. Add your recordings

Open `data/recordings/` in your file browser (Finder on Mac, File Explorer on Windows) and drag your audio files into it. Start with one file for your first test.

## 10. Start the app

Back in the Terminal, type this command and press Enter. It's one long command — copy and paste it as a single block.

**Mac / Linux:**

```
docker run --rm -p 127.0.0.1:7860:7860 \
  -e PHENOSCRIBE_INPUT_DIR=/data/recordings \
  -e PHENOSCRIBE_OUTPUT_DIR=/data/output \
  -v "$(pwd)/data/recordings:/data/recordings:ro" \
  -v "$(pwd)/output:/data/output" \
  phenoscribe
```

**Windows (Command Prompt):**

```
docker run --rm -p 127.0.0.1:7860:7860 ^
  -e PHENOSCRIBE_INPUT_DIR=/data/recordings ^
  -e PHENOSCRIBE_OUTPUT_DIR=/data/output ^
  -v "%cd%\data\recordings:/data/recordings:ro" ^
  -v "%cd%\output:/data/output" ^
  phenoscribe
```

The `127.0.0.1:` prefix on `-p` is what keeps the app reachable only from your own laptop — without it, anyone on the same wifi could open the page.

After a few seconds you'll see a line like `Running on local URL: http://0.0.0.0:7860` (that's the address *inside* the Docker container — from your browser, you still use `localhost`). The app is now running. **Leave this Terminal window open** — closing it stops the app.

## 11. Use the app in your browser

1. Open your browser (Chrome, Safari, Firefox, Edge — any of them).
2. Go to <http://localhost:7860>.
3. You'll see the Phenoscribe page. The files you put in `data/recordings/` show up as checkboxes. **Tick the ones you want to process.**
4. On the right, pick your settings:
   - **Audio language** — `French` is the default.
   - **LLM provider** — `openai` or `anthropic`, whichever key you have.
   - **LLM model** — pick the cheapest one for testing (`gpt-4o-mini` or `claude-haiku-4-5`).
   - **API key** — paste the key you copied in step 3.
5. Click the big **Run** button.
6. A progress bar shows which file is being processed. The first run on a new machine takes 5–10 minutes extra because Whisper downloads itself. After that, expect roughly **5–15 minutes per recording** depending on length.

## 12. Find your Excel

When Phenoscribe finishes, the Excel is at `output/results.xlsx` in your project folder. Open it like any other Excel file — double-click in Finder or File Explorer.

You'll also see a download button in the browser if you'd rather grab it that way.

The patient column in the Excel contains hashed ids like `pt-7a4b3c8e` instead of your original filenames. This is deliberate — filenames often carry patient names, and we don't want those in the spreadsheet. To map an id back to the source recording, open `output/filename_mapping.json`.

Phenoscribe also saves:

- `output/transcripts/pt-<hash>.txt` — the raw transcript Whisper produced.
- `output/pseudo/pt-<hash>.txt` — the same transcript with names replaced by placeholders. This is what the AI saw.

These are useful if you want to re-run just the AI step later (uncheck "Transcribe audio" in the GUI).

## 13. Stop the app

Go back to the Terminal window where the app is running. Press `Ctrl + C` (on Mac, that's the literal Control key, not Cmd). The app stops. You can close the Terminal window.

## 14. Running it again later

Once everything is installed, the everyday flow is just:

1. Open Docker Desktop (if it isn't already running).
2. Open Terminal, run the `cd` command from step 6.
3. Run the `docker run` command from step 10.
4. Open <http://localhost:7860>.

The first build (step 7) and Docker Desktop install (step 2) are one-time. The Whisper model lives inside the Docker image, so every fresh `docker run` already has it.

---

## Troubleshooting

**`docker: command not found` or `'docker' is not recognized`**
Docker Desktop isn't installed or isn't running. Go back to step 2. Make sure the Docker icon in your menu bar / system tray says "Docker Desktop is running" before retrying.

**The build (step 7) fails with a network error**
Your internet hiccuped. Just run the same `docker build` command again — it picks up where it left off.

**`Cannot connect to the Docker daemon`**
Docker Desktop isn't running. Open it from your Applications / Start Menu and wait for the icon to stop animating.

**Port 7860 already in use**
Another program is using that port. Easiest fix: change `-p 7860:7860` to `-p 7861:7861` in the run command, then open <http://localhost:7861> instead.

**The browser page is blank or won't load**
Wait another 30 seconds — the first start takes a moment. Then hard-refresh: `Cmd + Shift + R` (Mac) or `Ctrl + F5` (Windows). If still blank, check the Terminal window for an error.

**"insufficient_quota" error on OpenAI**
Your prepaid OpenAI credit is at $0. Top up at <https://platform.openai.com/settings/organization/billing/overview>. The "monthly usage limit" is separate from the actual credit balance.

**Whisper download fails during `docker build`**
The build pulls the model from Hugging Face. If your connection is unstable, the build step may fail mid-download. Re-run `docker build -t phenoscribe .` — completed steps are cached, so it picks up where it left off.

**Run failed with a Python error in the Terminal**
Copy the last 20 lines of the Terminal output and send them to Tarik. The transcript and pseudonymised file (if produced) are in `output/transcripts/` and `output/pseudo/` — useful for debugging without re-running Whisper.

**My results.xlsx already exists from an old run**
A new run overwrites it. If you want to keep the old one, rename it first.

## Getting help

Open an issue at <https://github.com/tarikjamoulle/phenoscribe/issues> or email Tarik directly. Include:

- Your operating system and Docker Desktop version.
- The full command you ran.
- The last ~20 lines of output from the Terminal window.
