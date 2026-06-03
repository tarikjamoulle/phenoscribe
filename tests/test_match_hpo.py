"""Tests for the HPO judge response parser and match_hpo confidence flow.

Two failure modes from Peter Robinson's review drive these tests:

1. The LLM emits a valid HP ID but a mislabeled term — the parser should trust
   the candidate's canonical name once the ID passes the shortlist check.
2. The judge cannot justify a match (declines, hallucinates, or returns junk)
   — the parser must never ship that as an endorsed code. It falls back to the
   top candidate but flags needs_review so the GP sees what to check.
"""

import logging

import pytest

from phenoscribe.match_hpo import _parse_judge_response, match_hpo


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
    # The model says no candidate fits. We keep the top candidate but flag it.
    result = _parse_judge_response(_json(None, None, match=False, confidence=0.1), CANDIDATES)

    assert result["hpo_id"] == "HP:0012378"
    assert result["confidence"] == 0.0
    assert result["needs_review"] is True
    assert "declined" in caplog.text


def test_id_not_in_candidates_flags_review(caplog):
    caplog.set_level(logging.WARNING, logger="phenoscribe.match_hpo")
    # LLM hallucinates an ID not in the shortlist. Never ship a non-candidate ID.
    result = _parse_judge_response(_json("HP:9999999", "Made-up term"), CANDIDATES)

    assert result["hpo_id"] == "HP:0012378"
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


def test_unparseable_response_falls_back_and_flags_review(caplog):
    caplog.set_level(logging.WARNING, logger="phenoscribe.match_hpo")
    result = _parse_judge_response("I have no idea, sorry.", CANDIDATES)

    assert result["hpo_id"] == "HP:0012378"
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


def test_match_hpo_judge_exception_flags_review(monkeypatch):
    """If the LLM call raises, the top candidate is kept and flagged for review."""
    import phenoscribe.match_hpo as mod

    monkeypatch.setattr(
        mod, "search_hpo", lambda term, k, chroma_path, **kw: list(CANDIDATES)
    )

    def boom(**kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(mod, "llm_call", boom)

    out = match_hpo([{"clinical_term": "tiredness"}])

    assert out[0]["hpo_id"] == "HP:0012378"
    assert out[0]["needs_review"] is True
    assert out[0]["confidence"] == 0.0


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
