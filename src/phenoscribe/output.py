"""Excel output module — dual format support."""

import logging
from pathlib import Path

from openpyxl import Workbook, load_workbook

logger = logging.getLogger(__name__)


def write_excel(
    patient_id: str,
    matches: list[dict],
    output_path: str,
    fmt: str = "semicolon",
) -> None:
    """Write HPO matches to Excel file.

    Args:
        patient_id: Patient identifier (e.g., "MGA.014").
        matches: List of dicts with hpo_id, hpo_term, patient_verbatim.
        output_path: Path to output Excel file.
        fmt: Output format — "semicolon" or "purl".
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        wb = load_workbook(path)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        if fmt == "purl":
            ws.append(["CASE ID", "HPO TERM", "HPO Code Purl", "Verbatim"])
        else:
            ws.append(["Patient_ID", "observation_source_value"])

    if fmt == "purl":
        _write_purl_format(ws, patient_id, matches)
    else:
        _write_semicolon_format(ws, patient_id, matches)

    wb.save(path)
    logger.info("Wrote %d matches for %s to %s (%s format)", len(matches), patient_id, path, fmt)


def _write_semicolon_format(ws, patient_id: str, matches: list[dict]) -> None:
    """One row per patient, all matches semicolon-separated."""
    parts = []
    for m in matches:
        entry = f"{m['hpo_term']} ({m['hpo_id']})"
        if m.get("patient_verbatim"):
            entry += f" [{m['patient_verbatim']}]"
        parts.append(entry)

    observation = "; ".join(parts)
    ws.append([patient_id, observation])


def _write_purl_format(ws, patient_id: str, matches: list[dict]) -> None:
    """One row per HPO term per patient, with PURL-style code links."""
    for m in matches:
        # Convert HP:0002027 -> http://purl.obolibrary.org/obo/HP_0002027
        purl = _hpo_id_to_purl(m["hpo_id"])
        verbatim = m.get("patient_verbatim", "")
        ws.append([patient_id, m["hpo_term"], purl, verbatim])


def _hpo_id_to_purl(hpo_id: str) -> str:
    """Convert HP:0002027 to PURL format."""
    # HP:0002027 -> HP_0002027
    obo_id = hpo_id.replace(":", "_")
    return f"http://purl.obolibrary.org/obo/{obo_id}"
