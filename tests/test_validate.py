"""Tests for the hpo-toolkit-backed validation scorer.

The previous strictly-up/strictly-down hierarchy walk missed siblings;
the BFS scorer should credit shared-ancestor relationships within
max_hops=2.
"""

import pytest

from phenoscribe.validate import _get_hpo, hop_distance, score_match


# Known HPO terms used in the tests (stable IDs from the public ontology):
#   HP:0012531  Pain                       (parent of Abdominal pain + Chest pain)
#   HP:0002027  Abdominal pain             (sibling of Chest pain under Pain)
#   HP:0100749  Chest pain                 (sibling of Abdominal pain under Pain)
#   HP:0012378  Fatigue                    (unrelated to the pain subtree)


@pytest.fixture(scope="module")
def hpo():
    return _get_hpo()


def test_exact_match_scores_one(hpo):
    assert score_match("HP:0002027", {"HP:0002027"}, hpo) == 1.0


def test_parent_relationship_scores_three_quarters(hpo):
    # Pain is the direct parent of Abdominal pain.
    assert hop_distance(hpo, "HP:0012531", "HP:0002027") == 1
    assert score_match("HP:0012531", {"HP:0002027"}, hpo) == 0.75


def test_child_relationship_scores_three_quarters(hpo):
    # Symmetric: predicting a child when ground truth is the parent.
    assert score_match("HP:0002027", {"HP:0012531"}, hpo) == 0.75


def test_sibling_via_shared_parent_scores_half(hpo):
    # Abdominal pain and Chest pain both have Pain as a direct parent.
    # The old strictly-up/strictly-down code scored this 0; the BFS should
    # find them at distance 2 through Pain.
    assert hop_distance(hpo, "HP:0002027", "HP:0100749") == 2
    assert score_match("HP:0002027", {"HP:0100749"}, hpo) == 0.5


def test_unrelated_terms_score_zero(hpo):
    # Abdominal pain and Fatigue are far apart in the ontology.
    assert hop_distance(hpo, "HP:0002027", "HP:0012378") is None
    assert score_match("HP:0002027", {"HP:0012378"}, hpo) == 0.0


def test_best_of_multiple_ground_truth_codes_wins(hpo):
    # Predicting Abdominal pain when ground truth has both an unrelated term
    # (Fatigue) and the exact match should yield 1.0.
    assert score_match("HP:0002027", {"HP:0012378", "HP:0002027"}, hpo) == 1.0


def test_hop_distance_is_symmetric(hpo):
    assert hop_distance(hpo, "HP:0002027", "HP:0012531") == hop_distance(
        hpo, "HP:0012531", "HP:0002027"
    )


def test_unknown_id_returns_none(hpo):
    assert hop_distance(hpo, "HP:9999999", "HP:0002027") is None


# --- Strict exact-match (document-level) F1 ---------------------------------
#
# The strict metric credits only exact HPO ID matches, no hierarchy expansion,
# matching the document-level convention in HPO concept-recognition benchmarks.

import openpyxl

from phenoscribe.output import write_excel
from phenoscribe.validate import load_codes_from_excel, validate


def _write(path, patient_id, codes):
    matches = [{"hpo_term": "t", "hpo_id": c, "patient_verbatim": ""} for c in codes]
    write_excel(patient_id, matches, str(path), fmt="semicolon")


def test_strict_f1_perfect_match(tmp_path):
    gt = tmp_path / "gt.xlsx"
    pred = tmp_path / "pred.xlsx"
    _write(gt, "MGA.467", ["HP:0002315", "HP:0002828"])
    _write(pred, "MGA.467", ["HP:0002315", "HP:0002828"])
    r = validate(str(gt), str(pred))
    assert r["strict_precision"] == 1.0
    assert r["strict_recall"] == 1.0
    assert r["strict_f1"] == 1.0


def test_strict_f1_partial(tmp_path):
    # 2 GT, 2 pred, 1 exact overlap -> P=0.5, R=0.5, F1=0.5
    gt = tmp_path / "gt.xlsx"
    pred = tmp_path / "pred.xlsx"
    _write(gt, "MGA.467", ["HP:0002315", "HP:0002828"])
    _write(pred, "MGA.467", ["HP:0002315", "HP:0009999"])
    r = validate(str(gt), str(pred))
    assert r["strict_tp"] == 1
    assert r["strict_precision"] == 0.5
    assert r["strict_recall"] == 0.5
    assert r["strict_f1"] == 0.5


def test_strict_f1_ignores_hierarchy(tmp_path):
    # Predicting the parent of a GT term scores >0 in partial credit but 0 strict.
    # HP:0012531 (Pain) is the parent of HP:0002027 (Abdominal pain).
    gt = tmp_path / "gt.xlsx"
    pred = tmp_path / "pred.xlsx"
    _write(gt, "MGA.467", ["HP:0002027"])
    _write(pred, "MGA.467", ["HP:0012531"])
    r = validate(str(gt), str(pred))
    assert r["strict_tp"] == 0
    assert r["strict_f1"] == 0.0
    # Partial-credit precision still credits the near miss.
    assert r["precision"] > 0.0


def _write_raw(path, rows):
    """Write a two-column (Patient_ID, observation) workbook verbatim."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Patient_ID", "observation_source_value"])
    for pid, obs in rows:
        ws.append([pid, obs])
    wb.save(str(path))


def test_mixed_delimiter_ground_truth_codes_are_extracted(tmp_path):
    # The ground truth mixes delimiter styles between patients. All three
    # forms below should yield the same HP codes for scoring.
    gt = tmp_path / "gt.xlsx"
    _write_raw(gt, [
        ("MGA.467", "Syncope/HP:0001279[fainting];\nVertige/HP:0002321[dizzy]"),
        ("MGA.087", "Fatigue|HP:0012378|tired ;\nAnxiety|HP:0000739|anxious"),
        ("MGA.014", "Cough (HP:0012735); Chest pain (HP:0100749)"),
    ])
    codes = load_codes_from_excel(str(gt))
    assert codes["MGA.467"] == {"HP:0001279", "HP:0002321"}
    assert codes["MGA.087"] == {"HP:0012378", "HP:0000739"}
    assert codes["MGA.014"] == {"HP:0012735", "HP:0100749"}
