"""Excel output module — dual format support."""

import logging
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from phenoscribe.match_hpo import absent_codes, present_codes

logger = logging.getLogger(__name__)

# Shown in code cells for an absent finding, which carries no present HPO code.
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
        "Needs Review",
        "Confidence",
    ],
    "semicolon": ["Patient_ID", "observation_source_value", "excluded_source_value"],
    "purl": ["CASE ID", "HPO TERM", "HPO Code Purl", "Verbatim", "Needs Review", "Confidence"],
}

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
    since collapsing them would hide a contradiction.

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
        key = (m["hpo_id"], bool(m.get("negated", False)))
        if key not in by_key:
            by_key[key] = dict(m)
            order.append(key)
            verbatims[key] = []
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
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        wb = load_workbook(path)
        ws = wb[wb.sheetnames[0]]
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
        _write_detailed_format(ws, patient_id, deduped)

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


def _write_detailed_format(ws, patient_id: str, matches: list[dict]) -> None:
    """One row per finding with present/absent and modifier columns.

    Negated findings stay in the sheet marked 'Absent' so the excluded
    findings are documented, not silently dropped.
    """
    for m in matches:
        absent = m.get("negated", False)
        status = "Absent" if absent else "Present"
        verbatim = m.get("patient_verbatim", "")
        # An absent finding has no present HPO code; show the placeholder so the
        # cell never reads as a code the patient was assigned.
        code_cell = m.get("hpo_id") or (_ABSENT_CODE_PLACEHOLDER if absent else m.get("hpo_id"))
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
                _review_label(m),
                _confidence_cell(m),
            ]
        )
        row = ws.max_row
        for col in range(1, 11):
            ws.cell(row=row, column=col).alignment = _TOP_ALIGNMENT
        ws.cell(row=row, column=8).alignment = _WRAP_ALIGNMENT
        if m.get("needs_review"):
            for col in range(1, 11):
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


def _absent_entry(m: dict) -> str:
    """Render an excluded finding by term/verbatim, with no present HPO code."""
    label = m.get("hpo_term") or m.get("clinical_term") or ""
    if m.get("patient_verbatim"):
        label += f" [{m['patient_verbatim']}]"
    return label


def _write_semicolon_format(ws, patient_id: str, matches: list[dict]) -> None:
    """One row per patient. Present codes and excluded findings in separate columns.

    Present codes go in observation_source_value; denied findings go in
    excluded_source_value as plain term + verbatim, carrying no HPO code so a
    denied symptom never appears with a minted present code.
    """
    present = present_codes(matches)
    absent = absent_codes(matches)

    observation = "; ".join(_format_entry(m) for m in present)
    excluded = "; ".join(_absent_entry(m) for m in absent)
    ws.append([patient_id, observation, excluded])


def _write_purl_format(ws, patient_id: str, matches: list[dict]) -> None:
    """One row per present HPO term per patient, with PURL-style code links.

    Absent findings are dropped here: this format feeds tools that expect a
    list of phenotypes the patient has.
    """
    for m in matches:
        if m.get("negated", False):
            continue
        purl = _hpo_id_to_purl(m["hpo_id"])
        verbatim = m.get("patient_verbatim", "")
        ws.append(
            [patient_id, m["hpo_term"], purl, verbatim, _review_label(m), _confidence_cell(m)]
        )
        if m.get("needs_review"):
            row = ws.max_row
            for col in range(1, 7):
                ws.cell(row=row, column=col).fill = _REVIEW_FILL


def _hpo_id_to_purl(hpo_id: str) -> str:
    """Convert HP:0002027 to PURL format."""
    obo_id = hpo_id.replace(":", "_")
    return f"http://purl.obolibrary.org/obo/{obo_id}"
