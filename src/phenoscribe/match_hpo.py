"""HPO matching module — ChromaDB search + LLM judge (LLM Call 2)."""

import json
import logging

from phenoscribe.hpo_index import search_hpo
from phenoscribe.llm import llm_call

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = """\
You are an HPO (Human Phenotype Ontology) coding expert. Given a clinical concept and a \
list of candidate HPO terms, select the BEST matching HPO term.

Rules:
- Pick the most specific term that accurately describes the clinical concept
- If none of the candidates match well, pick the closest one and note it
- Return ONLY a JSON object with "hpo_id" and "hpo_term" keys
- Do NOT invent or modify HPO codes — only use codes from the candidates provided

Example output:
{"hpo_id": "HP:0002027", "hpo_term": "Abdominal pain"}"""


def match_hpo(
    symptoms: list[dict],
    provider: str = "openai",
    model: str = "gpt-4o",
    ollama_base_url: str = "http://localhost:11434",
    chroma_path: str = "data/chroma_db",
    k: int = 5,
) -> list[dict]:
    """Match extracted symptoms to HPO codes.

    For each symptom:
    1. Search ChromaDB with clinical_term -> top-k HPO candidates
    2. Ask LLM to pick the best match from the candidates

    Args:
        symptoms: List of dicts with clinical_term, patient_verbatim, context.
        provider: LLM provider.
        model: LLM model name.
        ollama_base_url: Ollama URL.
        chroma_path: Path to ChromaDB persistent storage.
        k: Number of HPO candidates to retrieve.

    Returns:
        List of dicts with hpo_id, hpo_term, patient_verbatim, clinical_term.
    """
    results = []

    for symptom in symptoms:
        clinical_term = symptom["clinical_term"]

        # Stage 1: Vector search
        candidates = search_hpo(clinical_term, k=k, chroma_path=chroma_path)
        if not candidates:
            logger.warning("No HPO candidates found for: %s", clinical_term)
            continue

        # Stage 2: LLM judge
        candidates_text = "\n".join(
            f"  {i+1}. {c['name']} ({c['hpo_id']})" for i, c in enumerate(candidates)
        )
        user_prompt = (
            f"Clinical concept: \"{clinical_term}\"\n"
            f"Context: {symptom.get('context', 'none')}\n\n"
            f"Candidate HPO terms:\n{candidates_text}\n\n"
            f"Which HPO term is the best match?"
        )

        try:
            response = llm_call(
                system_prompt=JUDGE_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                provider=provider,
                model=model,
                ollama_base_url=ollama_base_url,
            )
            selected = _parse_judge_response(response, candidates)
        except Exception as e:
            logger.warning("LLM judge failed for '%s', using top candidate: %s", clinical_term, e)
            selected = {"hpo_id": candidates[0]["hpo_id"], "hpo_term": candidates[0]["name"]}

        results.append(
            {
                "hpo_id": selected["hpo_id"],
                "hpo_term": selected["hpo_term"],
                "patient_verbatim": symptom.get("patient_verbatim", ""),
                "clinical_term": clinical_term,
            }
        )
        logger.info(
            "Matched '%s' -> %s (%s)",
            clinical_term,
            selected["hpo_term"],
            selected["hpo_id"],
        )

    return results


def _parse_judge_response(response: str, candidates: list[dict]) -> dict:
    """Parse LLM judge response, falling back to top candidate."""
    text = response.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
        if "hpo_id" in data and "hpo_term" in data:
            # Verify the code is from our candidates
            candidate_ids = {c["hpo_id"] for c in candidates}
            if data["hpo_id"] in candidate_ids:
                return data
            logger.warning("LLM selected code not in candidates: %s", data["hpo_id"])
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: try to find an HP: code in the response
    import re

    match = re.search(r"HP:\d{7}", text)
    if match:
        hpo_id = match.group()
        for c in candidates:
            if c["hpo_id"] == hpo_id:
                return {"hpo_id": hpo_id, "hpo_term": c["name"]}

    # Final fallback: top candidate
    logger.warning("Could not parse judge response, using top candidate.")
    return {"hpo_id": candidates[0]["hpo_id"], "hpo_term": candidates[0]["name"]}
