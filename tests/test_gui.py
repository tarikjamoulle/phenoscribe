"""The page logic of the gui package (no browser needed)."""

import json
from datetime import datetime
from unittest.mock import MagicMock

import anthropic
import httpx
import openai
import pytest

from phenoscribe import gui, pipeline
from phenoscribe.batch import BatchResult, FilePlan, FileResult
from phenoscribe.config import DEFAULT_MODELS, Config
from phenoscribe.gui.handlers import DROP_HEIGHT, SLIM_DROP_HEIGHT
from phenoscribe.pii import Span
from phenoscribe.review import APPROVED, BULK, PII_WARNING, Review, add_value, load_review, save_review

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
    assert gui.model_for("anthropic", config) == DEFAULT_MODELS["anthropic"]


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
    monkeypatch.setattr(openai, "OpenAI", lambda api_key: MagicMock())
    assert gui.check_key("openai", "sk-good") == ""


def test_check_key_names_a_rejected_key(monkeypatch):
    client = MagicMock()
    client.models.list.side_effect = _auth_error(openai.AuthenticationError)
    monkeypatch.setattr(openai, "OpenAI", lambda api_key: client)
    assert gui.check_key("openai", "sk-bad") == "Key rejected"


def test_a_working_key_folds_away_and_is_saved_only_with_remember(monkeypatch):
    monkeypatch.setattr(gui.handlers, "check_key", lambda provider, key: "")
    provider, line, problem, saved, entry, short = gui.on_key_entered(" sk-ant-abc ", True)
    assert provider == "anthropic" and problem == "" and saved == "sk-ant-abc"
    assert "Anthropic key works" in line
    assert entry["visible"] is False and short["visible"] is True

    *_, saved, _, _ = gui.on_key_entered("sk-ant-abc", False)
    assert saved == ""


def test_a_rejected_key_keeps_the_box_open(monkeypatch):
    monkeypatch.setattr(gui.handlers, "check_key", lambda provider, key: "Key rejected")
    _, line, problem, _, entry, _ = gui.on_key_entered("sk-bad", True)
    assert problem == "Key rejected" and line == ""
    assert entry["visible"] is True


def test_a_saved_key_shows_the_short_line():
    key, provider, line, _entry, short = gui.load_saved_key("sk-proj-abc")
    assert (key, provider) == ("sk-proj-abc", "openai")
    assert "saved in this browser" in line and short["visible"] is True


def test_add_files_empties_the_drop_box_and_skips_repeats():
    box, files, added = gui.add_files(["/t/a.mp3", "/t/b.txt"], ["/t/a.mp3"])
    assert box is None and files == ["/t/a.mp3", "/t/b.txt"]
    assert added == ["/t/b.txt"]


def test_count_line_names_added_and_skipped():
    plans = [
        FilePlan("/t/1.mp3", "1.mp3", "P.1", "transcribe"),
        FilePlan("/t/x.pdf", "x.pdf", "pt-1", "skip", "Not an audio file or transcript"),
    ]
    assert "1 file added · 1 skipped" in gui.count_line(plans)
    assert "2 files added" in gui.count_line(plans[:1] * 2)


def test_only_the_new_rows_flash():
    plans = [
        FilePlan("/t/1.mp3", "1.mp3", "P.1", "transcribe"),
        FilePlan("/t/2.mp3", "2.mp3", "P.2", "transcribe"),
    ]
    page = gui.plan_html(plans, fresh=["/t/2.mp3"])
    assert page.count('class="ps-row new"') == 1
    assert gui.plan_html(plans).count(" new") == 0


def test_plan_html_has_one_row_per_file_and_escapes_names():
    plans = [
        FilePlan("/t/003.mp3", "003.mp3", "P.003", "transcribe"),
        FilePlan("/t/005.mp3", "005.mp3", "P.005", "reuse"),
        FilePlan("/t/<x>.pdf", "<x>.pdf", "pt-1", "skip", "Not an audio file or transcript"),
    ]
    page = gui.plan_html(plans)
    assert page.count('class="ps-row') == 3
    assert "Transcribe, then remove personal details" in page and "Saved transcript found" in page
    assert "&lt;x&gt;.pdf" in page and "<x>" not in page


def test_selection_view_enables_start_only_with_files_and_a_key(monkeypatch):
    """The HPO tool needs a key. The removal-only tool runs without one."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        gui.handlers, "file_plan",
        lambda files, language: [FilePlan(f, f, "P.1", "transcribe") for f in files],
    )
    files = ["003.mp3", "005.mp3"]
    start = gui.selection_view(files, [], "fr", "")[3]
    assert start["interactive"] is False and start["value"] == "Paste an API key to start"
    assert gui.selection_view(files, [], "fr", "", gui.MODE_CLEAN)[3]["interactive"] is True
    _, language, reuse, start, clear, drop, hint, fresh = gui.selection_view(files, files, "fr", "sk-x")
    assert start["interactive"] is True and start["value"] == "Start with 2 files"
    assert language["visible"] is True and reuse["visible"] is False and clear["visible"] is True
    # With files in the list, the drop box shrinks to a slim bar and the hint goes.
    assert drop["height"] == SLIM_DROP_HEIGHT and "ps-slim" in drop["elem_classes"]
    assert hint["visible"] is False
    assert fresh == []  # the flash plays once


def test_the_language_menu_shows_for_text_files_too(monkeypatch):
    # The language picks the privacy models, so a .txt or .docx needs it too.
    monkeypatch.setattr(
        gui.handlers, "file_plan",
        lambda files, language: [FilePlan(f, f, "P.1", "transcript") for f in files],
    )
    language = gui.selection_view(["003.txt"], [], "nl", "sk-x")[1]
    assert language["visible"] is True
    _, _, _, start, _, drop, hint, _ = gui.selection_view([], [], "fr", "sk-x")
    assert start["interactive"] is False
    assert drop["height"] == DROP_HEIGHT and "ps-slim" not in drop["elem_classes"] and hint["visible"] is True


def _batch():
    found = [
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": "épuisée",
         "negated": False, "needs_review": False, "first_position": 12.4, "n_mentions": 3},
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
            FileResult("001.mp3", "P.001", n_codes=4, matches=found),
            FileResult("x.mp3", "pt-12345678", error=ValueError("Unsupported file type")),
        ],
    )


def test_result_rows_show_the_symptom_and_match_the_excel_rows():
    assert gui.result_rows(_batch()) == [
        ["P.001", "épuisée", "Fatigue", "HP:0012378", "Present", "12 % · 3×"],
        ["P.001", "pas de fièvre", "Fever", "HP:0001945", "Absent · Review", ""],
        ["P.001", "Tingling", "No good match", "—", "Present · Review", ""],
    ]


def test_filter_to_review():
    findings = gui.coded_findings(_batch())
    assert len(gui.filter_rows(findings, "To review")) == 2
    assert gui.filter_rows(findings, "All") == gui.result_rows(_batch())


def test_summary_counts_codes_reviews_and_files():
    page = gui.summary_html(_batch(), gui.coded_findings(_batch()), datetime(2026, 10, 22, 14, 5))
    assert "22 October 2026, 14:05" in page
    assert "HPO codes<b>2</b>" in page and '<b class="warn">2</b>' in page
    assert "<b>1 of 2</b>" in page


def test_status_has_one_plain_line_per_file():
    page = gui.status_html(_batch())
    assert "001.mp3 → P.001" in page and "3 findings · 2 to review" in page
    assert "Audio format not supported" in page


def test_coded_interviews_close_under_one_line_and_failures_stay_open():
    page = gui.status_html(_batch())
    assert page.index("Audio format not supported") < page.index("<details")
    assert "<details " in page and "<details open" not in page
    assert "1 interview coded</summary>" in page


def test_smoke_the_page_builds():
    gui.build_app()


def test_working_panel_names_the_files_and_the_wait():
    plans = [
        FilePlan("/t/1.mp3", "1.mp3", "P.1", "transcribe"),
        FilePlan("/t/2.txt", "2.txt", "P.2", "transcript"),
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
        gui.handlers, "file_plan", lambda files, language: [FilePlan(f, f, "P.1", "transcribe") for f in files]
    )
    start, working, review, results = gui.begin_run(["1.mp3"], "fr")
    assert start["interactive"] is False and start["value"] == "Working…"
    assert working["visible"] is True and "Reading 1 file" in working["value"]
    assert review["visible"] is False and results["visible"] is False


# --- the review panel ----------------------------------------------------------------


@pytest.fixture
def review_on_disk(tmp_path, monkeypatch):
    """One review saved in a temp results folder: Dupont (a name) and 54 ans (an age)."""
    monkeypatch.setattr(gui.handlers, "OUTPUT_DIR", tmp_path)
    text = "Madame Dupont a 54 ans. Le docteur Vandermeulen la suit. Dupont va mieux."
    review = Review.from_spans("004.docx", "P.004", text, [
        Span(7, 13, "Dupont", "PERSON", 0.98), Span(16, 22, "54 ans", "AGE", 0.7),
    ])
    review.models = ["camembert", "gliner"]  # two models: "Unsure about" does not block
    save_review(review, tmp_path)
    return ["P.004"]


def _view(view):
    names = ["queue", "pick", "line", "text", "remove", "found", "missed", "approve", "code", "clean",
             "rest"]
    return dict(zip(names, view, strict=True))


def test_review_view_shows_counts_rows_and_the_missed_name(review_on_disk):
    view = _view(gui.review_view("P.004", review_on_disk, []))
    assert "2 removed: 1 name · 1 age" in view["line"]
    assert "No removed value is left" in view["line"]
    assert view["found"] == [[True, "54 ans", "age", 1], [True, "Dupont", "name", 2]]
    assert view["missed"][0][:2] == [False, "Vandermeulen"]
    assert ("Dupont", "name") in view["text"]
    assert view["approve"]["interactive"] is True and view["code"]["interactive"] is False


def test_unticking_puts_the_value_back(review_on_disk):
    view = _view(gui.on_removed_edit([[False, "54 ans", "age", 1], [True, "Dupont", "name", 2]],
                                     "P.004", review_on_disk, []))
    assert view["found"][0][0] is False
    assert "1 removed: 1 name" in view["line"]


def test_a_ticked_missed_word_is_removed_everywhere(review_on_disk):
    view = _view(gui.on_missed_edit([[True, "Vandermeulen", "…"]], "P.004", review_on_disk, []))
    assert view["missed"] == []
    assert "2 names" in view["line"]


def test_approve_writes_the_clean_text_and_enables_the_next_step(review_on_disk, tmp_path):
    view = _view(gui.on_approve("P.004", review_on_disk, []))
    assert view["approve"]["value"] == "✓ Approved" and view["approve"]["interactive"] is False
    assert view["code"]["interactive"] is True
    clean = (tmp_path / "clean" / "P.004.txt").read_text(encoding="utf-8")
    assert "Dupont" not in clean and "PERSON_1" in clean
    assert (tmp_path / "clean" / "P.004.docx").exists()  # a Word source gets a Word copy


def test_an_edit_after_approval_needs_a_new_approval(review_on_disk):
    gui.on_approve("P.004", review_on_disk, [])
    view = _view(gui.on_add("Vandermeulen", "PERSON", "P.004", review_on_disk, [])[1:])
    assert view["approve"]["interactive"] is True
    assert view["code"]["interactive"] is False


def test_resume_opens_the_left_reviews(review_on_disk):
    panel, queue, _failed, *_ = gui.resume_reviews()
    assert panel["visible"] is True and queue == review_on_disk


def test_clean_run_gives_a_zip_of_the_approved_texts(review_on_disk):
    assert gui.clean_run(review_on_disk)[1] == "No approved file yet."
    gui.on_approve("P.004", review_on_disk, [])
    download, note = gui.clean_run(review_on_disk)
    assert download["visible"] is True and download["value"].endswith(".zip")
    assert "clean_texts_" in note


def test_the_line_names_the_warnings(review_on_disk):
    view = _view(gui.review_view("P.004", review_on_disk, []))
    assert "⚠ Possibly missed: Vandermeulen" in view["line"]
    assert "⚠ Unsure about: 54 ans" in view["line"]
    assert "Check" in view["queue"] and "1 file to check" in view["queue"]


def test_approve_the_rest_needs_the_confirm(review_on_disk, tmp_path):
    # "docteur Vandermeulen" is a blocking sign: remove it first, then bulk works.
    gui.on_approve_rest(True, "P.004", review_on_disk, [])
    assert load_review(tmp_path, "P.004").status == "needs review"
    save_review(add_value(load_review(tmp_path, "P.004"), "Vandermeulen"), tmp_path)
    gui.on_approve_rest(False, "P.004", review_on_disk, [])
    assert load_review(tmp_path, "P.004").status == "needs review"
    view = _view(gui.on_approve_rest(True, "P.004", review_on_disk, []))
    assert load_review(tmp_path, "P.004").approved_how == BULK
    assert "All 1 file approved" in view["queue"] and view["rest"]["visible"] is False


def test_begin_coding_names_the_files(review_on_disk):
    gui.on_approve("P.004", review_on_disk, [])
    button, panel = gui.begin_coding(review_on_disk)
    assert button["interactive"] is False
    assert panel["visible"] is True and "004.docx" in panel["value"] and "1 file" in panel["value"]


def test_the_page_says_that_the_removal_can_miss_a_name():
    app = gui.build_app()
    values = [str(c.get("props", {}).get("value", "")) for c in app.config["components"]]
    assert any(PII_WARNING in v for v in values)
    assert "can miss a name" in PII_WARNING


def test_the_approve_rest_question_is_a_valid_js_string(monkeypatch):
    question = 'Approve "all"? It\'s a \\ test.'
    monkeypatch.setattr(gui.app, "APPROVE_REST_QUESTION", question)
    js = gui.approve_rest_js()
    literal = js[js.index("confirm(") + len("confirm("):js.index("), p, q, f]")]
    assert json.loads(literal) == question


# --- the two runs behind the main buttons ---------------------------------------------------


def _no_progress(*args, **kwargs):
    pass


@pytest.fixture
def results_folder(tmp_path, monkeypatch):
    """The page writes to tmp_path/out with the default config. The detector finds "Dupont"."""
    out = tmp_path / "out"
    monkeypatch.setattr(gui.handlers, "OUTPUT_DIR", out)
    monkeypatch.setattr(gui.handlers, "CONFIG_PATH", str(tmp_path / "none.yaml"))

    def detect(text, **k):
        start = text.find("Dupont")
        return ([Span(start, start + 6, "Dupont", "PERSON", 0.98)] if start >= 0 else []), k["models"]

    monkeypatch.setattr(pipeline, "detect_personal_details", detect)
    monkeypatch.setattr(pipeline, "extract_symptoms", lambda text, **k: [{"clinical_term": "fatigue"}])
    monkeypatch.setattr(pipeline, "match_hpo", lambda symptoms, **k: [
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": "fatiguée", "negated": False}])
    return out


def _source(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_prepare_run_puts_the_files_in_the_queue_and_opens_the_review(tmp_path, results_folder):
    files = [_source(tmp_path, "004.txt", "Le docteur Vandermeulen voit madame Dupont."),
             _source(tmp_path, "005.txt", "Madame Dupont va mieux.")]
    working, review, queue, failed, *view = gui.prepare_run(files, "fr", list(gui.handlers.OPTIONAL_TYPES), [],
                                                            progress=_no_progress)
    assert working["visible"] is False and review["visible"] is True
    assert queue == ["004", "005"] and failed == []
    # 005 is clear and approved on its own; 004 has a missed name and opens first.
    assert load_review(results_folder, "005").status == APPROVED
    assert "Vandermeulen" in _view(view)["line"]


def test_code_run_codes_the_approved_files_into_a_new_excel(tmp_path, results_folder):
    files = [_source(tmp_path, "005.txt", "Madame Dupont est fatiguée.")]
    queue = gui.prepare_run(files, "fr", list(gui.handlers.OPTIONAL_TYPES), [], progress=_no_progress)[2]
    out = gui.code_run(queue, "openai", "sk-test", progress=_no_progress)
    rows, download = out[7], out[9]
    assert [row[:5] for row in rows] == [["005", "fatiguée", "Fatigue", "HP:0012378", "Present"]]
    assert download["visible"] is True and download["value"].endswith(".xlsx")
    assert "Saved in" in out[10]


def test_unticking_a_type_keeps_its_values(review_on_disk):
    view = _view(gui.handlers.on_types(["DATE", "GENDER", "JOB"], "P.004", review_on_disk, []))  # AGE unticked
    assert "1 removed: 1 name" in view["line"]
    assert [row[1] for row in view["found"] if row[0]] == ["Dupont"]
