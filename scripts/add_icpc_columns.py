"""Add the ICPC columns (chapter, component, ICPC-3, ICPC-2, source) to an existing results workbook.

For a workbook that the pipeline did not write in one run, for example a merged
workbook with hand codes in it. The script finds the "HPO Code" column by name
in the first sheet (or --sheet) and adds the 3 columns after the last column,
or after the column named with --after. A row with the review colour keeps it
in the new cells, as in a pipeline workbook. dst can be the same file as src.
The ICPC columns of an earlier run are removed first, so a new table
replaces the old values.

Usage:
    uv run python scripts/add_icpc_columns.py data/results_all_2026-10-27.xlsx out.xlsx --after Mentions
"""

import argparse
from collections import Counter
from copy import copy

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from phenoscribe.icpc import ICPC_COLUMNS, icpc_for, table_stamp
from phenoscribe.output import PROVENANCE_SHEET, set_provenance

# The headers of the 3 columns that an earlier version of this script wrote.
OLD_HEADERS = ["ICPC-3", "ICPC-2", "ICPC Source"]


def add_icpc_columns(src: str, dst: str, sheet: str | None = None, after: str | None = None) -> Counter:
    """Write `dst` with the ICPC columns. Returns a count of rows per ICPC source."""
    wb = load_workbook(src)
    ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
    headers = [c.value for c in ws[1]]
    if "HPO Code" not in headers:
        raise SystemExit(f"No 'HPO Code' column in sheet {ws.title!r}")
    old = [i + 1 for i, name in enumerate(headers) if name in ICPC_COLUMNS or name in OLD_HEADERS]
    for col in reversed(old):  # from the right, so the column numbers stay right
        ws.delete_cols(col)
        # delete_cols moves the cells but not the column widths.
        for c in range(col, len(headers)):
            ws.column_dimensions[get_column_letter(c)].width = ws.column_dimensions[get_column_letter(c + 1)].width
        del headers[col - 1]
    if after and after not in headers:
        raise SystemExit(f"No {after!r} column in sheet {ws.title!r}")
    code_col = headers.index("HPO Code") + 1
    first = headers.index(after) + 2 if after else len(headers) + 1
    n = len(ICPC_COLUMNS)
    if first <= len(headers):
        # insert_cols moves the cells but not the column widths.
        widths = {col: ws.column_dimensions[get_column_letter(col)].width for col in range(first, len(headers) + 1)}
        ws.insert_cols(first, n)
        for col, width in widths.items():
            ws.column_dimensions[get_column_letter(col + n)].width = width
    style_col = first - 1  # the column before the new ones gives the cell style
    for i, name in enumerate(ICPC_COLUMNS):
        ws.cell(row=1, column=first + i, value=name)._style = copy(ws.cell(row=1, column=style_col)._style)
        ws.column_dimensions[get_column_letter(first + i)].width = 30 if i < 2 else 13

    counts = Counter()
    for row in range(2, ws.max_row + 1):
        hpo_id = ws.cell(row=row, column=code_col).value
        icpc = icpc_for(str(hpo_id).strip() if hpo_id else None)
        for i, key in enumerate(ICPC_COLUMNS.values()):
            cell = ws.cell(row=row, column=first + i, value=icpc[key] or None)
            cell._style = copy(ws.cell(row=row, column=style_col)._style)
        if hpo_id:
            counts[icpc["source"] or "none"] += 1
            counts["icpc2"] += bool(icpc["icpc2"])

    if PROVENANCE_SHEET in wb.sheetnames:
        set_provenance(wb[PROVENANCE_SHEET], "icpc_table", table_stamp())
    wb.save(dst)
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("src", help="results workbook to read")
    parser.add_argument("dst", help="new workbook to write")
    parser.add_argument("--sheet", help="sheet with the HPO codes (default: the first sheet)")
    parser.add_argument("--after", help="put the columns after this column (default: at the end)")
    args = parser.parse_args()

    counts = add_icpc_columns(args.src, args.dst, args.sheet, args.after)
    coded = sum(counts[s] for s in ("marc", "draft", "none"))
    for source in ("marc", "draft", "none"):
        print(f"{source:6} {counts[source]:5}  {counts[source] / max(coded, 1):.0%}")
    print(f"icpc2  {counts['icpc2']:5}  {counts['icpc2'] / max(coded, 1):.0%} of the rows with an HPO code have an ICPC-2 code")
    print(f"Wrote {args.dst}")
