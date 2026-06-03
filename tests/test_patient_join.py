"""Tests for the patient-ID join between pipeline output and ground truth.

Robinson issue #5: the cached transcript is named "467" but the ground truth
keys the patient as "MGA.467", so the join produced zero overlap and blocked
the F1 number. These tests pin the prefix derivation and the resulting join.
"""

from phenoscribe.cli import derive_patient_id
from phenoscribe.config import Config, PatientConfig
from phenoscribe.output import write_excel
from phenoscribe.validate import load_codes_from_excel


def test_prefix_is_prepended_to_bare_stem():
    assert derive_patient_id("467", "MGA.") == "MGA.467"


def test_prefix_not_double_applied():
    assert derive_patient_id("MGA.467", "MGA.") == "MGA.467"


def test_empty_prefix_is_a_noop():
    assert derive_patient_id("467", "") == "467"


def test_config_exposes_patient_id_prefix():
    cfg = Config(patient=PatientConfig(id_prefix="MGA."))
    assert cfg.patient.id_prefix == "MGA."


def test_default_prefix_is_empty():
    assert Config().patient.id_prefix == ""


def _write_gt(path, patient_id, codes):
    """Write a minimal semicolon-format GT workbook mirroring the real one."""
    matches = [{"hpo_term": "t", "hpo_id": c, "patient_verbatim": ""} for c in codes]
    write_excel(patient_id, matches, str(path), fmt="semicolon")


def test_pipeline_output_id_joins_against_ground_truth(tmp_path):
    # Ground truth keys the patient with the MGA. prefix.
    gt_path = tmp_path / "gt.xlsx"
    _write_gt(gt_path, "MGA.467", ["HP:0002315", "HP:0002828"])

    # The transcript file is the bare stem "467"; the pipeline derives the
    # join key by prepending the configured prefix.
    stem = "467"
    patient_id = derive_patient_id(stem, "MGA.")
    pred_path = tmp_path / "pred.xlsx"
    _write_gt(pred_path, patient_id, ["HP:0002315"])

    gt = load_codes_from_excel(str(gt_path))
    pred = load_codes_from_excel(str(pred_path))

    # The join works: both sides share the same patient key.
    assert set(gt) == {"MGA.467"}
    assert set(pred) == {"MGA.467"}
    assert set(gt) & set(pred) == {"MGA.467"}


def test_bare_stem_does_not_join_without_prefix(tmp_path):
    # Reproduces the original bug: deriving the id without the prefix yields
    # "467", which shares no key with the "MGA.467" ground truth.
    gt_path = tmp_path / "gt.xlsx"
    _write_gt(gt_path, "MGA.467", ["HP:0002315"])
    pred_path = tmp_path / "pred.xlsx"
    _write_gt(pred_path, derive_patient_id("467", ""), ["HP:0002315"])

    gt = load_codes_from_excel(str(gt_path))
    pred = load_codes_from_excel(str(pred_path))
    assert set(gt) & set(pred) == set()
