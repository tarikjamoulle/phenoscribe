# Proposal: personal-detail removal as a feature, with a review step

Date: 2026-10-26. Status: implemented on branch `pii-review` (not committed). Open questions 1–4 use the proposals as defaults.
Scope: `pii.py`, a new `review.py`, `pipeline.py`, `transcribe.py` (`.docx` input),
`output.py`, `gui.py`, `cli.py`, INSTALL.md, README.

## Problem

The stakeholder says the personal-detail removal is as useful as the HPO coding. Today
the user cannot see it. `pseudonymize()` runs inside `process_recording()`, writes
`pseudo/<patient_id>.txt`, and logs one count. The page shows only "removing personal
details" in the progress bar.

The user cannot:

- see what the app removed, or how many items;
- undo a wrong removal (a symptom word taken for a name);
- add a name that the app missed;
- get a clean transcript without HPO coding;
- drop a `.docx` transcript. Only `.txt` is accepted (`TEXT_EXTENSIONS = {".txt"}`).

The detector has a known gap (see the `pii.py` docstring): rare surnames, clinic names and
drug eponyms can pass. Today a missed name goes to the AI service with no human check.

## What we use today

Two layers, both local. No text leaves the computer during this step.

1. A French named-entity model, `Anonym-IA/V2-camembert-ner-pii-french` (CamemBERT-base,
   110M parameters, MIT, about 445 MB). It tags 39 types: first and last names, streets,
   cities, postal codes, companies, phones, emails, social-security numbers, IBAN, dates,
   ages, job titles. Reported validation micro-F1: 0.93. Detections under
   `pii.min_score: 0.6` are dropped.
2. Regular expressions for dates, Belgian and French phones, emails and the Belgian
   national number.

Fallback when the first model does not load: `Jean-Baptiste/camembert-ner` (4 labels,
F1 0.89). Each found value gets a numbered replacement (`PERSON_1`, `LOCATION_2`). The
same value gets the same replacement in the whole text.

`skip_pii` exists on `process_recording()` but only tests use it. The page and the CLI
cannot skip the step today.

## Proposal

Two tools on one page. Both start with the same removal and the same review.

| Tool | Needs an API key | Output |
|---|---|---|
| Remove personal details | No | Clean `.txt` and `.docx` for each file, plus a report |
| Remove personal details, then find HPO codes | Yes | The above, plus the Excel of HPO codes |

The HPO coding reads only an approved clean text. The approval is the gate before text
goes to the AI service.

### Page flow

```
 1  API key        (only for "then find HPO codes")
 2  Files          drop .mp3 .wav .m4a .ogg .flac .txt .docx
    What to do     (•) Remove personal details, then find HPO codes
                   ( ) Remove personal details only
 3  [ Start: transcribe and remove personal details in 3 files ]

    ── long, unattended wait: Whisper, then the detector ──

 4  Review         467.mp3  ✓ approved   │ 412.docx  needs review │ 503.mp3  needs review
    ┌─ 412.docx ──────────────────────────────────────────────────────────────────┐
    │ 14 removed: 6 names · 3 places · 2 dates · 1 phone · 2 other                  │
    │ ✓ No removed value is left in the clean text                                 │
    │                                                                              │
    │ Text, with each replacement highlighted and colored by type                  │
    │   "Bonjour madame [PERSON_1], vous venez de [LOCATION_1] ..."                │
    │                                                                              │
    │ Removed                                 Keep removed                         │
    │   Dupont        → PERSON_1   name   ×4   [x]                                 │
    │   Namur         → LOCATION_1 place  ×1   [x]                                 │
    │   Lyrica        → PERSON_2   name   ×2   [ ]  ← a drug, the user unticks      │
    │                                                                              │
    │ Possibly missed                         Remove                               │
    │   Vandermeulen  "le docteur Vandermeulen m'a dit"   [ ]                      │
    │   Add a word the app missed: [__________] [name ▾] [Add]                     │
    │                                                                              │
    │ [ Approve 412.docx ]                                                         │
    └──────────────────────────────────────────────────────────────────────────────┘
 5  [ Find HPO codes in 3 approved files ]      or, for the first tool:
    [ Download clean texts (.zip) ]
```

Why one stop after all files: Whisper takes about as long as the audio (15.2 min for a
15.4 min recording on 2026-10-22). The user leaves the computer for that part. Review
then takes a few minutes for each file. The HPO coding takes about 1 minute for each file.

### Types to remove

The user picks which types of personal detail the app removes. The choice sits under
"What to do" on the page:

```
 Personal details to remove
   Always:  names · addresses and places · phones · emails · ID numbers · organizations
   [x] Dates      [x] Ages      [x] Gender      [x] Job titles
```

- The "Always" group identifies a person directly. The user cannot turn it off.
- The 4 optional types are on by default. Dates, age, gender and job can each identify a
  person together, so the safe choice comes first. Age and gender can matter for HPO
  coding, so the user can keep them in the text.
- The page remembers the choice in the browser. `config.yaml` gets
  `pii.keep_types: []` for the CLI.
- The detector always finds every type. The choice acts only in `clean_text()`. A change
  of the choice in the review needs no new run of the detector.
- Rows of a kept type stay in the review table, unticked and grey, so the user sees them.
- The leak check skips the kept types. The report and the review JSON record the choice
  for each file.

### Review rules

- One row for each distinct removed value, with its type and its count in the text.
  Model rows with a low score come first. The page shows no score, because regex rows
  have none. Unticking a row puts the original back at every position.
- "Possibly missed" lists only a word after a title (`docteur`, `Dr`, `madame`, `Mme`,
  `monsieur`, `M.`, `Mlle`) that the app left in place. The list shows one sample
  sentence for each word. Ticking a word removes it at every position (whole word, any
  case). A broad scan of capitalized words is cut: drug names and diagnoses give too
  many false alerts.
- The table is a `gr.Dataframe` with a Boolean column, `interactive=True`, and an
  `.input` event that saves the review JSON. `gr.HighlightedText` only shows the text.
- "Add a word" removes any free text the user types, at every position, with the type
  the user picks.
- The leak check runs after each change. It takes every removed value (detected and
  added), folds case and accents, and normalizes punctuation and spaces. Then it looks
  for each value as a whole word in the clean text. The page shows the result as one
  line. Approve is off while the check fails. The check cannot find a name that the
  detector never found; the review is the protection for that.
- Approve saves the decisions. The user can open an approved file again and change it,
  until the HPO coding starts.
- A file that the user does not approve is left out of the HPO coding. The page says so.
- The review panel is a queue: a status for each file ("needs review", "approved") and a
  "Continue later" note. The page opens the drafts from `review/` again on the next start.

### Saved state

`<results>/review/<patient_id>.json`, one file for each patient, written after the
detector and after each change:

```json
{
  "source": "412.docx", "patient_id": "MGA.412", "status": "draft",
  "raw_text": "...", "spans": [{"start": 10, "end": 16, "value": "Dupont",
  "category": "PERSON", "score": 0.98, "origin": "model"}],
  "kept": ["Lyrica"], "added": [{"value": "Vandermeulen", "category": "PERSON"}],
  "approved_at": null
}
```

The page reads this file back, so a closed browser tab loses no review work. The file
holds the real names. It stays in the local results folder only, like `transcripts/`
today. The page never offers it as a download.

### Outputs

In `Documents/Phenoscribe results/`:

- `clean/<patient_id>.txt`: the approved clean text. It replaces `pseudo/<patient_id>.txt`.
- `clean/<patient_id>.docx`, only for a `.docx` input. The page says before upload that
  the clean copy keeps the text and loses the layout.
- No delete button (cut on 2026-10-26: the user wants the originals kept). INSTALL.md
  states where the files with real names stay.
- `personal_details_<date>.xlsx` for the first tool, or a "Personal details" sheet in the
  results Excel for the second tool. One row for each patient: counts for each type, the
  number of values the user unticked and added, and the approval time. No original values.

## `.docx` input

- Add `python-docx` (MIT, pure Python, no system library) to the main dependencies.
- `read_docx(path)` returns the text of the body paragraphs and table cells in document
  order. Headers and footers go first, because they often hold the patient name.
- `.docx` joins `TEXT_EXTENSIONS`. `plan_batch` treats it as a transcript: it wins over
  audio with the same stem. Two transcripts for one patient (`412.txt` and `412.docx`)
  are both skipped, as two audio files are today.
- Old `.doc` files are skipped with the note "Save as .docx in Word, then add it again".
- The clean `.docx` is a new document with one paragraph for each source paragraph. Bold,
  fonts and images are lost. Open question 3 covers a copy that keeps the layout.

## Code changes

1. `pii.py`: a new `find_personal_details(text, ...) -> list[Span]` returns every span
   with position, value, type, score and origin (`model` or `regex`).
   `pseudonymize()` stays as a thin wrapper, so the CLI and tests keep working.
2. New `review.py` with pure functions, easy to test:
   - `Review` dataclass and `load_review` / `save_review`;
   - `clean_text(review) -> (text, mapping)`: applies `kept` and `added` to the spans;
   - `possibly_missed(review, hpo_labels) -> list[(word, sample)]`;
   - `leaks(review) -> list[str]`: removed values still in the clean text;
   - `summary(review) -> dict[type, count]`.
3. `pipeline.py`: split `process_recording()` in two.
   - `prepare_file(plan, config, out_dir, on_step)`: transcribe or read, find the personal
     details, save `review/<patient_id>.json` with status `draft`.
   - `code_file(review, config, excel_path, on_step)`: refuse a review that is not
     approved or that has leaks, then run extraction, matching and the Excel.
   - `run_batch()` (CLI and tests) calls both. It codes unreviewed text only with an
     explicit `no_review=True`, which the CLI passes only for `--no-review`. Without the
     flag, `phenoscribe run` stops after the removal and says "Review in the page".
     The CLI log names each value removed for each file.
   - `process_recording()` keeps its signature for the existing tests. Remove `skip_pii`.
4. `transcribe.py`: `.docx` read path and `TEXT_EXTENSIONS`.
5. `output.py`: write the clean `.docx`, the "Personal details" sheet and the report file.
6. `cli.py`: `phenoscribe clean <files...>` runs the first tool without review and prints
   the summary for each file. Users who want a review use the page.
7. `gui.py`:
   - the "What to do" choice; Start needs a key only for the second tool;
   - a review panel: a file picker (tabs or a dropdown) with a status for each file,
     the summary line, the leak line, `gr.HighlightedText` for the text (read-only),
     a `gr.Dataframe` with a tick column for "Removed", a second one for "Possibly
     missed", the "Add a word" row and the Approve button;
   - "Find HPO codes in N approved files" or "Download clean texts (.zip)".
8. Docs: INSTALL.md (the two tools, the review, `.docx`) and README.

## Tests

- `find_personal_details`: spans have exact offsets and the same values as today's
  mapping on the existing PII fixtures.
- `clean_text`: unticking a value puts it back at every position; an added word is
  removed in any case at word boundaries only ("Marie" does not hit "mariage").
- `leaks`: finds a value that the detector caught at one position and missed at another.
- `possibly_missed`: catches "docteur Vandermeulen"; ignores the first word of a sentence,
  "Paracétamol" if it is in the common list, and HPO labels.
- `code_file` refuses a draft review and a review with leaks.
- `read_docx`: paragraphs, tables, headers in order; `plan_batch` with `.docx` + `.mp3`,
  `.txt` + `.docx`, `.doc`.
- `clean_text` with `keep_types={"AGE"}` keeps ages and still removes names; the leak
  check ignores kept types; the "Always" types cannot be kept.
- The review JSON survives a save and a load. A corrupted JSON gives a plain error and
  the file goes back to "needs review".
- Neither LLM call gets text before approval: stub `extract_symptoms` and `match_hpo`,
  assert they never run for a draft, and assert they get only the clean text.
- Repeated, overlapping and regex-only spans; an edit after approval sets the status back
  to "needs review".
- A Windows-style path with spaces and accents; a 2-hour transcript (speed of `clean_text`
  and `leaks`).
- GUI: Start without a key works for the first tool and not for the second; Approve is
  off while a leak exists; coding runs only the approved files.
- Live test: the 2 real recordings (467, 327) with the setup.sh key, in Chrome.

## Open questions for review

1. Must every file get a review, or may the user click "Approve all" after a look at the
   counts? Proposal: one review per file; an "Approve all" button is a later choice.
2. Clean `.docx` with the layout kept: replace text inside the Word runs. A name split
   over two runs makes this hard. Proposal: plain paragraphs now, layout later on request.
3. Default for the optional types (see "Types to remove"): all on, or keep age and gender
   in the text by default? Proposal: all on; the user turns a type off.
4. Should the clean transcript feed the HPO coding with the replacements (`PERSON_1`) or
   with neutral words ("[name]")? Today the replacements go to the AI service.

## Codex review (gpt-6-luna, high, 2026-10-26) and decisions

| Codex point | Decision |
|---|---|
| The CLI auto-approve bypasses the gate | CLI codes unreviewed text only with `--no-review`. |
| Raw transcripts and review JSON hold real names | "Delete names and raw transcripts" button; INSTALL.md names the folders; no download. |
| A whole-word leak check overstates safety | Fold case, accents, punctuation; check every detected and added value; page text says the check covers only found values. |
| The capitalized-word scan gives false alerts | Cut. Keep only words after a title. |
| The Gradio edit design is vague | `gr.Dataframe` with a Boolean column, `interactive=True`, save on `.input`. |
| A review for each file burdens a GP | Kept, with a queue, a status for each file and "Continue later". Score column cut. |
| The clean Word copy loses layout | `.txt` for every file; `.docx` only for a `.docx` input, with a note before upload. |
| Tests miss privacy cases | Added: no LLM call before approval, overlaps, regex-only, corrupted JSON, edit after approval, Windows paths, long text. |

## Change on 2026-10-26: check only the doubtful files

The user asked for 100 files with no deep review of each. Decision: the page approves each
file with no warning sign (`review.warnings`), and the user checks only the doubtful files.

- Warning signs: a word after a title stays in the text; a model removal under score 0.8
  (`LOW_SCORE`); no removal at all.
- "Approve the N other files with no check" approves the rest after a browser confirm.
- The review records `approved_how`: `checked`, `auto: no warning`, `approved in bulk` or
  `no review (CLI)`. The "Personal details" sheet shows it.
- The page text says that a name missed with no warning goes to the AI provider.
- The "Delete names and raw transcripts" button is removed.

## Codex code review (gpt-6-luna, high, 2026-10-26) and fixes

| Codex point | Fix |
|---|---|
| `phenoscribe clean` used the file name as patient ID, so `martin-jean.txt` put a name in the ZIP | Uses `batch_patient_id` (hash). `process` keeps `derive_patient_id` for the numbered eval files. |
| "Jean-Dupont" escaped a removal of "Jean Dupont" | `fold` maps hyphens, apostrophes and spaces to one space; a space in a value matches a run of them. |
| Tables in Word headers and footers were not read | Headers and footers use the same paragraph-and-table reader as the body. |
| "ς" and "Σ" did not match | `fold` uses `casefold`. |
| A review file with bad bytes stopped the page load | `UnicodeDecodeError` gives "damaged", like bad JSON. |
| The old-file test proved nothing | It now loads the first file shape and checks status, fields and clean text. |
