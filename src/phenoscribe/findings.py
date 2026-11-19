"""One finding: a symptom of the interview, from its extraction to its Excel row.

A finding is a dict that each step fills in. Finding names every key and the
step that sets it. It is a TypedDict: it types the record and keeps the plain
dict that every step, test and JSON file already uses.
"""

from typing import TypedDict

# Who brings up a finding (extract_symptoms): the patient on their own, the
# patient in answer to the doctor, or the doctor only.
SOURCES = ("patient", "confirmed", "doctor")

# The strongest source first. A restated finding keeps the row with the
# strongest source. "" is a finding with no valid source.
_SOURCE_RANK = {"patient": 0, "confirmed": 1, "": 2, "doctor": 3}


class Finding(TypedDict, total=False):
    # extract_symptoms
    patient_verbatim: str  # the patient's words (the doctor's for a doctor-only finding)
    clinical_term: str  # the English medical term
    negated: bool  # True: the patient denies it (status "Absent")
    source: str  # one of SOURCES, or ""
    frequency: str
    onset: str
    severity: str
    context: str  # other detail (time, trigger); match_hpo does not keep it
    mentions: list[str]  # the quotes where the finding comes up
    runs_found: list[int]  # the extraction runs that found it (merge_runs)
    n_runs: int  # how many extraction runs there were
    # match_hpo
    hpo_id: str | None  # None: no candidate was a defensible match
    hpo_term: str  # the official name of hpo_id
    confidence: float | None  # the judge's confidence, 0 to 1
    needs_review: bool  # a person should check the row
    frequency_hpo_id: str  # the HPO codes of the modifiers, when they map
    frequency_hpo_term: str
    onset_hpo_id: str
    onset_hpo_term: str
    severity_hpo_id: str
    severity_hpo_term: str
    # speakers.check_speakers (also changes source, needs_review and patient_verbatim)
    spoken_by: str  # "patient", "patient answer", "doctor" or "not found"
    # timeline.add_timeline
    first_position: float | None  # first mention, % of the interview
    last_position: float | None
    n_mentions: int


def row_key(finding: Finding) -> tuple:
    """The Excel row of a finding: its HPO code (or its uncoded clinical term) and its negation.

    A code that is present and the same code that is absent are two rows, so a
    contradiction stays visible.
    """
    code = finding.get("hpo_id") or ("uncoded", (finding.get("clinical_term") or "").lower())
    return code, bool(finding.get("negated", False))


def source_rank(finding: Finding) -> int:
    """0 for the strongest source (the patient), 3 for the weakest (the doctor only)."""
    return _SOURCE_RANK.get(finding.get("source", ""), 2)


def finding_status(finding: Finding) -> str:
    """The Present/Absent cell: "Absent", "Doctor only" or "Present".

    "Doctor only" is a finding that only the doctor says and the patient does
    not confirm. It is not a finding of the patient, so it is not counted.
    """
    if finding.get("negated", False):
        return "Absent"
    if finding.get("source") == "doctor":
        return "Doctor only"
    return "Present"


def present_codes(findings: list[Finding]) -> list[Finding]:
    """The coded findings that the patient has. Absent and doctor-only findings are left out."""
    return [f for f in findings if finding_status(f) == "Present" and f.get("hpo_id")]


def absent_codes(findings: list[Finding]) -> list[Finding]:
    """The findings that the patient denies."""
    return [f for f in findings if f.get("negated", False)]


def runs_label(finding: Finding) -> str:
    """"2/2": in how many extraction runs the finding came up. "" for one run."""
    n = finding.get("n_runs") or 0
    if n < 2:
        return ""
    return f"{len(finding.get('runs_found') or [])}/{n}"
