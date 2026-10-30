"""Excel output module — dual format support."""

import logging
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from phenoscribe.icpc import ICPC_COLUMNS, icpc_for, table_stamp
from phenoscribe.match_hpo import absent_codes, finding_status, present_codes, runs_label

logger = logging.getLogger(__name__)

# Code cell of pre-2026-10 workbooks for a denied finding. Denied findings are
# coded now; the constant stays for scripts that read the older workbooks.
_ABSENT_CODE_PLACEHOLDER = "(absent — not coded)"

HEADERS = {
    "detailed": [
        "Patient_ID",
        "HPO Term",
        "HPO Code",
        "Present/Absent",
        "Frequency",
        "Onset",
        "Severity",
        "Patient Verbatim",
        "Clinical Term",
        "Needs Review",
        "Confidence",
        "First Mention %",
        "Last Mention %",
        "Mentions",
        "Patient",
        "Interview",
        "Date",
        # New columns go at the end: write_excel adds missing headers to an
        # older workbook only after its last column.
        *ICPC_COLUMNS,
        "Source",
        "Spoken by",
        "Runs",
    ],
    "semicolon": ["Patient_ID", "observation_source_value", "excluded_source_value"],
    "purl": [
        "CASE ID",
        "HPO TERM",
        "HPO Code Purl",
        "Verbatim",
        "Clinical Term",
        "Needs Review",
        "Confidence",
    ],
}

# Who brings up a finding, strongest first (extract_symptoms.SOURCES). A
# restated finding keeps the row with the strongest source.
_SOURCE_RANK = {"patient": 0, "confirmed": 1, "": 2, "doctor": 3}


def source_rank(match: dict) -> int:
    return _SOURCE_RANK.get(match.get("source", ""), 2)

# Light amber fill for rows the GP should double-check.
_REVIEW_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")


def _review_label(match: dict) -> str:
    """Human-readable review flag for a match row."""
    return "REVIEW" if match.get("needs_review") else ""


def _confidence_cell(match: dict):
    """Confidence as a 0-1 float, or '' when the match carries none."""
    conf = match.get("confidence")
    return conf if conf is not None else ""


# Name of the worksheet that holds the true-path (ancestor-closed) set.
CLOSURE_SHEET = "Ancestor Closure"
CLOSURE_HEADERS = ["Patient_ID", "HPO Term", "HPO Code", "Origin", "Derived From"]

_HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_WRAP_ALIGNMENT = Alignment(wrap_text=True, vertical="top")
_TOP_ALIGNMENT = Alignment(vertical="top")


PROVENANCE_SHEET = "Provenance"


def dedup_by_code(matches: list[dict], fmt: str = "semicolon") -> list[dict]:
    """Collapse restated findings to one match per (HPO code, negation status).

    Each transcript is one document for one patient, so a symptom restated
    several times must not produce several rows. We key on (hpo_id, negated):
    a code asserted present and the same code asserted absent stay as two rows,
    since collapsing them would hide a contradiction. Uncoded findings key on
    their clinical term instead, so two different unmatched symptoms stay apart.

    Verbatim handling follows the format's intent (D1 in the dedup plan):
      - "detailed": concatenate the distinct verbatims with "; " (evidence view).
      - everything else ("semicolon"/"purl"): keep the single longest verbatim.

    Input order is preserved: the first occurrence of a key fixes a match's
    position and its non-verbatim fields; later occurrences only feed verbatims.
    """
    by_key: dict[tuple, dict] = {}
    order: list[tuple] = []
    # Per key, the verbatims seen, in first-seen order, de-duplicated.
    verbatims: dict[tuple, list[str]] = {}

    for m in matches:
        code = m["hpo_id"] or ("uncoded", (m.get("clinical_term") or "").lower())
        key = (code, bool(m.get("negated", False)))
        if key not in by_key:
            by_key[key] = dict(m)
            order.append(key)
            verbatims[key] = []
        elif source_rank(m) < source_rank(by_key[key]):
            # A stronger source replaces the row: the doctor's words of a
            # doctor-only row must not stand as the patient's words.
            by_key[key] = dict(m)
            verbatims[key] = []
        elif m.get("source") == "doctor" and by_key[key].get("source") != "doctor":
            continue
        v = m.get("patient_verbatim") or ""
        if v and v not in verbatims[key]:
            verbatims[key].append(v)

    result = []
    for key in order:
        out = by_key[key]
        seen = verbatims[key]
        if seen:
            if fmt == "detailed":
                out["patient_verbatim"] = "; ".join(seen)
            else:
                out["patient_verbatim"] = max(seen, key=len)
        result.append(out)
    return result


def write_excel(
    patient_id: str,
    matches: list[dict],
    output_path: str,
    fmt: str = "semicolon",
    hpo_release: str | None = None,
    propagate_ancestors: bool = False,
    obo_path: str = "data/hpo/hp.obo",
    interview: dict | None = None,
) -> None:
    """Write HPO matches to Excel file.

    Args:
        patient_id: Patient identifier (e.g., "MGA.014").
        matches: List of dicts with hpo_id, hpo_term, patient_verbatim.
        output_path: Path to output Excel file.
        fmt: Output format — "detailed", "semicolon", or "purl".
        hpo_release: Pinned HPO release string (e.g. "hp/releases/2026-02-16").
            Stamped into a Provenance sheet so the workbook is self-describing.
        propagate_ancestors: If True, add an "Ancestor Closure" sheet holding
            the true-path expansion (predicted terms plus all is_a ancestors).
            The primary sheet keeps the leaf-level human view unchanged.
        obo_path: HPO OBO release used to build the is_a graph for propagation.
        interview: "patient", "interview" and "date" of the file
            (pipeline.interview_details), for the last 3 columns of the
            detailed format. They link the files of one patient.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        wb = load_workbook(path)
        ws = wb[wb.sheetnames[0]]
        # A workbook of an older version has fewer columns: add the new headers.
        headers = HEADERS.get(fmt, HEADERS["detailed"])
        old = [c.value for c in ws[1]]
        if old and len(old) < len(headers) and old == headers[: len(old)]:
            for col in range(len(old) + 1, len(headers) + 1):
                ws.cell(row=1, column=col, value=headers[col - 1])
            _style_header_row(ws, len(headers))
    else:
        wb = Workbook()
        ws = wb.active
        headers = HEADERS.get(fmt, HEADERS["detailed"])
        ws.append(headers)
        _style_header_row(ws, len(headers))

    # One row per (patient, HPO code) is the default. Restated symptoms must
    # not produce duplicate rows, and must not leak duplicate leaves into the
    # closure either. Dedup once, then both the primary sheet and the closure
    # read from the same collapsed set.
    deduped = dedup_by_code(matches, fmt)

    if fmt == "purl":
        _write_purl_format(ws, patient_id, deduped)
    elif fmt == "semicolon":
        _write_semicolon_format(ws, patient_id, deduped)
    else:
        _write_detailed_format(ws, patient_id, deduped, interview or {})

    _auto_fit_columns(ws)
    if hpo_release:
        _stamp_provenance(wb, hpo_release)

    if propagate_ancestors:
        _write_closure_sheet(wb, patient_id, deduped, obo_path)

    wb.save(path)
    logger.info("Wrote %d matches for %s to %s (%s format)", len(deduped), patient_id, path, fmt)


def _stamp_provenance(wb, hpo_release: str) -> None:
    """Write or refresh a Provenance sheet carrying the pinned HPO release.

    Idempotent: re-running the pipeline against an existing workbook updates the
    release cell rather than appending a second sheet.
    """
    if PROVENANCE_SHEET in wb.sheetnames:
        ws = wb[PROVENANCE_SHEET]
    else:
        ws = wb.create_sheet(PROVENANCE_SHEET)
        ws["A1"] = "Key"
        ws["B1"] = "Value"
        ws["A1"].font = _HEADER_FONT
        ws["B1"].font = _HEADER_FONT
        ws["A1"].fill = _HEADER_FILL
        ws["B1"].fill = _HEADER_FILL
        ws["A2"] = "hpo_release"
        ws.column_dimensions["A"].width = 18
        ws.column_dimensions["B"].width = 32
    ws["B2"] = hpo_release
    set_provenance(ws, "icpc_table", table_stamp())


def set_provenance(ws, key: str, value: str) -> None:
    """Write `value` in the row of `key`, or in a new row at the end."""
    for row in range(2, ws.max_row + 1):
        if ws.cell(row=row, column=1).value == key:
            ws.cell(row=row, column=2, value=value)
            return
    ws.append([key, value])


def _write_closure_sheet(wb, patient_id: str, matches: list[dict], obo_path: str) -> None:
    """Append the true-path (ancestor-closed) set to a dedicated sheet.

    The closure sheet is one row per term per patient: leaves plus the
    is_a ancestors they imply. Downstream phenotype tools consume this set.
    """
    # Lazy import: only pay the graph-build cost when propagation is requested.
    from phenoscribe.ontology import propagate_matches

    if CLOSURE_SHEET in wb.sheetnames:
        ws = wb[CLOSURE_SHEET]
    else:
        ws = wb.create_sheet(CLOSURE_SHEET)
        ws.append(CLOSURE_HEADERS)
        _style_header_row(ws, len(CLOSURE_HEADERS))

    # Only present codes have a true-path closure; denied findings carry no
    # HPO code, so they contribute nothing to the ancestor-closed set.
    closure = propagate_matches(present_codes(matches), obo_path)
    for row in closure:
        derived = ", ".join(row["derived_from"]) if row["derived_from"] else ""
        ws.append([patient_id, row["hpo_term"], row["hpo_id"], row["origin"], derived])
        r = ws.max_row
        for col in range(1, 5):
            ws.cell(row=r, column=col).alignment = _TOP_ALIGNMENT
        ws.cell(row=r, column=5).alignment = _WRAP_ALIGNMENT

    _auto_fit_columns(ws)
    logger.info(
        "Wrote ancestor closure for %s: %d term(s) on sheet '%s'",
        patient_id, len(closure), CLOSURE_SHEET,
    )


def _style_header_row(ws, num_cols: int) -> None:
    """Apply styling to the header row and freeze it."""
    for col in range(1, num_cols + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"


def _auto_fit_columns(ws) -> None:
    """Auto-fit column widths based on content, with a max width cap."""
    max_width = 60
    for col_idx, col_cells in enumerate(ws.iter_cols(min_row=1, max_row=ws.max_row), start=1):
        max_len = 0
        for cell in col_cells:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        width = min(max_len + 4, max_width)
        ws.column_dimensions[get_column_letter(col_idx)].width = max(width, 12)


def _modifier_cell(m: dict, field: str) -> str:
    """Render a modifier as 'Label (HP:xxxx)' when mapped, else the raw text."""
    hpo_id = m.get(f"{field}_hpo_id")
    hpo_term = m.get(f"{field}_hpo_term")
    if hpo_id and hpo_term:
        return f"{hpo_term} ({hpo_id})"
    return m.get(field, "") or ""


def _write_detailed_format(ws, patient_id: str, matches: list[dict], interview: dict) -> None:
    """One row per finding with present/absent and modifier columns.

    Negated findings stay in the sheet marked 'Absent', with their code, so
    the excluded findings are documented. A finding with no defensible match
    has an empty code cell.
    """
    for m in matches:
        status = finding_status(m)
        verbatim = m.get("patient_verbatim", "")
        code_cell = m.get("hpo_id") or ""
        clinical_term = m.get("clinical_term", "")
        icpc = icpc_for(m.get("hpo_id"))
        ws.append(
            [
                patient_id,
                m["hpo_term"],
                code_cell,
                status,
                _modifier_cell(m, "frequency"),
                _modifier_cell(m, "onset"),
                _modifier_cell(m, "severity"),
                verbatim,
                clinical_term,
                _review_label(m),
                _confidence_cell(m),
                m.get("first_position"),
                m.get("last_position"),
                m.get("n_mentions"),
                interview.get("patient", ""),
                interview.get("interview", ""),
                interview.get("date", ""),
                *(icpc[key] for key in ICPC_COLUMNS.values()),
                m.get("source", ""),
                m.get("spoken_by", ""),
                runs_label(m),
            ]
        )
        row = ws.max_row
        n_cols = len(HEADERS["detailed"])
        for col in range(1, n_cols + 1):
            ws.cell(row=row, column=col).alignment = _TOP_ALIGNMENT
        ws.cell(row=row, column=8).alignment = _WRAP_ALIGNMENT
        if m.get("needs_review"):
            for col in range(1, n_cols + 1):
                ws.cell(row=row, column=col).fill = _REVIEW_FILL


def _format_entry(m: dict) -> str:
    """'Name (HP:code) [verbatim]' with modifier annotations appended."""
    entry = f"{m['hpo_term']} ({m['hpo_id']})"
    mods = []
    for field in ("frequency", "onset", "severity"):
        cell = _modifier_cell(m, field)
        if cell:
            mods.append(cell)
    if mods:
        entry += " {" + "; ".join(mods) + "}"
    if m.get("patient_verbatim"):
        entry += f" [{m['patient_verbatim']}]"
    # Mark low-confidence codes inline so they survive the flat cell format.
    if m.get("needs_review"):
        entry += " {REVIEW}"
    return entry


def _uncoded_entry(m: dict) -> str:
    """'clinical term (no HPO match) [verbatim] {REVIEW}' for a finding with no code."""
    entry = f"{m.get('clinical_term') or ''} (no HPO match)"
    if m.get("patient_verbatim"):
        entry += f" [{m['patient_verbatim']}]"
    if m.get("needs_review"):
        entry += " {REVIEW}"
    return entry


def _cell_entry(m: dict) -> str:
    return _format_entry(m) if m.get("hpo_id") else _uncoded_entry(m)


def _write_semicolon_format(ws, patient_id: str, matches: list[dict]) -> None:
    """One row per patient. Present codes and excluded findings in separate columns.

    Present findings go in observation_source_value; denied findings go in
    excluded_source_value, each as 'Name (HP:code) [verbatim]'. A denied code
    never appears in the observation column. A doctor-only finding goes in
    neither column. Findings with no HPO match keep
    their place in their column, marked '(no HPO match)'.
    """
    present = [m for m in matches if finding_status(m) == "Present"]
    absent = absent_codes(matches)

    observation = "; ".join(_cell_entry(m) for m in present)
    excluded = "; ".join(_cell_entry(m) for m in absent)
    ws.append([patient_id, observation, excluded])


def _write_purl_format(ws, patient_id: str, matches: list[dict]) -> None:
    """One row per present HPO term per patient, with PURL-style code links.

    Absent and uncoded findings are dropped here: this format feeds tools that
    expect a list of coded phenotypes the patient has.
    """
    for m in present_codes(matches):
        purl = _hpo_id_to_purl(m["hpo_id"])
        verbatim = m.get("patient_verbatim", "")
        clinical_term = m.get("clinical_term", "")
        ws.append(
            [
                patient_id,
                m["hpo_term"],
                purl,
                verbatim,
                clinical_term,
                _review_label(m),
                _confidence_cell(m),
            ]
        )
        if m.get("needs_review"):
            row = ws.max_row
            for col in range(1, 8):
                ws.cell(row=row, column=col).fill = _REVIEW_FILL


def _hpo_id_to_purl(hpo_id: str) -> str:
    """Convert HP:0002027 to PURL format."""
    obo_id = hpo_id.replace(":", "_")
    return f"http://purl.obolibrary.org/obo/{obo_id}"


MENTIONS_SHEET = "Mentions"
MENTIONS_HEADERS = [
    "Patient_ID", "HPO Code", "HPO Term", "Clinical Term", "Present/Absent",
    "Order", "Mention", "Position %", "Time", "Quote", "Found",
]


def write_mentions(path, patient_id: str, rows: list[dict]) -> None:
    """One row for each mention of a finding, in interview order (timeline.mention_rows).

    "Position %" is the place in the interview (0 = start, 100 = end). "Order"
    is 1 for the first finding to come up. "Time" comes from the [mm:ss]
    markers, when the transcript has them.
    """
    from phenoscribe.timeline import time_label

    path = Path(path)
    wb = load_workbook(path) if path.exists() else Workbook()
    if MENTIONS_SHEET in wb.sheetnames:
        ws = wb[MENTIONS_SHEET]
    else:
        ws = wb.create_sheet(MENTIONS_SHEET) if path.exists() else wb.active
        ws.title = MENTIONS_SHEET
        ws.append(MENTIONS_HEADERS)
        _style_header_row(ws, len(MENTIONS_HEADERS))
    for r in rows:
        ws.append([
            patient_id,
            r["hpo_id"] or "",
            r["hpo_term"] if r["hpo_id"] else "",
            r["clinical_term"],
            r["status"],
            r["order"],
            r["mention"],
            r["position"],
            time_label(r["seconds"]),
            r["quote"],
            r["found"],
        ])
    _auto_fit_columns(ws)
    wb.save(path)


PERSONAL_DETAILS_SHEET = "Personal details"


def write_personal_details(path, reviews) -> None:
    """One row for each patient: how many values of each type were removed.

    No original value goes in this sheet. The sheet is added to the workbook
    at ``path``, or the workbook is made with only this sheet. A patient
    already in the sheet gets its row replaced.
    """
    from phenoscribe.review import TYPE_NAMES, entries, summary

    path = Path(path)
    types = list(TYPE_NAMES)
    headers = (
        ["Patient_ID"]
        + [TYPE_NAMES[t][1].capitalize() for t in types]
        + ["Other", "Types kept", "Put back", "Added", "Approved how", "Approved"]
    )
    if path.exists():
        wb = load_workbook(path)
        ws = wb[PERSONAL_DETAILS_SHEET] if PERSONAL_DETAILS_SHEET in wb.sheetnames else None
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = PERSONAL_DETAILS_SHEET
        ws.append(headers)
        _style_header_row(ws, len(headers))
    if ws is None:
        ws = wb.create_sheet(PERSONAL_DETAILS_SHEET)
        ws.append(headers)
        _style_header_row(ws, len(headers))

    ids = {r.patient_id for r in reviews}
    for row in range(ws.max_row, 1, -1):
        if ws.cell(row, 1).value in ids:
            ws.delete_rows(row)
    for review in reviews:
        counts = summary(review)
        rows = entries(review)
        ws.append(
            [review.patient_id]
            + [counts.get(t, 0) for t in types]
            + [
                sum(n for t, n in counts.items() if t not in TYPE_NAMES),
                ", ".join(review.keep_types),
                sum(1 for e in rows if e.origin == "found" and not e.removed
                    and e.category not in review.keep_types),
                sum(1 for e in rows if e.origin == "added"),
                review.approved_how,
                review.approved_at or "",
            ]
        )
    _auto_fit_columns(ws)
    wb.save(path)


def write_docx(text: str, path) -> None:
    """A plain Word file: one paragraph for each line of ``text``."""
    from docx import Document

    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    doc.save(str(path))
