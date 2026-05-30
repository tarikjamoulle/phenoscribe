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
