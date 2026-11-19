"""Tests for the IC-based validation scorer.

Term similarity uses the information content (IC) of the most informative
common ancestor (Resnik 1995, Lin 1998), built from phenotype.hpoa. These
tests pin two behaviours:

  - a near-root prediction against a specific truth scores ~0;
  - ancestor-vs-descendant predictions land on opposite error sides.
"""

import openpyxl
import pytest
from conftest import write_codes

from phenoscribe.config import HpoConfig
from phenoscribe.hpo_index import DEFAULT_HPO_RELEASE
from phenoscribe.semantic_similarity import (
    DIR_NON_SPECIFIC,
    DIR_OVER_SPECIFIC,
    DIR_RELATED,
    DIR_UNRELATED,
    error_direction,
    ic_distribution,
    ic_of,
    lin_similarity,
    resnik_similarity,
)
from phenoscribe.validate import classify_prediction, load_codes_from_excel, validate

# Known HPO terms used in the tests (stable IDs from the public ontology):
#   HP:0000118  Phenotypic abnormality     (near-root, IC ~ 0)
#   HP:0012531  Pain                       (parent of Abdominal pain + Chest pain)
#   HP:0002027  Abdominal pain             (sibling of Chest pain under Pain)
#   HP:0100749  Chest pain                 (sibling of Abdominal pain under Pain)
#   HP:0012378  Fatigue                    (unrelated to the pain subtree)


# --- the pinned release ---------------------------------------------------------


def test_validation_loads_the_pinned_release(hpo):
    assert hpo.version == DEFAULT_HPO_RELEASE.rsplit("/", 1)[-1]


def test_the_module_default_release_equals_the_config_default():
    assert HpoConfig().release == DEFAULT_HPO_RELEASE


# --- IC and similarity primitives -------------------------------------------


def test_root_term_has_zero_ic(ic_map):
    # Phenotypic abnormality subsumes every annotated disease -> IC 0.
    assert ic_of(ic_map, "HP:0000118") == 0.0


def test_specific_term_has_higher_ic_than_its_parent(ic_map):
    # Abdominal pain is more specific than Pain, so it carries more information.
    assert ic_of(ic_map, "HP:0002027") > ic_of(ic_map, "HP:0012531") > 0


def test_resnik_self_similarity_equals_ic(hpo, ic_map):
    # Resnik(x, x) == IC(x): the MICA of a term with itself is the term.
    term = "HP:0002027"
    assert resnik_similarity(ic_map, hpo, term, term) == pytest.approx(ic_of(ic_map, term))


def test_lin_self_similarity_is_one(hpo, ic_map):
    assert lin_similarity(ic_map, hpo, "HP:0002027", "HP:0002027") == pytest.approx(1.0)


# --- near-root predictions score ~0 ------------------------------------------


def test_near_root_prediction_scores_near_zero(hpo, ic_map):
    # A hop count would give this 0.75. Phenotypic abnormality shares
    # only a zero-IC ancestor with a specific truth, so Lin similarity is 0.
    score = lin_similarity(ic_map, hpo, "HP:0000118", "HP:0002027")
    assert score == pytest.approx(0.0, abs=1e-9)


def test_exact_match_scores_one(hpo, ic_map):
    assert lin_similarity(ic_map, hpo, "HP:0002027", "HP:0002027") == 1.0


def test_specific_relative_scores_high_but_below_one(hpo, ic_map):
    # Pain vs Abdominal pain share an informative ancestor (Pain), so Lin is
    # high, well above the near-root case, and below an exact match.
    score = lin_similarity(ic_map, hpo, "HP:0012531", "HP:0002027")
    assert 0.5 < score < 1.0


def test_unrelated_terms_score_zero(hpo, ic_map):
    # Abdominal pain vs Arachnodactyly (a hand-morphology term) share only the
    # zero-IC root, so Lin similarity is exactly 0.
    assert lin_similarity(ic_map, hpo, "HP:0002027", "HP:0001166") == pytest.approx(0.0, abs=1e-9)


# --- directional error classification ---------------------------------------


def test_predicting_ancestor_is_non_specific(hpo):
    # Predicting Pain when the truth is Abdominal pain is a recall-side error.
    assert error_direction(hpo, "HP:0012531", "HP:0002027") == DIR_NON_SPECIFIC


def test_predicting_descendant_is_over_specific(hpo):
    # Predicting Abdominal pain when the truth is Pain fabricates specificity.
    assert error_direction(hpo, "HP:0002027", "HP:0012531") == DIR_OVER_SPECIFIC


def test_classify_prediction_labels_ancestor_non_specific(hpo, ic_map):
    result = classify_prediction("HP:0012531", {"HP:0002027"}, hpo, ic_map)
    assert result["direction"] == DIR_NON_SPECIFIC
    assert result["matched_gt"] == "HP:0002027"


def test_classify_prediction_labels_descendant_over_specific(hpo, ic_map):
    result = classify_prediction("HP:0002027", {"HP:0012531"}, hpo, ic_map)
    assert result["direction"] == DIR_OVER_SPECIFIC
    assert result["matched_gt"] == "HP:0012531"


def test_siblings_under_an_informative_parent_are_related(hpo):
    # Abdominal pain and Chest pain share Pain.
    assert error_direction(hpo, "HP:0002027", "HP:0100749") == DIR_RELATED


def test_terms_that_share_only_the_roots_are_unrelated(hpo):
    # Abdominal pain and Arachnodactyly meet only at Phenotypic abnormality.
    assert error_direction(hpo, "HP:0002027", "HP:0001166") == DIR_UNRELATED


def test_directions_are_not_symmetric(hpo):
    # A too-general and a too-specific prediction are different errors.
    forward = error_direction(hpo, "HP:0012531", "HP:0002027")
    backward = error_direction(hpo, "HP:0002027", "HP:0012531")
    assert forward != backward


# --- IC distribution report ---------------------------------------------------


def test_ic_distribution_flags_low_ic_domination(ic_map):
    # A prediction set dominated by near-root terms is flagged.
    near_root = ["HP:0000118", "HP:0000001", "HP:0000118"]
    dist = ic_distribution(ic_map, near_root)
    assert dist["low_ic_dominated"] is True
    assert dist["low_ic_fraction"] == pytest.approx(1.0)


def test_ic_distribution_specific_terms_not_flagged(ic_map):
    specific = ["HP:0002027", "HP:0100749"]
    dist = ic_distribution(ic_map, specific)
    assert dist["low_ic_dominated"] is False
    assert dist["count"] == 2 and dist["max"] > dist["min"]


def test_ic_distribution_empty(ic_map):
    dist = ic_distribution(ic_map, [])
    assert dist["count"] == 0
    assert dist["low_ic_dominated"] is False


# --- Strict exact-match (document-level) F1 ---------------------------------
#
# The strict metric credits only exact HPO ID matches, no hierarchy expansion,
# matching the document-level convention in HPO concept-recognition benchmarks.


def test_strict_f1_perfect_match(tmp_path, mini_hpo_obo, ic_map):
    gt = tmp_path / "gt.xlsx"
    pred = tmp_path / "pred.xlsx"
    write_codes(gt, "P.001", ["HP:0002315", "HP:0002828"])
    write_codes(pred, "P.001", ["HP:0002315", "HP:0002828"])
    r = validate(str(gt), str(pred), obo_path=mini_hpo_obo)
    assert r["strict_precision"] == 1.0
    assert r["strict_recall"] == 1.0
    assert r["strict_f1"] == 1.0


def test_strict_f1_partial(tmp_path, mini_hpo_obo, ic_map):
    # 2 GT, 2 pred, 1 exact overlap -> P=0.5, R=0.5, F1=0.5
    gt = tmp_path / "gt.xlsx"
    pred = tmp_path / "pred.xlsx"
    write_codes(gt, "P.001", ["HP:0002315", "HP:0002828"])
    write_codes(pred, "P.001", ["HP:0002315", "HP:0009999"])
    r = validate(str(gt), str(pred), obo_path=mini_hpo_obo)
    assert r["strict_tp"] == 1
    assert r["strict_precision"] == 0.5
    assert r["strict_recall"] == 0.5
    assert r["strict_f1"] == 0.5


def test_strict_f1_ignores_hierarchy(tmp_path, mini_hpo_obo, ic_map):
    # Predicting the parent of a GT term scores >0 in partial credit but 0 strict.
    # HP:0012531 (Pain) is the parent of HP:0002027 (Abdominal pain).
    gt = tmp_path / "gt.xlsx"
    pred = tmp_path / "pred.xlsx"
    write_codes(gt, "P.001", ["HP:0002027"])
    write_codes(pred, "P.001", ["HP:0012531"])
    r = validate(str(gt), str(pred), obo_path=mini_hpo_obo)
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
        ("P.001", "Syncope/HP:0001279[fainting];\nVertige/HP:0002321[dizzy]"),
        ("P.009", "Fatigue|HP:0012378|tired ;\nAnxiety|HP:0000739|anxious"),
        ("P.003", "Cough (HP:0012735); Chest pain (HP:0100749)"),
    ])
    codes = load_codes_from_excel(str(gt))
    assert codes["P.001"] == {"HP:0001279", "HP:0002321"}
    assert codes["P.009"] == {"HP:0012378", "HP:0000739"}
    assert codes["P.003"] == {"HP:0012735", "HP:0100749"}


def test_no_shared_patient_scores_zero(tmp_path, mini_hpo_obo, ic_map):
    gt, pred = tmp_path / "gt.xlsx", tmp_path / "pred.xlsx"
    write_codes(gt, "P.001", ["HP:0002315"])
    write_codes(pred, "P.002", ["HP:0002315"])
    r = validate(str(gt), str(pred), obo_path=mini_hpo_obo)
    assert (r["strict_tp"], r["strict_precision"], r["strict_recall"], r["strict_f1"]) == (0, 0.0, 0.0, 0.0)
