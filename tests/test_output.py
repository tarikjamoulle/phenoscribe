"""Tests that needs_review and confidence reach the output workbook.

Robinson's complaint: a code the model could not justify must not look like one
it endorsed. The output has to carry the review flag in every format.
"""

import openpyxl

from phenoscribe.output import write_excel


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


def _read(path):
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    return headers, rows


def test_detailed_format_has_review_and_confidence_columns(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("MGA.014", MATCHES, str(path), fmt="detailed")

    headers, rows = _read(path)
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
    write_excel("MGA.014", MATCHES, str(path), fmt="purl")

    headers, rows = _read(path)
    assert "Needs Review" in headers
    review_col = headers.index("Needs Review")
    assert rows[1][review_col] == "REVIEW"


def test_semicolon_format_marks_review_inline(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("MGA.014", MATCHES, str(path), fmt="semicolon")

    _, rows = _read(path)
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
    write_excel("MGA.014", MATCHES, str(path), fmt="detailed")

    headers, rows = _read(path)
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
    write_excel("MGA.014", MATCHES, str(path), fmt="purl")

    headers, rows = _read(path)
    assert "Clinical Term" in headers
    term_col = headers.index("Clinical Term")
    for row in rows:
        assert row[term_col]


def test_matches_without_flags_still_write(tmp_path):
    """Older match dicts that predate the confidence fields must not crash."""
    path = tmp_path / "out.xlsx"
    legacy = [{"hpo_id": "HP:0012378", "hpo_term": "Fatigue", "patient_verbatim": "x"}]
    write_excel("P1", legacy, str(path), fmt="detailed")

    headers, rows = _read(path)
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
    write_excel("MGA.404", UNCODED, str(path), fmt="detailed")

    headers, rows = _read(path)
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
    write_excel("MGA.404", UNCODED, str(path), fmt="semicolon")

    headers, rows = _read(path)
    observation = rows[0][headers.index("observation_source_value")]
    assert "Fatigue (HP:0012378)" in observation
    assert "difficulty driving (no HPO match)" in observation


def test_purl_format_drops_uncoded(tmp_path):
    path = tmp_path / "out.xlsx"
    write_excel("MGA.404", UNCODED, str(path), fmt="purl")

    _, rows = _read(path)
    assert len(rows) == 1


def test_prevalence_ignores_uncoded(tmp_path):
    from phenoscribe.aggregate import load_patient_codes

    for fmt in ("detailed", "semicolon"):
        path = tmp_path / f"{fmt}.xlsx"
        write_excel("MGA.404", UNCODED, str(path), fmt=fmt)
        codes = load_patient_codes(str(path))
        assert [e["hpo_id"] for e in codes["MGA.404"]] == ["HP:0012378"]
