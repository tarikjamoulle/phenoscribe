"""A file with a blocking warning sign never reaches llm.llm_call with a cloud provider.

Blocking signs (review.blocking_warnings): the leak check finds a first name or a
town in the clean text, or a word after a title stays in it. The detector here
misses everything, so only the leak check can stop the file.

Every LLM step (speaker labels, symptom extraction, HPO judge) is real up to
llm_call, which records each call. A case that must call the model checks that
the recorder sees the call, so the "no call" cases cannot pass by accident.
"""

import zipfile

import openpyxl
import pytest

from phenoscribe import batch as runs
from phenoscribe import extract_symptoms, match_hpo, pipeline, speakers
from phenoscribe.cli import main
from phenoscribe.config import Config, LLMConfig
from phenoscribe.output import ResultsWorkbook
from phenoscribe.pii import Span
from phenoscribe.review import (
    AUTO,
    BULK,
    CHECKED,
    NEEDS_REVIEW,
    NO_REVIEW,
    Review,
    ReviewError,
    approve,
    blocking_warnings,
    load_review,
    reopen_if_blocked,
    save_review,
    warning_signs,
)

pytestmark = pytest.mark.real_speaker_pass

LEAKY = "Bonjour docteur. J'habite à Namur avec ma fille Claire, elle m'aide. J'ai mal au dos."
UNSURE = "Bonjour. Madame Dupont a mal au dos depuis un mois."
NOTHING = "Le patient décrit une toux sèche et une fatigue importante."


@pytest.fixture
def calls(monkeypatch):
    """Record each LLM call. Answer "[]" (no symptoms, no speaker labels)."""
    seen = []

    def fake_llm_call(system_prompt, user_prompt, llm, **kwargs):
        seen.append((llm.provider, user_prompt))
        return "[]"

    for module in (extract_symptoms, match_hpo, speakers):
        monkeypatch.setattr(module, "llm_call", fake_llm_call)
    monkeypatch.setattr(pipeline, "detect_personal_details", _detector)
    return seen


def _detector(text, models, **kwargs):
    """Finds "Dupont" only, at a low score. Misses Namur and Claire. Every model runs."""
    if "Dupont" not in text:
        return [], list(models)
    start = text.index("Dupont")
    return [Span(start, start + 6, "Dupont", "PERSON", 0.65)], list(models)


def _config(provider="openai"):
    config = Config()
    config.llm.provider = provider
    config.llm.extraction_runs = 1
    return config


def _file(tmp_path, text, name="005.txt"):
    path = tmp_path / "in" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_the_leak_check_is_a_blocking_sign():
    review = Review.from_spans("a.txt", "A", LEAKY, [])
    assert any(s.text.startswith("Possibly left: Namur, Claire") for s in blocking_warnings(review))


@pytest.mark.parametrize("provider", ["openai", "anthropic", "some-new-cloud"])
def test_batch_mode_stops_a_file_with_a_blocking_sign(tmp_path, calls, provider):
    out = tmp_path / "out"
    with pytest.raises(pipeline.NeedsReview, match="stopped, not sent to the AI provider"):
        pipeline.process_recording(_file(tmp_path, LEAKY), "P.005", _config(provider),
                                   book=ResultsWorkbook(out / "r.xlsx"), no_review=True)
    assert calls == []
    assert load_review(out, "P.005").status == NEEDS_REVIEW


def test_run_batch_keeps_the_stop_on_the_file_and_codes_the_others(tmp_path, calls):
    files = [_file(tmp_path, LEAKY, "005.txt"), _file(tmp_path, NOTHING, "006.txt")]
    batch = runs.run_batch(files, _config(), tmp_path / "out", no_review=True)
    by_name = {r.filename: r for r in batch.files}
    assert isinstance(by_name["005.txt"].error, pipeline.NeedsReview)
    assert by_name["006.txt"].error is None
    assert calls and all("Namur" not in prompt and "Claire" not in prompt for _, prompt in calls)


@pytest.mark.parametrize("how", [AUTO, BULK, NO_REVIEW, ""])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_no_cloud_call_without_a_person_check(tmp_path, calls, how, provider):
    # Approved with no person check (auto, bulk, CLI, or an empty mark): the
    # gate in code_review stops it.
    review = approve(Review.from_spans("005.txt", "P.005", LEAKY, []), how=how)
    with pytest.raises(ReviewError, match="not sent to the AI provider"):
        pipeline.code_review(review, _config(provider), ResultsWorkbook(tmp_path / "r.xlsx"))
    assert calls == []


def test_code_batch_does_not_send_a_file_with_a_blocking_sign(tmp_path, calls):
    save_review(approve(Review.from_spans("005.txt", "P.005", LEAKY, []), how=AUTO), tmp_path)
    batch = runs.code_batch(["P.005"], _config(), tmp_path)
    assert isinstance(batch.files[0].error, ReviewError)
    assert calls == []


def test_a_review_approved_by_clean_is_still_stopped_in_batch_mode(tmp_path, calls):
    # `phenoscribe clean` approves with NO_REVIEW. A later --no-review run reuses it.
    out = tmp_path / "out"
    source = _file(tmp_path, LEAKY)
    save_review(approve(Review.from_spans("005.txt", "P.005", LEAKY, []), how=NO_REVIEW), out)
    with pytest.raises(ReviewError):
        pipeline.process_recording(source, "P.005", _config(), book=ResultsWorkbook(out / "r.xlsx"),
                                   no_review=True)
    assert calls == []


def test_a_person_check_lets_the_file_go(tmp_path, calls):
    # The person read "Namur" and "Claire" and approved: their decision.
    review = pipeline.find_details(LEAKY, "005.txt", "P.005", _config(), tmp_path)
    approve(review, how=CHECKED)
    pipeline.code_review(review, _config(), ResultsWorkbook(tmp_path / "r.xlsx"))
    assert calls
    # The workbook names the language and the privacy models of the removal.
    rows = dict(openpyxl.load_workbook(tmp_path / "r.xlsx")["Provenance"].iter_rows(values_only=True))
    assert rows["pii_language"] == "fr"
    assert rows["pii_models"] == ", ".join(Config().pii.models_for("fr"))


def test_a_language_change_finds_the_details_again(tmp_path, calls):
    # An approved French review is not reused for a Dutch run: other models run.
    review = pipeline.find_details(LEAKY, "005.txt", "P.005", _config(), tmp_path)
    save_review(approve(review, how=CHECKED), tmp_path)
    dutch = _config()
    dutch.transcription.language = "nl"
    again = pipeline.find_details(LEAKY, "005.txt", "P.005", dutch, tmp_path)
    assert again.status == NEEDS_REVIEW and again.language == "nl"
    assert again.models == Config().pii.models_for("nl")  # the models the detector reports
    assert pipeline.find_details(LEAKY, "005.txt", "P.005", _config(), tmp_path).language == "fr"


def test_a_local_model_is_not_a_cloud_call(tmp_path, calls):
    review = approve(Review.from_spans("005.txt", "P.005", LEAKY, []), how=AUTO)
    pipeline.code_review(review, _config("ollama"), ResultsWorkbook(tmp_path / "r.xlsx"))
    assert calls and all(provider == "ollama" for provider, _ in calls)


@pytest.mark.parametrize("text, sign", [(UNSURE, "Unsure about"), (NOTHING, "No personal detail found")])
def test_batch_mode_continues_when_nothing_can_be_left(tmp_path, calls, text, sign):
    review = Review.from_spans("a.txt", "A", text, _detector(text, [])[0])
    assert [s.kind for s in warning_signs(review)] == [sign]
    pipeline.process_recording(_file(tmp_path, text), "P.005", _config(),
                               book=ResultsWorkbook(tmp_path / "out" / "r.xlsx"), no_review=True)
    assert calls
    assert all("Dupont" not in prompt for _, prompt in calls)


def test_batch_mode_stops_an_unsure_file_when_one_model_ran(tmp_path, calls):
    # German uses GLiNER only: no second model checks a low-score value.
    config = _config()
    config.transcription.language = "de"
    with pytest.raises(pipeline.NeedsReview, match="Unsure about"):
        pipeline.process_recording(_file(tmp_path, UNSURE), "P.005", config,
                                   book=ResultsWorkbook(tmp_path / "out" / "r.xlsx"), no_review=True)
    assert calls == []


def test_a_remote_ollama_server_is_a_cloud_provider(tmp_path, calls):
    config = _config("ollama")
    config.llm.ollama_base_url = "https://gpu.example.org"
    review = approve(Review.from_spans("005.txt", "P.005", LEAKY, []), how=AUTO)
    with pytest.raises(ReviewError):
        pipeline.code_review(review, config, ResultsWorkbook(tmp_path / "r.xlsx"))
    assert calls == []
    assert LLMConfig(provider="ollama", ollama_base_url="http://127.0.0.1:11434").is_cloud is False


def test_the_page_reopens_an_approved_file_with_a_blocking_sign():
    auto = approve(Review.from_spans("005.txt", "P.005", LEAKY, []), how=AUTO)
    assert reopen_if_blocked(auto) and auto.status == NEEDS_REVIEW
    checked = approve(Review.from_spans("005.txt", "P.005", LEAKY, []), how=CHECKED)
    assert not reopen_if_blocked(checked)


def test_clean_leaves_a_file_with_a_blocking_sign_out_of_the_zip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "detect_personal_details", _detector)
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "005.txt").write_text(LEAKY, encoding="utf-8")
    (folder / "006.txt").write_text(UNSURE, encoding="utf-8")
    out = tmp_path / "out"
    main(["clean", str(folder), "--output", str(out)])
    assert "NOT IN THE ZIP" in capsys.readouterr().out
    (archive,) = out.glob("clean_texts_*.zip")
    names = zipfile.ZipFile(archive).namelist()
    assert any(n.endswith("006.txt") for n in names) and not any(n.endswith("005.txt") for n in names)
