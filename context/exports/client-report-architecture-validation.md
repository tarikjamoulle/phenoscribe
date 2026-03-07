# HPO Identifier — Architecture Report for Validation

**Prepared for:** Dr. Jamoulle
**Date:** March 2026
**Purpose:** Validate the proposed technical approach before development begins

---

## 1. What We're Building

A tool that automates your current manual workflow:

**Today (manual):**
> Record patient interview → Transcribe audio → Manually remove personal data → Paste into an LLM → Manually format HPO codes into Excel

**Proposed (automated):**
> Drop audio files into a folder → Run one command → Get a formatted Excel file with HPO codes, terms, and patient verbatim

The tool processes your ~150 existing recordings in batch, and can handle new recordings as they come in. It produces output in your existing Excel format:

**Format A — Semicolon-separated (existing Excel):**
```
Patient_ID | observation_source_value
MGA.014    | Transient anosmia (HP:0030447); Fatigue (HP:0012378) [tiredness]; ...
```

**Format B — PURL-based (from your ChatGPT workflow):**
```
CASE ID; HPO TERM; HPO Code Purl; Verbatim
MGA.376; Memory impairment; http://purl.obolibrary.org/obo/HP_0002354; (perte de mémoire immédiate, besoin de prendre des notes, désorientation)
```

The tool will support both output formats. You choose which one in the configuration. The PURL format produces one row per HPO term per patient; the semicolon format produces one row per patient with all terms concatenated.

---

## 2. How It Works — The Pipeline

The tool works in six sequential steps. Each step uses a specialized component chosen for reliability and compliance.

```
Step 1 — TRANSCRIBE        Your phone recording OR written text → standardized transcript
         (runs on your machine, nothing leaves your computer)
         Accepts both audio files AND pre-existing text transcriptions.

Step 2 — PROTECT PRIVACY   Personal data (names, dates, hospitals) → pseudonymized
         (runs on your machine, nothing leaves your computer)

Step 3 — EXTRACT SYMPTOMS  LLM reads the safe text → identifies symptoms in medical terms
         (uses cloud AI, but text contains no personal data)

Step 4 — FIND HPO CODES    Each symptom → matched against the full HPO ontology
         (runs on your machine, searches a local database)

Step 5 — VERIFY MATCHES    LLM picks the best HPO code from a shortlist of candidates
         (uses cloud AI, no personal data involved)

Step 6 — FORMAT OUTPUT     Results → Excel file in your existing format
         (runs on your machine)
```

**Key principle:** Steps that handle personal data (1, 2, 4, 6) run entirely on your machine. Steps that use cloud AI (3, 5) only ever see text where all personal data has already been replaced.

---

## 3. Architecture Choices — Explained

### 3.1 Transcription: faster-whisper (local)

**What it is:** An optimized version of OpenAI's Whisper speech recognition, running entirely on your computer.

**Why this choice:**
- Runs locally — audio recordings never leave your machine
- French-tuned models available with high accuracy (~92% on clear audio)
- 2-4x faster than the standard Whisper model
- Free to use (open source)

**Trade-off:** Phone recordings in a consultation room may have background noise. We will test with your actual recordings first. If quality is insufficient, we can add a lightweight noise reduction step later — but we prefer not to over-engineer before testing.

**Alternative considered:** Cloud transcription services (e.g., OpenAI's Whisper API) offer slightly better accuracy, but audio would leave your machine. Given GDPR requirements, this was ruled out.

---

### 3.2 Privacy Protection: OpenMed with Pseudonymization

**What it is:** OpenMed is an open-source AI model specifically trained to detect personal information in French medical text. It achieves 97.97% accuracy on French medical documents.

**Runs locally on your machine:** Yes. OpenMed is downloaded once (~500MB model file) and runs entirely on your computer. It requires no internet connection, no cloud service, and no subscription. The model runs on your CPU — no special hardware needed. It is installed as part of the tool's setup.

**How it works:**
Instead of simply deleting personal information (which would lose clinical context), we use *pseudonymization* — replacing identifiers with consistent numbered placeholders:

| Original text | Pseudonymized text |
|---|---|
| "Dr. Martin m'a orienté vers l'Hôpital Erasme en mars 2023" | "Dr. 1 m'a orienté vers Hospital 1 en Date 1" |
| "Dr. Martin a aussi prescrit..." | "Dr. 1 a aussi prescrit..." |

This preserves the clinical narrative (the patient saw the same doctor twice, was referred to a specific hospital at a specific time) while removing all identifying information.

**Why pseudonymization over full anonymization:**
- A patient saying *"since my grandmother died in March, I've had chest pain"* contains medically relevant temporal and emotional context
- Full anonymization would strip "March" and lose the temporal relationship
- Pseudonymization replaces "March" with "Date 1" — the AI can still understand the timeline

**The mapping table** (which knows that "Dr. 1" = "Dr. Martin") is kept locally on your machine and never sent anywhere.

**What it detects:** Names, dates, addresses, phone numbers, email addresses, national ID numbers, hospital names, doctor names — over 55 entity types.

---

### 3.3 HPO Code Matching: Hybrid Approach (AI + Ontology Database)

**The problem with asking an AI to generate HPO codes directly:**
Large language models are excellent at understanding symptoms described in everyday language. However, they are unreliable when asked to produce specific HPO codes — they frequently "hallucinate" codes that look plausible but are incorrect or don't exist.

**Evidence from your own data:** We spot-checked 20 HPO codes from your manually coded file (`hop_list_terms.csv`, produced with ChatGPT assistance). The symptom term names were excellent — but only **35% of the codes were correct**:

| Result | Count | Examples |
|---|---|---|
| Correct code | 7/20 | HP:0002354 = Memory impairment, HP:0100749 = Chest pain |
| Wrong code (exists but wrong term) | 10/20 | HP:0001250 labeled "Fatigue" — actually means **Seizure**; HP:0000855 labeled "Amnesia" — actually means **Insulin resistance** |
| Fabricated code (doesn't exist in HPO) | 3/20 | HP:0035415, HP:0035416, HP:0002369 — these codes do not exist in the ontology at all |

This confirms your intuition: the semantic matching is reliable, but the codes cannot be trusted when generated directly by an LLM. Our pipeline solves this by separating the two tasks.

**Our approach — a two-stage process:**

**Stage 1 — Clinical Translation (AI):**
The AI reads the French interview transcript and, for each symptom mentioned, produces:
- The patient's exact words (in French): *"j'ai mal au ventre"*
- A standardized medical term (in English): *"abdominal pain"*
- Clinical context: *"ongoing, worsening after meals"*

This plays to the AI's strength: understanding natural language and medical concepts.

**Stage 2 — Code Lookup (Database + AI verification):**
The English medical term is searched against a local database containing all ~17,000 HPO terms, their synonyms, and definitions. The database returns the 5 closest matches. Then the AI picks the best one from this shortlist.

**Why this is better than direct AI coding:**
- The AI never invents a code — it only selects from real, verified HPO codes
- The database search uses semantic similarity (understands that "belly pain" relates to "abdominal pain")
- The shortlist of 5 candidates is small enough for the AI to reason carefully
- Each term in the database is enriched with its official name, all synonyms, and definition for maximum matching accuracy

**Example:**

```
Patient says: "j'ai mal au ventre"
        ↓
AI extracts: clinical_term = "abdominal pain"
        ↓
Database returns top 5:
  1. Abdominal pain (HP:0002027)
  2. Chronic abdominal pain (HP:0011458)
  3. Recurrent abdominal pain (HP:0002574)
  4. Abdominal cramps (HP:0002829)
  5. Epigastric pain (HP:0410017)
        ↓
AI selects: Abdominal pain (HP:0002027) — best match for general complaint
```

---

### 3.4 LLM-Agnostic Design

**What it means:** The tool is not locked to any single AI provider. A configuration file lets you switch between:
- **OpenAI** (GPT-4o, etc.)
- **Anthropic** (Claude)
- **Local models** (via Ollama — runs entirely on your machine)

**Why this matters:**
- If a provider changes pricing or availability, you switch with one config change
- If you obtain access to a medical-specialized local model, you can use it without modifying the tool
- You control cost vs. quality trade-offs per provider

**Cost estimate for the full batch:**
- ~150 recordings × 2 AI calls each = ~300 API calls
- Total cost: approximately **$3-15** depending on provider (one-time for the existing batch)

---

### 3.5 Error Handling and Batch Processing

**Job tracking:** A local database tracks each recording's processing status:
- Which recordings have been processed
- Which failed and at which step
- Error messages for debugging

**Retry policy:** If a step fails (e.g., temporary API error), the tool retries once automatically. If it fails again, it logs the error and moves to the next recording. You can re-run failed recordings after investigating.

**This means:** You don't need to babysit a batch of 150 recordings. Run the command, come back later, and check the results. Any failures will be clearly reported.

---

## 4. GDPR and Data Security

| Data type | Where it's processed | Leaves your machine? |
|---|---|---|
| Audio recordings | Local (faster-whisper) | No |
| Raw transcripts | Local (OpenMed) | No |
| Personal data mapping | Local (SQLite) | No |
| Pseudonymized text | Cloud API (LLM) | Yes — but contains no personal data |
| HPO ontology database | Local (ChromaDB) | No |
| Final Excel output | Local (openpyxl) | No |

**Architecture guarantee:** The pipeline is designed so that personal data and cloud AI access are structurally separated. It is not possible for identifiable patient data to reach an external service — the pseudonymization step sits between local processing and any external call.

**If you use a local LLM (Ollama):** Nothing leaves your machine at all. The entire pipeline runs offline.

---

## 5. Validation Against Your Existing Work

You have 1003 manually coded patient records. We will use these as ground truth to measure the tool's accuracy.

**Additional validation data:** Your `hop_list_terms.csv` file contains ~767 rows across 30+ patients with HPO term names and French verbatim. A preliminary code audit revealed that while the **symptom term names are clinically accurate**, the HPO codes have a ~65% error rate (wrong codes or fabricated codes from ChatGPT). This means:

- The **term names** in this file are reliable ground truth for validating our symptom extraction (Stage 1)
- The **codes** need to be re-mapped against the official ontology — which is exactly what our pipeline's Stage 2 does
- As a bonus, the pipeline can **re-code this existing file** to produce a corrected version with verified HPO codes

**Scoring approach:**
- **Exact match** (same HPO code as your manual coding) = full credit
- **Close match** (parent or child term in the HPO hierarchy, e.g., "Dyspnea" vs "Dyspnea on exertion") = partial credit
- **Missed symptoms** and **incorrectly added symptoms** are tracked separately

This gives us a measurable quality score to evaluate the tool and tune it over time.

**Expectation setting:** The tool will likely not match your manual coding perfectly on the first run. The goal is to get it accurate enough that you only need to review and correct edge cases, rather than doing the full coding manually.

---

## 6. What the Tool Will NOT Do (Scope)

- **No real-time processing** — this is a batch tool. You collect recordings, then run the pipeline.
- **No graphical interface (for now)** — the tool runs from the command line. A user interface can be added later if the tool proves useful.
- **No automatic patient ID assignment** — you provide the patient ID with each recording.
- **No clinical interpretation** — the tool identifies and codes phenotypes. It does not diagnose, correlate, or analyze patterns across patients. That remains your scientific work.

---

## 7. Trade-offs Summary

| Decision | What we gain | What we accept |
|---|---|---|
| Local transcription | Full privacy, GDPR compliance | Slightly lower accuracy than cloud services on noisy audio |
| Pseudonymization (not anonymization) | Preserved clinical context | Depends on OpenMed's detection accuracy (97.97%) |
| Two-stage HPO matching | No hallucinated codes | Two AI calls per recording (minimal cost) |
| Sequential processing | Simple, debuggable | Slower than parallel (acceptable for 150 recordings) |
| LLM-agnostic design | Provider flexibility | Thin abstraction layer to maintain |
| Pipeline-first (no UI) | Faster to build, ship, and iterate | Requires command-line comfort (or someone to run it for you) |

---

## 8. Next Steps

1. **Your validation:** Review this document. Flag any concerns, misunderstandings, or missing requirements.
2. **Sample test:** We will process 3-5 real recordings to validate transcription quality and HPO matching accuracy before running the full batch.
3. **Development:** ~11 implementation tasks, built and tested incrementally.
4. **Validation run:** Compare pipeline output against your 1003 manually coded records.
5. **Iteration:** Tune prompts and matching parameters based on validation results.

---

*This document is intended for architecture validation before development begins. All technical choices are open for discussion.*
