"""Tests for the page logic in gui.py (no browser needed)."""

from unittest.mock import MagicMock

import anthropic
import httpx
import openai
import pytest

from phenoscribe import gui
from phenoscribe.config import Config
from phenoscribe.pipeline import BatchResult, FileResult

_REQUEST = httpx.Request("GET", "https://api.example.com")


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
        (
            openai.AuthenticationError(
                "bad key", response=httpx.Response(401, request=_REQUEST), body=None
            ),
            "Key rejected",
        ),
        (
            anthropic.AuthenticationError(
                "bad key", response=httpx.Response(401, request=_REQUEST), body=None
            ),
            "Key rejected",
        ),
        (openai.APIConnectionError(request=_REQUEST), "No internet"),
        (ValueError("Unsupported file type '.xyz'"), "Audio format not supported"),
        (RuntimeError("Failed to load audio: moov atom not found"), "Audio format not supported"),
    ],
)
def test_plain_error(error, line):
    assert gui.plain_error(error) == line


def test_other_errors_point_to_the_log_file():
    assert str(gui.LOG_PATH) in gui.plain_error(KeyError("x"))


def test_key_change_saves_only_when_remember_is_on():
    _, saved = gui.on_key_change("sk-ant-abc", True)
    assert saved == "sk-ant-abc"
    _, saved = gui.on_key_change("sk-ant-abc", False)
    assert saved == ""


def test_check_key_says_works_or_rejected(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(gui.openai, "OpenAI", lambda api_key: client)
    assert gui.check_key("openai", "sk-good") == "Key works"

    client.models.list.side_effect = openai.AuthenticationError(
        "bad key", response=httpx.Response(401, request=_REQUEST), body=None
    )
    assert gui.check_key("openai", "sk-bad") == "Key rejected"


def _batch():
    found = [
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "negated": False, "needs_review": False},
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "negated": False, "needs_review": False},
        {"hpo_id": "HP:0001945", "hpo_term": "Fever", "negated": True, "needs_review": True},
    ]
    return BatchResult(
        excel_path=None,
        files=[
            FileResult("467.mp3", "MGA.467", n_codes=3, matches=found),
            FileResult("x.mp3", "pt-12345678", error=ValueError("Unsupported file type")),
        ],
    )


def test_result_rows_match_the_excel_rows():
    assert gui.result_rows(_batch()) == [
        ["MGA.467", "Fatigue", "HP:0012378", "Present", ""],
        ["MGA.467", "Fever", "HP:0001945", "Absent", "REVIEW"],
    ]


def test_status_has_one_plain_line_per_file():
    assert gui.status_lines(_batch()).splitlines() == [
        "- 467.mp3 (MGA.467): 3 findings",
        "- x.mp3: Audio format not supported",
    ]


def test_run_without_a_key_asks_for_one(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    status, rows, excel = gui.run_pipeline(["467.mp3"], "fr", "openai", "", False)
    assert status == "Paste your API key first."


def test_the_page_builds():
    assert gui.build_app() is not None
