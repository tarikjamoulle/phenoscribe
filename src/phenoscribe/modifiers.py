"""Map extracted clinical modifiers to HPO subontology roots.

Frequency, onset, and severity belong in the HPO subontologies, not in a
free-text context field. This module turns the
loosely-typed strings the LLM returns into HPO codes under the right root.

Subontology roots (HPO release hp/releases/2026-02-16):
- Frequency           HP:0040279
- Clinical modifier   HP:0012823  (Severity HP:0012824 lives under it)
- Onset               HP:0003674  (under Clinical course HP:0031797)
- Past medical history HP:0032443

Mapping is deliberately small and rule-based. The LLM already emits a
normalized English value (e.g. "severe", "frequent", "childhood onset");
we snap that to the nearest HPO leaf. Unmapped values are kept as free
text so nothing is silently dropped.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Frequency leaves (children of HP:0040279). Keys are lowercase cues.
_FREQUENCY = {
    "obligate": ("HP:0040280", "Obligate"),
    "very frequent": ("HP:0040281", "Very frequent"),
    "frequent": ("HP:0040282", "Frequent"),
    "often": ("HP:0040282", "Frequent"),
    "occasional": ("HP:0040283", "Occasional"),
    "occasionally": ("HP:0040283", "Occasional"),
    "sometimes": ("HP:0040283", "Occasional"),
    "intermittent": ("HP:0040283", "Occasional"),
    "very rare": ("HP:0040284", "Very rare"),
    "rare": ("HP:0040284", "Very rare"),
    "rarely": ("HP:0040284", "Very rare"),
    "excluded": ("HP:0040285", "Excluded"),
}

# Severity leaves (children of HP:0012824, under Clinical modifier).
_SEVERITY = {
    "borderline": ("HP:0012827", "Borderline"),
    "mild": ("HP:0012825", "Mild"),
    "moderate": ("HP:0012826", "Moderate"),
    "severe": ("HP:0012828", "Severe"),
    "profound": ("HP:0012829", "Profound"),
}

# Onset leaves (children of HP:0003674, under Clinical course).
_ONSET = {
    "congenital": ("HP:0003577", "Congenital onset"),
    "congenital onset": ("HP:0003577", "Congenital onset"),
    "neonatal": ("HP:0003623", "Neonatal onset"),
    "neonatal onset": ("HP:0003623", "Neonatal onset"),
    "antenatal": ("HP:0030674", "Antenatal onset"),
    "antenatal onset": ("HP:0030674", "Antenatal onset"),
    "pediatric": ("HP:0410280", "Pediatric onset"),
    "pediatric onset": ("HP:0410280", "Pediatric onset"),
    "childhood": ("HP:0011463", "Childhood onset"),
    "childhood onset": ("HP:0011463", "Childhood onset"),
    "juvenile": ("HP:0003621", "Juvenile onset"),
    "juvenile onset": ("HP:0003621", "Juvenile onset"),
    "adult": ("HP:0003581", "Adult onset"),
    "adult onset": ("HP:0003581", "Adult onset"),
}


def map_frequency(value: str | None) -> tuple[str, str] | None:
    """Map a frequency value to an HPO Frequency leaf, or None."""
    return _lookup(value, _FREQUENCY)


def map_severity(value: str | None) -> tuple[str, str] | None:
    """Map a severity value to an HPO Severity leaf, or None."""
    return _lookup(value, _SEVERITY)


def map_onset(value: str | None) -> tuple[str, str] | None:
    """Map an onset value to an HPO Onset leaf, or None."""
    return _lookup(value, _ONSET)


# A cue preceded by one of these words is negated: "not often", "never severe".
# "un" is the prefix of "unfrequent" written apart; "in" stays out ("in childhood").
_NEGATION_WORDS = {"not", "no", "never", "non", "un"}
# A cue joined to one of these prefixes by a hyphen is negated: "in-frequent".
_NEGATION_PREFIX = re.compile(r"\b(?:in|un|non)-$")


def _lookup(value: str | None, table: dict) -> tuple[str, str] | None:
    """The leaf of the longest cue found as whole words in ``value``, or None.

    A negated cue gives None: "not often" and "in-frequent" are no frequency.
    """
    if not value:
        return None
    key = value.strip().lower()
    for cue in sorted(table, key=len, reverse=True):
        match = re.search(rf"\b{re.escape(cue)}\b", key)
        if match:
            return None if _is_negated(key[: match.start()]) else table[cue]
    logger.debug("modifier value not mapped to HPO leaf: %r", value)
    return None


def _is_negated(before: str) -> bool:
    """True when the text before a cue negates it."""
    if _NEGATION_PREFIX.search(before):
        return True
    return any(word in _NEGATION_WORDS for word in re.findall(r"[a-z]+", before))
