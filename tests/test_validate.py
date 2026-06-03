"""Tests for the IC-based validation scorer.

Term similarity uses the information content (IC) of the most informative
common ancestor (Resnik 1995, Lin 1998), built from phenotype.hpoa. The old
scorer counted is_a hops and walked the DAG undirected; these tests pin the
two behaviours Robinson asked for:

  - a near-root prediction against a specific truth scores ~0 (not 0.75);
  - ancestor-vs-descendant predictions land on opposite error sides.

`hop_distance` is retained for the ontogpt benchmark script and still tested.
"""

import math

import pytest

from phenoscribe.semantic_similarity import (
    DIR_NON_SPECIFIC,
    DIR_OVER_SPECIFIC,
    error_direction,
    ic_distribution,
    ic_of,
    lin_similarity,
    resnik_similarity,
)
from phenoscribe.validate import (
    _get_hpo,
    _get_ic,
    classify_prediction,
    hop_distance,
    score_match,
)

# Known HPO terms used in the tests (stable IDs from the public ontology):
#   HP:0000118  Phenotypic abnormality     (near-root, IC ~ 0)
#   HP:0012531  Pain                       (parent of Abdominal pain + Chest pain)
#   HP:0002027  Abdominal pain             (sibling of Chest pain under Pain)
#   HP:0100749  Chest pain                 (sibling of Abdominal pain under Pain)
#   HP:0012378  Fatigue                    (unrelated to the pain subtree)


@pytest.fixture(scope="module")
def hpo():
    return _get_hpo()


@pytest.fixture(scope="module")
def ic(hpo):
    return _get_ic(hpo)


# --- IC and similarity primitives -------------------------------------------


def test_root_term_has_zero_ic(ic):
    # Phenotypic abnormality subsumes every annotated disease -> IC 0.
    assert ic_of(ic, "HP:0000118") == 0.0


def test_specific_term_has_higher_ic_than_its_parent(ic):
    # Abdominal pain is more specific than Pain, so it carries more information.
    assert ic_of(ic, "HP:0002027") > ic_of(ic, "HP:0012531") > 0


def test_resnik_self_similarity_equals_ic(hpo, ic):
    # Resnik(x, x) == IC(x): the MICA of a term with itself is the term.
    term = "HP:0002027"
    assert resnik_similarity(ic, hpo, term, term) == pytest.approx(ic_of(ic, term))


def test_lin_self_similarity_is_one(hpo, ic):
    assert lin_similarity(ic, hpo, "HP:0002027", "HP:0002027") == pytest.approx(1.0)


# --- the headline Robinson fix: near-root predictions score ~0 --------------


def test_near_root_prediction_scores_near_zero(hpo, ic):
    # The old hop-count scorer gave this 0.75. Phenotypic abnormality shares
    # only a zero-IC ancestor with a specific truth, so Lin similarity is 0.
    score = score_match("HP:0000118", {"HP:0002027"}, hpo, ic)
    assert score == pytest.approx(0.0, abs=1e-9)
    assert score < 0.05


def test_exact_match_scores_one(hpo, ic):
    assert score_match("HP:0002027", {"HP:0002027"}, hpo, ic) == 1.0


def test_specific_relative_scores_high_but_below_one(hpo, ic):
    # Pain vs Abdominal pain share an informative ancestor (Pain), so Lin is
    # high, well above the near-root case, and below an exact match.
    score = score_match("HP:0012531", {"HP:0002027"}, hpo, ic)
    assert 0.5 < score < 1.0


def test_unrelated_terms_score_zero(hpo, ic):
    # Abdominal pain vs Arachnodactyly (a hand-morphology term) share only the
    # zero-IC root, so Lin similarity is exactly 0.
    assert score_match("HP:0002027", {"HP:0001166"}, hpo, ic) == pytest.approx(0.0, abs=1e-9)


def test_best_of_multiple_ground_truth_codes_wins(hpo, ic):
    assert score_match("HP:0002027", {"HP:0012378", "HP:0002027"}, hpo, ic) == 1.0


# --- directional error classification ---------------------------------------


def test_predicting_ancestor_is_non_specific(hpo):
    # Predicting Pain when the truth is Abdominal pain is a recall-side error.
    assert error_direction(hpo, "HP:0012531", "HP:0002027") == DIR_NON_SPECIFIC


def test_predicting_descendant_is_over_specific(hpo):
    # Predicting Abdominal pain when the truth is Pain fabricates specificity.
    assert error_direction(hpo, "HP:0002027", "HP:0012531") == DIR_OVER_SPECIFIC


def test_classify_prediction_labels_ancestor_non_specific(hpo, ic):
    result = classify_prediction("HP:0012531", {"HP:0002027"}, hpo, ic)
    assert result["direction"] == DIR_NON_SPECIFIC
    assert result["matched_gt"] == "HP:0002027"


def test_classify_prediction_labels_descendant_over_specific(hpo, ic):
    result = classify_prediction("HP:0002027", {"HP:0012531"}, hpo, ic)
    assert result["direction"] == DIR_OVER_SPECIFIC
    assert result["matched_gt"] == "HP:0012531"


def test_directions_are_not_symmetric(hpo):
    # The whole point of issue #4b: the two directions differ.
    forward = error_direction(hpo, "HP:0012531", "HP:0002027")
    backward = error_direction(hpo, "HP:0002027", "HP:0012531")
    assert forward != backward


# --- IC distribution report (Q9) --------------------------------------------


def test_ic_distribution_flags_low_ic_domination(ic):
    # A prediction set dominated by near-root terms is flagged.
    near_root = ["HP:0000118", "HP:0000001", "HP:0000118"]
    dist = ic_distribution(ic, near_root)
    assert dist["low_ic_dominated"] is True
    assert dist["low_ic_fraction"] == pytest.approx(1.0)


def test_ic_distribution_specific_terms_not_flagged(ic):
    specific = ["HP:0002027", "HP:0100749"]
    dist = ic_distribution(ic, specific)
    assert dist["low_ic_dominated"] is False
    assert dist["max"] > dist["min"] or dist["count"] == 1


def test_ic_distribution_empty(ic):
    dist = ic_distribution(ic, [])
    assert dist["count"] == 0
    assert dist["low_ic_dominated"] is False


# --- hop_distance still works (used by the ontogpt benchmark) ---------------


def test_hop_distance_parent_is_one(hpo):
    assert hop_distance(hpo, "HP:0012531", "HP:0002027") == 1


def test_hop_distance_unknown_id_returns_none(hpo):
    assert hop_distance(hpo, "HP:9999999", "HP:0002027") is None
