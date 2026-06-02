"""Map extracted clinical modifiers to HPO subontology roots.

Robinson issue #6: frequency, onset, and severity belong in the HPO
subontologies, not in a free-text context blob. This module turns the
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

logger = logging.getLogger(__name__)

# Subontology root identifiers, exported for callers and tests.
FREQUENCY_ROOT = "HP:0040279"
CLINICAL_MODIFIER_ROOT = "HP:0012823"
SEVERITY_ROOT = "HP:0012824"
ONSET_ROOT = "HP:0003674"
PAST_MEDICAL_HISTORY_ROOT = "HP:0032443"

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


def _lookup(value: str | None, table: dict) -> tuple[str, str] | None:
    if not value:
        return None
    key = value.strip().lower()
    if key in table:
        return table[key]
    # Substring fall-through: "severe headache" -> severe, "since childhood" -> childhood.
    # Prefer the longest cue so "very rare" wins over "rare".
    for cue in sorted(table, key=len, reverse=True):
        if cue in key:
            return table[cue]
    logger.debug("modifier value not mapped to HPO leaf: %r", value)
    return None
