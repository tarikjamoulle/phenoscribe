"""Tests for the HPO true-path (ancestor propagation) rule.

HPO is a DAG, and downstream tools assume the annotation set is
ancestor-closed. These tests pin the closure
against the real ontology release so the true-path expansion is correct.
"""

import pytest
from conftest import read_sheet

from phenoscribe import pipeline
from phenoscribe.config import Config
from phenoscribe.ontology import (
    HPO_ROOT,
    PHENOTYPIC_ABNORMALITY,
    load_hpo_graph,
    propagate_matches,
)
from phenoscribe.output import CLOSURE_SHEET, ResultsWorkbook, write_excel

# Episodic ataxia is a child of Ataxia, deep under Phenotypic abnormality.
EPISODIC_ATAXIA = "HP:0002131"
ATAXIA = "HP:0001251"


@pytest.fixture(scope="module")
def hpo_graph(hpo_obo):
    return load_hpo_graph(hpo_obo)


def test_episodic_ataxia_closure_reaches_ataxia_and_root(hpo_graph):
    ancestors = hpo_graph.ancestors(EPISODIC_ATAXIA)
    assert ATAXIA in ancestors
    assert PHENOTYPIC_ABNORMALITY in ancestors
    assert HPO_ROOT in ancestors
    # The term itself is not in its own ancestor set unless asked for.
    assert EPISODIC_ATAXIA not in ancestors


def test_include_source_adds_the_term_itself(hpo_graph):
    with_self = hpo_graph.ancestors(EPISODIC_ATAXIA, include_source=True)
    assert EPISODIC_ATAXIA in with_self
    assert ATAXIA in with_self


def test_closure_set_is_ancestor_closed(hpo_graph):
    closure = hpo_graph.closure([EPISODIC_ATAXIA])
    assert EPISODIC_ATAXIA in closure
    assert ATAXIA in closure
    assert PHENOTYPIC_ABNORMALITY in closure
    # An ancestor-closed set contains every ancestor of every member.
    for member in list(closure):
        assert hpo_graph.ancestors(member).issubset(closure)


def test_unknown_id_does_not_crash(hpo_graph):
    assert hpo_graph.ancestors("HP:9999999") == set()
    assert hpo_graph.ancestors("HP:9999999", include_source=True) == {"HP:9999999"}


def test_propagate_matches_keeps_leaves_and_adds_ancestors(hpo_obo):
    matches = [
        {"hpo_id": EPISODIC_ATAXIA, "hpo_term": "Episodic ataxia",
         "patient_verbatim": "comes and goes"},
    ]
    rows = propagate_matches(matches, hpo_obo)
    by_id = {r["hpo_id"]: r for r in rows}

    # Leaf preserved and flagged.
    assert by_id[EPISODIC_ATAXIA]["origin"] == "leaf"
    # Ancestors present, flagged, and traceable to the leaf.
    assert by_id[ATAXIA]["origin"] == "ancestor"
    assert EPISODIC_ATAXIA in by_id[ATAXIA]["derived_from"]
    assert by_id[ATAXIA]["hpo_term"] == "Ataxia"
    assert PHENOTYPIC_ABNORMALITY in by_id


def test_explicit_leaf_stays_a_leaf_even_if_it_is_an_ancestor(hpo_obo):
    # If both a term and its ancestor are predicted directly, the ancestor is
    # an explicit leaf, not a derived row.
    matches = [
        {"hpo_id": EPISODIC_ATAXIA, "hpo_term": "Episodic ataxia"},
        {"hpo_id": ATAXIA, "hpo_term": "Ataxia"},
    ]
    rows = propagate_matches(matches, hpo_obo)
    ataxia_rows = [r for r in rows if r["hpo_id"] == ATAXIA]
    assert len(ataxia_rows) == 1
    assert ataxia_rows[0]["origin"] == "leaf"


def test_write_excel_no_propagation_leaves_single_sheet(tmp_path):
    out = tmp_path / "results.xlsx"
    matches = [{"hpo_id": EPISODIC_ATAXIA, "hpo_term": "Episodic ataxia",
                "patient_verbatim": "v"}]
    write_excel("P1", matches, str(out), fmt="detailed", propagate_ancestors=False)

    header, rows = read_sheet(out)
    assert [row[header.index("HPO Code")] for row in rows] == [EPISODIC_ATAXIA]
    with pytest.raises(KeyError):
        read_sheet(out, CLOSURE_SHEET)


def test_write_excel_with_propagation_adds_closure_sheet(tmp_path, hpo_obo):
    out = tmp_path / "results.xlsx"
    matches = [{"hpo_id": EPISODIC_ATAXIA, "hpo_term": "Episodic ataxia",
                "patient_verbatim": "v"}]
    write_excel(
        "P1", matches, str(out), fmt="detailed",
        propagate_ancestors=True, obo_path=hpo_obo,
    )

    # The leaf sheet keeps one data row, the leaf only.
    header, rows = read_sheet(out)
    assert [row[header.index("HPO Code")] for row in rows] == [EPISODIC_ATAXIA]

    header, rows = read_sheet(out, CLOSURE_SHEET)
    closure_codes = {row[header.index("HPO Code")] for row in rows}
    assert EPISODIC_ATAXIA in closure_codes
    assert ATAXIA in closure_codes
    assert PHENOTYPIC_ABNORMALITY in closure_codes


def test_default_process_run_emits_ancestor_closure(tmp_path, monkeypatch, hpo_obo):
    """A plain Config(), as `phenoscribe process` builds it, writes the closure sheet."""
    config = Config()
    config.paths.hpo_obo = hpo_obo
    assert config.output.propagate_ancestors is True

    transcript = tmp_path / "P1.txt"
    transcript.write_text("seizure", encoding="utf-8")
    monkeypatch.setattr(pipeline, "detect_personal_details", lambda *a, **k: ([], []))
    monkeypatch.setattr(pipeline, "extract_symptoms", lambda *a, **k: [{"symptom": "seizure"}])
    monkeypatch.setattr(pipeline, "match_hpo", lambda *a, **k: [
        {"hpo_id": EPISODIC_ATAXIA, "hpo_term": "Episodic ataxia", "patient_verbatim": "seizure"}])
    excel = tmp_path / "out" / "results.xlsx"
    pipeline.process_recording(str(transcript), "P1", config, ResultsWorkbook.for_config(excel, config),
                               no_review=True)

    header, rows = read_sheet(excel)
    assert [row[header.index("HPO Code")] for row in rows] == [EPISODIC_ATAXIA]
    header, rows = read_sheet(excel, CLOSURE_SHEET)
    closure = {row[header.index("HPO Code")]: row[header.index("Origin")] for row in rows}
    assert {ATAXIA, PHENOTYPIC_ABNORMALITY} <= set(closure)
    assert closure[ATAXIA] == "ancestor"
