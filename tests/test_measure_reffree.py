"""Pins two harness-reporting defects in scripts/validation/measure_reffree.py.

(a) A4 judge-hygiene: 'Needs Review' carries string flags ('REVIEW'/'Yes'),
    not python True, and 'Confidence' may be a numeric string. The harness must
    count needs-review by the real flag values and parse numeric-string
    confidences.
(b) The ground-truth parser must accept both 'HP:' and 'HPO:' code prefixes
    (Marc writes some codes as 'HPO:0030051') and normalise to HP:#######.
"""
import importlib.util
from pathlib import Path

import openpyxl

# Load the measurement script as a module by path (it lives outside the package).
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validation" / "measure_reffree.py"
_spec = importlib.util.spec_from_file_location("measure_reffree", _SCRIPT)
mrf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrf)


def _make_results(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet"
    ws.append(
        ["Patient_ID", "HPO Code", "Present/Absent", "Needs Review", "Confidence"]
    )
    # string review flags, not python True; some confidences are numeric strings
    ws.append(["P1", "HP:0012378", "present", "REVIEW", "0.42"])
    ws.append(["P2", "HP:0002027", "present", "Yes", 0.91])
    ws.append(["P3", "HP:0001250", "present", "No", "0.80"])
    ws.append(["P4", "HP:0004322", "absent", "", None])
    wb.save(path)


def _make_gt(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["MGA.100", "Some phenotype HP:0012378"])
    # Marc writes this one with the 'HPO:' prefix
    ws.append(["MGA.460", "Another phenotype HPO:0030051"])
    wb.save(path)


def test_needs_review_counts_string_flags_and_numeric_strings(tmp_path):
    rpath = tmp_path / "results.xlsx"
    _make_results(rpath)
    R = mrf.load_results(str(rpath))

    # 'REVIEW' and 'Yes' are flagged; 'No' and '' are not.
    assert R["needs_review"] == 2

    # Confidence parsed from both float and numeric-string cells (0.42, 0.91, 0.80).
    assert len(R["confidences"]) == 3
    assert all(isinstance(c, float) for c in R["confidences"])
    assert min(R["confidences"]) == 0.42


def test_gt_parser_accepts_hpo_prefix(tmp_path):
    gpath = tmp_path / "gt.xlsx"
    _make_gt(gpath)
    GT = mrf.load_gt(str(gpath))

    # The 'HPO:'-prefixed patient must not be dropped.
    assert "MGA.460" in GT
    # ... and the code is normalised to HP:#######.
    assert GT["MGA.460"] == {"HP:0030051"}
    assert GT["MGA.100"] == {"HP:0012378"}
