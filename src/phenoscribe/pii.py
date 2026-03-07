"""PII pseudonymization using camembert-ner (named entities) + regex (dates, phones, etc.)."""

import logging
import re
from collections import defaultdict

from transformers import pipeline

logger = logging.getLogger(__name__)

NER_MODEL = "Jean-Baptiste/camembert-ner"

# Regex patterns for PII not caught by NER
DATE_PATTERN = re.compile(
    r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"  # 15/03/2023, 15.03.23
    r"|\b\d{1,2}\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|"
    r"septembre|octobre|novembre|décembre)\s+\d{2,4}\b",  # 15 mars 2023
    re.IGNORECASE,
)
PHONE_PATTERN = re.compile(
    r"\b(?:\+32|0032|0)\s*\d[\s./\-]?\d{2}[\s./\-]?\d{2}[\s./\-]?\d{2}[\s./\-]?\d{2}\b"  # Belgian
    r"|\b(?:\+33|0033|0)\s*[1-9][\s./\-]?\d{2}[\s./\-]?\d{2}[\s./\-]?\d{2}[\s./\-]?\d{2}\b",  # French
)
EMAIL_PATTERN = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")
NISS_PATTERN = re.compile(r"\b\d{2}[.\-]?\d{2}[.\-]?\d{2}[.\-]?\d{3}[.\-]?\d{2}\b")  # Belgian national number

_ner_pipeline = None


def _get_ner():
    """Load or return cached NER pipeline."""
    global _ner_pipeline
    if _ner_pipeline is None:
        logger.info("Loading camembert-ner model...")
        _ner_pipeline = pipeline(
            "ner",
            model=NER_MODEL,
            aggregation_strategy="simple",
        )
        logger.info("NER model loaded.")
    return _ner_pipeline


def pseudonymize(text: str) -> tuple[str, dict]:
    """Detect PII and replace with consistent numbered pseudonyms.

    Uses camembert-ner for named entities (persons, locations, organizations)
    and regex patterns for dates, phone numbers, emails, and national IDs.

    Args:
        text: Raw French text potentially containing PII.

    Returns:
        Tuple of (pseudonymized_text, mapping_table).
        mapping_table maps pseudonym -> original value.
    """
    ner = _get_ner()

    # Collect entities from NER model
    entities = _detect_entities(ner, text)

    # Map NER labels to our categories
    ner_category = {"PER": "PERSON", "LOC": "LOCATION", "ORG": "ORGANIZATION", "MISC": "MISC"}
    spans = []
    for e in entities:
        category = ner_category.get(e["entity_group"], e["entity_group"])
        spans.append({"start": e["start"], "end": e["end"], "category": category})

    # Add regex-detected entities
    for match in DATE_PATTERN.finditer(text):
        spans.append({"start": match.start(), "end": match.end(), "category": "DATE"})
    for match in PHONE_PATTERN.finditer(text):
        spans.append({"start": match.start(), "end": match.end(), "category": "PHONE"})
    for match in EMAIL_PATTERN.finditer(text):
        spans.append({"start": match.start(), "end": match.end(), "category": "EMAIL"})
    for match in NISS_PATTERN.finditer(text):
        spans.append({"start": match.start(), "end": match.end(), "category": "ID"})

    # Remove overlapping spans (prefer longer spans)
    spans = _remove_overlaps(spans)

    # Build consistent pseudonym mapping
    value_to_pseudonym: dict[str, str] = {}
    category_counters: dict[str, int] = defaultdict(int)
    mapping: dict[str, str] = {}

    # First pass: assign pseudonyms (forward order for consistent numbering)
    for span in sorted(spans, key=lambda s: s["start"]):
        original = text[span["start"]:span["end"]].strip()
        if not original:
            continue
        if original not in value_to_pseudonym:
            category_counters[span["category"]] += 1
            pseudonym = f"{span['category']}_{category_counters[span['category']]}"
            value_to_pseudonym[original] = pseudonym
            mapping[pseudonym] = original

    # Second pass: replace in text (reverse order to preserve offsets)
    result = text
    for span in sorted(spans, key=lambda s: s["start"], reverse=True):
        original = text[span["start"]:span["end"]].strip()
        if original and original in value_to_pseudonym:
            result = result[:span["start"]] + value_to_pseudonym[original] + result[span["end"]:]

    return result, mapping


def _detect_entities(ner, text: str) -> list[dict]:
    """Detect entities, handling long texts by chunking."""
    if len(text) < 1500:
        return ner(text)

    chunks = _split_into_chunks(text, max_chars=1200)
    all_entities = []
    offset = 0

    for chunk in chunks:
        entities = ner(chunk)
        for e in entities:
            e["start"] += offset
            e["end"] += offset
        all_entities.extend(entities)
        offset += len(chunk)

    return all_entities


def _split_into_chunks(text: str, max_chars: int = 1200) -> list[str]:
    """Split text into chunks at sentence boundaries."""
    sentences = text.replace(". ", ".\n").split("\n")
    chunks = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) > max_chars and current:
            chunks.append(current)
            current = sentence
        else:
            current += sentence

    if current:
        chunks.append(current)

    return chunks


def _remove_overlaps(spans: list[dict]) -> list[dict]:
    """Remove overlapping spans, preferring longer ones."""
    # Sort by length descending, then start ascending
    spans.sort(key=lambda s: (-(s["end"] - s["start"]), s["start"]))
    kept = []
    used = set()

    for span in spans:
        positions = set(range(span["start"], span["end"]))
        if not positions & used:
            kept.append(span)
            used |= positions

    return kept
