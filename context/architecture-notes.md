# HPO Identifier — Architecture Notes

## Problem
A GP doing long COVID research needs to automate his manual pipeline:
Audio interview → Transcribe → Remove PII → Extract HPO phenotype codes → Structured output

Currently done manually using various LLM tools. ~150 existing recordings, ongoing use.

## How it works, in plain words

The pipeline turns a recorded GP–patient conversation into a list of standardized medical codes (HPO codes) describing the patient's symptoms. Six steps:

1. **Listen to the recording and write it down.** A speech-to-text model tuned for French (faster-whisper) runs on the laptop and produces a raw transcript. The audio never leaves the machine. Optionally, a second model separates who is speaking — doctor or patient — so the transcript reads as a dialogue.

2. **Replace personal information with consistent placeholders.** A medical-French model (OpenMed) detects names, places, dates and other identifying details and swaps them for stable tokens: "Dr. Martin" becomes "Dr. 1", "Erasme hospital" becomes "Hospital 1". The same name always gets the same placeholder throughout the transcript, so the story still reads as a story — only the identifying surface is hidden. The mapping stays on the machine.

3. **Pull out every symptom the patient mentions.** An AI model reads the cleaned transcript and lists each complaint, keeping the patient's own French words ("j'ai mal au ventre") alongside a clinical English label ("abdominal pain"). Because identifying details were replaced in step 2, only de-identified text ever reaches the AI.

4. **Shortlist five candidate HPO codes per symptom.** HPO has about 19,000 standardized phenotype terms. For each extracted symptom, a meaning-based search picks the five HPO terms whose definitions are closest. This bounds what the AI is allowed to pick from in the next step.

5. **Pick the best code from the shortlist.** The AI is shown only those five candidates and asked which one fits best. It returns the chosen HPO code.

   Peter Robinson, who created HPO, has flagged a catch with using AI for this kind of work: these models are reliable at naming a medical condition. Getting the exact identifier code right is harder for them — they can drop a digit or pick a nearby ID. The system handles this by reading the official term name back from its own shortlist using the AI's chosen code. The code and the name can never end up disagreeing.

6. **Write the Excel file.** Each patient gets rows like *Fatigue | HP:0012378 | "I'm tired all the time"* — standardized code, term name, and the patient's own words side by side.

A batch of recordings can run unattended. Each recording's progress is tracked in a small local database; if a step fails (network blip, malformed audio, etc.) the system retries once and logs the rest for the user to inspect later.

## Pipeline Flow

```
Audio file (.wav/.mp3)
    │
    ▼
[1. faster-whisper] ── local transcription (French-tuned Whisper model)
    │
    ▼
Raw transcript (text)
    │
    ▼
[2. OpenMed] ── local PII detection & redaction (French medical, 97.97% F1)
    │
    ▼
PII-redacted transcript
    │
    ▼
[3. LLM — symptom extraction] ── extract symptoms + patient verbatim from text
    │                               (API-based, PII already removed)
    ▼
List of (symptom_description, patient_verbatim) pairs
    │
    ▼
[4. ChromaDB] ── vector search: symptom → top-K HPO candidates
    │               (17K HPO terms + synonyms embedded locally)
    ▼
Top-K HPO candidates per symptom
    │
    ▼
[5. LLM — HPO judge] ── pick best HPO term from shortlist per symptom
    │
    ▼
Final (HPO_term, HPO_code, patient_verbatim) triplets
    │
    ▼
[6. openpyxl] ── Excel output matching GP's existing format
```

## Tech Stack (Locked)

| Step | Tool | Why |
|------|------|-----|
| Transcription | **faster-whisper** | 2-4x faster than Whisper, pip install, French-tuned models available (WER 8.15%). Phone recordings — test first, add noise reduction later if needed |
| PII Detection | **OpenMed** (HuggingFace) | 97.97% F1 on French medical text, 55+ entity types, Apache 2.0, fully local |
| LLM (extraction + judging) | **Direct API + thin config** | LLM-agnostic — swap OpenAI/Anthropic/Ollama via config. PII stripped before API call |
| HPO Search | **ChromaDB** (embedded) | Zero-infra, Python-native, persists to disk. 17K terms is tiny |
| Excel Output | **openpyxl** | Standard Python Excel lib |

## Output Format

Target format (from GP's existing manual work):
```
Patient_ID | observation_source_value
MGA.014    | Transient anosmia (HP:0030447); Cough (HP:0012735); Fatigue (HP:0012378) [tiredness]; ...
```

Standardized triplets separated by `;`:
- HPO Term (HP:code) [patient verbatim]

## Key Design Decisions

1. **Hybrid HPO matching**: LLM identifies symptoms in natural language → vector search gets top-K HPO candidates → LLM picks from bounded shortlist. Prevents hallucinated codes.

2. **PII before LLM**: All PII redaction happens locally (OpenMed) BEFORE any text reaches an external API. GDPR-safe by design.

3. **LLM-agnostic**: Thin config layer — provider + model in a config file. No LiteLLM dependency.

4. **Pipeline-first**: No UI for now. CLI/script that processes batch recordings. UI comes later.

## Constraints

- **GDPR + Belgian health data law**: PII must never leave local machine
- **LLM-agnostic**: Must work with any provider (OpenAI, Anthropic, local Ollama)
- **Local-first**: Transcription and PII redaction always local
- **Validation**: 1003 manually coded rows exist as ground truth

## HPO Ontology

- ~17,000 terms organized hierarchically
- Available as OBO/OWL file
- Each term has: ID (HP:XXXXXXX), name, synonyms, parent categories
- Need both leaf-level (specific) and category-level output

## PII Strategy: Pseudonymization (not anonymization)

Instead of destroying PII context (e.g., replacing "Dr. John" with "[REDACTED]"), use **pseudonymization**:
- `Dr. John` → `Dr. 1`, `Dr. Mary` → `Dr. 2` (consistent throughout transcript)
- `Hôpital Erasme` → `Hospital 1`
- `March 2023` → `Date 1`
- Non-identifying context preserved: "grandmother", "school", "work" stay as-is

Implementation:
1. OpenMed detects PII entities with type labels
2. Replacement layer assigns numbered pseudonyms per entity type (PERSON_1, DATE_1, LOCATION_1...)
3. Mapping table kept locally (never sent to API)
4. Pseudonymized text sent to LLM — relationships and temporal flow preserved

## Open Questions (Stress Testing)

- [x] What happens when PII redaction removes medically relevant context? → Pseudonymization preserves context
- [x] Error handling: what if one step fails mid-batch? → Retry once, then log failure. SQLite job tracker.

## LLM Call Strategy: Two Separate Calls

Two LLM calls per recording (not collapsed into one):
1. **Symptom extraction**: transcript (French) → list of (patient_verbatim, clinical_term, context) triplets
   - LLM acts as "clinical translator": colloquial French patient language → standard English medical concept
   - e.g., "j'ai mal au ventre" → clinical_term: "abdominal pain", verbatim stays French
   - e.g., "dresses in black, emotional changes" → clinical_term: "emotional instability"
   - Also captures temporal/severity context (onset, frequency, triggers)
   - Output as structured JSON
   - **Language strategy**: clinical_term always in English (for HPO matching), patient_verbatim always in original French (for GP's output)
2. **HPO judging**: clinical_term + top-5 HPO candidates from ChromaDB → best match

Why separate:
- Separation of concerns — prevents HPO matching from being biased by extraction context
- Each call has a focused, testable prompt
- Easier to debug which step went wrong

Cost analysis (150 recordings):
- ~6K tokens per recording × 150 = ~900K tokens total
- Estimated cost: $3-5 (Sonnet) to $15 (GPT-4o) for full batch
- **Cost is negligible** — separation of concerns is worth it

## Batch Processing & State

- **SQLite** single-file database tracks pipeline state
- Schema: `jobs (id, audio_file, status, step_failed, error_msg, retries, created_at, updated_at)`
- Status flow: `pending → processing → completed | failed`
- Retry policy: 1 automatic retry, then mark failed with error context
- GP can re-run failures after investigating logs
- [x] How to handle inconsistent transcript quality? → Test first with raw audio, add noise reduction later if needed
- [x] What's the right top-K for HPO candidate retrieval? → K=5, tune later against ground truth
- [x] How to validate pipeline output against ground truth at scale? → Hierarchical scoring with partial credit

## Validation & Scoring

Ground truth: 1003 manually coded rows in Excel.

Scoring model (per symptom match):
- **Exact match** (same HP code) = 1.0
- **One hop apart** in the HPO tree = 0.75 (direct parent or direct child)
- **Two hops apart** = 0.5 (grandparent, grandchild, sibling via shared parent, or uncle/nephew)
- **Beyond 2 hops or wrong** = 0
- **Missed symptom** (in ground truth but not extracted) = 0 (recall penalty)
- **Hallucinated symptom** (extracted but not in ground truth) = precision penalty

Hierarchy walks use [hpo-toolkit](https://pypi.org/project/hpo-toolkit/) (recommended by Peter Robinson, who created HPO). The library auto-downloads the HPO release and exposes the `is_a` graph. The scorer runs a small bounded BFS over parents and children, so siblings and uncle/nephew relationships score correctly via a shared ancestor. The earlier hand-rolled walker only counted strictly-up or strictly-down paths and scored those relationships as 0.

Aggregate score per recording + across full dataset to track improvements.
- [x] Error handling: what if one step fails mid-batch? → Retry once, then log. SQLite job tracker.
- [x] Language gap (French interviews, English HPO)? → LLM outputs clinical_term in English, verbatim stays French
