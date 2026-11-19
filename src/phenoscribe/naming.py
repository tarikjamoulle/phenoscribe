"""Names and paths: the patient ID of a file, the interview details, and the files of a run."""

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from phenoscribe.config import Config
from phenoscribe.transcribe import resolve_backend

logger = logging.getLogger(__name__)


def derive_patient_id(stem: str, prefix: str) -> str:
    """Build the join key from a filename stem and the configured prefix.

    The transcript and audio files are named by bare stem (e.g. "001"). A
    ground truth can key patients with a prefix ("P.001"). Prepend the prefix so
    the pipeline output joins against it. A stem that already carries the
    prefix stays as it is, so a rerun gives the same ID.
    """
    if not prefix or stem.startswith(prefix):
        return stem
    return f"{prefix}{stem}"


def batch_patient_id(filename: str, prefix: str) -> str:
    """Patient ID for an uploaded file.

    A file named by patient number ("001.mp3") gets the configured prefix
    ("P.001"), so the Excel joins against a ground truth. Any other filename
    can carry a patient name ("martin-jean-2025-04-12.mp3"), which must stay
    out of the Excel, so it gets a short hash of the stem instead. The stem,
    so a recording and its transcript ("a.mp3", "a.txt") share one ID.
    """
    stem = Path(filename).stem
    if stem.isdigit():
        return derive_patient_id(stem, prefix)
    digest = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:8]
    return f"pt-{digest}"


_FILE_DATE = re.compile(r"(?<!\d)(20\d\d)[-_.]?(\d\d)[-_.]?(\d\d)(?!\d)")


def _patient_number(prefix: str) -> re.Pattern:
    """The patient number at the start of a file name, after an optional prefix.

    The prefix match ignores case, the dot and the spaces: with prefix "P.",
    "P.006 x", "p 006 x" and "006 x" all give 006.
    """
    core = prefix.strip().rstrip(".").strip()
    head = rf"(?:{re.escape(core)}\s*\.?\s*)?" if core else ""
    return re.compile(rf"^\s*{head}(\d{{1,4}})(?!\d)", re.IGNORECASE)


def interview_details(filename: str, prefix: str) -> dict:
    """Patient, interview type and date from the file name, for the Excel.

    One patient can have several files ("P.006 interview.docx", "006 interview
    evolution 2025-03-11_08-30-00.docx"), and each file gets its own patient ID.
    The number at the start links the files of one patient. "evolution" marks a
    follow-up, "mail" an email; any other file is an interview. The date comes
    from the name (YYYYMMDD or YYYY-MM-DD), else it stays empty. No other part of
    the name goes to the Excel, because a name can hold a patient's name.
    """
    stem = Path(filename).stem
    lower = stem.lower()
    number = _patient_number(prefix).match(stem)
    kind = "email" if "mail" in lower else "follow-up" if "evol" in lower else "interview"
    date = ""
    for year, month, day in _FILE_DATE.findall(stem):
        if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            date = f"{year}-{month}-{day}"
            break
    return {
        "patient": f"{prefix}{number.group(1)}" if number else "",
        "interview": kind,
        "date": date,
    }


def transcript_cache_path(audio_path: str, config: Config, transcript_dir) -> Path:
    """Where the transcript of these exact audio bytes is kept.

    The name is a SHA-256 of the audio bytes plus the backend, model and
    language, so a changed file or another Whisper setting never reuses an
    old transcript, and reuse never depends on the patient ID.
    """
    digest = hashlib.sha256()
    with open(audio_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    t = config.transcription
    digest.update(f"|{resolve_backend(t.backend)}|{t.model}|{t.language}".encode())
    return Path(transcript_dir) / "by-audio" / f"{digest.hexdigest()}.txt"


def dated_path(out_dir: Path, prefix: str, suffix: str, now: datetime | None = None) -> Path:
    """A free file name for one run: <prefix>_YYYY-MM-DD_HHMM<suffix>.

    A second run in the same minute gets a "_2" suffix, so a run never writes
    into an older file, and an older file that is open in Excel never blocks the save.
    """
    base = f"{prefix}_{(now or datetime.now()):%Y-%m-%d_%H%M}"
    path = out_dir / f"{base}{suffix}"
    n = 2
    while path.exists():
        path = out_dir / f"{base}_{n}{suffix}"
        n += 1
    return path


def new_results_path(out_dir: Path, now: datetime | None = None) -> Path:
    """A new dated Excel path for one run: results_YYYY-MM-DD_HHMM.xlsx."""
    return dated_path(out_dir, "results", ".xlsx", now)


def record_filename_mapping(out_dir: Path, mapping: dict[str, str]) -> None:
    """Merge the patient_id -> filename mapping into out_dir/filename_mapping.json.

    Lets the user map an Excel row back to the source recording without putting the
    raw filename in the Excel itself.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "filename_mapping.json"
    existing: dict[str, str] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("filename_mapping.json was malformed; rewriting from scratch")
    existing.update(mapping)
    path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
