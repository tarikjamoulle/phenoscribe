"""The review of personal details, before any text leaves the computer.

The detector (pii.detect_personal_details) finds the personal details. A person
then checks them: untick a wrong removal, add a missed name, keep a type such
as age. Only an approved review gives the clean text that the HPO coding reads.

The functions read a Review and change nothing, except three groups: the
edits (set_removed, add_value, set_keep_types, approve, reopen_if_blocked)
change the review in place and return it, and load_review and save_review
read and write the file. A Review holds the raw text with the real names, so
it stays in <results>/review/ on this computer and is never offered as a
download.
"""

import json
import os
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import cache
from importlib.resources import files
from pathlib import Path

from phenoscribe.pii import Span
from phenoscribe.text import TITLE_WORDS, fold

NEEDS_REVIEW = "needs review"
APPROVED = "approved"
DONE = "done"

# The warning to the user: on the page above the review step, in the docs and
# in the Provenance sheet of each workbook.
PII_WARNING = (
    "The removal of personal details is automatic and can miss a name. Phenoscribe "
    "shows you the files that it doubts. Open a file in the review if you want to check it."
)

# How a review was approved, for the report.
CHECKED = "checked"  # a person looked at it and clicked Approve
AUTO = "auto: no warning"  # the page approved it: no warning sign
BULK = "approved in bulk"  # "Approve all the rest", warnings not checked
NO_REVIEW = "no review (CLI)"  # CLI --no-review or phenoscribe clean

# A model detection under this score is a warning sign.
LOW_SCORE = 0.8

# Types the user can choose to keep in the text. Every other type (names,
# places, phones, IDs, ...) identifies a person directly and is always removed.
OPTIONAL_TYPES = {"DATE": "Dates", "AGE": "Ages", "GENDER": "Gender", "JOB": "Job titles"}

TYPE_NAMES = {
    "PERSON": ("name", "names"),
    "LOCATION": ("place", "places"),
    "ORGANIZATION": ("organization", "organizations"),
    "PHONE": ("phone", "phones"),
    "EMAIL": ("email", "emails"),
    "ID": ("ID number", "ID numbers"),
    "URL": ("web address", "web addresses"),
    "DATE": ("date", "dates"),
    "AGE": ("age", "ages"),
    "GENDER": ("gender", "gender"),
    "JOB": ("job title", "job titles"),
}

# A word after one of these titles is most likely a name. A full title takes
# no dot ("docteur. Oui" ends a sentence); a short one may ("M. Dupont").
# An apostrophe before the title is an elision or a contraction ("I'm Dr Maes"):
# that "m" is no title.
_TITLE_NAME = re.compile(
    r"(?i:(?<!['’])\b(?:(?:docteur|professeur|madame|mademoiselle|monsieur"
    r"|dokter|professor|mevrouw|meneer|juffrouw|doctor|mister|miss)"
    r"|(?:dr|pr|prof|mme|mlle|mevr|mr|mrs|ms|m)\.?))\s+"
    r"([A-ZÀ-ÖØ-Þ][\w'’-]+)"
)


# The kinds of warning sign. The first two say that a detail can still be in
# the clean text: a file with one of them stops in batch mode (--no-review) and
# never goes to a cloud provider without a person's check (pipeline.code_review).
POSSIBLY_MISSED = "Possibly missed"  # a word after a title
POSSIBLY_LEFT = "Possibly left"  # a first name or a town of the word lists
UNSURE_ABOUT = "Unsure about"  # a removed value with a low score
NOTHING_FOUND = "No personal detail found"
BLOCKING_SIGNS = (POSSIBLY_MISSED, POSSIBLY_LEFT)

# A capitalized word (or hyphenated name) of 3 letters or more.
_CAPITALIZED = re.compile(r"(?<![\w'’-])([A-ZÀ-ÖØ-Þ][a-zß-öø-ÿ]+(?:[-'’][A-ZÀ-ÖØ-Þ]?[a-zß-öø-ÿ]+)*)(?![\w'’])")
# A word right after one of these starts a sentence or a turn, so its capital
# letter says nothing: ". ", "? ", "! ", ": " (speaker label), a new line, "[00:12]".
_SENTENCE_END = re.compile(r"(?:^|[.?!…:;\n\]\)\"«“]|—)\s*$")
_PSEUDONYM_BEFORE = re.compile(r"\b(?:PERSON|LOCATION)_\d+\s+$")
_PARTICLE_BEFORE = re.compile(
    r"(?<![\w'’])(?:(?:Van|Vande|Vanden|Vander|Van de|Van den|Van der|De|Den|Le|La|Du|Di|Da|Della|Von)"
    r"|(?:van|vande|vanden|vander|van de|van den|van der|ver|ten|ter))\s+$"  # Dutch particles in lower case too
)
# A first name of the list directly followed by a capitalized word ("Claire Dubois")
# is a full name, also at the start of a sentence or a turn.
_FULL_NAME_AFTER = re.compile(r"^[ \t]+[A-ZÀ-ÖØ-Þ][a-zß-öø-ÿ]{2,}")
# An eponym of a disease ("maladie de Crohn", "ziekte van Lyme", "Addison's
# disease") names the disease, not a person.
_DISEASE_BEFORE = re.compile(
    r"(?i:\b(?:maladie|syndrome|signe|ziekte|syndroom|teken|disease|syndrome|sign|morbus)\s+(?:de\s+|d['’]|van\s+|of\s+)?(?:la\s+|le\s+)?)$"
)
_DISEASE_AFTER = re.compile(r"^['’]s?\s+(?i:disease|syndrome|sign|ziekte|syndroom)")
# Capitalized words that are on a list and are no name in a text: months and
# days, holidays, a few common words (fr, nl, en). Folded like the text.
_NOT_NAMES_FOLDED = frozenset(map(fold, ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "christmas", "easter", "god", "jesus", "jésus", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche", "noël", "pâques", "toussaint", "dieu", "januari", "februari", "maart", "mei", "juni", "juli", "augustus", "oktober", "maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag", "kerstmis", "pasen", "kerst", "van", "elle", "mon", "rien", "can", "ils", "hope", "faith", "joy"]))


@cache
def _word_list(name: str) -> frozenset[str]:
    """A list of resources/, folded like the text (fold): no accents, a hyphen is a space.

    One word a line; "#" lines are the header.
    """
    text = (files("phenoscribe") / "resources" / name).read_text(encoding="utf-8")
    return frozenset(fold(line.strip()) for line in text.splitlines()
                     if line.strip() and not line.startswith("#"))


def first_names() -> frozenset[str]:
    """Belgian first names (Statbel 2026, CC BY 4.0), see the header of the file."""
    return _word_list("first_names_be.txt")


def towns() -> frozenset[str]:
    """Belgian municipalities in Dutch and French (Statbel 2026, CC BY 4.0)."""
    return _word_list("towns_be.txt")


class ReviewError(Exception):
    """A review file that cannot be read, or a step that needs an approved review."""


@dataclass
class Review:
    source: str
    patient_id: str
    raw_text: str
    spans: list[dict]
    keep_types: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # found values the user put back
    added: list[dict] = field(default_factory=list)  # {"value", "category"}
    status: str = NEEDS_REVIEW
    approved_at: str | None = None
    approved_how: str = ""  # CHECKED, AUTO, BULK or NO_REVIEW
    # The language of the removal and the models that ran, for the Provenance
    # sheet. A review file without a language is French.
    language: str = ""
    models: list[str] = field(default_factory=list)

    @classmethod
    def from_spans(cls, source: str, patient_id: str, raw_text: str, spans: list[Span],
                   keep_types=()) -> "Review":
        return cls(source, patient_id, raw_text, [asdict(s) for s in spans],
                   keep_types=[t for t in keep_types if t in OPTIONAL_TYPES])


def type_name(category: str, plural: bool = False) -> str:
    one, many = TYPE_NAMES.get(category, ("other", "other"))
    return many if plural else one


# --- matching -----------------------------------------------------------------------


def _occurrences(folded_text: str, value: str) -> list[tuple[int, int]]:
    """Positions of ``value`` as a whole word, in any case, accents and separators.

    One space in the value matches a run of spaces or hyphens. A word followed
    by an apostrophe is an elision ("m'avait", "d'Anvers"), never the value.
    """
    words = fold(value).split()
    if not words:
        return []
    needle = " +".join(re.escape(w) for w in words)
    return [m.span() for m in re.finditer(rf"(?<!\w){needle}(?![\w'’ʼ])", folded_text)]


# --- the review table ---------------------------------------------------------------


@dataclass
class Entry:
    """One row of the review table: one distinct value."""

    value: str
    category: str
    count: int
    removed: bool
    origin: str  # "found" or "added"
    score: float = 1.0


def entries(review: Review) -> list[Entry]:
    """The distinct values, the doubtful ones (lowest model score) first.

    The order does not depend on the ticks, so a row stays in place when the
    user ticks or unticks it.
    """
    folded = fold(review.raw_text)
    rows: dict[str, Entry] = {}
    kept = {fold(v) for v in review.kept}
    added = {fold(a["value"]) for a in review.added}
    for span in review.spans:
        key = fold(span["value"])
        if key in rows:
            rows[key].score = min(rows[key].score, span["score"])
            continue
        removed = key not in kept and (span["category"] not in review.keep_types or key in added)
        rows[key] = Entry(span["value"], span["category"], len(_occurrences(folded, span["value"])),
                          removed, "found", span["score"])
    for item in review.added:
        key = fold(item["value"])
        if key not in rows:
            rows[key] = Entry(item["value"], item["category"],
                              len(_occurrences(folded, item["value"])), True, "added")
    return sorted(rows.values(), key=lambda e: (e.origin == "added", e.score, e.value.lower()))


def removed_values(review: Review) -> list[Entry]:
    return [e for e in entries(review) if e.removed]


def clean_text(review: Review, removed: list[Entry] | None = None) -> tuple[str, dict[str, str]]:
    """The text with every removed value replaced, at every position.

    Returns (clean text, pseudonym -> original value). A value found at one
    position is also replaced where the detector missed it. ``removed`` is
    removed_values(review), when the caller has it already.
    """
    text = review.raw_text
    folded = fold(text)
    if removed is None:
        removed = removed_values(review)
    by_key = {fold(e.value): e for e in removed}

    positions = []  # (start, end, key)
    for span in review.spans:
        key = fold(span["value"])
        if key in by_key and text[span["start"]:span["end"]] == span["value"]:
            positions.append((span["start"], span["end"], key))
    for key, entry in by_key.items():
        positions += [(s, e, key) for s, e in _occurrences(folded, entry.value)]

    # Keep the longest of overlapping positions.
    chosen: list[tuple[int, int, str]] = []
    for start, end, key in sorted(set(positions), key=lambda p: (-(p[1] - p[0]), p[0])):
        if all(end <= s or start >= e for s, e, _ in chosen):
            chosen.append((start, end, key))
    chosen.sort()

    # Number the pseudonyms for each type in order of first use.
    pseudonyms: dict[str, str] = {}
    counters: dict[str, int] = defaultdict(int)
    mapping: dict[str, str] = {}
    for _, _, key in chosen:
        if key not in pseudonyms:
            category = by_key[key].category
            counters[category] += 1
            pseudonyms[key] = f"{category}_{counters[category]}"
            mapping[pseudonyms[key]] = by_key[key].value

    for start, end, key in reversed(chosen):
        text = text[:start] + pseudonyms[key] + text[end:]
    return text, mapping


def highlighted(review: Review) -> list[tuple[str, str | None]]:
    """The raw text cut into pieces for gr.HighlightedText: (piece, type or None)."""
    text = review.raw_text
    folded = fold(text)
    removed = removed_values(review)
    marks = []
    for entry in removed:
        marks += [(s, e, type_name(entry.category)) for s, e in _occurrences(folded, entry.value)]
    removed_keys = {fold(e.value) for e in removed}
    for span in review.spans:  # positions where the value is not a whole word
        if fold(span["value"]) in removed_keys and text[span["start"]:span["end"]] == span["value"]:
            marks.append((span["start"], span["end"], type_name(span["category"])))
    pieces: list[tuple[str, str | None]] = []
    at = 0
    for start, end, label in sorted(set(marks)):
        if start < at:
            continue
        if start > at:
            pieces.append((text[at:start], None))
        pieces.append((text[start:end], label))
        at = end
    if at < len(text):
        pieces.append((text[at:], None))
    return pieces


def leaks(review: Review) -> list[str]:
    """Removed values that still appear in the clean text (should be none)."""
    removed = removed_values(review)
    clean = fold(clean_text(review, removed)[0])
    return [e.value for e in removed if _occurrences(clean, e.value)]


def _sample(text: str, start: int, end: int, margin: int = 40) -> str:
    """The text around ``start``-``end`` on one line, with "…" where it is cut."""
    first, last = max(0, start - margin), min(len(text), end + margin)
    return ("…" if first else "") + text[first:last].replace("\n", " ") + ("…" if last < len(text) else "")


def possibly_missed(review: Review, removed: list[Entry] | None = None) -> list[tuple[str, str]]:
    """Words after a title ("docteur Vandermeulen") that stay in the text.

    Returns (word, sample sentence) pairs, one for each distinct word.
    ``removed`` is removed_values(review), when the caller has it already.
    """
    done = {fold(e.value) for e in (removed_values(review) if removed is None else removed)}
    # A removed value of several words ("Jean Dupont") covers each word.
    done |= {w for v in list(done) for w in v.split()}
    found: dict[str, tuple[str, str]] = {}
    text = review.raw_text
    for match in _TITLE_NAME.finditer(text):
        word = match.group(1).strip("'’-")
        key = fold(word)
        if key in done or key in found or len(word) < 2 or key in TITLE_WORDS:
            continue
        found[key] = (word, _sample(text, match.start(), match.end()))
    return list(found.values())


def possibly_left(review: Review, clean: str | None = None) -> list[tuple[str, str]]:
    """The leak check: words in the clean text that can be a name or a town.

    Runs on the clean text, after the removal. A capitalized word inside a
    sentence is a sign when it is a Belgian first name or town ("Claire",
    "Namur"), when it follows a removed name ("PERSON_1 Peeters"), or when it
    follows a family-name particle ("Van den Broeck", "De Smet"). Lower-case
    words never are ("peau rose", "geel", "spa"). A word at the start of a
    sentence or a turn, an eponym of a disease ("maladie de Crohn"), and a word
    that a person put back in the review are skipped.

    Returns (word, sample sentence) pairs, one for each distinct word.
    ``clean`` is the clean text, when the caller has it already.
    """
    text = clean_text(review)[0] if clean is None else clean
    kept = {fold(v) for v in review.kept}
    kept |= {w for v in list(kept) for w in v.split()}
    names, places = first_names(), towns()
    found: dict[str, tuple[str, str]] = {}
    for match in _CAPITALIZED.finditer(text):
        word = match.group(1)
        key = fold(word)
        if key in found or key in kept or key in _NOT_NAMES_FOLDED or len(word) < 3:
            continue
        before = text[max(0, match.start() - 40):match.start()]
        after = text[match.end():match.end() + 20]
        if _DISEASE_BEFORE.search(before) or _DISEASE_AFTER.match(after):
            continue
        particle = _PARTICLE_BEFORE.search(before)
        # A particle at the start of a sentence is an article ("Le Covid", "Du Dafalgan").
        if particle and _SENTENCE_END.search(before[:particle.start()]):
            particle = None
        follows_name = bool(_PSEUDONYM_BEFORE.search(before) or particle)
        if not follows_name:
            parts = {key, *key.split(" ")}
            if not parts & (names | places):
                continue
            if _SENTENCE_END.search(before) and not (key in names and _FULL_NAME_AFTER.match(after)):
                continue
        found[key] = (word, _sample(text, match.start(), match.end()))
    return list(found.values())


@dataclass(frozen=True)
class Sign:
    """One warning sign of a review: its kind and the words it names."""

    kind: str  # POSSIBLY_MISSED, POSSIBLY_LEFT, UNSURE_ABOUT or NOTHING_FOUND
    words: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """The sign as the page and the CLI show it: "Possibly missed: Dupont, Claire…"."""
        if not self.words:
            return self.kind
        return f"{self.kind}: " + ", ".join(self.words[:3]) + ("…" if len(self.words) > 3 else "")


def warning_signs(review: Review) -> list[Sign]:
    """The reasons to check this file by hand. An empty list: the page approves it.

    - a word after a title stays in the text ("docteur Vandermeulen");
    - a first name or a town stays in the clean text (the leak check, possibly_left);
    - the detector is unsure about a value it removed (score under LOW_SCORE);
    - nothing was removed, which is rare for an interview.

    The first two are blocking signs (blocking_warnings): a detail can still be
    in the text.
    """
    removed = removed_values(review)
    signs = []
    missed = [word for word, _ in possibly_missed(review, removed)]
    if missed:
        signs.append(Sign(POSSIBLY_MISSED, tuple(missed)))
    shown = {fold(w) for w in missed}
    left = [word for word, _ in possibly_left(review, clean_text(review, removed)[0]) if fold(word) not in shown]
    if left:
        signs.append(Sign(POSSIBLY_LEFT, tuple(left)))
    unsure = [e.value for e in removed if e.origin == "found" and e.score < LOW_SCORE]
    if unsure:
        signs.append(Sign(UNSURE_ABOUT, tuple(unsure)))
    if not removed:
        signs.append(Sign(NOTHING_FOUND))
    return signs


def blocking_warnings(review: Review, signs: list[Sign] | None = None) -> list[Sign]:
    """The warning signs that say a detail can still be in the clean text.

    A file with one of them stops in batch mode and on the page, and needs a
    person's check before a cloud provider gets its text. "No personal detail
    found" does not block. "Unsure about" blocks only when one model ran
    (Dutch without DEDUCE, German, Spanish, Italian, or a review with no
    recorded models): no second model checks the first. On the Dutch test set,
    GLiNER alone missed details in files whose only sign was "Unsure about".
    ``signs`` is warning_signs(review), when the caller has it already.
    """
    kinds = BLOCKING_SIGNS if len(review.models) >= 2 else (*BLOCKING_SIGNS, UNSURE_ABOUT)
    return [s for s in (warning_signs(review) if signs is None else signs) if s.kind in kinds]


def reopen_if_blocked(review: Review, signs: list[Sign] | None = None) -> bool:
    """Put an approved review back in the queue when it has a blocking sign.

    A review approved with no person's check (auto, bulk, CLI, or a review file
    written before the leak check ran on it) cannot go to a cloud provider with a
    blocking sign (pipeline.code_review). The page shows it again, with its
    reasons and the Approve button. Returns True when the review changed.
    ``signs`` is warning_signs(review), when the caller has it already.
    """
    if review.status in (APPROVED, DONE) and review.approved_how != CHECKED and blocking_warnings(review, signs):
        review.status = NEEDS_REVIEW
        review.approved_at = None
        return True
    return False


def summary(review: Review) -> dict[str, int]:
    """Distinct removed values for each type, largest first, names before others."""
    counts: dict[str, int] = defaultdict(int)
    for entry in removed_values(review):
        counts[entry.category] += 1
    order = list(TYPE_NAMES)
    rank = lambda kv: (-kv[1], order.index(kv[0]) if kv[0] in order else len(order))  # noqa: E731
    return dict(sorted(counts.items(), key=rank))


def summary_line(review: Review) -> str:
    counts = summary(review)
    total = sum(counts.values())
    if not total:
        return "No personal details removed"
    parts = [f"{n} {type_name(c, plural=n != 1)}" for c, n in counts.items()]
    return f"{total} removed: " + " · ".join(parts)


# --- changes ------------------------------------------------------------------------


def _changed(review: Review) -> Review:
    review.status = NEEDS_REVIEW
    review.approved_at = None
    return review


def set_removed(review: Review, value: str, removed: bool) -> Review:
    """Tick or untick one value. Unticking puts it back at every position."""
    key = fold(value)
    review.kept = [v for v in review.kept if fold(v) != key]
    if removed:
        category = next((s["category"] for s in review.spans if fold(s["value"]) == key), None)
        if category in review.keep_types:
            # A value of a kept type: remove this one value only.
            add_value(review, value, category)
    elif any(fold(a["value"]) == key for a in review.added):
        review.added = [a for a in review.added if fold(a["value"]) != key]
    else:
        review.kept.append(value)
    return _changed(review)


def add_value(review: Review, value: str, category: str = "PERSON") -> Review:
    """Remove a value the detector missed (or a value of a kept type), at every position."""
    value = value.strip()
    if not value:
        return review
    key = fold(value)
    review.kept = [v for v in review.kept if fold(v) != key]
    if not any(fold(a["value"]) == key for a in review.added):
        review.added.append({"value": value, "category": category})
    return _changed(review)


def set_keep_types(review: Review, keep_types) -> Review:
    review.keep_types = [t for t in keep_types if t in OPTIONAL_TYPES]
    return _changed(review)


def approve(review: Review, when: datetime | None = None, how: str = CHECKED) -> Review:
    """Mark the review approved. ``how`` records who approved it and how (for the report)."""
    found = leaks(review)
    if found:
        raise ReviewError(f"Still in the clean text: {', '.join(found)}")
    review.status = APPROVED
    review.approved_how = how
    review.approved_at = (when or datetime.now()).isoformat(timespec="seconds")
    return review


# --- files --------------------------------------------------------------------------


def review_dir(out_dir) -> Path:
    return Path(out_dir) / "review"


def review_path(out_dir, patient_id: str) -> Path:
    return review_dir(out_dir) / f"{patient_id}.json"


def save_review(review: Review, out_dir) -> Path:
    """Write the review JSON. A temp file and a rename, so a crash never leaves half a file."""
    path = review_path(out_dir, review.patient_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(review), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_review(out_dir, patient_id: str) -> Review:
    path = review_path(out_dir, patient_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        known = Review.__dataclass_fields__
        return Review(**{k: v for k, v in data.items() if k in known})
    except FileNotFoundError:
        raise ReviewError(f"No review saved for {patient_id}") from None
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as e:
        raise ReviewError(f"The review file for {patient_id} is damaged ({path.name})") from e


def all_reviews(out_dir) -> list[Review]:
    """Every readable review of ``out_dir``, oldest first. A damaged file is left out."""
    folder = review_dir(out_dir)
    if not folder.is_dir():
        return []
    reviews = []
    for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            reviews.append(load_review(out_dir, path.stem))
        except ReviewError:
            continue
    return reviews


def open_reviews(out_dir) -> list[Review]:
    """Reviews that are not finished (needs review or approved), oldest first."""
    return [r for r in all_reviews(out_dir) if r.status != DONE]
