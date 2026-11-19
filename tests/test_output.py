"""The results workbook (output.py): the columns of each format, the review flag, and the
Provenance sheet.

A code the model could not justify must not look like one it endorsed, so the
output carries the review flag in every format.
"""

import openpyxl
from conftest import finding, read_sheet

from phenoscribe.aggregate import load_patient_codes
from phenoscribe.hpo_index import DEFAULT_HPO_RELEASE
from phenoscribe.output import write_excel
from phenoscribe.review import PII_WARNING

MATCHES = [
    {
        "hpo_id": "HP:0012378",
        "hpo_term": "Fatigue",
        "patient_verbatim": "fatigué",
        "clinical_term": "fatigue",
        "confidence": 0.92,
        "needs_review": False,
    },
    {
        "hpo_id": "HP:0002027",
        "hpo_term": "Abdominal pain",
        "patient_verbatim": "mal au ventre",
        "clinical_term": "abdominal pain",
        "confidence": 0.0,
        "needs_review": True,
    },
]


def test_detailed_format_has_review_and_confidence_columns(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("P.003", MATCHES, str(path), fmt="detailed")

    headers, rows = read_sheet(path)
    assert "Needs Review" in headers
    assert "Confidence" in headers

    review_col = headers.index("Needs Review")
    conf_col = headers.index("Confidence")

    # First row endorsed, second flagged.
    assert rows[0][review_col] in ("", None)
    assert rows[0][conf_col] == 0.92
    assert rows[1][review_col] == "REVIEW"


def test_purl_format_has_review_column(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("P.003", MATCHES, str(path), fmt="purl")

    headers, rows = read_sheet(path)
    assert "Needs Review" in headers
    review_col = headers.index("Needs Review")
    assert rows[1][review_col] == "REVIEW"


def test_semicolon_format_marks_review_inline(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("P.003", MATCHES, str(path), fmt="semicolon")

    _, rows = read_sheet(path)
    observation = rows[0][1]
    # The endorsed code has no marker; the flagged one does.
    assert "Fatigue (HP:0012378)" in observation
    assert "{REVIEW}" in observation
    # Marker attaches to the flagged code, not the endorsed one.
    assert "Fatigue (HP:0012378) [fatigué] {REVIEW}" not in observation
    assert "{REVIEW}" in observation.split("Abdominal pain")[1]


def test_detailed_format_persists_clinical_term(tmp_path):
    """The English clinical_term must reach the workbook as its own column.

    Without it, French-path retrieval recall@5 is unmeasurable after the fact:
    the row only carries the French verbatim and the chosen HPO code.
    """
    path = tmp_path / "out.xlsx"
    write_excel("P.003", MATCHES, str(path), fmt="detailed")

    headers, rows = read_sheet(path)
    assert "Clinical Term" in headers
    term_col = headers.index("Clinical Term")
    verbatim_col = headers.index("Patient Verbatim")

    # Every present row carries a populated clinical_term.
    assert rows[0][term_col] == "fatigue"
    assert rows[1][term_col] == "abdominal pain"
    # It sits alongside the patient verbatim, distinct from it.
    assert rows[0][verbatim_col] == "fatigué"


def test_purl_format_persists_clinical_term(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("P.003", MATCHES, str(path), fmt="purl")

    headers, rows = read_sheet(path)
    assert "Clinical Term" in headers
    term_col = headers.index("Clinical Term")
    for row in rows:
        assert row[term_col]


def test_matches_without_flags_still_write(tmp_path):
    """Older match dicts that predate the confidence fields must not crash."""
    path = tmp_path / "out.xlsx"
    legacy = [{"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": "x"}]
    write_excel("P1", legacy, str(path), fmt="detailed")

    headers, rows = read_sheet(path)
    review_col = headers.index("Needs Review")
    conf_col = headers.index("Confidence")
    assert rows[0][review_col] in ("", None)
    assert rows[0][conf_col] in ("", None)


UNCODED = [
    {
        "hpo_id": "HP:0012378",
        "hpo_term": "Fatigue",
        "patient_verbatim": "fatigué",
        "clinical_term": "fatigue",
        "confidence": 0.92,
        "needs_review": False,
    },
    {
        "hpo_id": None,
        "hpo_term": "(no good match)",
        "patient_verbatim": "comme après mon accident",
        "clinical_term": "post-concussion syndrome",
        "confidence": 0.0,
        "needs_review": True,
    },
    {
        "hpo_id": None,
        "hpo_term": "(no good match)",
        "patient_verbatim": "me déplacer en voiture",
        "clinical_term": "difficulty driving",
        "confidence": 0.0,
        "needs_review": True,
    },
]


def test_detailed_format_leaves_uncoded_code_cell_empty(tmp_path):
    """A finding with no defensible match keeps its row, flagged, with no code."""
    path = tmp_path / "out.xlsx"
    write_excel("P.004", UNCODED, str(path), fmt="detailed")

    headers, rows = read_sheet(path)
    code_col = headers.index("HPO Code")
    term_col = headers.index("HPO Term")
    # Two different uncoded symptoms stay two rows after dedup.
    assert len(rows) == 3
    for row in rows[1:]:
        assert row[code_col] is None
        assert row[term_col] == "(no good match)"
        assert row[headers.index("Needs Review")] == "REVIEW"


def test_semicolon_format_marks_uncoded_inline(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("P.004", UNCODED, str(path), fmt="semicolon")

    headers, rows = read_sheet(path)
    observation = rows[0][headers.index("observation_source_value")]
    assert "Fatigue (HP:0012378)" in observation
    assert "difficulty driving (no HPO match)" in observation


def test_purl_format_drops_uncoded(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("P.004", UNCODED, str(path), fmt="purl")

    _, rows = read_sheet(path)
    assert len(rows) == 1


def test_prevalence_ignores_uncoded(tmp_path):
    for fmt in ("detailed", "semicolon"):
        path = tmp_path / f"{fmt}.xlsx"
        write_excel("P.004", UNCODED, str(path), fmt=fmt)
        codes = load_patient_codes(str(path))
        assert [e["hpo_id"] for e in codes["P.004"]] == ["HP:0012378"]


def test_a_text_that_looks_like_a_formula_stays_text(tmp_path):
    path = tmp_path / "out.xlsx"
    words = ["=HYPERLINK(\"http://x\")", "+32 fatigue", "-mal", "@moi"]
    write_excel("P.001", [{"hpo_id": None, "hpo_term": "(no good match)", "clinical_term": w,
                           "patient_verbatim": w} for w in words], str(path), fmt="detailed")
    ws = openpyxl.load_workbook(path).active
    col = [c.value for c in ws[1]].index("Patient Verbatim")
    cells = [row[col] for row in ws.iter_rows(min_row=2)]
    assert [c.value for c in cells] == words
    assert all(c.data_type == "s" for c in cells)


def test_the_detailed_sheet_has_the_spoken_by_and_runs_columns(tmp_path):
    match = finding("Fatigue", code="HP:0012378", source="patient", verbatim="fatigué",
                    spoken_by="patient", runs_found=[1, 2], n_runs=2)
    path = tmp_path / "r.xlsx"
    write_excel("P.001", [match], str(path), fmt="detailed")
    header, rows = read_sheet(path)
    row = dict(zip(header, rows[0], strict=True))
    assert (row["Source"], row["Spoken by"], row["Runs"]) == ("patient", "patient", "2/2")


def test_the_detailed_sheet_has_patient_interview_and_date(tmp_path):
    path = tmp_path / "r.xlsx"
    matches = [{"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": "épuisée"}]
    write_excel("pt-1a2b3c4d", matches, str(path), fmt="detailed",
                interview={"patient": "P.006", "interview": "follow-up", "date": "2025-03-11"})
    header, rows = read_sheet(path)
    row = dict(zip(header, rows[0], strict=True))
    assert (row["Patient_ID"], row["Patient"], row["Interview"], row["Date"]) == (
        "pt-1a2b3c4d", "P.006", "follow-up", "2025-03-11")


# --- the Provenance sheet ----------------------------------------------------------------

FATIGUE = [{"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": "v"}]


def _provenance(path) -> dict:
    return dict(openpyxl.load_workbook(path)["Provenance"].iter_rows(values_only=True))


def test_the_workbook_names_the_hpo_release(tmp_path):
    out = tmp_path / "results.xlsx"
    write_excel("PAT.001", FATIGUE, str(out), fmt="detailed", hpo_release=DEFAULT_HPO_RELEASE)
    assert _provenance(out)["hpo_release"] == DEFAULT_HPO_RELEASE
    # The data sheet stays the first sheet, so loaders find the codes.
    assert openpyxl.load_workbook(out).sheetnames[0] != "Provenance"


def test_a_second_patient_keeps_one_provenance_sheet_and_row(tmp_path):
    out = tmp_path / "results.xlsx"
    for patient in ("PAT.001", "PAT.002"):
        write_excel(patient, FATIGUE, str(out), fmt="detailed", hpo_release=DEFAULT_HPO_RELEASE)
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames.count("Provenance") == 1
    keys = [row[0] for row in wb["Provenance"].iter_rows(values_only=True)]
    assert keys.count("hpo_release") == 1


def test_provenance_names_the_privacy_models_and_the_warning(tmp_path):
    path = tmp_path / "out.xlsx"
    models = ["Anonym-IA/V2-camembert-ner-pii-french", "urchade/gliner_multi_pii-v1"]
    for patient in ("P.001", "P.002"):
        write_excel(patient, [], str(path), hpo_release=DEFAULT_HPO_RELEASE,
                    privacy={"language": "fr", "models": models})
    rows = _provenance(path)
    assert rows["pii_language"] == "fr"
    assert rows["pii_models"] == ", ".join(models)
    assert rows["pii_warning"] == PII_WARNING
