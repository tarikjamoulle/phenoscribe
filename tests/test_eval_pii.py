"""The scores of scripts/eval_pii.py (the README numbers), on a hand-made test set with a stub detector."""

import json

import pytest
from conftest import load_script

from phenoscribe import pii
from phenoscribe.pii import Span

eval_pii = load_script("eval_pii")

TEXT = "Claire Dubois a mal au dos. Elle dit que Claire dort mal."


@pytest.fixture
def one_file(tmp_path, monkeypatch):
    """One French file: three planted names, one keep word. The detector finds the first Claire and,
    wrongly, dos."""
    folder = tmp_path / "fr"
    folder.mkdir()
    (folder / "01.txt").write_text(TEXT, encoding="utf-8")
    gold = {
        "details": [{"type": "first", "value": "Claire", "start": 0, "end": 6},
                    {"type": "last", "value": "Dubois", "start": 7, "end": 13},
                    {"type": "first", "value": "Claire", "start": 41, "end": 47}],
        "keep": [{"value": "dos", "start": 23, "end": 26}],
    }
    (folder / "01.json").write_text(json.dumps(gold), encoding="utf-8")
    monkeypatch.setattr(eval_pii, "ROOT", tmp_path)

    def detect(text, models, **kwargs):
        return [Span(0, 6, "Claire", "PERSON", 0.95), Span(23, 26, "dos", "LOCATION", 0.9)], list(models)

    monkeypatch.setattr(pii, "detect_personal_details", detect)


def test_covered_needs_every_letter_and_digit_inside_a_removed_span():
    assert eval_pii.covered(0, 6, [(0, 6)], TEXT)
    assert eval_pii.covered(0, 7, [(0, 6)], TEXT)  # the space after the name does not count
    assert not eval_pii.covered(0, 13, [(0, 6)], TEXT)


def test_evaluate_counts_the_found_missed_and_wrongly_removed_details(one_file):
    result = eval_pii.evaluate("fr", ["m1", "m2"])
    assert (result["planted"], result["found"], result["recall"]) == (3, 2, 0.667)
    assert result["missed_by_type"] == {"last": 1}
    assert result["missed"] == ["last:Dubois"]
    assert result["keep_words_removed"] == ["dos"]
    assert result["removed_by_mistake"] == []
    assert (result["clean_files"], result["files_with_a_miss"]) == (0, 1)


def test_a_value_found_once_counts_as_removed_at_every_place(one_file):
    # The detector finds the first Claire only; the second one is removed too.
    assert "first:Claire" not in eval_pii.evaluate("fr", ["m1"])["missed"]


def test_name_recall_counts_first_and_family_names_only(one_file):
    assert eval_pii.name_recall(eval_pii.evaluate("fr", ["m1"]), "fr") == 0.667
