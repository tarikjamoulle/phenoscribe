"""Batches of files (batch.py): the plan for each file, one new Excel per run, progress, errors,
the approval of clear files, and the coding of approved files."""

import json
import zipfile

import pytest
from conftest import read_sheet

from phenoscribe import batch as batch_module
from phenoscribe import pipeline
from phenoscribe.batch import (
    CODE_PROGRESS,
    STEP_PROGRESS,
    approve_clear_files,
    approve_the_rest,
    code_batch,
    finish_clean,
    plan_batch,
    prepare_batch,
    run_batch,
    step_fraction,
)
from phenoscribe.config import Config, PatientConfig
from phenoscribe.naming import transcript_cache_path
from phenoscribe.output import PERSONAL_DETAILS_SHEET
from phenoscribe.pii import Span
from phenoscribe.review import (
    APPROVED,
    AUTO,
    BULK,
    DONE,
    NEEDS_REVIEW,
    Review,
    approve,
    load_review,
    save_review,
)

CONFIG = Config(patient=PatientConfig(id_prefix="P."))
CLEAR_TEXT = "Madame Dupont va mieux."


def _files(folder, *names, content=b"audio"):
    paths = []
    for name in names:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        paths.append(str(path))
    return paths


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Replace process_recording: one Fatigue code per file, or raise for 'bad' files."""
    calls = []

    def fake(input_path, patient_id, config, book, on_step=None, **kwargs):
        calls.append((patient_id, kwargs.get("reuse_transcript")))
        on_step("removing personal details")
        if "bad" in input_path:
            raise ValueError("Unsupported file type '.xyz'")
        matches = [{"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": ""}]
        book.add_patient(patient_id, matches)
        return matches

    monkeypatch.setattr(batch_module, "process_recording", fake)
    return calls


def _patient_ids(path):
    return [row[0] for row in read_sheet(path)[1]]


# --- run_batch ----------------------------------------------------------------------------


def test_two_runs_give_two_files(tmp_path, fake_pipeline):
    files = _files(tmp_path / "in", "001.mp3")
    first = run_batch(files, CONFIG, tmp_path)
    second = run_batch(files, CONFIG, tmp_path)

    assert first.excel_path != second.excel_path
    assert first.excel_path.name.startswith("results_")
    # The rerun adds no rows to the first file.
    assert _patient_ids(first.excel_path) == ["P.001"]
    assert _patient_ids(second.excel_path) == ["P.001"]


def test_a_failed_file_does_not_stop_the_batch(tmp_path, fake_pipeline):
    files = _files(tmp_path / "in", "bad.mp3", "001.mp3")
    batch = run_batch(files, CONFIG, tmp_path)

    bad, good = batch.files
    assert isinstance(bad.error, ValueError)
    assert good.error is None and good.n_codes == 1
    assert good.matches[0]["hpo_id"] == "HP:0012378"
    assert _patient_ids(batch.excel_path) == ["P.001"]


def test_no_excel_when_every_file_fails(tmp_path, fake_pipeline):
    files = _files(tmp_path / "in", "bad.mp3")
    assert run_batch(files, CONFIG, tmp_path).excel_path is None


def test_progress_names_the_file_and_the_step(tmp_path, fake_pipeline):
    messages = []
    run_batch(
        _files(tmp_path / "in", "002.mp3", "001.mp3"),
        CONFIG,
        tmp_path,
        on_progress=lambda fraction, message: messages.append((fraction, message)),
    )
    # File 2 of 2, at the start of the removal step.
    fraction = (1 + STEP_PROGRESS["removing personal details"]) / 2
    assert (fraction, "2/2 · 001.mp3 · removing personal details") in messages
    assert messages[-1] == (1.0, "Finished")


def test_filename_mapping_keeps_the_name_out_of_the_excel(tmp_path, fake_pipeline):
    batch = run_batch(_files(tmp_path / "in", "jean-martin.mp3"), CONFIG, tmp_path)
    pid = batch.files[0].patient_id

    mapping = json.loads((tmp_path / "filename_mapping.json").read_text(encoding="utf-8"))
    assert mapping == {pid: "jean-martin.mp3"}
    assert _patient_ids(batch.excel_path) == [pid]


def test_run_batch_skips_planned_skips_and_honours_retranscribe(tmp_path, fake_pipeline):
    files = _files(tmp_path / "in", "002.mp3", "001.mp3", "001.txt", "notes.pdf")
    run_batch(files, CONFIG, tmp_path, retranscribe=frozenset({"002.mp3"}))
    assert fake_pipeline == [("P.002", False), ("P.001", True)]


def test_the_bar_moves_within_the_matching_step():
    matching, writing = CODE_PROGRESS["matching HPO codes"], CODE_PROGRESS["writing the Excel file"]
    assert step_fraction(CODE_PROGRESS, "matching HPO codes") == matching
    assert step_fraction(CODE_PROGRESS, "matching HPO codes · symptom 1 of 10") == matching
    # Symptom 6 of 10: five of ten done, half way to the next step.
    assert step_fraction(CODE_PROGRESS, "matching HPO codes · symptom 6 of 10") == pytest.approx(
        matching + (writing - matching) / 2)
    assert step_fraction(CODE_PROGRESS, "writing the Excel file") == writing


# --- plan_batch: the work for each file ----------------------------------------------------


def _actions(plans):
    return {p.name: (p.action, p.note) for p in plans}


def test_plan_audio_transcript_and_unsupported(tmp_path):
    files = _files(tmp_path / "in", "003.mp3", "009.txt", "consent.pdf", ".DS_Store", "103_pseudo.txt")
    assert _actions(plan_batch(files, CONFIG, tmp_path)) == {
        "003.mp3": ("transcribe", ""),
        "009.txt": ("transcript", ""),
        "consent.pdf": ("skip", "Not an audio file or transcript"),
    }


def test_plan_transcript_wins_over_its_audio(tmp_path):
    files = _files(tmp_path / "in", "001.mp3", "001.txt")
    assert _actions(plan_batch(files, CONFIG, tmp_path)) == {
        "001.mp3": ("skip", "Uses 001.txt instead"),
        "001.txt": ("transcript", ""),
    }


def test_plan_docx_is_a_transcript(tmp_path):
    files = _files(tmp_path / "in", "004.docx", "004.mp3", "~$004.docx", "old.doc")
    assert _actions(plan_batch(files, CONFIG, tmp_path)) == {
        "004.docx": ("transcript", ""),
        "004.mp3": ("skip", "Uses 004.docx instead"),
        "old.doc": ("skip", "Old Word file. Save it as .docx in Word, then add it again"),
    }


def test_plan_skips_two_files_for_one_patient(tmp_path):
    # 001.mp3 in two folders would overwrite each other's transcript.
    files = _files(tmp_path / "a", "001.mp3") + _files(tmp_path / "b", "001.mp3")
    plans = plan_batch(files, CONFIG, tmp_path)
    assert [p.action for p in plans] == ["skip", "skip"]
    assert plans[0].note == "Two files give patient P.001. Rename one."


def test_plan_skips_a_txt_and_a_docx_of_one_patient(tmp_path):
    files = _files(tmp_path / "in", "004.txt", "004.docx")
    assert {p.action for p in plan_batch(files, CONFIG, tmp_path)} == {"skip"}


def test_plan_reuses_the_transcript_of_the_same_audio(tmp_path):
    (audio,) = _files(tmp_path / "in", "001.mp3", content=b"bytes one")
    cache = transcript_cache_path(audio, CONFIG, str(tmp_path / "transcripts"))
    cache.parent.mkdir(parents=True)
    cache.write_text("bonjour", encoding="utf-8")
    assert plan_batch([audio], CONFIG, tmp_path)[0].action == "reuse"

    # Other bytes under the same name: transcribe again.
    (tmp_path / "in" / "001.mp3").write_bytes(b"bytes two")
    assert plan_batch([audio], CONFIG, tmp_path)[0].action == "transcribe"


# --- prepare, approve and code -------------------------------------------------------------


@pytest.fixture
def sent(monkeypatch):
    """Stub the detector and both LLM steps; record the text the LLM gets."""
    texts = []

    def detect(text, **k):
        start = text.find("Dupont")
        return ([Span(start, start + 6, "Dupont", "PERSON", 0.95)] if start >= 0 else []), k["models"]

    monkeypatch.setattr(pipeline, "detect_personal_details", detect)
    monkeypatch.setattr(pipeline, "extract_symptoms", lambda text, **k: texts.append(text) or [])
    monkeypatch.setattr(pipeline, "match_hpo", lambda symptoms, **k: [])
    return texts


def _prepared(tmp_path):
    """One transcript, prepared into tmp_path/out; returns that folder."""
    source = tmp_path / "in" / "004.txt"
    source.parent.mkdir()
    source.write_text("Bonjour madame Dupont, je suis fatiguée.", encoding="utf-8")
    out_dir = tmp_path / "out"
    results = prepare_batch([str(source)], Config(), out_dir)
    assert results[0].review.status == NEEDS_REVIEW
    return out_dir


def test_code_batch_refuses_a_file_that_is_not_approved(tmp_path, sent):
    out_dir = _prepared(tmp_path)
    assert code_batch(["004"], Config(), out_dir).files[0].error is not None
    assert sent == []


def test_code_batch_codes_an_approved_file_and_adds_the_counts_sheet(tmp_path, sent):
    out_dir = _prepared(tmp_path)
    save_review(approve(load_review(out_dir, "004")), out_dir)
    batch = code_batch(["004"], Config(), out_dir)
    assert sent == ["Bonjour madame PERSON_1, je suis fatiguée."]
    header, rows = read_sheet(batch.excel_path, PERSONAL_DETAILS_SHEET)
    row = dict(zip(header, rows[0], strict=True))
    assert row["Patient_ID"] == "004" and row["Names"] == 1 and row["Approved how"] == "checked"
    assert "Dupont" not in str(rows)


def test_a_credit_error_fails_the_file_and_the_next_run_codes_it(tmp_path, sent, monkeypatch):
    """The file stays approved (not done), so "Find HPO codes" picks it up again."""
    out_dir = _prepared(tmp_path)
    save_review(approve(load_review(out_dir, "004")), out_dir)

    def no_credit(symptoms, **k):
        raise RuntimeError("Error code: 429 - insufficient_quota")

    monkeypatch.setattr(pipeline, "match_hpo", no_credit)
    first = code_batch(["004"], Config(), out_dir)
    assert "insufficient_quota" in str(first.files[0].error)
    assert load_review(out_dir, "004").status == APPROVED

    monkeypatch.setattr(pipeline, "match_hpo", lambda symptoms, **k: [])
    second = code_batch(["004"], Config(), out_dir)
    assert second.files[0].error is None
    assert load_review(out_dir, "004").status == DONE


def test_finish_clean_zips_the_approved_texts(tmp_path, sent):
    out_dir = _prepared(tmp_path)
    assert finish_clean(["004"], out_dir) is None  # nothing approved
    save_review(approve(load_review(out_dir, "004")), out_dir)
    archive = finish_clean(["004"], out_dir)

    names = zipfile.ZipFile(archive).namelist()
    assert "clean/004.txt" in names and any(n.startswith("personal_details_") for n in names)
    assert load_review(out_dir, "004").status == DONE
    assert (out_dir / "clean" / "004.txt").exists()


def _one_name(patient_id, score, models=("camembert", "gliner")):
    review = Review.from_spans(f"{patient_id}.txt", patient_id, CLEAR_TEXT,
                               [Span(7, 13, "Dupont", "PERSON", score)])
    review.models = list(models)
    return review


@pytest.mark.parametrize("review, approved", [
    (_one_name("A", 0.97), True),  # a sure value: no sign
    (_one_name("C", 0.65), True),  # "Unsure about" only, two models ran: the value is removed
    (_one_name("D", 0.65, models=["gliner"]), False),  # "Unsure about" and one model: it waits
    (Review.from_spans("e.txt", "E", "Le docteur Vandermeulen la suit.", []), False),  # a missed name
])
def test_clear_files_are_approved_and_doubtful_files_wait(tmp_path, review, approved):
    assert approve_clear_files([review], tmp_path) == int(approved)
    assert review.status == (APPROVED if approved else NEEDS_REVIEW)
    assert review.approved_how == (AUTO if approved else "")


def test_an_approved_clear_file_has_its_clean_text(tmp_path):
    approve_clear_files([_one_name("A", 0.97)], tmp_path)
    assert (tmp_path / "clean" / "A.txt").read_text(encoding="utf-8") == "Madame PERSON_1 va mieux."


def test_approve_the_rest_never_approves_a_blocking_sign(tmp_path):
    save_review(Review.from_spans("e.txt", "E", "Le docteur Vandermeulen la suit.", []), tmp_path)
    assert approve_the_rest(["E"], tmp_path) == 0
    assert load_review(tmp_path, "E").status == NEEDS_REVIEW


def test_approve_the_rest_approves_an_unsure_file_in_bulk(tmp_path):
    save_review(_one_name("B", 0.65), tmp_path)
    assert approve_the_rest(["B"], tmp_path) == 1
    assert load_review(tmp_path, "B").approved_how == BULK
