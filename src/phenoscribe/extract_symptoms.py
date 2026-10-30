"""Symptom extraction from pseudonymized transcripts (LLM Call 1)."""

import json
import logging

from phenoscribe.llm import llm_call

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a clinical phenotyping expert. You read French medical interview transcripts \
and extract every symptom, complaint, or clinical finding about the patient. \
The transcript has two speakers, the doctor and the patient. Each line starts with \
"D:" (the doctor speaks) or "P:" (the patient speaks). When the lines have no such label, \
tell the speakers apart by what they say: the doctor asks the questions, and the patient \
answers them, with yes, no, or a description. A question is almost always the doctor's.

For each finding, output:
- patient_verbatim: the patient's exact words in French, copied from a "P:" line. Never \
copy the doctor's question here. For a "doctor" finding only, the doctor's words
- source: who first names the finding, one of:
  - "patient": the patient names it without a question about it ("je suis tout le temps fatiguée")
  - "confirmed": the doctor names it in a question or a suggestion, and the patient answers \
yes, no, or with a description
  - "doctor": only the doctor names it, and the patient does not answer about it (a summary \
of an earlier visit, or a question with no answer)
- clinical_term: the standardized English medical term for this finding
- negated: true if the finding is denied or absent, false if the patient has it
- frequency: how often it occurs, normalized to one of: very frequent, frequent, occasional, very rare. Empty string if not stated.
- onset: when it began, normalized to one of: congenital, neonatal, antenatal, childhood, juvenile, adult. Empty string if not stated.
- severity: how bad it is, normalized to one of: borderline, mild, moderate, severe, profound. Empty string if not stated.
- context: any remaining temporal or qualifying detail that does not fit the fields above (e.g., "since March 2021", "worse after meals")
- mentions: the places in the transcript where this finding comes up, in transcript order, \
at most 5. Each item is a short quote (3 to 10 words) copied character for character from the \
transcript, without the [mm:ss] time markers and without the "D:" or "P:" label. Quote \
"P:" lines (the patient's answer "Oui, le soir."), not the doctor's question. Include the \
quote of patient_verbatim.

Negation is critical. A denied or absent finding must have negated=true. French negation cues include:
ne...pas, n'...pas, pas de, pas d', plus de, aucun, aucune, sans, absence de, jamais, ni...ni, \
nie, dément, ne présente pas, n'a pas de. Examples:
- "je n'ai pas de fièvre" -> fever, negated=true
- "aucune douleur thoracique" -> chest pain, negated=true
- "sans perte de poids" -> weight loss, negated=true
- "il nie toute céphalée" -> headache, negated=true
A finding the patient actively reports having is negated=false.

Rules:
- Extract ALL findings mentioned, present or absent, even if mild or historical
- Set negated correctly for every finding. Do NOT drop denied findings; mark them negated=true
- Use standard English medical terminology for clinical_term (e.g., "headache", "fatigue", "abdominal pain")
- Keep patient_verbatim in the original French
- For frequency, onset, severity: only fill them when the text states it; otherwise use an empty string
- If the same finding is mentioned multiple times, include it once with combined detail, \
and list each place in mentions
- One symptom for each finding. "nausées et diarrhée" gives two findings, nausea and diarrhea, \
with the same patient_verbatim and mentions. Keep a fixed medical phrase whole \
("nausea and vomiting")
- Do NOT include diagnoses (e.g., "COVID-19"), treatments, or procedures — only symptoms and findings
- A comparison to a past event or illness is not a finding. "comme après ma commotion cérébrale" \
is not "post-concussion syndrome": extract the symptoms the patient describes, not the comparison
- When the patient ties a daily activity to a symptom ("me déplacer en voiture, est-ce que c'est \
lié au vertige ?"), extract the symptom (vertigo), not the activity ("difficulty driving")
- Do NOT invent findings not mentioned in the text
- Source and negation of an answer to the doctor. Examples:
  - "Des palpitations ?" "Oui, le soir." -> palpitations, source "confirmed", negated=false
  - "Comme des fourmis dans les mains ?" "Oui, c'est ça." -> paresthesia, source "confirmed", \
negated=false. The doctor's words name the symptom, so it is "confirmed", not "patient"
  - "Et de la fièvre ?" "Non." -> fever, source "confirmed", negated=true
  - "Le goût, l'odorat ?" "Normal." -> two findings, taste and smell disorder, source \
"confirmed", negated=true
  - "Ces vertiges, c'est fini ?" "Non, ça continue." -> vertigo, negated=false. The "non" \
denies that the symptom stopped, so the patient still has it
  - "La dernière fois, vous aviez aussi des nausées." and the patient does not answer about \
them -> nausea, source "doctor"

Output ONLY a JSON array. No other text. Put each finding on one line, with no indentation. Example:
[
  {"patient_verbatim": "j'ai mal au ventre", "clinical_term": "abdominal pain", "negated": false, "source": "patient", "frequency": "", "onset": "adult", "severity": "severe", "context": "worse after meals", "mentions": ["j'ai mal au ventre", "le ventre me fait encore mal après le repas"]},
  {"patient_verbatim": "je n'ai pas de fièvre", "clinical_term": "fever", "negated": true, "source": "confirmed", "frequency": "", "onset": "", "severity": "", "context": "", "mentions": ["je n'ai pas de fièvre"]},
  {"patient_verbatim": "je suis fatigué", "clinical_term": "fatigue", "negated": false, "source": "patient", "frequency": "frequent", "onset": "", "severity": "", "context": "since COVID infection, March 2021", "mentions": ["je suis fatigué", "toujours cette fatigue le soir"]}
]"""

# A long interview can make the answer longer than the model's output limit.
# The cut JSON does not parse (MGA.363, 383, 404 and 365 on 2026-10-27). The
# second try asks for no mention quotes, which makes the answer much shorter.
NO_MENTIONS = (
    "\n\nThis transcript is long. Give an empty mentions list ([]) for every finding, "
    "to keep the answer short."
)


def extract_symptoms(
    transcript: str,
    provider: str = "openai",
    model: str = "gpt-4o",
    ollama_base_url: str = "http://localhost:11434",
    runs: int = 1,
) -> list[dict]:
    """Extract symptoms from a pseudonymized French transcript.

    Args:
        transcript: Pseudonymized French transcript text, with the speaker
            labels of speakers.label_speakers when they exist.
        provider: LLM provider.
        model: LLM model name.
        ollama_base_url: Ollama URL (if applicable).
        runs: how many times to extract (merge_runs joins the results).

    Returns:
        List of dicts with keys: patient_verbatim, clinical_term, negated,
        source, frequency, onset, severity, context, mentions, runs_found,
        n_runs.
    """
    def ask(system_prompt: str) -> str:
        return llm_call(
            system_prompt=system_prompt,
            user_prompt=transcript,
            provider=provider,
            model=model,
            ollama_base_url=ollama_base_url,
            temperature=0.0,
        )

    def one_run() -> list[dict]:
        response = ask(SYSTEM_PROMPT)
        try:
            return _parse_response(response)
        except ExtractionParseError:
            logger.warning(
                "Symptom answer not valid JSON (%d chars, probably cut off). "
                "Trying again with no mention quotes.",
                len(response),
            )
        return _parse_response(ask(SYSTEM_PROMPT + NO_MENTIONS))

    return merge_runs([one_run() for _ in range(max(1, runs))])


def merge_runs(runs: list[list[dict]]) -> list[dict]:
    """One list from several extraction runs on the same text.

    Two findings are the same when they have the same clinical term (any
    case) and the same negation. The first run's fields stay; the mentions
    of the later runs are added. Each finding gets ``runs_found`` (the run
    numbers, from 1) and ``n_runs``, so match_hpo.flag_unstable can mark a
    finding that is not in every run.
    """
    merged: dict[tuple, dict] = {}
    for number, findings in enumerate(runs, start=1):
        for f in findings:
            key = ((f.get("clinical_term") or "").strip().lower(), bool(f.get("negated", False)))
            if key not in merged:
                merged[key] = dict(f, runs_found=[number], n_runs=len(runs))
                continue
            kept = merged[key]
            if number not in kept["runs_found"]:
                kept["runs_found"].append(number)
            for quote in f.get("mentions") or []:
                if quote not in kept.setdefault("mentions", []):
                    kept["mentions"].append(quote)
            if not kept.get("patient_verbatim") and f.get("patient_verbatim"):
                kept["patient_verbatim"] = f["patient_verbatim"]
    return list(merged.values())


class ExtractionParseError(ValueError):
    """The extractor's response is not a JSON array of findings.

    Raised so the job fails and --retry-failed picks it up. An empty list here
    once let MGA.466 "complete" with zero rows (truncated JSON, 2026-10-21).
    """


def _parse_response(response: str) -> list[dict]:
    """Parse LLM JSON response, handling common formatting issues.

    Raises ExtractionParseError when no JSON array can be recovered.
    """
    text = response.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON array in the response
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                raise ExtractionParseError(
                    f"symptom extraction: response is not valid JSON: {text[:200]}"
                )
        else:
            raise ExtractionParseError(
                f"symptom extraction: no JSON array in response: {text[:200]}"
            )

    if not isinstance(data, list):
        raise ExtractionParseError(f"symptom extraction: response is a {type(data).__name__}, not a list")

    # Validate each item has required keys
    valid = []
    for item in data:
        if isinstance(item, dict) and "clinical_term" in item:
            valid.append(
                {
                    "patient_verbatim": item.get("patient_verbatim", ""),
                    "clinical_term": item["clinical_term"],
                    "negated": _coerce_bool(item.get("negated", False)),
                    "source": _coerce_source(item.get("source")),
                    "frequency": _coerce_str(item.get("frequency", "")),
                    "onset": _coerce_str(item.get("onset", "")),
                    "severity": _coerce_str(item.get("severity", "")),
                    "context": _coerce_str(item.get("context", "")),
                    "mentions": _coerce_quotes(item.get("mentions"), item.get("patient_verbatim", "")),
                }
            )

    n_negated = sum(1 for v in valid if v["negated"])
    logger.info(
        "Extracted %d findings from transcript (%d negated/absent).",
        len(valid),
        n_negated,
    )
    return valid


def _coerce_bool(value) -> bool:
    """Coerce a JSON value to bool, tolerating string truthiness from the LLM."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "negated", "absent")
    return bool(value)


SOURCES = ("patient", "confirmed", "doctor")


def _coerce_source(value) -> str:
    """Who brings up the finding: patient, confirmed or doctor; '' when the LLM gives no valid value."""
    text = _coerce_str(value).lower()
    return text if text in SOURCES else ""


def _coerce_str(value) -> str:
    """Coerce a JSON value to a trimmed string; None/non-strings become ''."""
    if value is None:
        return ""
    return str(value).strip()


def _coerce_quotes(value, verbatim) -> list[str]:
    """The mention quotes as a list of strings; the verbatim when there are none."""
    if isinstance(value, str):
        value = [value]
    quotes = [str(q).strip() for q in (value or []) if q is not None and str(q).strip()]
    if not quotes and verbatim:
        quotes = [str(verbatim).strip()]
    return quotes
