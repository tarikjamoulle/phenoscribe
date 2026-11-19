"""Dedup-always: one row per (patient_id, HPO code).

A transcript is one document for one patient, so a symptom restated several
times must collapse to a single row. 'detailed' concatenates distinct
verbatims; the summary formats keep the longest. The ancestor closure is built from the deduped present set,
so no duplicate leaf can reach it.
"""

from conftest import read_sheet

from phenoscribe.ontology import HPO_ROOT, PHENOTYPIC_ABNORMALITY
from phenoscribe.output import CLOSURE_SHEET, dedup_by_code, write_excel


def _restated_matches():
    """Same code restated three times with different verbatims, plus a distinct code."""
    return [
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": "tired"},
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue",
         "patient_verbatim": "exhausted all the time"},
        {"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": "weary"},
        {"hpo_id": "HP:0002027", "hpo_term": "Abdominal pain", "patient_verbatim": "mal"},
    ]


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
    write_excel("P.003", _restated_matches(), str(path), fmt="detailed")

    headers, rows = read_sheet(path)
    code_col = headers.index("HPO Code")
    verb_col = headers.index("Patient Verbatim")

    codes = [r[code_col] for r in rows]
    assert codes.count("HP:0012378") == 1
    assert len(rows) == 2

    fatigue = next(r for r in rows if r[code_col] == "HP:0012378")
    assert fatigue[verb_col] == "tired; exhausted all the time; weary"


def test_purl_format_one_row_per_code_keeps_longest_verbatim(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("P.003", _restated_matches(), str(path), fmt="purl")

    headers, rows = read_sheet(path)
    purl_col = headers.index("HPO Code Purl")
    verb_col = headers.index("Verbatim")

    fatigue_rows = [r for r in rows if "HP_0012378" in str(r[purl_col])]
    assert len(fatigue_rows) == 1
    assert fatigue_rows[0][verb_col] == "exhausted all the time"


def test_semicolon_format_one_entry_per_code_keeps_longest_verbatim(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("P.003", _restated_matches(), str(path), fmt="semicolon")

    _, rows = read_sheet(path)
    observation = rows[0][1]
    assert observation.count("HP:0012378") == 1
    assert "[exhausted all the time]" in observation


def test_a_restated_leaf_appears_once_in_the_closure(tmp_path, mini_hpo_obo):
    path = tmp_path / "out.xlsx"
    matches = [{"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": v} for v in "abc"]
    write_excel("P1", matches, str(path), fmt="detailed", propagate_ancestors=True, obo_path=mini_hpo_obo)

    header, rows = read_sheet(path, CLOSURE_SHEET)
    codes = [row[header.index("HPO Code")] for row in rows]
    assert sorted(codes) == sorted(["HP:0012378", PHENOTYPIC_ABNORMALITY, HPO_ROOT])
