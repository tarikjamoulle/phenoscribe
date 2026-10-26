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
    box, files, added = gui.add_files(["/t/a.mp3", "/t/b.txt"], ["/t/a.mp3"])
    assert box is None and files == ["/t/a.mp3", "/t/b.txt"]
    assert added == ["/t/b.txt"]


def test_count_line_names_added_and_skipped():
    plans = [
        FilePlan("/t/1.mp3", "1.mp3", "MGA.1", "transcribe"),
        FilePlan("/t/x.pdf", "x.pdf", "pt-1", "skip", "Not an audio file or transcript"),
    ]
    assert "1 file added · 1 skipped" in gui.count_line(plans)
    assert "2 files added" in gui.count_line(plans[:1] * 2)


def test_only_the_new_rows_flash():
    plans = [
        FilePlan("/t/1.mp3", "1.mp3", "MGA.1", "transcribe"),
        FilePlan("/t/2.mp3", "2.mp3", "MGA.2", "transcribe"),
    ]
    page = gui.plan_html(plans, fresh=["/t/2.mp3"])
    assert page.count('class="ps-row new"') == 1
    assert gui.plan_html(plans).count(" new") == 0


def test_plan_html_has_one_row_per_file_and_escapes_names():
    plans = [
        FilePlan("/t/101.mp3", "101.mp3", "MGA.101", "transcribe"),
        FilePlan("/t/102.mp3", "102.mp3", "MGA.102", "reuse"),
        FilePlan("/t/<x>.pdf", "<x>.pdf", "pt-1", "skip", "Not an audio file or transcript"),
    ]
    page = gui.plan_html(plans)
    assert page.count('class="ps-row') == 3
    assert "Transcribe, then remove personal details" in page and "Saved transcript found" in page
    assert "&lt;x&gt;.pdf" in page and "<x>" not in page


def test_selection_view_enables_start_only_with_files_and_a_key(monkeypatch, tmp_path):
    """The HPO tool needs a key. The removal-only tool runs without one."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        gui, "file_plan",
        lambda files, language: [FilePlan(f, f, "MGA.1", "transcribe") for f in files],
    )
    files = ["101.mp3", "102.mp3"]
    start = gui.selection_view(files, [], "fr", "")[3]
    assert start["interactive"] is False
    assert gui.selection_view(files, [], "fr", "", gui.MODE_CLEAN)[3]["interactive"] is True
    _, language, reuse, start, clear, drop, hint, fresh = gui.selection_view(files, files, "fr", "sk-x")
    assert start["interactive"] is True and start["value"] == "Start with 2 files"
    assert language["visible"] is True and reuse["visible"] is False and clear["visible"] is True
    # With files in the list, the drop box shrinks to a slim bar and the hint goes.
    assert drop["height"] == 56 and "ps-slim" in drop["elem_classes"]
    assert hint["visible"] is False
    assert fresh == []  # the flash plays once
    _, _, _, start, _, drop, hint, _ = gui.selection_view([], [], "fr", "sk-x")
    assert start["interactive"] is False
    assert drop["height"] == 150 and "ps-slim" not in drop["elem_classes"] and hint["visible"] is True


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


def test_working_panel_names_the_files_and_the_wait():
    plans = [
        FilePlan("/t/1.mp3", "1.mp3", "MGA.1", "transcribe"),
        FilePlan("/t/2.txt", "2.txt", "MGA.2", "transcript"),
        FilePlan("/t/x.pdf", "x.pdf", "pt-1", "skip", "Not an audio file or transcript"),
    ]
    page = gui.working_html(plans)
    assert "Reading 2 files and finding the personal details…" in page
    assert "1 recording to transcribe" in page
    assert "about as long as the recording" in page
    assert "No text leaves this computer" in page
    assert "Keep this page and the Phenoscribe window open" in page
    assert "to transcribe" not in gui.working_html(plans[1:2])


def test_begin_run_locks_start_and_shows_the_panel(monkeypatch):
    monkeypatch.setattr(
        gui, "file_plan", lambda files, language: [FilePlan(f, f, "MGA.1", "transcribe") for f in files]
    )
    start, working, review, results = gui.begin_run(["1.mp3"], "fr")
    assert start["interactive"] is False and start["value"] == "Working…"
    assert working["visible"] is True and "Reading 1 file" in working["value"]
    assert review["visible"] is False and results["visible"] is False


# --- the review panel ----------------------------------------------------------------


@pytest.fixture
def review_on_disk(tmp_path, monkeypatch):
    """One review saved in a temp results folder: Dupont (a name) and 54 ans (an age)."""
    from phenoscribe.pii import Span
    from phenoscribe.review import Review, save_review

    monkeypatch.setattr(gui, "OUTPUT_DIR", tmp_path)
    text = "Madame Dupont a 54 ans. Le docteur Vandermeulen la suit. Dupont va mieux."
    review = Review.from_spans("412.docx", "MGA.412", text, [
        Span(7, 13, "Dupont", "PERSON", 0.98), Span(16, 22, "54 ans", "AGE", 0.7),
    ])
    save_review(review, tmp_path)
    return ["MGA.412"]


def _view(view):
    names = ["queue", "pick", "line", "text", "remove", "found", "missed", "approve", "code", "clean",
             "rest"]
    return dict(zip(names, view))


def test_review_view_shows_counts_rows_and_the_missed_name(review_on_disk):
    view = _view(gui.review_view("MGA.412", review_on_disk, []))
    assert "2 removed: 1 name · 1 age" in view["line"]
    assert "No removed value is left" in view["line"]
    assert view["found"] == [[True, "54 ans", "age", 1], [True, "Dupont", "name", 2]]
    assert view["missed"][0][:2] == [False, "Vandermeulen"]
    assert ("Dupont", "name") in view["text"]
    assert view["approve"]["interactive"] is True and view["code"]["interactive"] is False


def test_unticking_puts_the_value_back(review_on_disk):
    view = _view(gui.on_removed_edit([[False, "54 ans", "age", 1], [True, "Dupont", "name", 2]],
                                     "MGA.412", review_on_disk, []))
    assert view["found"][0][0] is False
    assert "1 removed: 1 name" in view["line"]


def test_a_ticked_missed_word_is_removed_everywhere(review_on_disk):
    view = _view(gui.on_missed_edit([[True, "Vandermeulen", "…"]], "MGA.412", review_on_disk, []))
    assert view["missed"] == []
    assert "2 names" in view["line"]


def test_approve_writes_the_clean_text_and_enables_the_next_step(review_on_disk, tmp_path):
    view = _view(gui.on_approve("MGA.412", review_on_disk, []))
    assert view["approve"]["value"] == "✓ Approved" and view["approve"]["interactive"] is False
    assert view["code"]["interactive"] is True
    clean = (tmp_path / "clean" / "MGA.412.txt").read_text(encoding="utf-8")
    assert "Dupont" not in clean and "PERSON_1" in clean
    assert (tmp_path / "clean" / "MGA.412.docx").exists()  # a Word source gets a Word copy


def test_an_edit_after_approval_needs_a_new_approval(review_on_disk):
    gui.on_approve("MGA.412", review_on_disk, [])
    view = _view(gui.on_add("Vandermeulen", "PERSON", "MGA.412", review_on_disk, [])[1:])
    assert view["approve"]["interactive"] is True
    assert view["code"]["interactive"] is False


def test_resume_opens_the_left_reviews(review_on_disk):
    panel, queue, failed, *_ = gui.resume_reviews()
    assert panel["visible"] is True and queue == review_on_disk


def test_clean_run_gives_a_zip_of_the_approved_texts(review_on_disk, tmp_path):
    assert gui.clean_run(review_on_disk)[1] == "No approved file yet."
    gui.on_approve("MGA.412", review_on_disk, [])
    download, note = gui.clean_run(review_on_disk)
    assert download["visible"] is True and download["value"].endswith(".zip")
    assert "clean_texts_" in note


def test_the_line_names_the_warnings(review_on_disk):
    view = _view(gui.review_view("MGA.412", review_on_disk, []))
    assert "⚠ Possibly missed: Vandermeulen" in view["line"]
    assert "⚠ Unsure about: 54 ans" in view["line"]
    assert "Check" in view["queue"] and "1 file to check" in view["queue"]


def test_approve_the_rest_needs_the_confirm(review_on_disk, tmp_path):
    from phenoscribe.review import BULK, load_review

    gui.on_approve_rest(False, "MGA.412", review_on_disk, [])
    assert load_review(tmp_path, "MGA.412").status == "needs review"
    view = _view(gui.on_approve_rest(True, "MGA.412", review_on_disk, []))
    assert load_review(tmp_path, "MGA.412").approved_how == BULK
    assert "All 1 files approved" in view["queue"] and view["rest"]["visible"] is False
