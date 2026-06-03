"""HPO matching module — ChromaDB search + LLM judge (LLM Call 2)."""

import json
import logging
import re

from phenoscribe.hpo_index import build_obsolete_map, resolve_obsolete, search_hpo
from phenoscribe.llm import llm_call
from phenoscribe.modifiers import map_frequency, map_onset, map_severity

logger = logging.getLogger(__name__)

# Cosine distance above which the top vector candidate is treated as weak.
# ChromaDB cosine distance runs 0 (identical) to 2 (opposite). A symptom whose
# best HPO candidate sits beyond this distance gets flagged for review even when
# the judge picks confidently, because the shortlist itself may be off-target.
WEAK_MATCH_DISTANCE = 0.6

JUDGE_SYSTEM_PROMPT = """\
You are an HPO (Human Phenotype Ontology) coding expert. Given a clinical concept and a \
list of candidate HPO terms, select the BEST matching HPO term.

Rules:
- Pick the most specific term that accurately describes the clinical concept
- Only choose from the candidates provided. Do NOT invent or modify HPO codes.
- If NONE of the candidates is a defensible match, say so by setting "match" to false.
- Report your confidence in [0.0, 1.0]: how well the chosen term captures the concept.
- Return ONLY a JSON object with keys "hpo_id", "hpo_term", "match", "confidence".

Example (good match):
{"hpo_id": "HP:0002027", "hpo_term": "Abdominal pain", "match": true, "confidence": 0.95}

Example (no candidate fits):
{"hpo_id": null, "hpo_term": null, "match": false, "confidence": 0.1}"""

# Below this self-reported confidence the match is surfaced for review.
LOW_CONFIDENCE_THRESHOLD = 0.5


def match_hpo(
    symptoms: list[dict],
    provider: str = "openai",
    model: str = "gpt-4o",
    ollama_base_url: str = "http://localhost:11434",
    chroma_path: str = "data/chroma_db",
    k: int = 5,
    obo_path: str | None = None,
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
        obo_path: If given, the final selected code is run through
            resolve_obsolete so a retired id (e.g. from a stale index) maps to
            its active replacement. Defaults to None (no resolution).

    Returns:
        List of dicts with hpo_id, hpo_term, patient_verbatim, clinical_term,
        confidence (float in [0, 1]), and needs_review (bool). When the judge
        cannot justify a match the row is still returned, flagged for review,
        so a code is never silently dressed up as one the model endorsed.
        Also carries negated, frequency, onset, severity, and the mapped HPO
        subontology codes (frequency_hpo_id, onset_hpo_id, severity_hpo_id)
        where the modifier text resolved to an HPO leaf.
    """
    obsolete_map = build_obsolete_map(obo_path) if obo_path else {}
    results = []

    for symptom in symptoms:
        clinical_term = symptom["clinical_term"]
        negated = bool(symptom.get("negated", False))

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
            logger.warning(
                "LLM judge failed for '%s', falling back to top candidate (needs review): %s",
                clinical_term,
                e,
            )
            selected = {
                "hpo_id": candidates[0]["hpo_id"],
                "hpo_term": candidates[0]["name"],
                "confidence": 0.0,
                "needs_review": True,
            }

        # A weak vector shortlist drags confidence down even when the judge is
        # sure: it can only be sure relative to candidates that may all be wrong.
        top_distance = candidates[0].get("distance")
        if top_distance is not None and top_distance > WEAK_MATCH_DISTANCE:
            if not selected["needs_review"]:
                logger.info(
                    "weak_shortlist: term=%r top_distance=%.3f > %.2f -> needs_review",
                    clinical_term,
                    top_distance,
                    WEAK_MATCH_DISTANCE,
                )
            selected["needs_review"] = True
            selected["confidence"] = min(selected["confidence"], 0.4)

        if obsolete_map:
            resolved = resolve_obsolete(selected["hpo_id"], obsolete_map)
            if resolved != selected["hpo_id"]:
                logger.info(
                    "resolved_obsolete: %s -> %s", selected["hpo_id"], resolved
                )
                selected["hpo_id"] = resolved

        result = {
            "hpo_id": selected["hpo_id"],
            "hpo_term": selected["hpo_term"],
            "patient_verbatim": symptom.get("patient_verbatim", ""),
            "clinical_term": clinical_term,
            "confidence": round(selected["confidence"], 2),
            "needs_review": selected["needs_review"],
            "negated": negated,
            "frequency": symptom.get("frequency", ""),
            "onset": symptom.get("onset", ""),
            "severity": symptom.get("severity", ""),
        }
        _attach_modifier_codes(result)
        results.append(result)
        logger.info(
            "Matched '%s' -> %s (%s) confidence=%.2f needs_review=%s%s",
            clinical_term,
            selected["hpo_term"],
            selected["hpo_id"],
            selected["confidence"],
            selected["needs_review"],
            " [NEGATED/absent]" if negated else "",
        )

    n_negated = sum(1 for r in results if r["negated"])
    logger.info(
        "Matched %d findings (%d present coded, %d negated/absent).",
        len(results),
        len(results) - n_negated,
        n_negated,
    )
    return results


def present_codes(matches: list[dict]) -> list[dict]:
    """Return only findings the patient has. Negated findings are dropped.

    Use this when emitting present phenotype codes. Negated findings stay in
    the full ``matches`` list so they can be reported in a separate column.
    """
    return [m for m in matches if not m.get("negated", False)]


def absent_codes(matches: list[dict]) -> list[dict]:
    """Return only the negated/absent findings."""
    return [m for m in matches if m.get("negated", False)]


def _attach_modifier_codes(result: dict) -> None:
    """Resolve frequency/onset/severity text to HPO subontology leaves.

    Adds *_hpo_id / *_hpo_term keys when a value maps to an HPO leaf. Leaves
    the original text in place so unmapped values are not lost.
    """
    for field, mapper in (
        ("frequency", map_frequency),
        ("onset", map_onset),
        ("severity", map_severity),
    ):
        mapped = mapper(result.get(field, ""))
        if mapped:
            result[f"{field}_hpo_id"], result[f"{field}_hpo_term"] = mapped


def _parse_judge_response(response: str, candidates: list[dict]) -> dict:
    """Parse the LLM judge response into a match decision.

    Returns a dict with hpo_id, hpo_term, confidence (float in [0, 1]) and
    needs_review (bool). A match is flagged for review when the model declines
    the shortlist, the JSON fails to parse, the chosen code is not a candidate,
    or the reported confidence is low. The fallback to the top candidate stays,
    but it is always marked needs_review so it can never pass as an endorsed code.
    """
    text = response.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    candidate_by_id = {c["hpo_id"]: c["name"] for c in candidates}

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        data = None

    if isinstance(data, dict):
        hpo_id = data.get("hpo_id")
        # Explicit abstention: the model says no candidate fits.
        if data.get("match") is False:
            logger.warning(
                "Judge declined the shortlist (match=false); top candidate flagged for review."
            )
            return _fallback_top_candidate(candidates)

        if hpo_id in candidate_by_id:
            # LLMs reliably produce HPO labels but can drift on identifiers
            # (Peter Robinson, dec 2025). Trust the candidate's canonical
            # name once the ID has been verified against the shortlist.
            canonical_name = candidate_by_id[hpo_id]
            llm_term = data.get("hpo_term")
            if llm_term and llm_term != canonical_name:
                logger.info(
                    "label_corrected: id=%s llm=%r canonical=%r",
                    hpo_id,
                    llm_term,
                    canonical_name,
                )
            confidence = _coerce_confidence(data.get("confidence"))
            needs_review = confidence < LOW_CONFIDENCE_THRESHOLD
            if needs_review:
                logger.info(
                    "low_confidence: id=%s confidence=%.2f < %.2f -> needs_review",
                    hpo_id,
                    confidence,
                    LOW_CONFIDENCE_THRESHOLD,
                )
            return {
                "hpo_id": hpo_id,
                "hpo_term": canonical_name,
                "confidence": confidence,
                "needs_review": needs_review,
            }

        if hpo_id is not None:
            logger.warning("LLM selected code not in candidates: %s", hpo_id)

    # Fallback: try to find an HP: code in the response. A clean candidate
    # match here still came from unstructured text, so treat it as uncertain.
    match = re.search(r"HP:\d{7}", text)
    if match:
        hpo_id = match.group()
        if hpo_id in candidate_by_id:
            logger.warning(
                "Recovered candidate code %s from unstructured judge text; flagged for review.",
                hpo_id,
            )
            return {
                "hpo_id": hpo_id,
                "hpo_term": candidate_by_id[hpo_id],
                "confidence": 0.3,
                "needs_review": True,
            }

    # Final fallback: top candidate, surfaced for review (never silent).
    logger.warning("Could not parse judge response; top candidate flagged for review.")
    return _fallback_top_candidate(candidates)


def _fallback_top_candidate(candidates: list[dict]) -> dict:
    """Return the top vector candidate, always flagged for human review."""
    return {
        "hpo_id": candidates[0]["hpo_id"],
        "hpo_term": candidates[0]["name"],
        "confidence": 0.0,
        "needs_review": True,
    }


def _coerce_confidence(value) -> float:
    """Clamp the model's reported confidence to [0, 1]; treat junk as 0."""
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, conf))
