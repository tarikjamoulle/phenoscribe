"""Patient IDs, interview details and file names (naming.py)."""

from datetime import datetime

import pytest

from phenoscribe.config import Config, PatientConfig
from phenoscribe.naming import (
    batch_patient_id,
    derive_patient_id,
    interview_details,
    new_results_path,
    transcript_cache_path,
)


def test_the_prefix_goes_before_a_bare_stem():
    assert derive_patient_id("001", "P.") == "P.001"


def test_the_prefix_is_not_applied_twice():
    assert derive_patient_id("P.001", "P.") == "P.001"


def test_an_empty_prefix_keeps_the_stem():
    assert derive_patient_id("001", "") == "001"


def test_a_digit_stem_gives_a_prefixed_id():
    assert batch_patient_id("001.mp3", "P.") == "P.001"


def test_a_name_in_the_file_name_gives_a_hashed_id():
    pid = batch_patient_id("jean-martin.mp3", "P.")
    assert pid.startswith("pt-") and len(pid) == len("pt-") + 8
    assert "jean" not in pid
    assert batch_patient_id("jean-martin.mp3", "P.") == pid  # stable
    # A recording and its transcript share one ID.
    assert batch_patient_id("jean-martin.txt", "P.") == pid


def test_the_results_path_is_dated_and_never_reused(tmp_path):
    now = datetime(2026, 10, 22, 14, 5)
    first = new_results_path(tmp_path, now)
    assert first.name == "results_2026-10-22_1405.xlsx"
    first.touch()
    assert new_results_path(tmp_path, now).name == "results_2026-10-22_1405_2.xlsx"


def test_the_cache_key_changes_with_the_whisper_settings(tmp_path):
    audio = tmp_path / "001.mp3"
    audio.write_bytes(b"audio")
    config = Config(patient=PatientConfig(id_prefix="P."))
    other = Config()
    other.transcription.language = "nl"
    assert transcript_cache_path(str(audio), config, "t") != transcript_cache_path(str(audio), other, "t")


@pytest.mark.parametrize("name, patient, kind, date", [
    ("P.006 interview.docx", "P.006", "interview", ""),
    ("006  interview evolution  2025-03-11_08-30-00.docx", "P.006", "follow-up", "2025-03-11"),
    ("P.006 mail du 20250402.docx", "P.006", "email", "2025-04-02"),
    ("008 interview evolution  20250517 mai à 14-20.docx", "P.008", "follow-up", "2025-05-17"),
    ("007 intrview evolution 20250517.docx", "P.007", "follow-up", "2025-05-17"),
    ("XX_ interview 20240105_.docx", "", "interview", "2024-01-05"),
    ("001.mp3", "P.001", "interview", ""),
])
def test_interview_details_link_the_files_of_one_patient(name, patient, kind, date):
    assert interview_details(name, "P.") == {"patient": patient, "interview": kind, "date": date}


@pytest.mark.parametrize("prefix, name, patient", [
    ("P.", "p 006 interview.docx", "P.006"),
    ("ABC.", "ABC.006 interview.docx", "ABC.006"),
    ("ABC.", "abc006 interview.docx", "ABC.006"),
    ("ABC.", "P.006 interview.docx", ""),  # another prefix is not a patient number
    ("", "006 interview.docx", "006"),
    ("", "P.006 interview.docx", ""),
])
def test_interview_details_read_the_configured_prefix(prefix, name, patient):
    assert interview_details(name, prefix)["patient"] == patient
