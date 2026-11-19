"""Step 6: the results workbook.

One workbook for each run, in one of three formats (output.format):
"detailed" (one row for each finding), "semicolon" (one row for each
patient) or "purl" (one row for each present code, as a PURL link). Next to
the main sheet: Provenance (HPO release, ICPC table, privacy models),
Ancestor Closure (output.propagate_ancestors), Mentions (timeline.py) and
Personal details (the counts of the removal, no original value).
"""

import logging
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from phenoscribe.config import Config
from phenoscribe.findings import (
    Finding,
    absent_codes,
    finding_status,
    present_codes,
    row_key,
    runs_label,
    source_rank,
)
from phenoscribe.icpc import ICPC_COLUMNS, icpc_for, table_stamp
from phenoscribe.ontology import propagate_matches
from phenoscribe.review import PII_WARNING, TYPE_NAMES, Review, entries, summary
from phenoscribe.timeline import time_label

logger = logging.getLogger(__name__)

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

PROVENANCE_SHEET = "Provenance"

# The true-path (ancestor-closed) set of the present codes.
CLOSURE_SHEET = "Ancestor Closure"
CLOSURE_HEADERS = ["Patient_ID", "HPO Term", "HPO Code", "Origin", "Derived From"]

MENTIONS_SHEET = "Mentions"
MENTIONS_HEADERS = [
    "Patient_ID", "HPO Code", "HPO Term", "Clinical Term", "Present/Absent",
    "Order", "Mention", "Position %", "Time", "Quote", "Found",
]

PERSONAL_DETAILS_SHEET = "Personal details"

_HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_WRAP_ALIGNMENT = Alignment(wrap_text=True, vertical="top")
_TOP_ALIGNMENT = Alignment(vertical="top")
# Light amber fill for rows that a person should check.
_REVIEW_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")

# A text that starts with one of these is a formula for Excel. A patient's
# words come from an LLM, so they are written as plain text.
_FORMULA_START = ("=", "+", "-", "@")


class ResultsWorkbook:
    """The results workbook of one run: made once, saved after each patient, never read again.

    Each run writes a new dated file (batch.new_results_path), so a crash keeps
    the patients that are done. Nothing is on disk before the first patient.
    """

    def __init__(self, path, fmt: str = "detailed", propagate_ancestors: bool = False,
                 obo_path: str = "data/hpo/hp.obo"):
        self.path = Path(path)
        self.fmt = fmt
        self.propagate_ancestors = propagate_ancestors
        self.obo_path = obo_path
        self._wb = Workbook()
        self._main = self._wb.active
        headers = HEADERS.get(fmt, HEADERS["detailed"])
        self._main.append(headers)
        _style_header_row(self._main, len(headers))

    @classmethod
    def for_config(cls, path, config: Config) -> "ResultsWorkbook":
        """A workbook with the format, the closure setting and the ontology of ``config``."""
        return cls(path, config.output.format, config.output.propagate_ancestors, config.paths.hpo_obo)

    def add_patient(
        self,
        patient_id: str,
        findings: list[Finding],
        mentions: list[dict] | None = None,
        interview: dict | None = None,
        hpo_release: str | None = None,
        privacy: dict | None = None,
    ) -> None:
        """The rows of one patient, then save.

        mentions: timeline.mention_rows, for the Mentions sheet. interview:
        "patient", "interview" and "date" of the file (naming.interview_details),
        for the last columns of the detailed format. hpo_release and privacy
        ("language", "models" of the removal) go to the Provenance sheet.
        """
        # Restated symptoms give one row, in the main sheet and in the closure.
        deduped = dedup_by_code(findings, self.fmt)
        if self.fmt == "purl":
            _write_purl_format(self._main, patient_id, deduped)
        elif self.fmt == "semicolon":
            _write_semicolon_format(self._main, patient_id, deduped)
        else:
            _write_detailed_format(self._main, patient_id, deduped, interview or {})
        _auto_fit_columns(self._main)
        if hpo_release or privacy:
            self._stamp_provenance(hpo_release, privacy)
        if self.propagate_ancestors:
            self._add_closure(patient_id, deduped)
        if mentions is not None:
            self._add_mentions(patient_id, mentions)
        self.save()
        logger.info("Wrote %d findings for %s to %s (%s format)", len(deduped), patient_id, self.path, self.fmt)

    def add_personal_details(self, reviews: list[Review]) -> None:
        """The Personal details sheet: the counts of the removal for each patient."""
        _fill_personal_details(self._sheet(PERSONAL_DETAILS_SHEET, _personal_details_headers()), reviews)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._wb.save(self.path)

    def _sheet(self, name: str, headers: list[str] | None = None):
        """The sheet ``name``, made with its header row on first use."""
        if name in self._wb.sheetnames:
            return self._wb[name]
        ws = self._wb.create_sheet(name)
        if headers:
            ws.append(headers)
            _style_header_row(ws, len(headers))
        return ws

    def _stamp_provenance(self, hpo_release: str | None, privacy: dict | None) -> None:
        """The HPO release, the ICPC table, and the privacy models with the warning."""
        if PROVENANCE_SHEET not in self._wb.sheetnames:
            ws = self._wb.create_sheet(PROVENANCE_SHEET)
            ws.append(["Key", "Value"])
            for cell in ws[1]:
                cell.font, cell.fill = _HEADER_FONT, _HEADER_FILL
            ws.column_dimensions["A"].width = 18
            ws.column_dimensions["B"].width = 32
        ws = self._wb[PROVENANCE_SHEET]
        if hpo_release:
            set_provenance(ws, "hpo_release", hpo_release)
        set_provenance(ws, "icpc_table", table_stamp())
        if privacy:
            set_provenance(ws, "pii_language", privacy.get("language", ""))
            set_provenance(ws, "pii_models", ", ".join(privacy.get("models", [])))
            set_provenance(ws, "pii_warning", PII_WARNING)

    def _add_closure(self, patient_id: str, findings: list[Finding]) -> None:
        """The true-path set: one row for each present code and each of its is_a ancestors.

        Only present codes have a closure. Absent and doctor-only findings are
        left out: the patient does not have them.
        """
        ws = self._sheet(CLOSURE_SHEET, CLOSURE_HEADERS)
        closure = propagate_matches(present_codes(findings), self.obo_path)
        for row in closure:
            cells = _append(ws, [patient_id, row["hpo_term"], row["hpo_id"], row["origin"],
                                 ", ".join(row["derived_from"])])
            for cell in cells[:-1]:
                cell.alignment = _TOP_ALIGNMENT
            cells[-1].alignment = _WRAP_ALIGNMENT  # Derived From
        _auto_fit_columns(ws)
        logger.info("Wrote ancestor closure for %s: %d term(s) on sheet '%s'",
                    patient_id, len(closure), CLOSURE_SHEET)

    def _add_mentions(self, patient_id: str, rows: list[dict]) -> None:
        """One row for each mention of a finding, in interview order (timeline.mention_rows).

        "Position %" is the place in the interview (0 = start, 100 = end). "Order"
        is 1 for the first finding to come up. "Time" comes from the [mm:ss]
        markers, when the transcript has them.
        """
        ws = self._sheet(MENTIONS_SHEET, MENTIONS_HEADERS)
        for r in rows:
            _append(ws, [
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


def write_excel(patient_id: str, findings: list[Finding], output_path, fmt: str = "semicolon",
                hpo_release: str | None = None, propagate_ancestors: bool = False,
                obo_path: str = "data/hpo/hp.obo", interview: dict | None = None,
                privacy: dict | None = None) -> None:
    """A new workbook with the rows of one patient (tests and scripts)."""
    book = ResultsWorkbook(output_path, fmt, propagate_ancestors, obo_path)
    book.add_patient(patient_id, findings, interview=interview, hpo_release=hpo_release, privacy=privacy)


def write_personal_details(path, reviews: list[Review]) -> None:
    """A new workbook with only the Personal details sheet (the report of the removal tool)."""
    wb = Workbook()
    ws = wb.active
    ws.title = PERSONAL_DETAILS_SHEET
    headers = _personal_details_headers()
    ws.append(headers)
    _style_header_row(ws, len(headers))
    _fill_personal_details(ws, reviews)
    wb.save(path)


def _personal_details_headers() -> list[str]:
    return (
        ["Patient_ID"]
        + [TYPE_NAMES[t][1].capitalize() for t in TYPE_NAMES]
        + ["Other", "Types kept", "Put back", "Added", "Approved how", "Approved"]
    )


def _fill_personal_details(ws, reviews: list[Review]) -> None:
    """One row for each patient: how many values of each type were removed. No original value."""
    for review in reviews:
        counts = summary(review)
        rows = entries(review)
        _append(ws, [review.patient_id]
                + [counts.get(t, 0) for t in TYPE_NAMES]
                + [
                    sum(n for t, n in counts.items() if t not in TYPE_NAMES),
                    ", ".join(review.keep_types),
                    sum(1 for e in rows if e.origin == "found" and not e.removed
                        and e.category not in review.keep_types),
                    sum(1 for e in rows if e.origin == "added"),
                    review.approved_how,
                    review.approved_at or "",
                ])
    _auto_fit_columns(ws)


def set_provenance(ws, key: str, value: str) -> None:
    """Write `value` in the row of `key`, or in a new row at the end."""
    for row in range(2, ws.max_row + 1):
        if ws.cell(row=row, column=1).value == key:
            ws.cell(row=row, column=2, value=value)
            return
    ws.append([key, value])


def _append(ws, values: list) -> tuple:
    """Append one row and return its cells. A text that Excel reads as a formula stays text."""
    ws.append(values)
    cells = ws[ws.max_row]
    for cell in cells:
        if isinstance(cell.value, str) and cell.value.startswith(_FORMULA_START):
            cell.data_type = "s"
    return cells


def _review_label(match: Finding) -> str:
    """Human-readable review flag for a match row."""
    return "REVIEW" if match.get("needs_review") else ""


def _confidence_cell(match: Finding):
    """Confidence as a 0-1 float, or '' when the match carries none."""
    conf = match.get("confidence")
    return conf if conf is not None else ""


def dedup_by_code(matches: list[Finding], fmt: str = "semicolon") -> list[Finding]:
    """Collapse restated findings to one match per Excel row (findings.row_key).

    Each transcript is one document for one patient, so a symptom restated
    several times must not produce several rows. A code asserted present and
    the same code asserted absent stay as two rows, since collapsing them would
    hide a contradiction. Uncoded findings key on their clinical term, so two
    different unmatched symptoms stay apart.

    Verbatim handling follows the format's intent:
      - "detailed": concatenate the distinct verbatims with "; " (evidence view).
      - everything else ("semicolon"/"purl"): keep the single longest verbatim.

    Input order is preserved: the first occurrence of a key fixes a match's
    position and its non-verbatim fields; later occurrences only feed verbatims.
    """
    by_key: dict[tuple, Finding] = {}
    order: list[tuple] = []
    # Per key, the verbatims seen, in first-seen order, de-duplicated.
    verbatims: dict[tuple, list[str]] = {}

    for m in matches:
        key = row_key(m)
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


def _modifier_cell(m: Finding, field: str) -> str:
    """Render a modifier as 'Label (HP:xxxx)' when mapped, else the raw text."""
    hpo_id = m.get(f"{field}_hpo_id")
    hpo_term = m.get(f"{field}_hpo_term")
    if hpo_id and hpo_term:
        return f"{hpo_term} ({hpo_id})"
    return m.get(field, "") or ""


def _write_detailed_format(ws, patient_id: str, matches: list[Finding], interview: dict) -> None:
    """One row per finding with present/absent and modifier columns.

    Negated findings stay in the sheet marked 'Absent', with their code, so
    the excluded findings are documented. A finding with no defensible match
    has an empty code cell.
    """
    wrap_col = HEADERS["detailed"].index("Patient Verbatim")
    for m in matches:
        icpc = icpc_for(m.get("hpo_id"))
        cells = _append(ws, [
            patient_id,
            m["hpo_term"],
            m.get("hpo_id") or "",
            finding_status(m),
            _modifier_cell(m, "frequency"),
            _modifier_cell(m, "onset"),
            _modifier_cell(m, "severity"),
            m.get("patient_verbatim", ""),
            m.get("clinical_term", ""),
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
        ])
        for cell in cells:
            cell.alignment = _TOP_ALIGNMENT
            if m.get("needs_review"):
                cell.fill = _REVIEW_FILL
        cells[wrap_col].alignment = _WRAP_ALIGNMENT


def _format_entry(m: Finding) -> str:
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


def _uncoded_entry(m: Finding) -> str:
    """'clinical term (no HPO match) [verbatim] {REVIEW}' for a finding with no code."""
    entry = f"{m.get('clinical_term') or ''} (no HPO match)"
    if m.get("patient_verbatim"):
        entry += f" [{m['patient_verbatim']}]"
    if m.get("needs_review"):
        entry += " {REVIEW}"
    return entry


def _cell_entry(m: Finding) -> str:
    return _format_entry(m) if m.get("hpo_id") else _uncoded_entry(m)


def _write_semicolon_format(ws, patient_id: str, matches: list[Finding]) -> None:
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
    _append(ws, [patient_id, observation, excluded])


def _write_purl_format(ws, patient_id: str, matches: list[Finding]) -> None:
    """One row per present HPO term per patient, with PURL-style code links.

    Absent and uncoded findings are dropped here: this format feeds tools that
    expect a list of coded phenotypes the patient has.
    """
    for m in present_codes(matches):
        cells = _append(ws, [
            patient_id,
            m["hpo_term"],
            _hpo_id_to_purl(m["hpo_id"]),
            m.get("patient_verbatim", ""),
            m.get("clinical_term", ""),
            _review_label(m),
            _confidence_cell(m),
        ])
        if m.get("needs_review"):
            for cell in cells:
                cell.fill = _REVIEW_FILL


def _hpo_id_to_purl(hpo_id: str) -> str:
    """Convert HP:0002027 to PURL format."""
    obo_id = hpo_id.replace(":", "_")
    return f"http://purl.obolibrary.org/obo/{obo_id}"


def write_docx(text: str, path) -> None:
    """A plain Word file: one paragraph for each line of ``text``."""
    from docx import Document  # slow to import: only a Word source needs it

    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    doc.save(str(path))
