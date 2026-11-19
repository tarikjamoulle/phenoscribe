"""Build the word lists of the leak check (src/phenoscribe/resources/).

Source: Statbel, "First names of the total population by municipality", 2026
(TA_POP_MALE_2026.xlsx and TA_POP_FEMALE_2026.xlsx). Licence: CC BY 4.0
(https://statbel.fgov.be/en/cc-40). The same files give the municipality names
in Dutch and French.

Changes to the source: national totals, first names of at least MIN_PEOPLE
people, lower case, sorted. The files are written with a header line that names
the source, the licence and the changes.

    uv run python scripts/build_name_lists.py <folder with the two .xlsx files>
"""

import sys
import unicodedata
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook

OUT = Path(__file__).resolve().parent.parent / "src" / "phenoscribe" / "resources"
FILES = ("TA_POP_MALE_2026.xlsx", "TA_POP_FEMALE_2026.xlsx")
MIN_PEOPLE = 50
SOURCE = ("Statbel, First names of the total population by municipality, 2026 "
          "(TA_POP_MALE_2026.xlsx, TA_POP_FEMALE_2026.xlsx). Licence: CC BY 4.0, "
          "https://statbel.fgov.be/en/cc-40")

# English names of Belgian towns (exonyms). Not in the Statbel files.
ENGLISH_TOWNS = ["antwerp", "brussels", "bruges", "ghent", "louvain", "mechlin", "ostend", "courtrai"]


def norm(word: str) -> str:
    return unicodedata.normalize("NFC", word.strip()).lower()


def main(folder: Path) -> int:
    names: Counter = Counter()
    towns: set[str] = set()
    for name in FILES:
        ws = load_workbook(folder / name, read_only=True).worksheets[0]
        for row in ws.iter_rows(min_row=2, values_only=True):
            _, town_nl, town_fr, first, count = row[:5]
            if first:
                names[norm(str(first))] += int(count or 0)
            for town in (town_nl, town_fr):
                if town:
                    towns |= {norm(t) for t in str(town).split("/")}
    kept = sorted(n for n, c in names.items() if c >= MIN_PEOPLE and len(n) >= 2)
    (OUT / "first_names_be.txt").write_text(
        f"# {SOURCE}. Changed: national totals, names of at least {MIN_PEOPLE} people, "
        "lower case.\n" + "\n".join(kept) + "\n", encoding="utf-8")
    all_towns = sorted(t for t in towns | set(ENGLISH_TOWNS) if t)
    (OUT / "towns_be.txt").write_text(
        f"# {SOURCE}. Changed: the municipality names (Dutch and French) of these files, "
        "lower case, plus the English names of 8 towns.\n" + "\n".join(all_towns) + "\n",
        encoding="utf-8")
    print(f"first names: {len(kept)}, towns: {len(all_towns)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
