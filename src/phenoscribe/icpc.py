"""ICPC-3 and ICPC-2 codes for an HPO code.

The table (resources/hpo_icpc.csv) covers the HPO terms that occur most in the
Long COVID interviews: Marc Jamoulle's HPO -> ICPC-3 mapping of the Deer et al.
terms, plus frequent terms we drafted. scripts/build_icpc_table.py writes it.
An HPO code that is not in the table gets empty ICPC cells: we do not guess.

ICPC-3 is a broad classification: one code covers many HPO terms (all mouth
complaints are DS20). So the chapter and the component of the code are the
main result, for a count of the phenotypes by domain of general practice.
The detailed code is provisional.
"""

import csv
import logging
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

logger = logging.getLogger(__name__)

# Column header -> key of icpc_for(), in the order of the Codes sheet.
ICPC_COLUMNS = {
    "ICPC-3 Chapter": "chapter",
    "ICPC-3 Component": "component",
    "ICPC-3": "icpc3",
    "ICPC-2": "icpc2",
    "ICPC Source": "source",
}
_EMPTY = {key: "" for key in ICPC_COLUMNS.values()}

# The ICPC-3 chapters, as the ICPC-3 browser names them (getClasses).
CHAPTERS = {
    "A": "General",
    "B": "Blood, blood-forming organs and immune system",
    "D": "Digestive system",
    "F": "Eye",
    "G": "Genital system",
    "H": "Ear",
    "K": "Circulatory system",
    "L": "Musculoskeletal system",
    "N": "Neurological system",
    "P": "Psychological, mental and neurodevelopmental",
    "R": "Respiratory system",
    "S": "Skin",
    "T": "Endocrine, metabolic and nutritional system",
    "U": "Urinary system",
    "W": "Pregnancy and childbearing",
    "Z": "Social problems",
}
# The second letter of a chapter code. Functioning codes ("2F85") have no
# chapter letter: they are in the chapter "II Functioning".
COMPONENTS = {
    "S": "Symptoms, complaints and abnormal findings",
    "D": "Diagnoses and diseases",
    "C": "Social problems",
}


def chapter_and_component(icpc3: str) -> tuple[str, str]:
    """('T Endocrine, ...', 'Symptoms, ...') for 'TS99'. Empty strings for an empty code."""
    if not icpc3:
        return "", ""
    if icpc3.startswith("2F"):
        return "II Functioning", "Functioning"
    return f"{icpc3[0]} {CHAPTERS[icpc3[0]]}", COMPONENTS[icpc3[1]]


@lru_cache(maxsize=None)
def load_icpc_table(path: str | None = None) -> dict[str, dict]:
    """HPO id -> {"chapter", "component", "icpc3", "icpc2", "source"}. An empty dict if the table is missing."""
    # A Traversable has its own open(): the package can sit in a zip.
    resource = Path(path) if path else files("phenoscribe") / "resources" / "hpo_icpc.csv"
    try:
        with resource.open(newline="", encoding="utf-8") as f:
            table = {}
            for row in csv.DictReader(f):
                chapter, component = chapter_and_component(row["icpc3"])
                table[row["hpo_id"]] = {
                    "chapter": chapter,
                    "component": component,
                    "icpc3": row["icpc3"],
                    "icpc2": row["icpc2"],
                    "source": row["source"],
                }
            return table
    except FileNotFoundError:
        logger.warning("ICPC table %s not found: the ICPC columns stay empty", resource)
        return {}


def icpc_for(hpo_id: str | None, path: str | None = None) -> dict:
    """ICPC codes of one HPO code, or empty strings."""
    if not hpo_id:
        return _EMPTY
    return load_icpc_table(path).get(hpo_id, _EMPTY)


def table_stamp(path: str | None = None) -> str:
    """Short description of the table for the Provenance sheet."""
    table = load_icpc_table(path)
    n_marc = sum(r["source"] == "marc" for r in table.values())
    return f"{len(table)} HPO terms ({n_marc} marc, {len(table) - n_marc} draft)"
