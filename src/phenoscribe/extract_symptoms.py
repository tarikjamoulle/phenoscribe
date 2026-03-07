"""Symptom extraction from pseudonymized transcripts (LLM Call 1)."""

import json
import logging

from phenoscribe.llm import llm_call

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a clinical phenotyping expert. You read French medical interview transcripts \
and extract every symptom, complaint, or clinical finding mentioned by the patient or clinician.

For each symptom, output:
- patient_verbatim: the patient's exact words in French (or the clinician's description)
- clinical_term: the standardized English medical term for this symptom
- context: any temporal, severity, or qualifying information (e.g., "since March 2021", "worsening", "intermittent")

Rules:
- Extract ALL symptoms mentioned, even if mild or historical
- Use standard English medical terminology for clinical_term (e.g., "headache", "fatigue", "abdominal pain")
- Keep patient_verbatim in the original French
- If the same symptom is mentioned multiple times with different context, include it once with combined context
- Do NOT include diagnoses (e.g., "COVID-19"), treatments, or procedures — only symptoms and findings
- Do NOT invent symptoms not mentioned in the text

Output ONLY a JSON array. No other text. Example:
[
  {"patient_verbatim": "j'ai mal au ventre", "clinical_term": "abdominal pain", "context": "ongoing, worsening after meals"},
  {"patient_verbatim": "je suis fatigué", "clinical_term": "fatigue", "context": "since COVID infection, March 2021"}
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
                    "context": item.get("context", ""),
                }
            )

    logger.info("Extracted %d symptoms from transcript.", len(valid))
    return valid
