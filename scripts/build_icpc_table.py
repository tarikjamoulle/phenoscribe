"""Build the HPO -> ICPC-3 / ICPC-2 table that the Codes sheet reads.

Inputs:
  * Marc Jamoulle's file "Covid_ontology- ICPC3 study (Deer et all. 2021).xlsx",
    sheet Full_en: HPO id in column C, ICPC-3 code in column D.
  * context/icpc/icpc_drafts.csv: our rows. A row replaces Marc's row with the
    same HPO id. It adds a frequent HPO term that is not in Marc's file, or
    picks the ICPC-2 code when the ICPC-3 code converts to more than one.
    A row with an empty icpc3 removes the HPO term from the table.
  * data/icpc/icpc3_browser.json: the ICPC-2 code(s) of each ICPC-3 code,
    from the ICPC-3 browser (scripts/fetch_icpc3_browser.py fills it).

Output: src/phenoscribe/resources/hpo_icpc.csv with the columns
hpo_id, hpo_term, icpc3, icpc2, source. source is "marc" when the ICPC-3 code is
Marc's and the ICPC-2 code is the single official conversion; else "draft".

Usage:
    uv run python scripts/build_icpc_table.py "data/Covid_ontology- ICPC3 study (Deer et all. 2021).xlsx"
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import openpyxl

from phenoscribe.config import load_config
from phenoscribe.hpo_index import build_obsolete_map, parse_obo

DRAFTS_PATH = Path("context/icpc/icpc_drafts.csv")
BROWSER_CACHE = Path("data/icpc/icpc3_browser.json")
OUT_PATH = Path("src/phenoscribe/resources/hpo_icpc.csv")

# Codes in Marc's 2021 file that the 2025 ICPC-3 browser does not have.
# The lab, lung function and imaging codes are a chapter letter + a process
# number ("R105"); Excel turned some into numbers ("-105.0"). They go to the
# "other abnormal findings" code of their chapter, so the chapter count stays
# right. A code with no chapter letter needs a row in the drafts file with
# the chapter of its body system; AS99 is only the fallback. All rows with a
# fix become "draft".
CODE_FIXES = {
    "-105": "AS99",
    "-110": "AS99",
    "-113": "AS99",
    "B105": "BS99",
    "K105": "KS99",
    "K113": "KS99",
    "R105": "RS99",
    "R110": "RS99",
    "R113": "RS99",
    "A03": "AS03",  # the ICPC-2 code for fever
    "A2F85": "2F85",
    "NS12": "NS08",
    "NS13": "NS08",
    "KD": "KD01",  # myocarditis
    "ZC5": "ZC16",  # occupational disability -> work problem
}

ICPC2_PATTERN = re.compile(r"^[A-Z]\d{2}$")


def _clean_code(value) -> str:
    """'-105.0' -> '-105', ' AS04 ' -> 'AS04'."""
    code = str(value).strip()
    return re.sub(r"^(-\d+)\.0$", r"\1", code)


def read_marc(xlsx_path: str) -> dict[str, str]:
    """HPO id -> ICPC-3 code from the Full_en sheet."""
    ws = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)["Full_en"]
    rows = {}
    for row in ws.iter_rows(values_only=True):
        hpo_id = re.sub(r"\s+", "", str(row[2] or ""))  # some cells read "HP: 0003493"
        if hpo_id.startswith("HP:") and row[3] is not None:
            rows[hpo_id] = _clean_code(row[3])
    return rows


def read_drafts(path: Path = DRAFTS_PATH) -> dict[str, dict]:
    with path.open(newline="") as f:
        return {r["hpo_id"].strip(): r for r in csv.DictReader(f)}


def build(marc: dict[str, str], drafts: dict[str, dict], browser: dict, obo_path: str) -> list[dict]:
    names = {t["id"]: t["name"] for t in parse_obo(obo_path)}
    obsolete = build_obsolete_map(obo_path)
    errors = []

    entries = {}  # hpo_id -> (icpc3, chosen icpc2 or "", from_marc_unchanged)
    for hpo_id, code in marc.items():
        fixed = CODE_FIXES.get(code, code)
        entries[hpo_id] = (fixed, "", fixed == code)
    for hpo_id, row in drafts.items():
        entries[hpo_id] = (row["icpc3"].strip(), row["icpc2"].strip(), False)

    table = []
    for hpo_id, (icpc3, icpc2, unchanged) in entries.items():
        if not icpc3:
            continue
        if hpo_id not in names:
            if hpo_id in obsolete:
                if obsolete[hpo_id] in entries:
                    continue  # the active id has its own row
                print(f"{hpo_id} is obsolete, using {obsolete[hpo_id]}", file=sys.stderr)
                hpo_id = obsolete[hpo_id]
            else:
                errors.append(f"{hpo_id}: not an active HPO id")
                continue
        info = browser.get(icpc3)
        if not info or not info.get("found"):
            errors.append(f"{hpo_id}: ICPC-3 {icpc3} not in the browser cache")
            continue
        official = info["icpc2"]
        if not icpc2:
            if len(official) > 1:
                errors.append(f"{hpo_id}: {icpc3} converts to {official}; choose one in {DRAFTS_PATH}")
                continue
            icpc2 = official[0] if official else ""
        if icpc2 and not ICPC2_PATTERN.match(icpc2):
            errors.append(f"{hpo_id}: bad ICPC-2 code {icpc2!r}")
            continue
        if icpc2 and icpc2 not in official:
            errors.append(f"{hpo_id}: {icpc2} is not a conversion of {icpc3} ({official})")
            continue
        source = "marc" if unchanged and len(official) <= 1 else "draft"
        table.append(
            {"hpo_id": hpo_id, "hpo_term": names[hpo_id], "icpc3": icpc3, "icpc2": icpc2, "source": source}
        )

    seen = set()
    for row in table:
        if row["hpo_id"] in seen:
            errors.append(f"{row['hpo_id']}: occurs twice")
        seen.add(row["hpo_id"])
    if errors:
        raise SystemExit("ICPC table not written:\n  " + "\n  ".join(errors))
    return sorted(table, key=lambda r: r["hpo_id"])


def write_table(table: list[dict], path: Path = OUT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["hpo_id", "hpo_term", "icpc3", "icpc2", "source"])
        writer.writeheader()
        writer.writerows(table)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("marc_xlsx", help="Marc's HPO -> ICPC-3 workbook")
    args = parser.parse_args()

    config = load_config()
    browser = json.loads(BROWSER_CACHE.read_text())
    table = build(read_marc(args.marc_xlsx), read_drafts(), browser, config.paths.hpo_obo)
    write_table(table)
    n_draft = sum(r["source"] == "draft" for r in table)
    print(f"Wrote {len(table)} rows to {OUT_PATH} ({len(table) - n_draft} marc, {n_draft} draft).")
