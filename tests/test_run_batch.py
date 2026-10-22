"""Tests for run_batch(): one new Excel per run, patient IDs, progress, errors."""

import json
from datetime import datetime

import pytest
from openpyxl import load_workbook

from phenoscribe import pipeline
from phenoscribe.config import Config, PatientConfig
from phenoscribe.output import write_excel
from phenoscribe.pipeline import batch_patient_id, new_results_path, run_batch

CONFIG = Config(patient=PatientConfig(id_prefix="MGA."))


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Replace process_recording: one Fatigue code per file, or raise for 'bad' files."""
    calls = []

    def fake(input_path, patient_id, config, output_path=None, on_step=None, **kwargs):
        calls.append(patient_id)
        on_step("removing personal details")
        if "bad" in input_path:
            raise ValueError("Unsupported file type '.xyz'")
        matches = [{"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": ""}]
        write_excel(patient_id, matches, output_path, fmt="semicolon")
        return matches

    monkeypatch.setattr(pipeline, "process_recording", fake)
    return calls


def _patient_ids(path):
    ws = load_workbook(path).worksheets[0]
    return [row[0] for row in ws.iter_rows(min_row=2, values_only=True)]


def test_two_runs_give_two_files(tmp_path, fake_pipeline):
    first = run_batch([str(tmp_path / "467.mp3")], CONFIG, tmp_path)
    second = run_batch([str(tmp_path / "467.mp3")], CONFIG, tmp_path)

    assert first.excel_path != second.excel_path
    assert first.excel_path.name.startswith("results_")
    # The rerun adds no rows to the first file.
    assert _patient_ids(first.excel_path) == ["MGA.467"]
    assert _patient_ids(second.excel_path) == ["MGA.467"]


def test_digit_stem_gives_mga_id():
    assert batch_patient_id("467.mp3", "MGA.") == "MGA.467"


def test_name_in_filename_gives_hash_id():
    pid = batch_patient_id("jean-martin.mp3", "MGA.")
    assert pid.startswith("pt-") and len(pid) == len("pt-") + 8
    assert "jean" not in pid
    assert batch_patient_id("jean-martin.mp3", "MGA.") == pid  # stable


def test_results_path_is_dated_and_never_reused(tmp_path):
    now = datetime(2026, 10, 22, 14, 5)
    first = new_results_path(tmp_path, now)
    assert first.name == "results_2026-10-22_1405.xlsx"
    first.touch()
    assert new_results_path(tmp_path, now).name == "results_2026-10-22_1405_2.xlsx"


def test_a_failed_file_does_not_stop_the_batch(tmp_path, fake_pipeline):
    batch = run_batch([str(tmp_path / "bad.xyz"), str(tmp_path / "467.mp3")], CONFIG, tmp_path)

    bad, good = batch.files
    assert isinstance(bad.error, ValueError)
    assert good.error is None and good.n_codes == 1
    assert good.matches[0]["hpo_id"] == "HP:0012378"
    assert _patient_ids(batch.excel_path) == ["MGA.467"]


def test_no_excel_when_every_file_fails(tmp_path, fake_pipeline):
    assert run_batch([str(tmp_path / "bad.xyz")], CONFIG, tmp_path).excel_path is None


def test_progress_names_the_file_and_the_step(tmp_path, fake_pipeline):
    messages = []
    run_batch(
        [str(tmp_path / "466.mp3"), str(tmp_path / "467.mp3")],
        CONFIG,
        tmp_path,
        on_progress=lambda fraction, message: messages.append((fraction, message)),
    )
    assert (0.5, "2/2 · 467.mp3 · removing personal details") in messages
    assert messages[-1] == (1.0, "Finished")


def test_filename_mapping_keeps_the_name_out_of_the_excel(tmp_path, fake_pipeline):
    batch = run_batch([str(tmp_path / "jean-martin.mp3")], CONFIG, tmp_path)
    pid = batch.files[0].patient_id

    mapping = json.loads((tmp_path / "filename_mapping.json").read_text(encoding="utf-8"))
    assert mapping == {pid: "jean-martin.mp3"}
    assert _patient_ids(batch.excel_path) == [pid]
