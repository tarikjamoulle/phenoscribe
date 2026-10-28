---
audience: Marc (clinical stakeholder)
date: 2026-06-04
type: results delivery + how-to
---

# Phenoscribe — your 26 recordings, coded

Hi Marc,

Attached is `results.xlsx`: all 26 recordings run through the updated pipeline.
This note explains what the tool does now, what each column means, and the two
things I'd like you to do with it.

## What the tool does, step by step

1. **Writes down the recording.** A French speech-to-text model runs on the laptop
   and produces a transcript. The audio never leaves the machine.
2. **Blanks out personal details.** A local model swaps names, places, dates, phone
   numbers and IDs for stable placeholders ("Dr. Martin" → "PERSON_1"), so the
   conversation still reads normally but identities are hidden. Nothing identifying
   is ever sent off the machine.
3. **Pulls out each symptom.** An AI reads the cleaned transcript and lists every
   complaint, keeping the patient's own French words next to a plain-English label.
4. **Shortlists five candidate codes per symptom.** A meaning-based search over the
   ~19,000 HPO terms picks the five whose definitions are closest. The AI is only
   allowed to choose from these five.
5. **Picks the best code from those five.** The AI chooses one. The official term
   name is then read back from that code, so the code and the name can never end up
   disagreeing.
6. **Writes the Excel.** One row per symptom: code, term name, and the patient's own
   words side by side.

## What changed since last time

- **Names are now fully blanked.** A bug used to leave half a name behind (e.g.
  "Héloïse" → "PERSON_1loïse"). Fixed — checked across all 26, zero leaks.
- **No more repeated rows.** A complaint mentioned three times used to produce three
  identical rows. Now it's one row per symptom.
- **Each code now shows a plain-English label** of the symptom, next to the quote.

## Safeguards we build in (and their limits)

These are the checks that keep the output honest. Worth knowing so you can trust —
and challenge — what you see.

- **The AI never invents a code.** It can only choose from the five official
  candidates the search offers, and the term name is re-read from the chosen code.
  This blocks the most common failure of AI coding tools: confidently writing a code
  that doesn't exist or doesn't match its label.
- **Denied symptoms are kept separate.** "I have no fever" is recorded as **Absent**,
  not coded as a present finding. Missing this is the classic mistake in clinical
  text tools.¹
- **The broader categories of each code are included** (the "Ancestor Closure" tab).
  In HPO, if a patient has a specific finding they also have its parent findings —
  so tools like Exomiser or LIRICAL get the full picture, not just the leaf.²
- **Closeness is measured by how specific a term is, not just tree distance.** When
  we score how near two codes are, a vague near-the-top term ("abnormality of the
  body") counts for little; a specific shared parent counts for a lot.³
- **The HPO version is pinned and printed** in the file (the "Provenance" tab,
  release 2026-02-16), so the same recording always gives the same codes.

Honest limits:

- **The right code reaches the shortlist about 91% of the time** on these French
  recordings. So in roughly 1 in 10 cases the correct code may never have been
  offered to the AI to pick — those show up as a near-miss or a gap, not an obvious
  error.
- **The Confidence number is the AI's own self-rating**, not yet a real probability.
  Treat it as a rough hint; we're working on replacing it with a properly calibrated
  one.

## What each column means

| Column | What it is |
|---|---|
| **Patient_ID** | The recording (e.g. `MGA.467`). |
| **HPO Term** | The official name of the coded symptom (e.g. *Headache*). |
| **HPO Code** | The official code (e.g. `HP:0002315`). |
| **Present / Absent** | Whether the patient *had* the symptom or *denied* it. |
| **Frequency / Onset / Severity** | Extra detail when the patient gave it. Often blank. |
| **Patient Verbatim** | The patient's own words behind this code. **This is your check** — does the code fit the quote? |
| **Clinical Term** | The plain-English symptom the AI extracted before matching. A second view on what it understood. |
| **Needs Review** | Says **REVIEW** when the tool wasn't sure. Look at these first. |
| **Confidence** | The AI's own 0–1 self-rating (see limits above). |

Two extra tabs you can ignore unless useful: **Provenance** (HPO version) and
**Ancestor Closure** (the broader code list for other genetics tools — not for
reading by hand).

## What I'd like you to do

1. **Skim the rows marked REVIEW first** — that's where the tool was least sure. The
   *Patient Verbatim* lets you judge the code against what was actually said.
2. **Mark anything wrong** (a code that doesn't fit the quote, or a missing symptom)
   and send it back. Your corrections are how the tool improves — there's no test
   we're grading you against; your reading is what we calibrate toward.

Treat this as a first-pass assistant, not a replacement — every code is worth your
eye. Even a quick pass on a few recordings tells us a lot.

Anything unclear, or a column you'd shape differently, just say.

---

¹ Chapman et al., *A simple algorithm for identifying negated findings and diseases
in discharge summaries* (NegEx), J Biomed Inform, 2001.
² Köhler et al., *The Human Phenotype Ontology*, Nucleic Acids Research — the
annotation-propagation ("true-path") rule.
³ Resnik, *Using information content to evaluate semantic similarity* (1995); Lin,
*An information-theoretic definition of similarity* (1998).
