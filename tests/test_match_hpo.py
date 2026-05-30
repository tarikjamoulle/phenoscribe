"""Tests for the HPO judge response parser.

Focus: Peter Robinson's flagged failure mode (LLM emits valid HP ID but a
mislabeled term). The parser should trust the candidate's canonical name
once the ID passes the shortlist check.
"""

import logging

from phenoscribe.match_hpo import _parse_judge_response


CANDIDATES = [
    {"hpo_id": "HP:0012378", "name": "Fatigue"},
    {"hpo_id": "HP:0002027", "name": "Abdominal pain"},
    {"hpo_id": "HP:0001945", "name": "Fever"},
]


def test_matching_id_and_term_returned_unchanged(caplog):
    caplog.set_level(logging.INFO, logger="phenoscribe.match_hpo")
    response = '{"hpo_id": "HP:0012378", "hpo_term": "Fatigue"}'

    result = _parse_judge_response(response, CANDIDATES)

    assert result == {"hpo_id": "HP:0012378", "hpo_term": "Fatigue"}
    assert "label_corrected" not in caplog.text


def test_valid_id_with_wrong_term_gets_canonical_name(caplog):
    caplog.set_level(logging.INFO, logger="phenoscribe.match_hpo")
    # LLM mislabels: HP:0012378 is "Fatigue", not "Tiredness".
    response = '{"hpo_id": "HP:0012378", "hpo_term": "Tiredness"}'

    result = _parse_judge_response(response, CANDIDATES)

    assert result == {"hpo_id": "HP:0012378", "hpo_term": "Fatigue"}
    assert "label_corrected" in caplog.text
    assert "HP:0012378" in caplog.text
    assert "Tiredness" in caplog.text
    assert "Fatigue" in caplog.text


def test_id_not_in_candidates_warns_and_uses_top_candidate(caplog):
    caplog.set_level(logging.WARNING, logger="phenoscribe.match_hpo")
    # LLM hallucinates an ID that isn't in the shortlist. The parser must
    # never ship a non-candidate ID, even if the LLM's label looks plausible.
    response = '{"hpo_id": "HP:9999999", "hpo_term": "Made-up term"}'

    result = _parse_judge_response(response, CANDIDATES)

    assert result == {"hpo_id": "HP:0012378", "hpo_term": "Fatigue"}
    assert "not in candidates" in caplog.text
    assert "HP:9999999" in caplog.text


def test_regex_fallback_uses_canonical_name():
    response = "I think the best match is HP:0001945 from the list."

    result = _parse_judge_response(response, CANDIDATES)

    assert result == {"hpo_id": "HP:0001945", "hpo_term": "Fever"}


def test_unparseable_response_returns_top_candidate(caplog):
    caplog.set_level(logging.WARNING, logger="phenoscribe.match_hpo")
    response = "I have no idea, sorry."

    result = _parse_judge_response(response, CANDIDATES)

    assert result == {"hpo_id": "HP:0012378", "hpo_term": "Fatigue"}
    assert "Could not parse" in caplog.text


def test_markdown_code_fence_stripped():
    response = '```json\n{"hpo_id": "HP:0002027", "hpo_term": "Abdominal pain"}\n```'

    result = _parse_judge_response(response, CANDIDATES)

    assert result == {"hpo_id": "HP:0002027", "hpo_term": "Abdominal pain"}
