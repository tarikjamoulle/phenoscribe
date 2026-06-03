"""Local PII pseudonymisation: a French NER head plus regex for structured PII.

The pipeline never sends raw transcript text to an external LLM. This module
runs entirely on the local machine and replaces identifying spans with stable
numbered pseudonyms (PERSON_1, LOCATION_1, DATE_1, ...).

Default NER head: ``Anonym-IA/V2-camembert-ner-pii-french`` (CamemBERT-base,
110M params, MIT). It is a French *PII* NER trained on ~57k examples and tags
39 entity types with BIO labels (names, street/city/postal, email, phone,
social-security number, IBAN, dates, job titles, ...). Reported validation
micro-F1 is 0.9327 (best_metrics.json on the model card).

Offline fallback: ``Jean-Baptiste/camembert-ner``, a general 4-label French
NER (PER/LOC/ORG/MISC) trained on WikiNER, overall F1 0.8914. It is selected
automatically if the default model cannot be loaded.

Known limitation (both models): general/PII NER under-redacts rare proper
nouns it never saw in training — hospital and clinic names, drug eponyms,
uncommon surnames. The regex layer below catches structured PII (dates,
phones, emails, national IDs) deterministically; free-text identifiers depend
on the NER head and should be spot-checked on the first batch.
"""

import logging
import re
from collections import defaultdict

from transformers import pipeline

logger = logging.getLogger(__name__)

# Configurable default; pipeline passes config.pii.model / fallback_model.
DEFAULT_MODEL = "Anonym-IA/V2-camembert-ner-pii-french"
FALLBACK_MODEL = "Jean-Baptiste/camembert-ner"

# Map every NER label we might see (from either model) onto the pseudonym
# categories used downstream. With aggregation_strategy="simple" the
# transformers pipeline strips the B-/I- BIO prefix, so we key on the bare
# label. Labels not listed here fall through to their own name as a category.
LABEL_TO_CATEGORY = {
    # General 4-label French NER (Jean-Baptiste/camembert-ner)
    "PER": "PERSON",
    "LOC": "LOCATION",
    "ORG": "ORGANIZATION",
    "MISC": "MISC",
    # French PII NER (Anonym-IA/V2-camembert-ner-pii-french)
    "NOM_PERSONNE": "PERSON",
    "PRENOM_PERSONNE": "PERSON",
    "USERNAME": "PERSON",
    "NUMERO_VOIE": "LOCATION",
    "NOM_VOIE": "LOCATION",
    "CODE_POSTAL": "LOCATION",
    "VILLE": "LOCATION",
    "SECONDARYADDRESS": "LOCATION",
    "STATE": "LOCATION",
    "COUNTRY": "LOCATION",
    "NOM_SOCIETE": "ORGANIZATION",
    "EMAIL": "EMAIL",
    "TELEPHONE": "PHONE",
    "NUM_SECURITE_SOCIALE": "ID",
    "IBAN": "ID",
    "NUM_DOSSIER": "ID",
    "REF_CADASTRALE": "ID",
    "ACCOUNTNUMBER": "ID",
    "CREDITCARD": "ID",
    "VEHICLEVIN": "ID",
    "VEHICLEVRM": "ID",
    "BITCOINADDRESS": "ID",
    "ETHEREUMADDRESS": "ID",
    "IP": "ID",
    "MAC": "ID",
    "PASSWORD": "ID",
    "URL": "URL",
    "USERAGENT": "MISC",
    "JOBTITLE": "JOB",
    "JOBAREA": "JOB",
    "JOBTYPE": "JOB",
    "DATE": "DATE",
    "DOB": "DATE",
    "TIME": "DATE",
    "AGE": "AGE",
    "GENDER": "GENDER",
    # The following describe the patient's body, not their identity. Leaving
    # them in keeps the clinical story readable; drop into LABEL_TO_CATEGORY
    # only if a deployment needs them redacted.
    # "HEIGHT", "EYECOLOR", "CURRENCY", "AMOUNT"
}

# Regex patterns for structured PII. These are deterministic and run
# regardless of which NER head is active.
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

# Cache one pipeline per resolved model name.
_ner_pipelines: dict[str, object] = {}


def _get_ner(model: str = DEFAULT_MODEL, fallback_model: str = FALLBACK_MODEL):
    """Load (or return cached) NER pipeline, falling back if the default fails.

    Returns a tuple of (pipeline, resolved_model_name) so callers can report
    which model actually ran.
    """
    for candidate in (model, fallback_model):
        if candidate is None:
            continue
        if candidate in _ner_pipelines:
            return _ner_pipelines[candidate], candidate
        try:
            logger.info("Loading PII NER model: %s", candidate)
            pipe = pipeline("ner", model=candidate, aggregation_strategy="simple")
            _ner_pipelines[candidate] = pipe
            logger.info("PII NER model loaded: %s", candidate)
            return pipe, candidate
        except Exception as exc:  # noqa: BLE001 — any load failure → try fallback
            if candidate != fallback_model:
                logger.warning(
                    "Could not load PII model %s (%s). Falling back to %s.",
                    candidate, exc, fallback_model,
                )
            else:
                logger.error("Could not load fallback PII model %s: %s", candidate, exc)
                raise
    raise RuntimeError("No PII NER model could be loaded.")


def resolve_model(model: str = DEFAULT_MODEL, fallback_model: str = FALLBACK_MODEL) -> str:
    """Return the model name that ``_get_ner`` would actually load.

    Used by tests to assert no drift between config/docs and the loaded model.
    """
    _, resolved = _get_ner(model, fallback_model)
    return resolved


def pseudonymize(
    text: str,
    model: str = DEFAULT_MODEL,
    fallback_model: str = FALLBACK_MODEL,
    min_score: float = 0.6,
) -> tuple[str, dict]:
    """Detect PII and replace it with consistent numbered pseudonyms.

    Uses a French NER head for named entities and regex for dates, phones,
    emails and national IDs.

    Args:
        text: Raw French text potentially containing PII.
        model: NER model to load (config.pii.model).
        fallback_model: Model used if ``model`` cannot be loaded.
        min_score: Minimum NER confidence to keep a detection.

    Returns:
        Tuple of (pseudonymized_text, mapping_table).
        mapping_table maps pseudonym -> original value.
    """
    ner, _ = _get_ner(model, fallback_model)

    # Collect entities from the NER model
    entities = _detect_entities(ner, text, min_score=min_score)

    # Map NER labels to our pseudonym categories
    spans = []
    for e in entities:
        label = e["entity_group"]
        category = LABEL_TO_CATEGORY.get(label)
        if category is None:
            # Unknown label: keep it as its own category rather than drop PII.
            category = label
        spans.append({"start": e["start"], "end": e["end"], "category": category})

    # Add regex-detected entities (deterministic, model-independent)
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
