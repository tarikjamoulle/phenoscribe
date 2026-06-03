"""Tests for CLI argument resolution helpers and the validate subcommand."""

import pytest

from phenoscribe.cli import main, resolve_provider
from phenoscribe.output import write_excel


def test_resolve_canonical_names():
    assert resolve_provider("openai") == "openai"
    assert resolve_provider("anthropic") == "anthropic"
    assert resolve_provider("ollama") == "ollama"


def test_resolve_friendly_aliases():
    assert resolve_provider("claude") == "anthropic"
    assert resolve_provider("gpt") == "openai"


def test_resolve_is_case_insensitive():
    assert resolve_provider("Claude") == "anthropic"
    assert resolve_provider("OPENAI") == "openai"


def test_resolve_unknown_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        resolve_provider("gemini")


# --- `phenoscribe validate` subcommand, end-to-end --------------------------
#
# This is the load-bearing check for the round-2 Robinson objection: the IC
# validation machinery must be producible from the CLI, not just from
# scripts/ and tests. We build a tiny predicted-codes workbook and a tiny
# ground-truth workbook, run the subcommand through main(), and assert it
# prints the term-level F1 and an IC distribution.

# Stable public HPO IDs (same ones test_validate.py pins):
#   HP:0002027  Abdominal pain   (exact hit -> contributes to F1)
#   HP:0012378  Fatigue          (exact hit)
#   HP:0012531  Pain             (parent of Abdominal pain: near-miss, low-ish IC)
_GT_CODES = ["HP:0002027", "HP:0012378"]
_PRED_CODES = ["HP:0002027", "HP:0012378", "HP:0012531"]


def _write_codes(path, patient_id, codes):
    matches = [{"hpo_term": "t", "hpo_id": c, "patient_verbatim": ""} for c in codes]
    write_excel(patient_id, matches, str(path), fmt="semicolon")


def test_validate_subcommand_prints_f1_and_ic_distribution(tmp_path, monkeypatch, capsys):
    pred = tmp_path / "results.xlsx"
    gt = tmp_path / "ground_truth.xlsx"
    _write_codes(pred, "MGA.467", _PRED_CODES)
    _write_codes(gt, "MGA.467", _GT_CODES)

    # The obo is symlinked into the worktree under data/hpo/hp.obo; pass it
    # explicitly so the test does not depend on config.yaml resolution.
    argv = [
        "phenoscribe",
        "validate",
        str(pred),
        str(gt),
        "--obo",
        "data/hpo/hp.obo",
    ]
    monkeypatch.setattr("sys.argv", argv)

    main()

    out = capsys.readouterr().out
    # Term-level F1 is reported (both strict and partial-credit blocks print F1).
    assert "F1:" in out
    assert "Strict (exact HPO ID match, document-level):" in out
    # The IC distribution of the predicted terms is reported (Robinson Q9).
    assert "IC distribution" in out
    assert "n=" in out and "median=" in out
    # Two of the three predicted codes are exact hits in the ground truth.
    assert "Exact matches:  2" in out
