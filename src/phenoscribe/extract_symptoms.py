"""Symptom extraction from pseudonymized transcripts (LLM Call 1)."""

import json
import logging

from phenoscribe.llm import llm_call

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a clinical phenotyping expert. You read French medical interview transcripts \
and extract every symptom, complaint, or clinical finding mentioned by the patient or clinician.

For each finding, output:
- patient_verbatim: the patient's exact words in French (or the clinician's description)
- clinical_term: the standardized English medical term for this finding
- negated: true if the finding is denied or absent, false if the patient has it
- frequency: how often it occurs, normalized to one of: very frequent, frequent, occasional, very rare. Empty string if not stated.
- onset: when it began, normalized to one of: congenital, neonatal, antenatal, childhood, juvenile, adult. Empty string if not stated.
- severity: how bad it is, normalized to one of: borderline, mild, moderate, severe, profound. Empty string if not stated.
- context: any remaining temporal or qualifying detail that does not fit the fields above (e.g., "since March 2021", "worse after meals")

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
- If the same finding is mentioned multiple times, include it once with combined detail
- Do NOT include diagnoses (e.g., "COVID-19"), treatments, or procedures — only symptoms and findings
- Do NOT invent findings not mentioned in the text

Output ONLY a JSON array. No other text. Example:
[
  {"patient_verbatim": "j'ai mal au ventre", "clinical_term": "abdominal pain", "negated": false, "frequency": "", "onset": "adult", "severity": "severe", "context": "worse after meals"},
  {"patient_verbatim": "je n'ai pas de fièvre", "clinical_term": "fever", "negated": true, "frequency": "", "onset": "", "severity": "", "context": ""},
  {"patient_verbatim": "je suis fatigué", "clinical_term": "fatigue", "negated": false, "frequency": "frequent", "onset": "", "severity": "", "context": "since COVID infection, March 2021"}
]"""


def extract_symptoms(
    transcript: str,
    provider: str = "openai",
    model: str = "gpt-4o",
    ollama_base_url: str = "http://localhost:11434",
) -> list[dict]:
    """Extract symptoms from a pseudonymized French transcript.

    Args:
        transcript: Pseudonymized French transcript text.
        provider: LLM provider.
        model: LLM model name.
        ollama_base_url: Ollama URL (if applicable).

    Returns:
        List of dicts with keys: patient_verbatim, clinical_term, context.
    """
    response = llm_call(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=transcript,
        provider=provider,
        model=model,
        ollama_base_url=ollama_base_url,
    )

    return _parse_response(response)


def _parse_response(response: str) -> list[dict]:
    """Parse LLM JSON response, handling common formatting issues."""
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
                logger.error("Failed to parse LLM response as JSON: %s", text[:200])
                return []
        else:
            logger.error("No JSON array found in LLM response: %s", text[:200])
            return []

    if not isinstance(data, list):
        logger.error("LLM response is not a list: %s", type(data))
        return []

    # Validate each item has required keys
    valid = []
    for item in data:
        if isinstance(item, dict) and "clinical_term" in item:
            valid.append(
                {
                    "patient_verbatim": item.get("patient_verbatim", ""),
                    "clinical_term": item["clinical_term"],
                    "negated": _coerce_bool(item.get("negated", False)),
                    "frequency": _coerce_str(item.get("frequency", "")),
                    "onset": _coerce_str(item.get("onset", "")),
                    "severity": _coerce_str(item.get("severity", "")),
                    "context": _coerce_str(item.get("context", "")),
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


def _coerce_str(value) -> str:
    """Coerce a JSON value to a trimmed string; None/non-strings become ''."""
    if value is None:
        return ""
    return str(value).strip()
