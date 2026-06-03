"""Tests for the ontoGPT benchmark's comparison logic.

The translate / run_ontogpt steps hit external services and aren't
unit-tested. The `categorise` step is the load-bearing piece of the report. It
now credits "close" by Lin information-content similarity (the same primitive
`validate.py` uses), not by undirected hop count, so a near-root term does not
count as a near-hit to a specific code.
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

from phenoscribe.semantic_similarity import get_ic_map  # noqa: E402
from phenoscribe.validate import _get_hpo  # noqa: E402


@pytest.fixture(scope="module")
def hpo():
    return _get_hpo()


@pytest.fixture(scope="module")
def ic_map(hpo):
    return get_ic_map(hpo)


def test_categorise_exact_match(hpo, ic_map):
    pred = {"HP:0012378"}  # Fatigue
    ref = {"HP:0012378"}
    exact, close, unique = benchmark_ontogpt.categorise(pred, ref, hpo, ic_map)
    assert exact == {"HP:0012378"}
    assert close == set()
    assert unique == set()


def test_categorise_parent_counts_as_close(hpo, ic_map):
    # A direct parent of a leaf shares all of its IC with the leaf's MICA, so
    # Lin similarity is high -> "close". Exact still misses it.
    leaf = "HP:0002027"  # Abdominal pain
    parent = str(next(iter(hpo.graph.get_parents(leaf))))
    pred = {parent}
    ref = {leaf}
    exact, close, unique = benchmark_ontogpt.categorise(pred, ref, hpo, ic_map, tau=0.5)
    assert exact == set()
    assert close == {parent}
    assert unique == set()


def test_categorise_near_root_term_is_unique_not_close(hpo, ic_map):
    # "Phenotypic abnormality" (HP:0000118) would have been "close" under the
    # retired hop count; under IC it scores ~0 against a specific code, so it is
    # unique, not a near-hit.
    pred = {"HP:0000118"}
    ref = {"HP:0002027"}
    exact, close, unique = benchmark_ontogpt.categorise(pred, ref, hpo, ic_map)
    assert exact == set()
    assert close == set()
    assert unique == {"HP:0000118"}


def test_categorise_unrelated_term_is_unique(hpo, ic_map):
    # Fatigue (HP:0012378) is far from Abdominal pain (HP:0002027)
    pred = {"HP:0012378"}
    ref = {"HP:0002027"}
    exact, close, unique = benchmark_ontogpt.categorise(pred, ref, hpo, ic_map)
    assert exact == set()
    assert close == set()
    assert unique == {"HP:0012378"}


def test_categorise_empty_reference_means_everything_is_unique(hpo, ic_map):
    pred = {"HP:0012378", "HP:0002027"}
    exact, close, unique = benchmark_ontogpt.categorise(pred, set(), hpo, ic_map)
    assert exact == set()
    assert close == set()
    assert unique == pred


def test_categorise_empty_prediction_returns_empties(hpo, ic_map):
    exact, close, unique = benchmark_ontogpt.categorise(set(), {"HP:0012378"}, hpo, ic_map)
    assert exact == set()
    assert close == set()
    assert unique == set()
