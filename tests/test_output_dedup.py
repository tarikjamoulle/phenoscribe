"""Dedup-always: one row per (patient_id, HPO code).

A transcript is one document for one patient, so a symptom restated several
times must collapse to a single row. Verbatim handling follows D1 of the
dedup plan: 'detailed' concatenates distinct verbatims, the summary formats
keep the longest. The ancestor closure is built from the deduped present set,
so no duplicate leaf can reach it.
"""

from pathlib import Path

import openpyxl

from phenoscribe.output import CLOSURE_SHEET, dedup_by_code, write_excel

# Episodic ataxia sits deep under Ataxia / Phenotypic abnormality in the real
# release, so its closure has real ancestors to count.
EPISODIC_ATAXIA = "HP:0002131"
ATAXIA = "HP:0001251"
PHENOTYPIC_ABNORMALITY = "HP:0000118"
OBO_PATH = str(Path(__file__).resolve().parent.parent / "data" / "hpo" / "hp.obo")


def _restated_matches():
    """Same code restated three times with different verbatims, plus a distinct code."""
    return [
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": "tired"},
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue",
         "patient_verbatim": "exhausted all the time"},
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": "weary"},
        {"hpo_id": "HP:0002027", "hpo_term": "Abdominal pain", "patient_verbatim": "mal"},
    ]


def _read(path):
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    return headers, rows


def test_dedup_helper_detailed_concatenates_distinct_verbatims():
    out = dedup_by_code(_restated_matches(), fmt="detailed")
    by_code = {m["hpo_id"]: m for m in out}
    assert len(out) == 2
    assert by_code["HP:0012378"]["patient_verbatim"] == "tired; exhausted all the time; weary"


def test_dedup_helper_summary_keeps_longest_verbatim():
    for fmt in ("semicolon", "purl"):
        out = dedup_by_code(_restated_matches(), fmt=fmt)
        by_code = {m["hpo_id"]: m for m in out}
        assert len(out) == 2
        assert by_code["HP:0012378"]["patient_verbatim"] == "exhausted all the time"


def test_dedup_does_not_collapse_present_and_absent_of_same_code():
    matches = [
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "negated": False},
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "negated": True},
    ]
    out = dedup_by_code(matches, fmt="detailed")
    assert len(out) == 2


def test_detailed_format_one_row_per_code_with_concatenated_verbatim(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("MGA.014", _restated_matches(), str(path), fmt="detailed")

    headers, rows = _read(path)
    code_col = headers.index("HPO Code")
    verb_col = headers.index("Patient Verbatim")

    codes = [r[code_col] for r in rows]
    assert codes.count("HP:0012378") == 1
    assert len(rows) == 2

    fatigue = next(r for r in rows if r[code_col] == "HP:0012378")
    assert fatigue[verb_col] == "tired; exhausted all the time; weary"


def test_purl_format_one_row_per_code_keeps_longest_verbatim(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("MGA.014", _restated_matches(), str(path), fmt="purl")

    headers, rows = _read(path)
    purl_col = headers.index("HPO Code Purl")
    verb_col = headers.index("Verbatim")

    fatigue_rows = [r for r in rows if "HP_0012378" in str(r[purl_col])]
    assert len(fatigue_rows) == 1
    assert fatigue_rows[0][verb_col] == "exhausted all the time"


def test_semicolon_format_one_entry_per_code_keeps_longest_verbatim(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("MGA.014", _restated_matches(), str(path), fmt="semicolon")

    _, rows = _read(path)
    observation = rows[0][1]
    assert observation.count("HP:0012378") == 1
    assert "[exhausted all the time]" in observation


def test_closure_is_dedup_free(tmp_path):
    """Duplicate leaves must not propagate into the Ancestor Closure sheet."""
    path = tmp_path / "out.xlsx"
    matches = [
        {"hpo_id": EPISODIC_ATAXIA, "hpo_term": "Episodic ataxia", "patient_verbatim": "a"},
        {"hpo_id": EPISODIC_ATAXIA, "hpo_term": "Episodic ataxia", "patient_verbatim": "b"},
        {"hpo_id": EPISODIC_ATAXIA, "hpo_term": "Episodic ataxia", "patient_verbatim": "c"},
    ]
    write_excel(
        "P1", matches, str(path), fmt="detailed",
        propagate_ancestors=True, obo_path=OBO_PATH,
    )

    wb = openpyxl.load_workbook(path)
    closure = wb[CLOSURE_SHEET]
    codes = [row[2] for row in closure.iter_rows(min_row=2, values_only=True)]
    # The restated leaf appears exactly once; ancestors are unique too.
    assert codes.count(EPISODIC_ATAXIA) == 1
    assert len(codes) == len(set(codes))
    assert ATAXIA in codes
    assert PHENOTYPIC_ABNORMALITY in codes
