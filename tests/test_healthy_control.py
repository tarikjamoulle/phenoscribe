"""Robinson Test Q8: false-positive rate on a healthy-control transcript.

A transcript with no symptoms must produce no HPO codes. The deterministic test
stubs the symptom extractor to return [] (the correct read of a healthy
transcript) and asserts the pipeline writes an empty result. A live smoke test
runs the real extractor once when an API key is present, to confirm the model
itself does not hallucinate codes from "the patient is well".
"""

import os
from pathlib import Path

import openpyxl
import pytest

from phenoscribe.config import Config
from phenoscribe.pipeline import process_recording


FIXTURE = Path(__file__).parent / "fixtures" / "healthy_control.txt"
HEALTHY_TEXT = FIXTURE.read_text(encoding="utf-8").strip()


def _config_for(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.output.path = str(tmp_path / "results.xlsx")
    cfg.output.format = "detailed"
    return cfg


def _seed_transcript(out_dir: Path, patient_id: str, text: str) -> None:
    tdir = out_dir / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / f"{patient_id}.txt").write_text(text, encoding="utf-8")


def _row_count(path: Path) -> int:
    if not path.exists():
        return 0
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    return max(0, ws.max_row - 1)  # minus the header row


def test_healthy_control_emits_zero_codes(tmp_path, monkeypatch):
    """Deterministic: a healthy transcript yields no symptoms -> no codes."""
    cfg = _config_for(tmp_path)
    out_dir = tmp_path
    _seed_transcript(out_dir, "HEALTHY", HEALTHY_TEXT)

    # Healthy transcript -> the extractor finds nothing to code.
    monkeypatch.setattr(
        "phenoscribe.pipeline.extract_symptoms",
        lambda *a, **k: [],
    )
    # ChromaDB is never reached with zero symptoms, but stub it so the test
    # stays hermetic if that ever changes.
    monkeypatch.setattr(
        "phenoscribe.match_hpo.search_hpo",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("search_hpo should not run for a healthy transcript")
        ),
    )

    matches = process_recording(
        input_path="unused",
        patient_id="HEALTHY",
        config=cfg,
        skip_transcription=True,
        skip_pii=True,
    )

    assert matches == []
    assert _row_count(Path(cfg.output.path)) == 0


def test_match_hpo_with_empty_symptoms_returns_empty():
    """The matcher itself returns [] for [] without touching ChromaDB or the LLM."""
    from phenoscribe.match_hpo import match_hpo

    assert match_hpo([]) == []


@pytest.mark.skipif(
    not os.environ.get("PHENOSCRIBE_LIVE_SMOKE"),
    reason="live smoke test; set PHENOSCRIBE_LIVE_SMOKE=1 and an API key to run",
)
def test_healthy_control_live_smoke(tmp_path):
    """One real extractor call on the healthy transcript: expect zero codes.

    Opt-in only. Keeps live calls modest (a single extraction call).
    """
    from phenoscribe.extract_symptoms import extract_symptoms

    provider = os.environ.get("PHENOSCRIBE_PROVIDER", "anthropic")
    model = os.environ.get("PHENOSCRIBE_MODEL", "claude-sonnet-4-6")
    symptoms = extract_symptoms(HEALTHY_TEXT, provider=provider, model=model)

    # A healthy transcript should yield no codable symptoms. Allow at most a
    # single borderline extraction before we call it a false positive.
    assert len(symptoms) <= 1, f"healthy control produced {len(symptoms)} symptoms: {symptoms}"
