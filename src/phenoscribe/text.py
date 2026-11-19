"""Text helpers shared by the steps: folding, timestamps, titles and code fences.

This module imports nothing heavy, so every step can use it.
"""

import re
import unicodedata

# Hyphens and spaces fold to one space, so "Jean-Dupont" and "Jean Dupont"
# match. Each folds to one character, so positions stay the same. Apostrophes
# stay: in French they mark an elision ("m'avait", "l'hôpital").
_SEPARATORS = set("-‐‑‒–—_  \t\n\r ")


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


# Transcript timestamps: "[27:25]", "(01:02:03)", "[00:12.5]", or a bare
# "01:02:03". The privacy models do not see them (they read them as ID
# numbers), and the timeline reads the time from them. They stay in the text.
TIMESTAMP_PATTERN = re.compile(
    r"[\[(]\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?[\])]"
    r"|\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\b"
)

# Titles and roles (fr, nl, en). They never identify a person, and a speaker
# label ("Médecin :", "dokter:") must stay for the speaker step. A model span
# loses these words at its edges ("Dokter Mertens" gives "Mertens"); a span of
# titles only is dropped.
TITLE_WORDS = {
    "docteur", "dr", "médecin", "medecin", "professeur", "pr", "prof", "madame", "mme",
    "mademoiselle", "mlle", "monsieur", "m", "patient", "patiente", "infirmière", "infirmier",
    "dokter", "dokteur", "huisarts", "mevrouw", "mevr", "meneer", "mr", "juffrouw",
    "patiënt", "verpleegkundige", "doctor", "mrs", "ms", "miss", "mister", "professor",
    "nurse", "sir",
}


def strip_code_fence(answer: str) -> str:
    """An LLM answer without its markdown code fence lines ("```json", "```")."""
    lines = [line for line in answer.strip().splitlines() if not line.strip().startswith("```")]
    return "\n".join(lines)
