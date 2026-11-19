"""A transcript of a healthy person (tests/fixtures/healthy_control.txt) gives no HPO codes."""

import pytest
from conftest import FIXTURES, read_sheet

from phenoscribe import pipeline
from phenoscribe.config import Config
from phenoscribe.extract_symptoms import extract_symptoms
from phenoscribe.output import ResultsWorkbook

HEALTHY = FIXTURES / "healthy_control.txt"


def test_no_symptoms_give_a_workbook_with_the_header_only(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "detect_personal_details", lambda *a, **k: ([], []))
    monkeypatch.setattr(pipeline, "extract_symptoms", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "match_hpo", lambda symptoms, **k: pytest.fail("no symptom to match")
                        if symptoms else [])
    excel = tmp_path / "results.xlsx"

    matches = pipeline.process_recording(str(HEALTHY), "HEALTHY", Config(),
                                         ResultsWorkbook.for_config(excel, Config()), no_review=True)

    assert matches == []
    header, rows = read_sheet(excel)
    assert header[0] == "Patient_ID"
    assert rows == []


@pytest.mark.live
def test_the_live_extractor_finds_at_most_one_borderline_symptom(live_llm):
    """One real extraction of the healthy transcript: no symptom, or one borderline symptom at most."""
    symptoms = extract_symptoms(HEALTHY.read_text(encoding="utf-8"), live_llm)
    assert len(symptoms) <= 1, f"healthy control produced {len(symptoms)} symptoms: {symptoms}"
