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

    # First pass: assign pseudonyms and adjust spans to trim whitespace
    adjusted_spans = []
    for span in sorted(spans, key=lambda s: s["start"]):
        raw = text[span["start"]:span["end"]]
        stripped = raw.strip()
        if not stripped:
            continue
        # Adjust offsets to match the stripped content
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        adj_start = span["start"] + leading
        adj_end = span["end"] - trailing
        adjusted_spans.append({"start": adj_start, "end": adj_end, "category": span["category"]})
        if stripped not in value_to_pseudonym:
            category_counters[span["category"]] += 1
            pseudonym = f"{span['category']}_{category_counters[span['category']]}"
            value_to_pseudonym[stripped] = pseudonym
            mapping[pseudonym] = stripped

    # Second pass: replace in text (reverse order to preserve offsets)
    result = text
    for span in sorted(adjusted_spans, key=lambda s: s["start"], reverse=True):
        original = text[span["start"]:span["end"]]
        if original and original in value_to_pseudonym:
            result = result[:span["start"]] + value_to_pseudonym[original] + result[span["end"]:]

    return result, mapping


def _detect_entities(ner, text: str, min_score: float = 0.6) -> list[dict]:
    """Detect entities, handling long texts by chunking."""
    if len(text) < 1500:
        entities = ner(text)
    else:
        chunks = _split_into_chunks(text, max_chars=1200)
        entities = []
        offset = 0

        for chunk in chunks:
            chunk_entities = ner(chunk)
            for e in chunk_entities:
                e["start"] += offset
                e["end"] += offset
            entities.extend(chunk_entities)
            offset += len(chunk)

    # Filter out low-confidence detections
    filtered = [e for e in entities if e.get("score", 0) >= min_score]
    logger.debug(
        "NER: %d entities detected, %d kept (score >= %.2f)",
        len(entities), len(filtered), min_score,
    )
    return filtered


def _split_into_chunks(text: str, max_chars: int = 1200) -> list[str]:
    """Split text into chunks at sentence boundaries, preserving exact offsets."""
    # Find split points after ". " sequences
    split_points = [m.end() for m in re.finditer(r"\.\s", text)]

    if not split_points:
        return [text]

    chunks = []
    start = 0

    for point in split_points:
        if point - start >= max_chars and start < point:
            chunks.append(text[start:point])
            start = point

    # Add the remaining text
    if start < len(text):
        chunks.append(text[start:])

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
