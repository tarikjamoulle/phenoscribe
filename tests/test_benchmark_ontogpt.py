"""Tests for the ontoGPT benchmark's comparison logic.

The translate / run_ontogpt steps hit external services and aren't
unit-tested. The `categorise` step is the load-bearing piece of the
report — pure set logic over hpo-toolkit's hop_distance.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# scripts/ is not a package; load the module by path so we can test it.
_spec = importlib.util.spec_from_file_location(
    "benchmark_ontogpt",
    Path(__file__).parent.parent / "scripts" / "benchmark_ontogpt.py",
)
benchmark_ontogpt = importlib.util.module_from_spec(_spec)
sys.modules["benchmark_ontogpt"] = benchmark_ontogpt
_spec.loader.exec_module(benchmark_ontogpt)

from phenoscribe.validate import _get_hpo  # noqa: E402


@pytest.fixture(scope="module")
def hpo():
    return _get_hpo()


def test_categorise_exact_match(hpo):
    pred = {"HP:0012378"}  # Fatigue
    ref = {"HP:0012378"}
    exact, close, unique = benchmark_ontogpt.categorise(pred, ref, hpo)
    assert exact == {"HP:0012378"}
    assert close == set()
    assert unique == set()


def test_categorise_sibling_via_shared_parent_counts_as_close(hpo):
    # Abdominal pain ↔ Chest pain via the shared parent Pain (distance 2)
    pred = {"HP:0002027"}
    ref = {"HP:0100749"}
    exact, close, unique = benchmark_ontogpt.categorise(pred, ref, hpo)
    assert exact == set()
    assert close == {"HP:0002027"}
    assert unique == set()


def test_categorise_unrelated_term_is_unique(hpo):
    # Fatigue (HP:0012378) is far from Abdominal pain (HP:0002027)
    pred = {"HP:0012378"}
    ref = {"HP:0002027"}
    exact, close, unique = benchmark_ontogpt.categorise(pred, ref, hpo)
    assert exact == set()
    assert close == set()
    assert unique == {"HP:0012378"}


def test_categorise_mixed_predictions(hpo):
    # Predict three codes: one exact, one sibling, one unrelated.
    pred = {"HP:0012378", "HP:0002027", "HP:0001945"}  # Fatigue, Abdominal pain, Fever
    ref = {"HP:0012378", "HP:0100749"}                 # Fatigue, Chest pain
    exact, close, unique = benchmark_ontogpt.categorise(pred, ref, hpo)
    assert exact == {"HP:0012378"}
    assert close == {"HP:0002027"}  # sibling via Pain
    assert unique == {"HP:0001945"}  # Fever unrelated to anything in ref


def test_categorise_empty_reference_means_everything_is_unique(hpo):
    pred = {"HP:0012378", "HP:0002027"}
    exact, close, unique = benchmark_ontogpt.categorise(pred, set(), hpo)
    assert exact == set()
    assert close == set()
    assert unique == pred


def test_categorise_empty_prediction_returns_empties(hpo):
    exact, close, unique = benchmark_ontogpt.categorise(set(), {"HP:0012378"}, hpo)
    assert exact == set()
    assert close == set()
    assert unique == set()
