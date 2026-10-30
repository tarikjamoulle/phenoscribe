# Handoff: who brings up a symptom (doctor vs patient) — 2026-10-30

## Goal

PhenoScribe extracts HPO codes from French Long COVID interviews between a family
doctor (Dr Marc Jamoulle) and a patient. The question: how do we make sure an
extracted symptom comes from the patient, and not from the doctor who suggests it?
This matters for the EGPRN poster, whose title says the symptoms come from the
patients' own words.

## Constraints and decisions

- No diarization. The user decided it: the LLM must work out who speaks from the
  text. Rule: the doctor asks the questions; the patient answers yes, no, or with a
  description.
- The transcripts often have mixed speaker turns (a doctor question inside a patient
  turn), and the labels (`SPEAKER_00`, `Intervenant 1`) do not say who the doctor is.
- No gold standard. Measure agreement (tool vs person, person vs person), never
  "% correct".
- LLM: OpenAI gpt-4o, temperature 0.1 (`src/phenoscribe/llm.py`). The LLM reads
  only text after a personal-details review that a person approved.

## What the code did before

The extraction prompt (`src/phenoscribe/extract_symptoms.py`, `SYSTEM_PROMPT`)
asked for every finding "mentioned by the patient or clinician". Nothing marked the
speaker. A symptom that only the doctor said could become a present HPO code.

## What changed (branch `symptom-source`, uncommitted, 354 tests pass)

- Prompt: a new field `source` for each finding:
  - `patient`: the patient names it without a question about it.
  - `confirmed`: the doctor names it in a question or suggestion; the patient
    answers yes, no, or with a description.
  - `doctor`: only the doctor names it; the patient does not answer about it.
- Prompt: 6 examples of answers to the doctor: "Oui, le soir." (present),
  a symptom the doctor names ("Comme des fourmis… ?" "Oui" -> confirmed),
  "Non." (denied), "Le goût, l'odorat ? Normal." (two denied findings),
  "C'est fini ? Non, ça continue." (present: "non" denies the stop, not the
  symptom), a doctor summary with no answer (doctor).
- Parser: `_coerce_source`; an invalid or missing value gives "".
- `match_hpo.finding_status(m)`: "Absent" if negated, "Doctor only" if
  source == "doctor", else "Present". `present_codes` keeps only "Present".
- Excel detailed sheet: the `Present/Absent` column can now say "Doctor only";
  a new last column `Source`. Semicolon, purl, ancestor-closure sheets and
  `aggregate.py` prevalence leave out "Doctor only" rows.
- `output.dedup_by_code` (one row per code + negation): a row with a stronger
  source (patient > confirmed > "" > doctor) replaces the row; a doctor row adds
  no verbatim to a stronger row.
- `timeline.mention_rows`: every mention of a finding gets the status of its row.
- Old rows with an empty source stay "Present", so old workbooks keep their counts.
- Tests: `tests/test_source.py` (new), `tests/test_icpc.py` (column slices).

A Codex review (gpt-6-luna) found 4 bugs in the first version (empty status cells
dropped from prevalence, dedup keeping doctor words as patient words, mixed status
per mention, wrong log count). All 4 are fixed and tested.

## Evidence: interview 467 (cleaned text), 4 runs

| | Old prompt run 1 | Old prompt run 2 | New prompt run 1 | New prompt run 2 |
|---|---|---|---|---|
| Findings | 18 | 20 | 25 | 18 |
| `confirmed`, present | 0 | 5 | 7 | 6 |
| `confirmed`, denied | 2 | 2 | 5 | 1 |
| `doctor` | 0 | 0 | 0 | 0 |
| Taste/smell "Normal." -> 2 denied | missed | missed | found | missed |
| Paresthesia named by the doctor -> confirmed | patient | patient | patient | confirmed |
| Dry eyes ("c'est fini ?" "Non, ça continue") -> present | yes | yes | yes | yes |
| Dyspnea, fast heart rate | — | found | found | missed |

## Open problems

1. Run-to-run instability of extraction itself: the new prompt finds 25 vs 18
   findings on the same text. This existed before the source field. The source
   label is mostly right when the finding is found.
2. `patient_verbatim` sometimes holds the doctor's words (dry eyes:
   "sécheresse oculaire" is the doctor's term).
3. No `doctor` finding appeared on 467, so the "Doctor only" path is untested on
   real data. Interview 467 is a first interview; follow-up interviews
   ("evolution"), where the doctor summarizes earlier visits, are more likely to
   have them.
4. Only one interview tested. No hand labels exist.

## Options discussed (not built)

1. Quotes as proof: the model copies the doctor's question and the patient's
   answer for each `confirmed` finding; the code checks the quotes exist in the
   transcript (the `Transcript.locate_all` matcher in `timeline.py` can do it).
   Codex: a quote shows the text exists, not who said it.
2. More real examples in the prompt.
3. Temperature 0, and 2 runs per file; disagreement -> needs review. Codex:
   measure the disagreement rate first.
4. Diarization: rejected by the user.
5. Hand check: write a short label rule, two people label the source of each
   finding in the same 5 interviews, independently; measure agreement (Codex
   advice). About 1 hour per interview.

## Files

- `src/phenoscribe/extract_symptoms.py` (prompt, parser)
- `src/phenoscribe/match_hpo.py` (`finding_status`, `present_codes`)
- `src/phenoscribe/output.py` (`dedup_by_code`, `source_rank`, sheets)
- `src/phenoscribe/timeline.py`, `aggregate.py`, `gui.py`
- Check script (not in repo):
  `/private/tmp/claude-501/-Users-tarikjamoulle-projects-hpo-identifier/8dfdd959-32f1-42d6-a898-de7abb2783bd/scratchpad/src_check.py <clean .txt>`;
  run after `source setup.sh` (holds the API keys; never print them).

## Built on 2026-10-30 (same branch, uncommitted, 366 tests pass)

The two jobs are split. No manual step.

1. Speaker pass (`src/phenoscribe/speakers.py`, `label_speakers`): the model copies
   the clean text, one turn per line, `D:` or `P:`, temperature 0, in chunks of
   7,000 characters. The code checks the copy keeps the words (`copy_similarity`
   >= 0.9); a bad chunk is asked again, a second failure codes the plain text with a
   warning. The labeled text is saved as `output/speakers/<patient_id>.txt`.
2. Extraction reads the labeled text and quotes `P:` lines only. It runs twice
   (`llm.extraction_runs`, config). `merge_runs` joins the runs on clinical term;
   `match_hpo.flag_unstable` joins on HPO code and sets `needs_review` when a code
   is not in every run. Excel column `Runs` ("2/2", "1/2").
3. `check_speakers` finds each quote in the labeled text. Column `Spoken by`:
   `patient`, `patient answer` (the doctor names it, the patient's next turn starts
   with oui/non/surtout/...; the answer becomes the verbatim, source `confirmed`),
   `doctor` (status "Doctor only") or `not found` (needs review).
4. `llm_call` has a `temperature` argument; the two passes use 0, the HPO judge
   keeps 0.1. Config: `llm.speaker_labels` (true), `llm.extraction_runs` (2).

Interview 467, two full runs: speaker pass 170 and 173 turns, copy similarity
0.999, 21 s; 19 of ~170 lines differ between the runs (turn splits). Rows 26 and
28; not in both runs 7 and 9. Dry eyes verbatim is now the patient's words. Leg
weakness ("Oui, j'ai l'impression.") is `patient answer`, confirmed. Check script:
`/private/tmp/claude-501/-Users-tarikjamoulle-projects-hpo-identifier/bf543a83-c8a7-4f54-9497-6f58ecbc873d/scratchpad/real_check.py <clean .txt> <labeled out .txt>`
after `source setup.sh`.

Open: the extraction itself still differs between runs (the `Runs` column shows
it); no follow-up interview tested yet; the answer-word list (`_ANSWER`) is a
guess from one interview.
