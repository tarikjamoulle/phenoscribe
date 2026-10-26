"""The review of personal details, before any text leaves the computer.

The detector (pii.find_personal_details) finds the personal details. A person
then checks them: untick a wrong removal, add a missed name, keep a type such
as age. Only an approved review gives the clean text that the HPO coding reads.

Everything here is a pure function of a Review, except load_review and
save_review. A Review holds the raw text with the real names, so it stays in
<results>/review/ on this computer and is never offered as a download.
"""

import json
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from phenoscribe.pii import Span

NEEDS_REVIEW = "needs review"
APPROVED = "approved"
DONE = "done"

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
_TITLE_NAME = re.compile(
    r"(?i:\b(?:(?:docteur|professeur|madame|mademoiselle|monsieur)|(?:dr|pr|mme|mlle|m)\.?))\s+"
    r"([A-ZÀ-ÖØ-Þ][\w'’-]+)"
)


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

    @classmethod
    def from_spans(cls, source: str, patient_id: str, raw_text: str, spans: list[Span],
                   keep_types=()) -> "Review":
        return cls(source, patient_id, raw_text, [asdict(s) for s in spans],
                   keep_types=[t for t in keep_types if t in OPTIONAL_TYPES])


def type_name(category: str, plural: bool = False) -> str:
    one, many = TYPE_NAMES.get(category, ("other", "other"))
    return many if plural else one


# --- matching -----------------------------------------------------------------------


# Hyphens and spaces fold to one space, so "Jean-Dupont" and "Jean Dupont"
# match. Each folds to one character, so positions stay the same. Apostrophes
# stay: in French they mark an elision ("m'avait", "l'hôpital").
_SEPARATORS = set("-‐‑‒–—_\u00a0\u202f\t\n\r ")


def _fold_char(c: str) -> str:
    if c in _SEPARATORS:
        return " "
    base = unicodedata.normalize("NFD", c)[0]
    folded = base.casefold()  # casefold, so "ς" and "Σ" both give "σ"
    if len(folded) == 1:
        return folded
    low = base.lower()
    return low if len(low) == 1 else base


def fold(text: str) -> str:
    """Case, accents and separators off, one character for each character.

    The length stays the same, so a position in the folded text is the same
    position in the original text.
    """
    return "".join(map(_fold_char, text))


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


def clean_text(review: Review) -> tuple[str, dict[str, str]]:
    """The text with every removed value replaced, at every position.

    Returns (clean text, pseudonym -> original value). A value found at one
    position is also replaced where the detector missed it.
    """
    text = review.raw_text
    folded = fold(text)
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
    marks = []
    for entry in removed_values(review):
        marks += [(s, e, type_name(entry.category)) for s, e in _occurrences(folded, entry.value)]
    removed_keys = {fold(e.value) for e in removed_values(review)}
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
    clean = fold(clean_text(review)[0])
    return [e.value for e in removed_values(review) if _occurrences(clean, e.value)]


def possibly_missed(review: Review) -> list[tuple[str, str]]:
    """Words after a title ("docteur Vandermeulen") that stay in the text.

    Returns (word, sample sentence) pairs, one for each distinct word.
    """
    done = {fold(e.value) for e in removed_values(review)}
    # A removed value of several words ("Jean Dupont") covers each word.
    done |= {w for v in list(done) for w in v.split()}
    found: dict[str, tuple[str, str]] = {}
    text = review.raw_text
    for match in _TITLE_NAME.finditer(text):
        word = match.group(1).strip("'’-")
        key = fold(word)
        if key in done or key in found or len(word) < 2:
            continue
        start = max(0, match.start() - 40)
        end = min(len(text), match.end() + 40)
        sample = ("…" if start else "") + text[start:end].replace("\n", " ") + ("…" if end < len(text) else "")
        found[key] = (word, sample)
    return list(found.values())


def warnings(review: Review) -> list[str]:
    """The reasons to check this file by hand. An empty list: the page approves it.

    - a word after a title stays in the text ("docteur Vandermeulen");
    - the detector is unsure about a value it removed (score under LOW_SCORE);
    - nothing was removed, which is rare for an interview.
    """
    reasons = []
    missed = [word for word, _ in possibly_missed(review)]
    if missed:
        reasons.append("Possibly missed: " + ", ".join(missed[:3]) + ("…" if len(missed) > 3 else ""))
    unsure = [e.value for e in removed_values(review) if e.origin == "found" and e.score < LOW_SCORE]
    if unsure:
        reasons.append("Unsure about: " + ", ".join(unsure[:3]) + ("…" if len(unsure) > 3 else ""))
    if not removed_values(review):
        reasons.append("No personal detail found")
    return reasons


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


def open_reviews(out_dir) -> list[Review]:
    """Reviews that are not finished (needs review or approved), oldest first."""
    folder = review_dir(out_dir)
    if not folder.is_dir():
        return []
    reviews = []
    for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            review = load_review(out_dir, path.stem)
        except ReviewError:
            continue
        if review.status != DONE:
            reviews.append(review)
    return reviews
