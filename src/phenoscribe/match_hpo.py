"""Steps 4 and 5: a shortlist of HPO terms for each finding, then the LLM judge picks one."""

import json
import logging
import re

from phenoscribe.config import LLMConfig
from phenoscribe.findings import Finding, finding_status, row_key
from phenoscribe.hpo_index import build_obsolete_map, resolve_obsolete, search_hpo
from phenoscribe.llm import llm_call
from phenoscribe.modifiers import map_frequency, map_onset, map_severity
from phenoscribe.text import strip_code_fence

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
- Pick the most specific term that the patient's words support. Do NOT pick a child \
term that adds a detail the patient did not state: "tinnitus" is Tinnitus, not \
Pulsatile tinnitus, unless the patient describes a pulse.
- Match on meaning, not on shared words. In HPO, "concentration" in a term name is \
the chemical level of a substance. A patient who cannot concentrate has a mental \
finding, so an enzyme or blood "concentration" term is NOT a match.
- The patient can deny the finding ("je n'ai pas de fièvre"). The status is recorded \
separately. Code the finding itself (Fever). Never refuse a term because the patient \
denies the finding.
- Only choose from the candidates provided. Do NOT invent or modify HPO codes.
- If NONE of the candidates is a defensible match, say so by setting "match" to false. \
A row with no code is better than a wrong code.
- Report your confidence in [0.0, 1.0]: how well the chosen term captures the concept.
- Return ONLY a JSON object with keys "hpo_id", "hpo_term", "match", "confidence".

Example (good match):
{"hpo_id": "HP:0002027", "hpo_term": "Abdominal pain", "match": true, "confidence": 0.95}

Example (no candidate fits):
{"hpo_id": null, "hpo_term": null, "match": false, "confidence": 0.1}"""

# Below this self-reported confidence the match is surfaced for review.
LOW_CONFIDENCE_THRESHOLD = 0.5

# The highest confidence of a code picked from a weak shortlist (WEAK_MATCH_DISTANCE).
WEAK_SHORTLIST_CONFIDENCE = 0.4
# The confidence of a code found in the free text of a judge answer that is no JSON.
RECOVERED_CODE_CONFIDENCE = 0.3

# hpo_term shown when no candidate is a defensible match. The row stays in the
# output (flagged for review) but carries no code, so a guess never reads as one.
NO_MATCH_TERM = "(no good match)"


def match_hpo(
    symptoms: list[Finding],
    llm: LLMConfig,
    chroma_path: str = "data/chroma_db",
    k: int = 10,
    obo_path: str | None = None,
    embedding_model: str | None = None,
    on_symptom=None,
) -> list[Finding]:
    """Each finding with its HPO code, the official term name, a confidence and a review flag.

    A finding with no defensible match keeps its row, flagged for review, with
    hpo_id None and hpo_term NO_MATCH_TERM, so a guess never passes as a code.
    A denied finding is coded like any other and keeps negated=True.

    k: the shortlist size. With the per-synonym index, the right term was in
    the top 10 for 74% of the reviewed findings and 98% of the control set,
    against 72% and 88% in the top 5. obo_path: an obsolete code moves to its
    active code (resolve_obsolete), and an exact name hit comes first in the
    shortlist. on_symptom gets (number, total) before each finding, for a
    progress bar: the judge call takes most of the time.
    """
    obsolete_map = build_obsolete_map(obo_path) if obo_path else {}
    results = []
    for number, symptom in enumerate(symptoms, 1):
        if on_symptom:
            on_symptom(number, len(symptoms))
        candidates = search_hpo(symptom["clinical_term"], k=k, chroma_path=chroma_path,
                                embedding_model=embedding_model, obo_path=obo_path)
        if candidates:
            decision = _judge(symptom, candidates, llm, obsolete_map)
        else:  # the finding stays visible, with no code and flagged for review
            logger.warning("No HPO candidates found for: %s", symptom["clinical_term"])
            decision = {**_no_match(), "confidence": None}
        results.append(_record(symptom, decision))
    _log_counts(results)
    return results


def _judge(symptom: Finding, candidates: list[dict], llm: LLMConfig, obsolete_map: dict) -> dict:
    """The judge's pick from the shortlist, after the code's own checks.

    An error of the call itself (no credit, no network) stops the file, so a
    rerun of "Find HPO codes" codes it again instead of leaving findings
    uncoded in a file marked as done. llm_call retries once.
    """
    response = llm_call(system_prompt=JUDGE_SYSTEM_PROMPT, user_prompt=judge_prompt(symptom, candidates),
                        llm=llm)
    selected = _parse_judge_response(response, candidates)
    _check_decision(selected, candidates, symptom["clinical_term"], obsolete_map)
    selected["confidence"] = round(selected["confidence"], 2)
    logger.info(
        "Matched '%s' -> %s (%s) confidence=%.2f needs_review=%s%s",
        symptom["clinical_term"], selected["hpo_term"], selected["hpo_id"], selected["confidence"],
        selected["needs_review"], " [NEGATED/absent]" if symptom.get("negated") else "",
    )
    return selected


def judge_prompt(symptom: Finding, candidates: list[dict]) -> str:
    """The user prompt of the judge: the finding, the patient's words and the shortlist.

    The judge codes the finding itself; the negation is recorded separately,
    so a denied symptom is judged on the same terms as a present one.
    """
    candidates_text = "\n".join(f"  {i + 1}. {c['name']} ({c['hpo_id']})" for i, c in enumerate(candidates))
    status = (
        "Status: denied by the patient (recorded separately; code the finding itself)\n"
        if symptom.get("negated") else ""
    )
    return (
        f"Clinical concept: \"{symptom['clinical_term']}\"\n"
        f"Patient's words: \"{symptom.get('patient_verbatim', '')}\"\n"
        f"Context: {symptom.get('context') or 'none'}\n"
        f"{status}\n"
        f"Candidate HPO terms:\n{candidates_text}\n\n"
        f"Which HPO term is the best match?"
    )


def _check_decision(selected: dict, candidates: list[dict], clinical_term: str, obsolete_map: dict) -> None:
    """The code's own checks on the judge's pick. They change ``selected`` in place.

    - A name that several HPO terms carry: the judge picks, a person checks.
    - A weak shortlist (best vector distance over WEAK_MATCH_DISTANCE) caps the
      confidence and needs review: the judge can only be sure relative to
      candidates that may all be wrong. An exact name hit that the vector
      search missed has no distance; the best vector distance decides.
    - An obsolete code moves to its active code.
    """
    if not selected["hpo_id"]:
        return
    if selected["hpo_id"] in {c["hpo_id"] for c in candidates if c.get("ambiguous")}:
        selected["needs_review"] = True

    top_distance = min((c["distance"] for c in candidates if c.get("distance") is not None), default=None)
    if top_distance is not None and top_distance > WEAK_MATCH_DISTANCE:
        if not selected["needs_review"]:
            logger.info("weak_shortlist: term=%r top_distance=%.3f > %.2f -> needs_review",
                        clinical_term, top_distance, WEAK_MATCH_DISTANCE)
        selected["needs_review"] = True
        selected["confidence"] = min(selected["confidence"], WEAK_SHORTLIST_CONFIDENCE)

    if obsolete_map:
        resolved = resolve_obsolete(selected["hpo_id"], obsolete_map)
        if resolved != selected["hpo_id"]:
            logger.info("resolved_obsolete: %s -> %s", selected["hpo_id"], resolved)
            selected["hpo_id"] = resolved


def _log_counts(results: list[Finding]) -> None:
    n_negated = sum(1 for r in results if r["negated"])
    n_doctor = sum(1 for r in results if finding_status(r) == "Doctor only")
    logger.info(
        "Matched %d findings (%d present coded, %d negated/absent, %d doctor only).",
        len(results), len(results) - n_negated - n_doctor, n_negated, n_doctor,
    )


def _record(symptom: Finding, selected: dict) -> Finding:
    """The finding with the decision (code, term, confidence, review flag) and its modifier codes."""
    result: Finding = {
        "hpo_id": selected["hpo_id"],
        "hpo_term": selected["hpo_term"],
        "patient_verbatim": symptom.get("patient_verbatim", ""),
        "clinical_term": symptom["clinical_term"],
        "confidence": selected["confidence"],
        "needs_review": selected["needs_review"],
        "negated": bool(symptom.get("negated", False)),
        "source": symptom.get("source", ""),
        "runs_found": list(symptom.get("runs_found") or []),
        "n_runs": symptom.get("n_runs", 0),
        "frequency": symptom.get("frequency", ""),
        "onset": symptom.get("onset", ""),
        "severity": symptom.get("severity", ""),
        "mentions": symptom.get("mentions") or [],
    }
    _attach_modifier_codes(result)
    return result


def flag_unstable(findings: list[Finding]) -> int:
    """Mark the findings that are not in every extraction run. Returns the count.

    Two runs can name one finding differently ("fatigue", "chronic fatigue"),
    so the runs are joined on the Excel row (findings.row_key): a code found
    by either name in both runs is stable. Every finding of a row gets the
    joined run list, so dedup can keep any of them. A finding in fewer runs
    than there are runs gets ``needs_review``.
    """
    by_key: dict[tuple, set[int]] = {}
    for f in findings:
        by_key.setdefault(row_key(f), set()).update(f.get("runs_found") or [])
    unstable = 0
    for f in findings:
        n = f.get("n_runs") or 0
        if n < 2:
            continue
        found = sorted(by_key[row_key(f)])
        f["runs_found"] = found
        if len(found) < n:
            f["needs_review"] = True
            unstable += 1
    return unstable


def _attach_modifier_codes(result: Finding) -> None:
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
    text = strip_code_fence(response)

    candidate_by_id = {c["hpo_id"]: c["name"] for c in candidates}

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        data = None

    if isinstance(data, dict):
        hpo_id = data.get("hpo_id")
        # Explicit abstention: the model says no candidate fits.
        if data.get("match") is False:
            logger.warning("Judge declined the shortlist (match=false); left uncoded for review.")
            return _no_match()

        if hpo_id in candidate_by_id:
            # LLMs can mislabel a valid identifier. Trust the candidate's
            # canonical name once the ID is verified against the shortlist.
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
                "confidence": RECOVERED_CODE_CONFIDENCE,
                "needs_review": True,
            }

    # Final fallback: no code, surfaced for review (never silent).
    logger.warning("Could not parse judge response; left uncoded for review.")
    return _no_match()


def _no_match() -> dict:
    """Return an uncoded decision, always flagged for human review.

    The top vector candidate is NOT used as a stand-in: a flagged fallback
    (post-concussion -> Exploding head syndrome) still reads as a code. An empty
    code cell says "nothing in HPO fit" without a guess.
    """
    return {
        "hpo_id": None,
        "hpo_term": NO_MATCH_TERM,
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
