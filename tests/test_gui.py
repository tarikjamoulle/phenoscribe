"""Tests for the page logic in gui.py (no browser needed)."""

from datetime import datetime
from unittest.mock import MagicMock

import anthropic
import httpx
import openai
import pytest

from phenoscribe import gui
from phenoscribe.config import Config
from phenoscribe.pipeline import BatchResult, FilePlan, FileResult

_REQUEST = httpx.Request("GET", "https://api.example.com")


def _auth_error(cls):
    return cls("bad key", response=httpx.Response(401, request=_REQUEST), body=None)


def test_provider_comes_from_the_key_prefix():
    assert gui.provider_for_key("sk-ant-api03-abc") == "anthropic"
    assert gui.provider_for_key("  sk-proj-abc") == "openai"


def test_model_comes_from_config_for_the_configured_provider():
    config = Config()
    config.llm.provider, config.llm.model = "openai", "gpt-4o-mini"
    assert gui.model_for("openai", config) == "gpt-4o-mini"
    assert gui.model_for("anthropic", config) == gui.DEFAULT_MODELS["anthropic"]


@pytest.mark.parametrize(
    "error, line",
    [
        (_auth_error(openai.AuthenticationError), "Key rejected"),
        (_auth_error(anthropic.AuthenticationError), "Key rejected"),
        (openai.APIConnectionError(request=_REQUEST), "No internet. Check the connection and start again."),
        (
            openai.RateLimitError(
                "insufficient_quota", response=httpx.Response(429, request=_REQUEST), body=None
            ),
            "The AI account has no credit left, or hit its limit. Check the account billing.",
        ),
        (ValueError("Unsupported file type '.xyz'"), "Audio format not supported"),
        (RuntimeError("Failed to load audio: moov atom not found"), "Audio format not supported"),
    ],
)
def test_plain_error(error, line):
    assert gui.plain_error(error) == line


def test_other_errors_point_to_the_log_file():
    assert gui.LOG_PATH.name in gui.plain_error(KeyError("x"))


def test_check_key_is_empty_when_the_key_works(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(gui.openai, "OpenAI", lambda api_key: client)
    assert gui.check_key("openai", "sk-good") == ""
    client.models.list.side_effect = _auth_error(openai.AuthenticationError)
    assert gui.check_key("openai", "sk-bad") == "Key rejected"


def test_a_working_key_folds_away_and_is_saved_only_with_remember(monkeypatch):
    monkeypatch.setattr(gui, "check_key", lambda provider, key: "")
    provider, line, problem, saved, entry, short = gui.on_key_entered(" sk-ant-abc ", True)
    assert provider == "anthropic" and problem == "" and saved == "sk-ant-abc"
    assert "Anthropic key works" in line
    assert entry["visible"] is False and short["visible"] is True

    *_, saved, _, _ = gui.on_key_entered("sk-ant-abc", False)
    assert saved == ""


def test_a_rejected_key_keeps_the_box_open(monkeypatch):
    monkeypatch.setattr(gui, "check_key", lambda provider, key: "Key rejected")
    _, line, problem, _, entry, _ = gui.on_key_entered("sk-bad", True)
    assert problem == "Key rejected" and line == ""
    assert entry["visible"] is True


def test_a_saved_key_shows_the_short_line(monkeypatch):
    key, provider, line, entry, short = gui.load_saved_key("sk-proj-abc")
    assert (key, provider) == ("sk-proj-abc", "openai")
    assert "saved in this browser" in line and short["visible"] is True


def test_add_files_empties_the_drop_box_and_skips_repeats():
    box, files = gui.add_files(["/t/a.mp3", "/t/b.txt"], ["/t/a.mp3"])
    assert box is None and files == ["/t/a.mp3", "/t/b.txt"]


def test_plan_html_has_one_row_per_file_and_escapes_names():
    plans = [
        FilePlan("/t/101.mp3", "101.mp3", "MGA.101", "transcribe"),
        FilePlan("/t/102.mp3", "102.mp3", "MGA.102", "reuse"),
        FilePlan("/t/<x>.pdf", "<x>.pdf", "pt-1", "skip", "Not an audio file or transcript"),
    ]
    page = gui.plan_html(plans)
    assert page.count('class="ps-row') == 3
    assert "Transcribe, then code" in page and "Saved transcript found" in page
    assert "&lt;x&gt;.pdf" in page and "<x>" not in page


def test_selection_view_enables_start_only_with_files_and_a_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        gui, "file_plan",
        lambda files, language: [FilePlan(f, f, "MGA.1", "transcribe") for f in files],
    )
    *_, start, _ = gui.selection_view(["101.mp3", "102.mp3"], "fr", "")
    assert start["interactive"] is False
    _, language, reuse, start, clear = gui.selection_view(["101.mp3", "102.mp3"], "fr", "sk-x")
    assert start["interactive"] is True and start["value"] == "Start coding 2 files"
    assert language["visible"] is True and reuse["visible"] is False and clear["visible"] is True
    *_, start, _ = gui.selection_view([], "fr", "sk-x")
    assert start["interactive"] is False


def _batch():
    found = [
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": "épuisée",
         "negated": False, "needs_review": False},
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": "épuisée",
         "negated": False, "needs_review": False},
        {"hpo_id": "HP:0001945", "hpo_term": "Fever", "patient_verbatim": "pas de fièvre",
         "negated": True, "needs_review": True},
        {"hpo_id": None, "hpo_term": "", "clinical_term": "Tingling", "patient_verbatim": "",
         "negated": False, "needs_review": True},
    ]
    return BatchResult(
        excel_path=None,
        files=[
            FileResult("467.mp3", "MGA.467", n_codes=4, matches=found),
            FileResult("x.mp3", "pt-12345678", error=ValueError("Unsupported file type")),
        ],
    )


def test_result_rows_show_the_symptom_and_match_the_excel_rows():
    assert gui.result_rows(_batch()) == [
        ["MGA.467", "épuisée", "Fatigue", "HP:0012378", "Present"],
        ["MGA.467", "pas de fièvre", "Fever", "HP:0001945", "Absent · Review"],
        ["MGA.467", "Tingling", "No good match", "—", "Present · Review"],
    ]


def test_filter_to_review():
    rows = gui.result_rows(_batch())
    assert len(gui.filter_rows(rows, "To review")) == 2
    assert gui.filter_rows(rows, "All") == rows


def test_summary_counts_codes_reviews_and_files():
    rows = gui.result_rows(_batch())
    page = gui.summary_html(_batch(), rows, datetime(2026, 10, 22, 14, 5))
    assert "22 October 2026, 14:05" in page
    assert "HPO codes<b>2</b>" in page and '<b class="warn">2</b>' in page
    assert "<b>1 of 2</b>" in page


def test_status_has_one_plain_line_per_file():
    page = gui.status_html(_batch())
    assert "467.mp3 → MGA.467" in page and "3 findings · 2 to review" in page
    assert "Audio format not supported" in page


def test_the_page_builds():
    assert gui.build_app() is not None
