"""Tests for the GSC+ benchmark's pure logic.

The retrieval / FastHPOCR / LLM steps hit external resources and are not
unit-tested here. The load-bearing pieces are: parsing the GSC+ annotation
format, normalising HP identifiers, and the document-level scorer (exact and
lenient). Those are tested against a tiny synthetic corpus and the real HPO
graph.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# scripts/ is not a package; load the module by path.
_spec = importlib.util.spec_from_file_location(
    "benchmark_gsc",
    Path(__file__).parent.parent / "scripts" / "benchmark_gsc.py",
)
benchmark_gsc = importlib.util.module_from_spec(_spec)
sys.modules["benchmark_gsc"] = benchmark_gsc
_spec.loader.exec_module(benchmark_gsc)

from phenoscribe.semantic_similarity import get_ic_map  # noqa: E402
from phenoscribe.validate import _get_hpo  # noqa: E402


@pytest.fixture(scope="module")
def hpo():
    return _get_hpo()


@pytest.fixture(scope="module")
def ic_map(hpo):
    return get_ic_map(hpo)


# --- normalize_hp -----------------------------------------------------------


def test_normalize_hp_handles_purl():
    assert benchmark_gsc.normalize_hp(
        "http://purl.obolibrary.org/obo/HP_0001234"
    ) == "HP:0001234"


def test_normalize_hp_handles_colon_form():
    assert benchmark_gsc.normalize_hp("HP:0001234") == "HP:0001234"


def test_normalize_hp_handles_underscore_form():
    assert benchmark_gsc.normalize_hp("HP_0001234") == "HP:0001234"


def test_normalize_hp_rejects_garbage():
    assert benchmark_gsc.normalize_hp("not an hp code") is None
    assert benchmark_gsc.normalize_hp("") is None
    assert benchmark_gsc.normalize_hp(None) is None


# --- GSC loader -------------------------------------------------------------


def _write_corpus(tmp_path: Path) -> Path:
    gsc = tmp_path / "GSC"
    (gsc / "Text").mkdir(parents=True)
    (gsc / "Annotations").mkdir(parents=True)
    (gsc / "Text" / "111").write_text(
        "Patient with short stature and abdominal pain.", encoding="utf-8"
    )
    (gsc / "Annotations" / "111").write_text(
        "13:26\tHP:0004322\tshort stature\n"
        "31:45\tHP:0002027\tabdominal pain\n",
        encoding="utf-8",
    )
    # A doc whose annotation uses the PURL form to prove normalisation runs.
    (gsc / "Text" / "222").write_text("Fever.", encoding="utf-8")
    (gsc / "Annotations" / "222").write_text(
        "0:5\thttp://purl.obolibrary.org/obo/HP_0001945\tFever\n",
        encoding="utf-8",
    )
    return gsc


def test_load_gsc_parses_documents_and_mentions(tmp_path):
    gsc = _write_corpus(tmp_path)
    docs = {d.pmid: d for d in benchmark_gsc.load_gsc(gsc)}
    assert set(docs) == {"111", "222"}
    assert docs["111"].gold_ids == {"HP:0004322", "HP:0002027"}
    assert ("short stature", "HP:0004322") in docs["111"].gold_mentions
    # PURL form normalised to canonical HP:
    assert docs["222"].gold_ids == {"HP:0001945"}


def test_load_gsc_skips_text_files_without_annotations(tmp_path):
    gsc = _write_corpus(tmp_path)
    (gsc / "Text" / "333").write_text("Orphan.", encoding="utf-8")
    docs = {d.pmid: d for d in benchmark_gsc.load_gsc(gsc)}
    assert "333" not in docs


# --- document-level scorer (exact) -----------------------------------------


def _doc(pmid, gold):
    return benchmark_gsc.GscDoc(pmid=pmid, text="", gold_ids=set(gold))


def test_score_exact_perfect():
    docs = [_doc("a", {"HP:0001945", "HP:0002027"})]
    pred = {"a": {"HP:0001945", "HP:0002027"}}
    s = benchmark_gsc.score_documents(pred, docs)
    assert s["tp"] == 2 and s["fp"] == 0 and s["fn"] == 0
    assert s["precision"] == 1.0 and s["recall"] == 1.0 and s["f1"] == 1.0


def test_score_exact_counts_fp_and_fn():
    # gold {A,B}, pred {A,C}: TP=A, FP=C, FN=B
    docs = [_doc("a", {"HP:0001945", "HP:0002027"})]
    pred = {"a": {"HP:0001945", "HP:0012378"}}
    s = benchmark_gsc.score_documents(pred, docs)
    assert s["tp"] == 1 and s["fp"] == 1 and s["fn"] == 1
    assert s["precision"] == 0.5 and s["recall"] == 0.5


def test_score_exact_empty_prediction_is_zero_recall():
    docs = [_doc("a", {"HP:0001945"})]
    s = benchmark_gsc.score_documents({"a": set()}, docs)
    assert s["tp"] == 0 and s["fn"] == 1
    assert s["recall"] == 0.0


def test_score_micro_sum_across_documents():
    docs = [_doc("a", {"HP:0001945"}), _doc("b", {"HP:0002027"})]
    pred = {"a": {"HP:0001945"}, "b": {"HP:0012378"}}  # b wrong
    s = benchmark_gsc.score_documents(pred, docs)
    assert s["tp"] == 1 and s["fp"] == 1 and s["fn"] == 1


# --- IC-based near-hit scorer (real HPO graph + IC map) ---------------------


def test_near_hit_exact_is_full_credit(hpo, ic_map):
    docs = [_doc("a", {"HP:0002027"})]
    pred = {"a": {"HP:0002027"}}
    s = benchmark_gsc.score_near_hits(pred, docs, hpo, ic_map, tau=0.7)
    assert s["pred_near"] == 1 and s["gold_near"] == 1
    assert s["near_hit_precision"] == 1.0 and s["near_hit_recall"] == 1.0


def test_near_hit_credits_close_relative(hpo, ic_map):
    # A parent/child pair shares an informative ancestor, so Lin similarity is
    # well above the threshold; exact scores it a miss, near-hit credits it.
    # Abnormality of the digestive system (HP:0025031) is an ancestor of
    # Abdominal pain (HP:0002027) -- but it is near-root, so we instead use a
    # specific parent/child pair with high shared IC: Chronic pain (HP:0012532)
    # vs Pain (HP:0012531). Walk the graph for an actual parent of a leaf.
    leaf = "HP:0002027"  # Abdominal pain
    parents = [str(p) for p in hpo.graph.get_parents(leaf)]
    assert parents, "expected Abdominal pain to have a parent"
    parent = parents[0]
    docs = [_doc("a", {leaf})]
    pred = {"a": {parent}}
    exact = benchmark_gsc.score_documents(pred, docs)
    near = benchmark_gsc.score_near_hits(pred, docs, hpo, ic_map, tau=0.5)
    assert exact["tp"] == 0
    # The parent shares all its IC with the leaf's MICA, so Lin >= 0.5.
    assert near["pred_near"] == 1 and near["gold_near"] == 1


def test_near_hit_rejects_near_root_prediction(hpo, ic_map):
    # The retired undirected hop count would credit a near-root term as a
    # near-hit. Under IC, "Phenotypic abnormality" (HP:0000118) scores ~0
    # against a specific gold term, so it earns no near-hit credit.
    docs = [_doc("a", {"HP:0002027"})]
    pred = {"a": {"HP:0000118"}}
    near = benchmark_gsc.score_near_hits(pred, docs, hpo, ic_map, tau=0.7)
    assert near["pred_near"] == 0 and near["gold_near"] == 0
    assert near["near_hit_precision"] == 0.0 and near["near_hit_recall"] == 0.0


def test_near_hit_precision_and_recall_are_separate_counts(hpo, ic_map):
    # One gold, two predictions: an exact hit and an unrelated code. Precision
    # side counts predictions-with-a-near-hit (1/2); recall side counts
    # golds-covered (1/1). These are different numerators, reported separately,
    # not folded into one TP.
    docs = [_doc("a", {"HP:0002027"})]
    pred = {"a": {"HP:0002027", "HP:0001945"}}  # abdominal pain + fever
    near = benchmark_gsc.score_near_hits(pred, docs, hpo, ic_map, tau=0.7)
    assert near["total_pred"] == 2 and near["total_gold"] == 1
    assert near["pred_near"] == 1  # only the exact prediction is a near-hit
    assert near["gold_near"] == 1  # the gold is covered
    assert near["near_hit_precision"] == 0.5
    assert near["near_hit_recall"] == 1.0
    assert "f1" not in near  # no combined near-hit F1 to be misread
