"""Where each finding comes up in the interview.

The extractor returns, for each finding, every quote where it comes up
("mentions"). This module finds each quote in the clean transcript and gives
its position: a percentage of the interview (0 = start, 100 = end) and, when
the transcript has "[mm:ss]" markers, the time from the start. One row for
each mention lets the user study how the findings change over an interview.

The position % counts only the spoken text: timestamp markers and spaces do
not count, so a transcript with markers and one without give the same position.
"""

import re
from difflib import SequenceMatcher

from phenoscribe.pii import TIMESTAMP_PATTERN
from phenoscribe.review import fold

# An approximate match needs at least this similarity (0 to 1). The extractor
# copies quotes, but it can change a word, an accent or the punctuation.
MIN_SIMILARITY = 0.8

_WORD = re.compile(r"\w+")


def _seconds(stamp: str) -> int:
    parts = [int(p) for p in re.findall(r"\d+", stamp.split(".")[0].split(",")[0])][:3]
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def time_label(seconds: int | None) -> str:
    if seconds is None:
        return ""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class Transcript:
    """A clean transcript, ready for quote search."""

    def __init__(self, text: str):
        self.text = text
        self.folded = fold(text)
        stamps = list(TIMESTAMP_PATTERN.finditer(text))
        self.stamps = [(m.start(), _seconds(m.group(0))) for m in stamps]
        # Spoken characters before each position: no timestamp markers, no spaces.
        in_stamp = [False] * len(text)
        for m in stamps:
            for i in range(m.start(), m.end()):
                in_stamp[i] = True
        self._spoken_before = [0] * (len(text) + 1)
        for i, hidden in enumerate(in_stamp):
            spoken = not hidden and not text[i].isspace()
            self._spoken_before[i + 1] = self._spoken_before[i] + spoken
        self.words = [(m.start(), m.end(), m.group(0)) for m in _WORD.finditer(self.folded)]

    def position(self, offset: int) -> float:
        """Percent of the spoken text before ``offset``, 0 to 100."""
        total = self._spoken_before[-1]
        return round(100 * self._spoken_before[offset] / total, 1) if total else 0.0

    def seconds_at(self, offset: int) -> int | None:
        """The time of the last marker at or before ``offset``, or None."""
        found = None
        for start, seconds in self.stamps:
            if start > offset:
                break
            found = seconds
        return found

    def locate_all(self, quote: str) -> list[tuple[int, int, str]]:
        """The places of ``quote`` in the text, in text order.

        First an exact search (any case, accent or spacing). When it finds
        nothing, the best window of the same number of words, if it is
        similar enough (MIN_SIMILARITY).
        """
        words = _WORD.findall(fold(quote))
        if not words:
            return []
        pattern = r"\W+".join(re.escape(w) for w in words)
        exact = [(m.start(), m.end(), "exact")
                 for m in re.finditer(rf"(?<!\w){pattern}(?!\w)", self.folded)]
        if exact:
            return exact
        target = " ".join(words)
        n = len(words)
        best, best_score = None, 0.0
        for i in range(len(self.words)):
            # Only windows that share the first or the last word, for speed.
            if self.words[i][2] != words[0] and (
                i + n - 1 >= len(self.words) or self.words[i + n - 1][2] != words[-1]
            ):
                continue
            for size in (n - 1, n, n + 1):
                j = i + size - 1
                if size < 1 or j >= len(self.words):
                    continue
                window = " ".join(w[2] for w in self.words[i:j + 1])
                score = SequenceMatcher(None, target, window).ratio()
                if score > best_score:
                    best, best_score = (self.words[i][0], self.words[j][1]), score
        if best and best_score >= MIN_SIMILARITY:
            return [(best[0], best[1], "approximate")]
        return []


def _key(match: dict) -> tuple:
    """One key for each row of the Excel: the same as output.dedup_by_code."""
    code = match.get("hpo_id") or ("uncoded", (match.get("clinical_term") or "").lower())
    return code, bool(match.get("negated", False))


def mention_rows(matches: list[dict], text: str) -> list[dict]:
    """One row for each mention of each finding, in interview order.

    Each row: hpo_id, hpo_term, clinical_term, negated, quote, start,
    position (%), seconds, found ("exact", "approximate" or "not found"),
    mention (number of this mention for the finding) and order (1 = the
    first finding to come up in the interview). A quote that is not found
    keeps an empty position, at the end of the list.
    """
    transcript = Transcript(text)
    rows: list[dict] = []
    seen: set[tuple] = set()  # quotes not found, once for each finding
    spans: dict[tuple, list[tuple[int, int]]] = {}  # places taken, for each finding
    for match in matches:
        quotes = [q for q in (match.get("mentions") or []) if q and q.strip()]
        if not quotes and match.get("patient_verbatim"):
            quotes = [match["patient_verbatim"]]
        key = _key(match)
        for quote in quotes:
            places = transcript.locate_all(quote) or [None]
            for place in places:
                if place is None:
                    if (key, quote) in seen:
                        continue
                    seen.add((key, quote))
                else:
                    # Two quotes of one finding on one place ("mal à la tête" in
                    # "j'ai mal à la tête") count as one mention.
                    taken = spans.setdefault(key, [])
                    if any(place[0] < e and place[1] > s for s, e in taken):
                        continue
                    taken.append((place[0], place[1]))
                row = {
                    "key": key,
                    "hpo_id": match.get("hpo_id"),
                    "hpo_term": match.get("hpo_term", ""),
                    "clinical_term": match.get("clinical_term", ""),
                    "negated": bool(match.get("negated", False)),
                    "quote": quote.strip(),
                    "start": None, "position": None, "seconds": None, "found": "not found",
                }
                if place:
                    start, _, how = place
                    row.update(start=start, position=transcript.position(start),
                               seconds=transcript.seconds_at(start), found=how)
                rows.append(row)

    rows.sort(key=lambda r: (r["start"] is None, r["start"] or 0))
    counts: dict[tuple, int] = {}
    order: dict[tuple, int] = {}
    for row in rows:
        counts[row["key"]] = counts.get(row["key"], 0) + 1
        row["mention"] = counts[row["key"]]
        if row["start"] is not None and row["key"] not in order:
            order[row["key"]] = len(order) + 1
    for row in rows:
        row["order"] = order.get(row["key"])
    return rows


def add_timeline(matches: list[dict], rows: list[dict]) -> None:
    """Put first mention %, last mention % and the mention count on each match."""
    by_key: dict[tuple, list[dict]] = {}
    for row in rows:
        by_key.setdefault(row["key"], []).append(row)
    for match in matches:
        found = [r for r in by_key.get(_key(match), []) if r["position"] is not None]
        match["first_position"] = min((r["position"] for r in found), default=None)
        match["last_position"] = max((r["position"] for r in found), default=None)
        match["n_mentions"] = len(found)
