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

from phenoscribe.validate import _get_hpo  # noqa: E402


@pytest.fixture(scope="module")
def hpo():
    return _get_hpo()


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


# --- document-level scorer (lenient, real HPO graph) ------------------------


def test_score_lenient_credits_sibling(hpo):
    # Abdominal pain (HP:0002027) is within 2 hops of Chest pain (HP:0100749)
    # via the shared parent Pain. Exact scores it a miss; lenient credits it.
    docs = [_doc("a", {"HP:0100749"})]
    pred = {"a": {"HP:0002027"}}
    exact = benchmark_gsc.score_documents(pred, docs)
    lenient = benchmark_gsc.score_documents(pred, docs, hpo, lenient_hops=2)
    assert exact["tp"] == 0
    assert lenient["tp"] == 1
    assert lenient["fp"] == 0 and lenient["fn"] == 0


def test_score_lenient_still_rejects_unrelated(hpo):
    # Fever is not within 2 hops of Abdominal pain.
    docs = [_doc("a", {"HP:0002027"})]
    pred = {"a": {"HP:0001945"}}
    lenient = benchmark_gsc.score_documents(pred, docs, hpo, lenient_hops=2)
    assert lenient["tp"] == 0 and lenient["fp"] == 1 and lenient["fn"] == 1
