"""The command line (cli.py): provider names and the process, clean, status and validate commands."""

import logging
import zipfile
from datetime import datetime

import pytest
from conftest import read_sheet, write_codes

from phenoscribe import naming, pipeline
from phenoscribe.cli import main, resolve_provider
from phenoscribe.output import PERSONAL_DETAILS_SHEET
from phenoscribe.review import Review, approve, save_review


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


# --- `phenoscribe validate` -------------------------------------------------------------
#
#   HP:0002027  Abdominal pain   (exact hit)
#   HP:0012378  Fatigue          (exact hit)
#   HP:0012531  Pain             (parent of Abdominal pain: a near miss)


def test_validate_prints_f1_and_the_ic_distribution(tmp_path, capsys, hpo_obo, ic_map):
    pred, gt = tmp_path / "results.xlsx", tmp_path / "ground_truth.xlsx"
    write_codes(pred, "P.001", ["HP:0002027", "HP:0012378", "HP:0012531"])
    write_codes(gt, "P.001", ["HP:0002027", "HP:0012378"])

    main(["validate", str(pred), str(gt), "--obo", hpo_obo])

    out = capsys.readouterr().out
    assert "Strict (exact HPO ID match, document-level):" in out
    assert "F1:" in out
    assert "IC distribution" in out
    assert "n=" in out and "median=" in out
    assert "Exact matches:  2" in out


# --- `phenoscribe process` and `phenoscribe status` --------------------------


@pytest.fixture
def stub_steps(monkeypatch):
    """Every model step stubbed: no personal detail, one Fatigue code for each file."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(pipeline, "detect_personal_details", lambda text, **k: ([], list(k["models"])))
    monkeypatch.setattr(pipeline, "extract_symptoms", lambda text, **k: [{"symptom": "fatigue"}])
    monkeypatch.setattr(pipeline, "match_hpo", lambda symptoms, **k: [
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": "fatigué"}])


def _inputs(tmp_path, **files):
    folder = tmp_path / "in"
    folder.mkdir(exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def _process(tmp_path, folder, *flags):
    out = tmp_path / "out"
    main(["process", str(folder), "--output", str(out), "--config", str(tmp_path / "none.yaml"), *flags])
    return out


def _excel_ids(path):
    return [row[0] for row in read_sheet(path)[1]]


def test_process_codes_each_file_into_a_new_results_file(tmp_path, stub_steps, capsys):
    folder = _inputs(tmp_path, **{"001.txt": "Je suis fatigué.", "notes.pdf": ""})
    out = _process(tmp_path, folder, "--no-review")
    printed = capsys.readouterr().out
    (excel,) = out.glob("results_*.xlsx")
    assert "[001] 001.txt: done, 1 HPO codes" in printed
    assert "Skipped notes.pdf: Not an audio file or transcript" in printed
    assert f"Results: {excel}" in printed
    assert _excel_ids(excel) == ["001"]


def test_process_gives_a_hashed_id_to_a_name_like_file(tmp_path, stub_steps):
    folder = _inputs(tmp_path, **{"martin-jean.txt": "Je suis fatigué."})
    (excel,) = _process(tmp_path, folder, "--no-review").glob("results_*.xlsx")
    (patient_id,) = _excel_ids(excel)
    assert patient_id.startswith("pt-") and "martin" not in patient_id


def test_a_second_process_run_writes_a_new_file_with_no_duplicate_rows(tmp_path, stub_steps, monkeypatch):
    folder = _inputs(tmp_path, **{"001.txt": "Je suis fatigué."})
    monkeypatch.setattr(naming, "datetime", type("Fixed", (), {"now": staticmethod(lambda: datetime(2026, 1, 2, 3, 4))}))
    out = _process(tmp_path, folder, "--no-review")
    _process(tmp_path, folder, "--no-review")
    files = sorted(out.glob("results_*.xlsx"))
    assert [f.name for f in files] == ["results_2026-01-02_0304.xlsx", "results_2026-01-02_0304_2.xlsx"]
    assert [_excel_ids(f) for f in files] == [["001"], ["001"]]


def test_process_exits_with_1_when_a_file_fails(tmp_path, stub_steps, monkeypatch, capsys):
    def extract(text, **k):
        raise RuntimeError("the provider refused the key")

    monkeypatch.setattr(pipeline, "extract_symptoms", extract)
    folder = _inputs(tmp_path, **{"001.txt": "Je suis fatigué."})
    with pytest.raises(SystemExit) as stop:
        _process(tmp_path, folder, "--no-review")
    assert stop.value.code == 1
    assert "001.txt: FAILED: the provider refused the key" in capsys.readouterr().err


def test_process_stops_before_any_work_when_the_key_is_missing(tmp_path, stub_steps, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY")
    folder = _inputs(tmp_path, **{"001.txt": "Je suis fatigué."})
    with pytest.raises(SystemExit) as stop:
        _process(tmp_path, folder, "--no-review")
    assert stop.value.code == 1
    assert "set the environment variable OPENAI_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_a_file_that_waits_for_a_review_is_no_failure(tmp_path, stub_steps, capsys):
    folder = _inputs(tmp_path, **{"001.txt": "Je suis fatigué."})
    _process(tmp_path, folder)  # no --no-review: the file waits for the page
    printed = capsys.readouterr().out
    assert "001.txt: waits for a review." in printed
    assert "No results file" in printed


def test_status_counts_the_reviews_and_names_the_files_that_wait(tmp_path, capsys):
    out = tmp_path / "out"
    save_review(Review.from_spans("001.txt", "001", "Bonjour.", []), out)
    save_review(approve(Review.from_spans("002.txt", "002", "Bonjour.", [])), out)
    main(["status", "--output", str(out)])
    printed = capsys.readouterr().out
    assert "needs review: 1" in printed and "approved: 1" in printed and "done: 0" in printed
    assert "001  001.txt" in printed


def test_a_missing_input_folder_is_an_error_on_stderr(tmp_path, capsys):
    with pytest.raises(SystemExit) as stop:
        main(["process", str(tmp_path / "nowhere"), "--output", str(tmp_path / "out")])
    assert stop.value.code == 1
    assert "is not a directory" in capsys.readouterr().err


def test_a_config_error_prints_one_line_and_no_traceback(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("llm:\n  provder: openai\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["process", str(tmp_path), "--config", str(config)])
    err = capsys.readouterr().err
    assert "unknown key llm.provder" in err and "Traceback" not in err


def test_process_writes_the_personal_details_sheet(tmp_path, stub_steps):
    folder = _inputs(tmp_path, **{"001.txt": "Je suis fatigué."})
    (excel,) = _process(tmp_path, folder, "--no-review").glob("results_*.xlsx")
    header, rows = read_sheet(excel, PERSONAL_DETAILS_SHEET)
    assert rows[0][header.index("Patient_ID")] == "001"


def test_clean_exits_with_1_when_a_file_fails(tmp_path, monkeypatch, capsys):
    def detect(text, **k):
        raise RuntimeError("the model did not load")

    monkeypatch.setattr(pipeline, "detect_personal_details", detect)
    folder = _inputs(tmp_path, **{"001.txt": "Je suis fatigué."})
    with pytest.raises(SystemExit) as stop:
        main(["clean", str(folder), "--output", str(tmp_path / "out"), "--config", str(tmp_path / "none.yaml")])
    assert stop.value.code == 1
    assert "FAILED: the model did not load" in capsys.readouterr().err


def test_the_console_shows_warnings_only_unless_verbose(tmp_path, monkeypatch):
    levels = []
    monkeypatch.setattr(logging, "basicConfig", lambda **k: levels.append(k["level"]))
    main(["status", "--output", str(tmp_path)])
    main(["status", "--output", str(tmp_path), "--verbose"])
    assert levels == [logging.WARNING, logging.INFO]


def test_clean_keeps_a_name_in_the_file_name_out_of_the_zip(tmp_path, stub_steps):
    folder = _inputs(tmp_path, **{"martin-jean.txt": "Je suis fatigué."})
    main(["clean", str(folder), "--output", str(tmp_path / "out"), "--config", str(tmp_path / "none.yaml")])
    (archive,) = (tmp_path / "out").glob("clean_texts_*.zip")
    names = zipfile.ZipFile(archive).namelist()
    assert not any("martin" in n for n in names)
    assert any(n.startswith("clean/pt-") for n in names)
