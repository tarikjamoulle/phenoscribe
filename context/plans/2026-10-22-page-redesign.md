# Proposal: a simpler Phenoscribe page

Date: 2026-10-22. Status: draft, for review. Branch: `cross-platform-app` (PR #16).
Scope: `src/phenoscribe/gui.py`, plus a small transcript-reuse rule in `pipeline.py`.

## Problem

The current page (step 4 of `2026-10-22-cross-platform-app.md`) works but looks like a
form dump: the full width, a giant orange button, a long path in a code block, a provider
radio, a "Test key" button, and an "Advanced" panel with "I already have the transcripts
(skip transcription)".

That checkbox is redundant. `transcribe()` already reads a `.txt` file as it is and skips
Whisper. The checkbox does something else: it reuses a transcript that an earlier run of
the same audio saved in `<output>/transcripts/<patient_id>.txt`.

Users: Marc (GP, Windows, not technical) and other clinicians. Typical input: a folder of
`467.mp3`-style recordings, sometimes with `467.txt` transcripts next to them. Whisper
large-v3 takes about 10 minutes for one 20-minute recording (measured on an M1 Pro with
mlx, 2026-10-22). It is slower on a Windows CPU. The LLM part costs about $0.01 and takes
about 1 minute.

## Proposed page

One centered column, about 900 px wide, 3 numbered steps:

```
 Phenoscribe
 Turns patient interviews into HPO codes. Recordings stay on this computer.

 1  API key        ✓ OpenAI key saved in this browser        [Change]

 2  Files          ┌──────────────────────────────────────────────┐
                   │  Drop recordings or transcripts here         │
                   │  or  [Choose files]  [Choose a folder]       │
                   └──────────────────────────────────────────────┘
                   467.mp3   audio       → transcribe, then code
                   412.mp3   audio       → saved transcript found, code only
                   412.txt   transcript  → code only
                   notes.docx            → skipped (not supported)
                   Language of the recordings: [French ▾]   (only shown for audio)

 3                 [        Start coding 3 files        ]

 Results           467.mp3 → MGA.467   20 codes · 2 to review   ✓
                   412.txt → MGA.412   Key rejected             ✗
                   [table: Patient · Symptom · HPO term · Code · Present/Absent · Review]
                   [ Download Excel ]   [ Open results folder ]
```

- The key box shrinks to one line once a key is saved. The key is checked on paste (one
  `models.list()` call), so the "Test key" button goes. The provider is shown as text
  ("OpenAI key"), detected from the prefix (`sk-ant-` = Anthropic), so the radio goes.
- Start is enabled only when there is a key and at least one supported file.
- The table gets a "Symptom" column (the clinical term), so a "(no good match)" row says
  what was not coded.
- "Open results folder" opens `Documents/Phenoscribe` with the OS file browser
  (`os.startfile` / `open` / `xdg-open`). This works because the server runs on the
  user's own computer, on 127.0.0.1.
- The Gradio footer is hidden.

## Folder selection and automation

A browser cannot give the app a folder path. "Choose a folder" uploads every file in the
folder (Gradio `file_count="directory"`), which copies them to Gradio's temp folder.

The app decides the work for each file. The user does not tick anything.

1. Keep only supported files (`.mp3 .wav .m4a .ogg .flac .txt`). Ignore hidden files and
   `*_pseudo.txt`. List the other files as "skipped".
2. Group by filename stem. If a stem has both audio and a `.txt` in the selection, use the
   `.txt` and do not transcribe. One patient, one row group.
3. Audio with no `.txt` in the selection: reuse a saved transcript if one exists for this
   exact audio. "Exact" means the same SHA-256 of the audio bytes, stored next to the
   transcript (`transcripts/<patient_id>.json` with `{"sha256": ..., "model": ...,
   "language": ...}`). Same name with other content = transcribe again. A different
   Whisper model or language = transcribe again.
4. Otherwise transcribe, and save the transcript plus its `.json`.
5. Always re-run PII removal, extraction and coding. They are cheap, and each run writes a
   new dated Excel, so there is no "already coded" skip.

No override toggle. To force a new transcription, the user deletes the transcript file
(INSTALL.md says where). Open question for review: is that enough, or does the page need a
"Transcribe again" link per file?

The CLI keeps `--skip-transcription` as it is.

## Visual direction

Classy and calm: a neutral background, one accent color, a serif or refined sans for the
title, generous spacing, rounded cards, no full-width orange bar. Implemented with a custom
`gr.themes.Base` theme plus a small CSS block in `gui.py`. Light and dark mode both.

## Tests

- Stem grouping: audio + txt → txt wins; audio alone → transcribe; unsupported → skipped.
- Reuse: same SHA-256 → reuse; changed bytes, model or language → transcribe.
- The file plan text for each case.
- Start disabled with no key or no files.

## Codex review (gpt-6-luna, high, 2026-10-22) and decisions

| Codex point | Decision |
|---|---|
| One `gr.File` cannot offer both "files" and "folder" | Two upload buttons over one drop zone. |
| Key check "on paste" fires while typing | Check when the key box loses focus, and again at Start. |
| Do not promise that only safe text leaves the computer | Page text says the removal can miss details. |
| `467.mp3` in two folders gets `MGA.467` twice; transcripts overwrite | Refuse duplicate file names in one run, with one plain line. Store reused transcripts by audio SHA-256 (`transcripts/by-audio/<sha>.txt`), so reuse never depends on the patient ID. |
| Reuse key must also hold the backend | Key = SHA-256 + backend + model + language. |
| Folder upload copies all files to temp; no size limit | Accept for a local app. Unsupported files are listed as skipped. |
| Cut folder upload (KISS) | Kept: Marc works with folders of recordings. |
| Cut provider detection | Kept as a pre-selected two-option switch the user can change. |
| Cut "Open results folder" | Cut. Download button plus the folder name as text. |
| "Transcribe again" needed if reuse stays | Kept: one link per file with a saved transcript. |

Design canvas: https://claude.ai/artifact/Rk65X7cwwUDaA2YbtRrxWQ
