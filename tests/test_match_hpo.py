"""Tests for the HPO judge response parser and match_hpo confidence flow.

Two failure modes from Peter Robinson's review drive these tests:

1. The LLM emits a valid HP ID but a mislabeled term — the parser should trust
   the candidate's canonical name once the ID passes the shortlist check.
2. The judge cannot justify a match (declines, hallucinates, or returns junk)
   — the parser must never ship that as an endorsed code. It leaves the row
   uncoded and flags needs_review so the GP sees what to check. A flagged top
   candidate still read as a code in Marc's review (post-concussion -> Exploding
   head syndrome), so no candidate stands in.
"""

import logging

import pytest

from phenoscribe.match_hpo import NO_MATCH_TERM, _parse_judge_response, match_hpo


CANDIDATES = [
    {"hpo_id": "HP:0012378", "name": "Fatigue", "distance": 0.12},
    {"hpo_id": "HP:0002027", "name": "Abdominal pain", "distance": 0.20},
    {"hpo_id": "HP:0001945", "name": "Fever", "distance": 0.25},
]


def _json(hpo_id, term, match=True, confidence=0.95):
    import json

    return json.dumps(
        {"hpo_id": hpo_id, "hpo_term": term, "match": match, "confidence": confidence}
    )


def test_matching_id_and_term_high_confidence(caplog):
    caplog.set_level(logging.INFO, logger="phenoscribe.match_hpo")
    result = _parse_judge_response(_json("HP:0012378", "Fatigue", confidence=0.95), CANDIDATES)

    assert result["hpo_id"] == "HP:0012378"
    assert result["hpo_term"] == "Fatigue"
    assert result["confidence"] == 0.95
    assert result["needs_review"] is False
    assert "label_corrected" not in caplog.text


def test_valid_id_with_wrong_term_gets_canonical_name(caplog):
    caplog.set_level(logging.INFO, logger="phenoscribe.match_hpo")
    # LLM mislabels: HP:0012378 is "Fatigue", not "Tiredness".
    result = _parse_judge_response(_json("HP:0012378", "Tiredness"), CANDIDATES)

    assert result["hpo_id"] == "HP:0012378"
    assert result["hpo_term"] == "Fatigue"
    assert result["needs_review"] is False
    assert "label_corrected" in caplog.text
    assert "Tiredness" in caplog.text


def test_low_confidence_flags_needs_review(caplog):
    caplog.set_level(logging.INFO, logger="phenoscribe.match_hpo")
    # Valid candidate, but the model is not sure.
    result = _parse_judge_response(_json("HP:0002027", "Abdominal pain", confidence=0.3), CANDIDATES)

    assert result["hpo_id"] == "HP:0002027"
    assert result["confidence"] == 0.3
    assert result["needs_review"] is True
    assert "low_confidence" in caplog.text


def test_judge_declines_shortlist_flags_review(caplog):
    caplog.set_level(logging.WARNING, logger="phenoscribe.match_hpo")
    # The model says no candidate fits. No code, flagged for review.
    result = _parse_judge_response(_json(None, None, match=False, confidence=0.1), CANDIDATES)

    assert result["hpo_id"] is None
    assert result["hpo_term"] == NO_MATCH_TERM
    assert result["confidence"] == 0.0
    assert result["needs_review"] is True
    assert "declined" in caplog.text


def test_id_not_in_candidates_flags_review(caplog):
    caplog.set_level(logging.WARNING, logger="phenoscribe.match_hpo")
    # LLM hallucinates an ID not in the shortlist. Never ship a non-candidate ID.
    result = _parse_judge_response(_json("HP:9999999", "Made-up term"), CANDIDATES)

    assert result["hpo_id"] is None
    assert result["needs_review"] is True
    assert "not in candidates" in caplog.text
    assert "HP:9999999" in caplog.text


def test_regex_fallback_recovers_candidate_but_flags_review():
    response = "I think the best match is HP:0001945 from the list."

    result = _parse_judge_response(response, CANDIDATES)

    assert result["hpo_id"] == "HP:0001945"
    assert result["hpo_term"] == "Fever"
    # Recovered from unstructured text: usable, but not endorsed.
    assert result["needs_review"] is True


def test_unparseable_response_left_uncoded_and_flags_review(caplog):
    caplog.set_level(logging.WARNING, logger="phenoscribe.match_hpo")
    result = _parse_judge_response("I have no idea, sorry.", CANDIDATES)

    assert result["hpo_id"] is None
    assert result["confidence"] == 0.0
    assert result["needs_review"] is True
    assert "Could not parse" in caplog.text


def test_markdown_code_fence_stripped():
    response = '```json\n' + _json("HP:0002027", "Abdominal pain") + '\n```'

    result = _parse_judge_response(response, CANDIDATES)

    assert result["hpo_id"] == "HP:0002027"
    assert result["needs_review"] is False


def test_garbage_confidence_treated_as_zero():
    result = _parse_judge_response(_json("HP:0012378", "Fatigue", confidence="very sure"), CANDIDATES)

    assert result["confidence"] == 0.0
    assert result["needs_review"] is True


# --- match_hpo integration: confidence flows into the output rows ---


def test_match_hpo_surfaces_confidence_and_review(monkeypatch):
    """A confident judge call yields needs_review=False with the reported confidence."""
    import phenoscribe.match_hpo as mod

    monkeypatch.setattr(
        mod, "search_hpo", lambda term, k, chroma_path, **kw: list(CANDIDATES)
    )
    monkeypatch.setattr(
        mod, "llm_call", lambda **kw: _json("HP:0012378", "Fatigue", confidence=0.9)
    )

    out = match_hpo([{"clinical_term": "tiredness", "patient_verbatim": "fatigué"}])

    assert len(out) == 1
    assert out[0]["hpo_id"] == "HP:0012378"
    assert out[0]["needs_review"] is False
    assert out[0]["confidence"] == 0.9
    assert out[0]["patient_verbatim"] == "fatigué"


def test_match_hpo_judge_call_error_stops_the_file(monkeypatch):
    """An error of the call itself (no credit) raises, so the file fails and runs again.

    The credit outage of 2026-10-26 once left 48 findings uncoded in files marked as done.
    """
    import phenoscribe.match_hpo as mod

    monkeypatch.setattr(
        mod, "search_hpo", lambda term, k, chroma_path, **kw: list(CANDIDATES)
    )

    def boom(**kw):
        raise RuntimeError("429 insufficient_quota")

    monkeypatch.setattr(mod, "llm_call", boom)

    with pytest.raises(RuntimeError, match="insufficient_quota"):
        match_hpo([{"clinical_term": "tiredness"}])


def test_an_unreadable_judge_answer_stays_uncoded(monkeypatch):
    import phenoscribe.match_hpo as mod

    monkeypatch.setattr(
        mod, "search_hpo", lambda term, k, chroma_path, **kw: list(CANDIDATES)
    )
    monkeypatch.setattr(mod, "llm_call", lambda **kw: "I am not sure.")

    out = match_hpo([{"clinical_term": "tiredness"}])

    assert out[0]["hpo_id"] is None and out[0]["hpo_term"] == NO_MATCH_TERM
    assert out[0]["needs_review"] is True


def test_a_denied_finding_tells_the_judge_to_code_it(monkeypatch):
    """A denied "pas de maux de tête" once made the judge refuse Headache at rank 1."""
    import phenoscribe.match_hpo as mod

    prompts: list[dict] = []
    monkeypatch.setattr(
        mod, "search_hpo", lambda term, k, chroma_path, **kw: list(CANDIDATES)
    )

    def capture(**kw):
        prompts.append(kw)
        return _json("HP:0012378", "Fatigue", confidence=0.9)

    monkeypatch.setattr(mod, "llm_call", capture)

    match_hpo([
        {"clinical_term": "fatigue", "patient_verbatim": "pas fatiguée", "negated": True},
        {"clinical_term": "fatigue", "patient_verbatim": "très fatiguée"},
    ])

    assert "Status: denied by the patient" in prompts[0]["user_prompt"]
    assert "Status:" not in prompts[1]["user_prompt"]
    assert "Never refuse a term because the patient" in prompts[0]["system_prompt"]


def test_an_ambiguous_exact_name_needs_review(monkeypatch):
    import phenoscribe.match_hpo as mod

    hits = [
        {"hpo_id": "HP:0012378", "name": "Fatigue", "distance": None, "exact": True, "ambiguous": True},
        {"hpo_id": "HP:0003388", "name": "Easy fatigability", "distance": 0.2, "exact": True, "ambiguous": True},
    ]
    monkeypatch.setattr(mod, "search_hpo", lambda term, k, chroma_path, **kw: list(hits))
    monkeypatch.setattr(mod, "llm_call", lambda **kw: _json("HP:0012378", "Fatigue", confidence=0.95))

    out = match_hpo([{"clinical_term": "tiredness"}])

    assert out[0]["hpo_id"] == "HP:0012378" and out[0]["needs_review"] is True


def test_an_exact_hit_with_no_distance_uses_the_vector_distance(monkeypatch):
    """The weak flag reads the best vector distance; an exact hit with None does not break it."""
    import phenoscribe.match_hpo as mod

    hits = [
        {"hpo_id": "HP:0002094", "name": "Dyspnea", "distance": None, "exact": True, "ambiguous": False},
        {"hpo_id": "HP:0025404", "name": "Rest dyspnea", "distance": 0.3},
    ]
    monkeypatch.setattr(mod, "search_hpo", lambda term, k, chroma_path, **kw: list(hits))
    monkeypatch.setattr(mod, "llm_call", lambda **kw: _json("HP:0002094", "Dyspnea", confidence=0.95))

    out = match_hpo([{"clinical_term": "shortness of breath"}])

    assert out[0]["hpo_id"] == "HP:0002094" and out[0]["needs_review"] is False


def test_match_hpo_weak_shortlist_flags_review(monkeypatch):
    """A confident judge over a distant shortlist is still flagged for review."""
    import phenoscribe.match_hpo as mod

    weak = [{"hpo_id": "HP:0012378", "name": "Fatigue", "distance": 0.9}]
    monkeypatch.setattr(mod, "search_hpo", lambda term, k, chroma_path, **kw: list(weak))
    monkeypatch.setattr(
        mod, "llm_call", lambda **kw: _json("HP:0012378", "Fatigue", confidence=0.95)
    )

    out = match_hpo([{"clinical_term": "obscure complaint"}])

    assert out[0]["needs_review"] is True
    assert out[0]["confidence"] <= 0.4


def test_judge_prompt_carries_patient_words(monkeypatch):
    """The judge sees the verbatim, so it can refuse a detail the patient never said."""
    import phenoscribe.match_hpo as mod

    prompts: list[str] = []
    monkeypatch.setattr(
        mod, "search_hpo", lambda term, k, chroma_path, **kw: list(CANDIDATES)
    )

    def capture(**kw):
        prompts.append(kw["user_prompt"])
        return _json("HP:0012378", "Fatigue", confidence=0.9)

    monkeypatch.setattr(mod, "llm_call", capture)

    match_hpo([{"clinical_term": "tinnitus", "patient_verbatim": "les acouphènes"}])

    assert "les acouphènes" in prompts[0]
