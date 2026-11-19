"""Find personal details in a transcript, on the local machine.

The pipeline never sends raw transcript text to an external LLM. This module
finds the personal details that review.py replaces with stable placeholders
(PERSON_1, LOCATION_1, DATE_1, ...). Two layers:

- NER models, one list for each language (config.pii.models). The spans of
  all models are kept and _remove_overlaps merges them.
- Regex for dates, phones, e-mails and Belgian national numbers, in every language.

The models, their licences and their measured recall are in the README,
section "Personal details".
"""

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from phenoscribe.text import TIMESTAMP_PATTERN, TITLE_WORDS

logger = logging.getLogger(__name__)

# The defaults when no model list is given; the pipeline passes config.pii.models.
DEFAULT_MODEL = "Anonym-IA/V2-camembert-ner-pii-french"
FALLBACK_MODEL = "Jean-Baptiste/camembert-ner"
GLINER_MODEL = "urchade/gliner_multi_pii-v1"
DEDUCE = "deduce"  # the DEDUCE rules (optional install), not a Hugging Face model

# Download size of each model in the Hugging Face cache, in MB (measured
# 2026-11-18), for the message of phenoscribe setup.
MODEL_SIZES_MB = {
    DEFAULT_MODEL: 450,
    GLINER_MODEL: 1160,
    "StanfordAIMI/stanford-deidentifier-base": 440,
}


def size_text(model: str) -> str:
    """ "450 MB" or "1.2 GB" for the setup message. An unknown model gives "size unknown"."""
    mb = MODEL_SIZES_MB.get(model)
    if mb is None:
        return "size unknown"
    return f"{mb} MB" if mb < 1000 else f"{mb / 1000:.1f} GB"


# What the GLiNER model is asked to find, and the category of each answer.
GLINER_LABELS = {
    "person": "PERSON",
    "city": "LOCATION",
    "street address": "LOCATION",
    "hospital": "ORGANIZATION",
    "organization": "ORGANIZATION",
    "phone number": "PHONE",
    "email": "EMAIL",
    "date of birth": "DATE",
    "national id number": "ID",
}

# A GLiNER span of these categories must hold a capital letter (see _load).
_NEEDS_A_CAPITAL = {"PERSON", "LOCATION", "ORGANIZATION"}

# DEDUCE tags (Dutch) and their categories. A tag not listed keeps its own name.
DEDUCE_TAGS = {
    "persoon": "PERSON",
    "patient": "PERSON",
    "locatie": "LOCATION",
    "instelling": "ORGANIZATION",
    "ziekenhuis": "ORGANIZATION",
    "datum": "DATE",
    "leeftijd": "AGE",
    "telefoonnummer": "PHONE",
    "emailadres": "EMAIL",
    "url": "URL",
    "bsn": "ID",
    "id": "ID",
    "patientnummer": "ID",
}

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
    # English clinical de-identification (StanfordAIMI/stanford-deidentifier-base)
    "PATIENT": "PERSON",
    "HCW": "PERSON",
    "HOSPITAL": "ORGANIZATION",
    "VENDOR": "ORGANIZATION",
    "PHONE": "PHONE",
    "ID": "ID",
    # Dutch PII NER (OpenMed/OpenMed-PII-Dutch-*). Not in the default map;
    # scripts/eval_pii.py runs it for the comparison in the README.
    "FIRSTNAME": "PERSON",
    "MIDDLENAME": "PERSON",
    "LASTNAME": "PERSON",
    "ACCOUNTNAME": "PERSON",
    "CITY": "LOCATION",
    "STREET": "LOCATION",
    "BUILDINGNUMBER": "LOCATION",
    "ZIPCODE": "LOCATION",
    "COUNTY": "LOCATION",
    "GPSCOORDINATES": "LOCATION",
    "ORGANIZATION": "ORGANIZATION",
    "DATEOFBIRTH": "DATE",
    "SSN": "ID",
    "BANKACCOUNT": "ID",
    "BIC": "ID",
    "PIN": "ID",
    "IMEI": "ID",
    "IPADDRESS": "ID",
    "MACADDRESS": "ID",
    "MASKEDNUMBER": "ID",
    "VIN": "ID",
    "VRM": "ID",
    "SEX": "GENDER",
    "OCCUPATION": "JOB",
    "JOBDEPARTMENT": "JOB",
    # The following describe the patient's body, not their identity. Leaving
    # them in keeps the clinical story readable; drop into LABEL_TO_CATEGORY
    # only if a deployment needs them redacted.
    # "HEIGHT", "EYECOLOR", "CURRENCY", "AMOUNT"
}

# Labels that never name a person: a title ("Mevrouw") or a direction.
IGNORED_LABELS = {"O", "PREFIX", "ORDINALDIRECTION"}

_EDGE_WORD = re.compile(r"[^\W\d_]+\.?", re.UNICODE)
# Articles and possessives that can stand before a title ("le patient", "mijn dokter").
_ARTICLES = {"le", "la", "les", "l", "un", "une", "mon", "ma", "son", "sa", "votre", "notre",
             "de", "het", "een", "mijn", "uw", "zijn", "haar", "the", "a", "an", "my", "your", "his", "her"}

# Regex patterns for structured PII. These are deterministic and run
# regardless of which NER head is active.
_MONTHS = (
    "janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre"
    "|januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december"
    "|january|february|march|april|may|june|july|august|september|october|november|december"
    "|jan|feb|febr|mar|apr|jun|jul|aug|sep|sept|oct|okt|nov|dec|déc"
)
DATE_PATTERN = re.compile(
    r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"  # 15/03/2023, 15.03.23
    rf"|\b\d{{1,2}}(?:st|nd|rd|th|er)?\s+(?:{_MONTHS})\.?\s+\d{{2,4}}\b"  # 15 mars 2023, 4 mei 1962, 2 March 1984
    rf"|\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b",  # March 2, 1984
    re.IGNORECASE,
)
# Belgian numbers: a mobile has 9 digits after the 0 (0476 18 42 90), a fixed
# line 8 (02 345 67 89, 083 21 47 65), in any grouping. "+32" has no word
# boundary before it, so a lookbehind replaces \b.
PHONE_PATTERN = re.compile(
    r"(?<![\w+])(?:\+32|0032|0)[\s./\-]*(?:\(0\)\s*)?(?:\d[\s./\-]?){7,8}\d(?!\d)"  # Belgian
    r"|(?<![\w+])(?:\+33|0033|0)\s*[1-9][\s./\-]?\d{2}[\s./\-]?\d{2}[\s./\-]?\d{2}[\s./\-]?\d{2}\b",  # French
)
EMAIL_PATTERN = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")
NISS_PATTERN = re.compile(r"\b\d{2}[.\-]?\d{2}[.\-]?\d{2}[.\-]?\d{3}[.\-]?\d{2}\b")  # Belgian national number

# A number-only hit from the NER head (an ID, a phone) has at least this many
# digits. Shorter ones ("2", "36", "09") are counts, times or speaker numbers.
# Dates and ages are the exception: the user can keep those types anyway.
MIN_NUMBER_DIGITS = 6

# A text up to this length goes to a model in one piece; a longer text is cut
# into chunks of at most this length.
CHUNK_CHARS = 1200

# One loaded detector for each model name, and the load error of each model
# that failed, for the life of the process.
_ner_pipelines: dict[str, object] = {}
_load_errors: dict[str, Exception] = {}


def _load(name: str):
    """One detector: a function from a text (one chunk) to raw entities.

    Each entity is a dict with start, end, entity_group and score, as the
    transformers NER pipeline gives it.
    """
    if name == DEDUCE:
        from deduce import Deduce  # optional install (uv sync --extra dutch-rules)

        rules = Deduce()

        def run_deduce(text: str) -> list[dict]:
            return [{"start": a.start_char, "end": a.end_char, "score": 1.0,
                     "entity_group": DEDUCE_TAGS.get(a.tag, a.tag.upper())}
                    for a in rules.deidentify(text).annotations]
        return run_deduce
    if "gliner" in name.lower():
        from gliner import GLiNER  # loads torch: only a model load needs it

        model = GLiNER.from_pretrained(name)

        def run_gliner(text: str) -> list[dict]:
            found = model.predict_entities(text, list(GLINER_LABELS), threshold=0.3)
            # GLiNER also tags pronouns and family words ("maman", "lui") as names.
            # A name, a place or an organization in a transcript has a capital letter.
            return [{"start": e["start"], "end": e["end"], "score": e["score"],
                     "entity_group": GLINER_LABELS[e["label"]]} for e in found
                    if GLINER_LABELS[e["label"]] not in _NEEDS_A_CAPITAL
                    or any(c.isupper() for c in e["text"])]
        return run_gliner
    from transformers import pipeline  # loads torch: only a model load needs it

    return pipeline("ner", model=name, aggregation_strategy="simple")


def _get_ner(model: str = DEFAULT_MODEL, fallback_model: str | None = FALLBACK_MODEL):
    """The detector of ``model``, or of ``fallback_model`` when ``model`` cannot load.

    Returns (detector, name of the model that loaded). A model that failed to
    load is not tried again in this process.
    """
    candidates = [m for m in (model, fallback_model) if m]
    for i, candidate in enumerate(candidates):
        last = i == len(candidates) - 1
        if candidate in _ner_pipelines:
            return _ner_pipelines[candidate], candidate
        if candidate in _load_errors:
            if last:
                raise _load_errors[candidate]
            continue
        try:
            logger.info("Loading PII NER model: %s", candidate)
            _ner_pipelines[candidate] = _load(candidate)
        except Exception as exc:  # any load failure: try the fallback
            _load_errors[candidate] = exc
            if last:
                logger.error("Could not load PII model %s: %s", candidate, exc)
                raise
            logger.warning("Could not load PII model %s (%s). Falling back to %s.",
                           candidate, exc, candidates[-1])
            continue
        logger.info("PII NER model loaded: %s", candidate)
        return _ner_pipelines[candidate], candidate


def deduce_installed() -> bool:
    """True when the optional DEDUCE package is installed.

    The package metadata decides: an uninstall can leave the DEDUCE lookup
    cache (a "deduce" folder) behind, which an import check takes for a package.
    """
    try:
        version("deduce")
    except PackageNotFoundError:
        return False
    return True


def download_model(model: str = DEFAULT_MODEL, local_files_only: bool = False) -> str:
    """Put the PII NER model in the Hugging Face cache. Returns its folder.

    With ``local_files_only=True`` nothing downloads, and a model that is not
    cached yet raises FileNotFoundError. A GLiNER model also needs the
    tokenizer of its base model. DEDUCE is a package, so nothing downloads.
    """
    from huggingface_hub import snapshot_download  # slow to import: only setup needs it

    if model == DEDUCE:
        return ""
    folder = snapshot_download(model, local_files_only=local_files_only)
    if "gliner" in model.lower():
        base = json.loads((Path(folder) / "gliner_config.json").read_text())["model_name"]
        snapshot_download(base, local_files_only=local_files_only,
                          allow_patterns=["*.json", "*.model", "*.txt"])
    return folder


@dataclass
class Span:
    """One personal detail found in the text.

    origin is "model" (the NER head, with its score) or "regex" (score 1.0).
    """

    start: int
    end: int
    value: str
    category: str
    score: float = 1.0
    origin: str = "model"


def find_personal_details(
    text: str,
    models: Sequence[str] = (DEFAULT_MODEL,),
    fallback_model: str | None = FALLBACK_MODEL,
    min_score: float = 0.6,
) -> list[Span]:
    """Find every personal detail in ``text``, sorted by position (see detect_personal_details)."""
    return detect_personal_details(text, models, fallback_model, min_score)[0]


def detect_personal_details(
    text: str,
    models: Sequence[str] = (DEFAULT_MODEL,),
    fallback_model: str | None = FALLBACK_MODEL,
    min_score: float = 0.6,
) -> tuple[list[Span], list[str]]:
    """Find every personal detail in ``text``. Returns (spans, names of the models that ran).

    Uses each NER model in ``models`` for named entities and regex for dates,
    phones, emails and national IDs. The spans of all models are kept.
    Overlapping spans merge into one. Each span is trimmed of surrounding
    spaces, so ``value`` is the exact text.

    ``fallback_model`` replaces the first model if it cannot load. DEDUCE is
    skipped, with a warning, when the optional package is not installed.
    """
    raw = []
    ran = []
    for i, name in enumerate(models):
        if name == DEDUCE and not deduce_installed():
            logger.warning("DEDUCE is not installed (uv sync --extra dutch-rules). Skipped.")
            continue
        ner, loaded = _get_ner(name, fallback_model if i == 0 else None)
        ran.append(loaded)
        raw += _model_spans(ner, text, min_score)
    raw += _regex_spans(text)

    spans = []
    for span in _remove_overlaps(raw):
        chunk = text[span["start"]:span["end"]]
        value = chunk.strip()
        if not value:
            continue
        start = span["start"] + len(chunk) - len(chunk.lstrip())
        spans.append(Span(start, start + len(value), value, span["category"],
                          span["score"], span["origin"]))
    return sorted(spans, key=lambda s: s.start), ran


def _stamps(text: str) -> tuple[str, list[tuple[int, int]]]:
    """The text with each timestamp hidden (same length), and the timestamp positions."""
    stamps = [m.span() for m in TIMESTAMP_PATTERN.finditer(text)]
    masked = text
    for start, end in stamps:
        masked = masked[:start] + " " * (end - start) + masked[end:]
    return masked, stamps


def _model_spans(ner, text: str, min_score: float) -> list[dict]:
    """The spans of one NER model, snapped to whole words, before the overlap merge."""
    # Hide timestamps from the model: same length, so every offset stays valid.
    masked, stamps = _stamps(text)

    def in_stamp(start: int, end: int) -> bool:
        return any(start < e and end > s for s, e in stamps)

    # Two model runs: with the timestamps hidden, and on the text as it is.
    # Hidden timestamps stop false IDs but change the context around a word,
    # so a borderline name can fall under min_score in one run only. Keep the
    # hits of both runs, except any hit that touches a timestamp.
    entities = _detect_entities(ner, masked, min_score=min_score)
    if stamps:
        entities += _detect_entities(ner, text, min_score=min_score)

    raw = []
    for e in entities:
        label = e["entity_group"]
        if label in IGNORED_LABELS:
            continue
        # Unknown label: keep it as its own category rather than drop PII.
        category = LABEL_TO_CATEGORY.get(label, label)
        if not any(c.isalnum() for c in masked[e["start"]:e["end"]]):
            # A hit on punctuation only, such as the apostrophe of "j'ai": the
            # snap below would grow it to the whole word.
            continue
        # NER subword tokenisation (esp. for accented or multi-subword names)
        # often reports a span that covers only part of a word. Snap the span
        # out to whole-word boundaries so we never leave a leaked fragment such
        # as "PERSON_1ëlle" or "JOB_1ynéco".
        start, end = _snap_to_word_boundaries(masked, e["start"], e["end"])
        start, end = _trim_titles(masked, start, end)
        if start >= end:
            continue  # titles only: "Médecin", "mevrouw"
        if in_stamp(start, end):
            continue
        found = masked[start:end]
        letters = sum(c.isalpha() for c in found)
        digits = sum(c.isdigit() for c in found)
        if not letters and digits < MIN_NUMBER_DIGITS and category not in ("DATE", "AGE"):
            continue  # "2", "36", "1:" are counts, times or speaker numbers
        if letters < 2 and not digits:
            # One letter is never a name: "M" of "M." or the "m" of "m'avait".
            continue
        raw.append({"start": start, "end": end, "category": category,
                    "score": float(e.get("score", 1.0)), "origin": "model"})
    return raw


def _regex_spans(text: str) -> list[dict]:
    """Regex-detected entities (deterministic, model-independent)."""
    masked, _ = _stamps(text)
    raw = []
    for pattern, category in (
        (DATE_PATTERN, "DATE"),
        (PHONE_PATTERN, "PHONE"),
        (EMAIL_PATTERN, "EMAIL"),
        (NISS_PATTERN, "ID"),
    ):
        for match in pattern.finditer(masked):
            raw.append({"start": match.start(), "end": match.end(), "category": category,
                        "score": 1.0, "origin": "regex"})
    return raw


def _detect_entities(ner, text: str, min_score: float = 0.6) -> list[dict]:
    """Detect entities, handling long texts by chunking."""
    if len(text) <= CHUNK_CHARS:
        entities = ner(text)
    else:
        chunks = _split_into_chunks(text, max_chars=CHUNK_CHARS)
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


def _split_into_chunks(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Split text into chunks of at most ``max_chars``, preserving exact offsets.

    A chunk ends after the last sentence end (". ", "? ", "! " or a new line)
    that keeps it under ``max_chars``. A transcript with no punctuation is cut
    at the last space instead, so a model never gets more than it can read.
    speakers.split_chunks stays apart: it strips its chunks, so offsets do not hold.
    """
    chunks = []
    start = 0
    while len(text) - start > max_chars:
        window = text[start:start + max_chars]
        ends = [m.end() for m in re.finditer(r"[.?!]\s|\n", window)]
        cut = ends[-1] if ends and ends[-1] > max_chars // 3 else window.rfind(" ") + 1
        if cut <= 0:
            cut = max_chars
        chunks.append(text[start:start + cut])
        start += cut
    if start < len(text):
        chunks.append(text[start:])
    return chunks


# A "word" for the purpose of span snapping: Latin letters (incl. accented),
# digits and underscore. Apostrophes and hyphens are treated as boundaries so
# that "l'X" snaps to just "X" and adjacent hyphen-joined names are not merged
# beyond what the NER reported.
_WORD_CHAR = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]")


def _snap_to_word_boundaries(text: str, start: int, end: int) -> tuple[int, int]:
    """Expand an entity span outward to cover whole words.

    NER models return offsets aligned to subword tokens, which for accented or
    multi-subword names can cut a word in half (covering only "Ga" of
    "Gaëlle"). Replacing just that slice leaks the rest ("PERSON_1ëlle").
    We grow the span left and right while the neighbouring character is part of
    the same word, so the full identifier is replaced.
    """
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))

    # Grow left while the previous char continues a word.
    while start > 0 and _WORD_CHAR.match(text[start - 1]):
        start -= 1
    # Grow right while the next char continues a word.
    while end < len(text) and _WORD_CHAR.match(text[end]):
        end += 1
    return start, end


def _trim_titles(text: str, start: int, end: int) -> tuple[int, int]:
    """Move the span edges past title words ("Dokter Mertens" -> "Mertens").

    An article before a title goes with it ("Le patient", "De patiënt", "the
    doctor"), so no article stays as a name.
    """
    words = list(_EDGE_WORD.finditer(text, start, end))

    def is_title(m) -> bool:
        return m.group().rstrip(".").lower() in TITLE_WORDS

    def is_article(m) -> bool:
        return m.group().rstrip(".").lower() in _ARTICLES

    kept = list(words)
    while kept and (is_title(kept[0]) or (is_article(kept[0]) and len(kept) > 1 and is_title(kept[1]))):
        kept.pop(0)
    while kept and is_title(kept[-1]):
        kept.pop()
        if kept and is_article(kept[-1]):
            kept.pop()
    if not words or (kept and kept[0] is words[0] and kept[-1] is words[-1]):
        return start, end  # nothing dropped (or no words at all: a number)
    if not kept:
        rest = text[words[-1].end():end].strip(" \t\n:,.")
        return (end - len(text[words[-1].end():end].lstrip(" \t\n:,")), end) if rest else (start, start)
    new_start = start if kept[0] is words[0] else kept[0].start()
    new_end = end if kept[-1] is words[-1] else kept[-1].end()
    return new_start, new_end


def _remove_overlaps(spans: list[dict]) -> list[dict]:
    """Merge overlapping spans into their union.

    Two models can find one detail with different edges ("Dokter Mertens" and
    "Mertens", "geboortedatum 11/12" and "11/12/1991"). The merged span covers
    every character of both, so no part of the detail stays in the text. It
    takes the category and origin of its longest part and the highest score.
    """
    merged: list[dict] = []
    for span in sorted(spans, key=lambda s: (s["start"], -s["end"])):
        if merged and span["start"] < merged[-1]["end"]:
            last = merged[-1]
            longest = last.pop("_longest")
            if span["end"] - span["start"] > longest["end"] - longest["start"]:
                longest = span
            last.update(end=max(last["end"], span["end"]), score=max(last["score"], span["score"]),
                        category=longest["category"], origin=longest["origin"], _longest=longest)
        else:
            merged.append({**span, "_longest": span})
    for span in merged:
        del span["_longest"]
    return merged
