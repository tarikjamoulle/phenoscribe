"""Tests for the recall@k scorer: per-mention (micro) and per-concept (macro).

The retrieval itself hits ChromaDB and is not unit-tested here. The load-bearing
piece is `score_recall`, which must report both a per-mention and a per-concept
recall — they diverge when a concept has many synonymous labels, which is
Robinson's "per-mention recall wearing a per-concept label" objection.
"""

import importlib.util
import sys
from pathlib import Path

# scripts/ is not a package; load the module by path.
_spec = importlib.util.spec_from_file_location(
    "eval_recall",
    Path(__file__).parent.parent / "scripts" / "eval_recall.py",
)
eval_recall = importlib.util.module_from_spec(_spec)
sys.modules["eval_recall"] = eval_recall
_spec.loader.exec_module(eval_recall)


def _ranker(table):
    """Build a rank_fn from {label: [ranked hp ids]}."""
    return lambda label: table.get(label, [])


def test_per_mention_and_per_concept_diverge_on_synonyms():
    # Code A has three labels; two retrieve it at k=5, one misses.
    # Code B has one label that retrieves it.
    pairs = [
        ("a1", "HP:0000001"),
        ("a2", "HP:0000001"),
        ("a3", "HP:0000001"),
        ("b1", "HP:0000002"),
    ]
    present = {"HP:0000001", "HP:0000002"}
    table = {
        "a1": ["HP:0000001"],          # hit
        "a2": ["HP:0000001"],          # hit
        "a3": ["HP:0009999"],          # miss
        "b1": ["HP:0000002"],          # hit
    }
    s = eval_recall.score_recall(pairs, present, _ranker(table), ks=(5,))
    # Per-mention: 3 of 4 pairs hit.
    assert s["n_mentions"] == 4
    assert s["per_mention_recall@5"] == 0.75
    # Per-concept: both A (any label hits) and B hit -> 2/2.
    assert s["n_concepts"] == 2
    assert s["per_concept_recall@5"] == 1.0
    # The two numbers genuinely differ.
    assert s["per_mention_recall@5"] != s["per_concept_recall@5"]


def test_per_concept_hit_requires_any_label():
    # Code A has two labels, NEITHER retrieves it -> concept miss.
    pairs = [("a1", "HP:0000001"), ("a2", "HP:0000001"), ("b1", "HP:0000002")]
    present = {"HP:0000001", "HP:0000002"}
    table = {"a1": ["HP:0009"], "a2": ["HP:0009"], "b1": ["HP:0000002"]}
    s = eval_recall.score_recall(pairs, present, _ranker(table), ks=(5,))
    # Per-concept: only B hits -> 1/2.
    assert s["per_concept_recall@5"] == 0.5
    # Per-mention: only 1 of 3 pairs.
    assert round(s["per_mention_recall@5"], 3) == round(1 / 3, 3)


def test_missing_code_not_scored():
    # A code absent from `present` is skipped, not counted as a miss.
    pairs = [("a1", "HP:0000001"), ("b1", "HP:0000002")]
    present = {"HP:0000001"}  # B is not in the index
    table = {"a1": ["HP:0000001"], "b1": ["HP:0000002"]}
    s = eval_recall.score_recall(pairs, present, _ranker(table), ks=(5,))
    assert s["n_mentions"] == 1 and s["n_concepts"] == 1
    assert s["per_mention_recall@5"] == 1.0
    assert s["per_concept_recall@5"] == 1.0


def test_respects_k_cutoff():
    pairs = [("a1", "HP:0000001")]
    present = {"HP:0000001"}
    # gold at rank 8: hit at k=10, miss at k=5.
    table = {"a1": [f"HP:000000{i}" for i in range(2, 9)] + ["HP:0000001"]}
    s = eval_recall.score_recall(pairs, present, _ranker(table), ks=(5, 10))
    assert s["per_mention_recall@5"] == 0.0
    assert s["per_mention_recall@10"] == 1.0
    assert s["per_concept_recall@5"] == 0.0
    assert s["per_concept_recall@10"] == 1.0
