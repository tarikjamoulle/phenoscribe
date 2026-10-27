"""Pipeline orchestrator — chains all processing steps."""

import hashlib
import json
import logging
import os
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from phenoscribe.config import Config
from phenoscribe.extract_symptoms import extract_symptoms
from phenoscribe.hpo_index import check_obo_version
from phenoscribe.match_hpo import match_hpo
from phenoscribe.output import write_docx, write_excel, write_mentions, write_personal_details
from phenoscribe.pii import find_personal_details
from phenoscribe.timeline import add_timeline, mention_rows
from phenoscribe.review import (
    APPROVED,
    AUTO,
    BULK,
    DONE,
    NEEDS_REVIEW,
    NO_REVIEW,
    Review,
    ReviewError,
    approve,
    clean_text,
    leaks,
    load_review,
    removed_values,
    save_review,
    warnings,
)
from phenoscribe.transcribe import (
    AUDIO_EXTENSIONS,
    TEXT_EXTENSIONS,
    resolve_backend,
    transcribe,
    transcribe_segments,
)

logger = logging.getLogger(__name__)


def process_recording(
    input_path: str,
    patient_id: str,
    config: Config,
    output_path: str | None = None,
    skip_transcription: bool = False,
    transcript_stem: str | None = None,
    on_step: Callable[[str], None] | None = None,
    reuse_transcript: bool = False,
    no_review: bool = False,
) -> list[dict]:
    """Process a single recording through the full pipeline.

    Steps:
    1. Transcribe audio (or read text)
    2. Find the personal details (review)
    3. Extract symptoms (LLM Call 1) from the approved clean text
    4. Match HPO codes (ChromaDB + LLM Call 2)
    5. Write Excel output

    The LLM calls read only an approved review. A review approved in the page
    for the same transcript is used as it is. Otherwise the review is saved
    for the page and NeedsReview is raised, unless ``no_review`` approves the
    detector output without a person (CLI --no-review).

    Args:
        input_path: Path to audio or text file.
        patient_id: Patient identifier.
        config: Pipeline configuration.
        output_path: Override output path (uses config default if None).
        skip_transcription: If True, read from saved transcript instead of running Whisper.
        transcript_stem: Filename stem of the cached transcript. Defaults to
            patient_id. Use this when the join key carries a prefix (MGA.467)
            but the cached transcript is named by bare stem (467.txt).
        on_step: Called with a plain label ("transcribing", ...) as each step starts.
        reuse_transcript: For audio, read the transcript saved for the same
            audio bytes, backend, model and language (see transcript_cache_path)
            instead of running Whisper again, when one exists.
        no_review: Approve the detector output without a person.

    Returns:
        List of HPO matches.
    """
    step = on_step or (lambda label: None)
    out = output_path or config.output.path
    out_dir = os.path.dirname(out) or "output"

    raw_text = read_transcript(
        input_path, patient_id, config, out_dir, step,
        skip_transcription=skip_transcription,
        transcript_stem=transcript_stem,
        reuse_transcript=reuse_transcript,
    )
    review = find_details(raw_text, Path(input_path).name, patient_id, config, out_dir, step)
    if no_review and review.status == NEEDS_REVIEW:
        approve(review, how=NO_REVIEW)
        save_review(review, out_dir)
        logger.warning(
            "[%s] Personal details approved without review (--no-review): %s",
            patient_id, ", ".join(e.value for e in removed_values(review)) or "none",
        )
    elif review.status == NEEDS_REVIEW or (review.approved_how == NO_REVIEW and not no_review):
        review.status = NEEDS_REVIEW
        save_review(review, out_dir)
        raise NeedsReview(
            f"{patient_id}: review the personal details in the page first, "
            "or run again with --no-review"
        )
    matches = code_review(review, config, out, step)
    review.status = DONE
    save_review(review, out_dir)
    return matches


class NeedsReview(Exception):
    """The personal details of a file wait for a person to approve them."""


def read_transcript(
    input_path: str,
    patient_id: str,
    config: Config,
    out_dir: str | Path,
    step: Callable[[str], None],
    skip_transcription: bool = False,
    transcript_stem: str | None = None,
    reuse_transcript: bool = False,
) -> str:
    """Step 1: the raw transcript of one file (Whisper, a saved transcript, or the text file)."""
    transcript_dir = os.path.join(str(out_dir), "transcripts")

    if skip_transcription:
        # Read from previously saved transcript. Prefer the explicit stem;
        # fall back to the patient_id, then to the prefix-stripped id so a
        # prefixed retry ("MGA.467") still finds "467.txt".
        candidates = []
        for name in (transcript_stem, patient_id):
            if name and name not in candidates:
                candidates.append(name)
        prefix = config.patient.id_prefix
        if prefix and patient_id.startswith(prefix):
            stripped = patient_id[len(prefix):]
            if stripped not in candidates:
                candidates.append(stripped)

        transcript_path = None
        for name in candidates:
            cand = os.path.join(transcript_dir, f"{name}.txt")
            if os.path.exists(cand):
                transcript_path = cand
                break
        if transcript_path is None:
            tried = ", ".join(f"{n}.txt" for n in candidates)
            raise FileNotFoundError(
                f"No saved transcript for {patient_id} in {transcript_dir} "
                f"(tried: {tried}). Run the full pipeline first to generate transcripts."
            )
        step("reading the saved transcript")
        logger.info("[%s] Step 1: Skipped (reading saved transcript)", patient_id)
        raw_text = Path(transcript_path).read_text(encoding="utf-8").strip()
    else:
        # Step 1: Transcribe (with optional diarization)
        is_audio = Path(input_path).suffix.lower() in AUDIO_EXTENSIONS
        use_diarization = config.diarization.enabled and is_audio
        cache = (
            transcript_cache_path(input_path, config, transcript_dir)
            if is_audio and not use_diarization
            else None
        )

        if cache is not None and reuse_transcript and cache.exists():
            step("reading the saved transcript")
            logger.info("[%s] Step 1: Reusing the transcript of the same audio: %s", patient_id, cache)
            raw_text = cache.read_text(encoding="utf-8")
        elif use_diarization:
            step("transcribing")
            if resolve_backend(config.transcription.backend) == "mlx":
                logger.warning(
                    "[%s] Diarization is not supported with the mlx backend yet; "
                    "falling back to faster-whisper for this transcript.",
                    patient_id,
                )
            logger.info("[%s] Step 1: Transcribing with diarization...", patient_id)
            raw_text, whisper_segments = transcribe_segments(
                input_path,
                model_name=config.transcription.model,
                language=config.transcription.language,
                device=config.transcription.device,
            )

            # Lazy import to avoid loading pyannote when not needed
            from phenoscribe.diarize import diarize, align_segments, format_transcript

            logger.info("[%s] Step 1b: Running speaker diarization...", patient_id)
            diarization_segments = diarize(
                input_path, num_speakers=config.diarization.num_speakers
            )
            aligned = align_segments(whisper_segments, diarization_segments)
            raw_text = format_transcript(aligned)
            logger.info("[%s] Diarized transcript: %d chars", patient_id, len(raw_text))
        else:
            if config.diarization.enabled and not is_audio:
                logger.info("[%s] Diarization enabled but input is text — skipping.", patient_id)
            step("transcribing" if is_audio else "reading the transcript")
            logger.info("[%s] Step 1: Transcribing...", patient_id)
            raw_text = transcribe(
                input_path,
                backend=config.transcription.backend,
                model_name=config.transcription.model,
                language=config.transcription.language,
                device=config.transcription.device,
            )
            if cache is not None:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(raw_text, encoding="utf-8")

        # Save raw transcript
        os.makedirs(transcript_dir, exist_ok=True)
        transcript_path = os.path.join(transcript_dir, f"{patient_id}.txt")
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        logger.info("[%s] Transcript saved: %s", patient_id, transcript_path)

    logger.info("[%s] Transcript: %d chars", patient_id, len(raw_text))
    return raw_text


def find_details(
    raw_text: str,
    source: str,
    patient_id: str,
    config: Config,
    out_dir: str | Path,
    step: Callable[[str], None] = lambda label: None,
    keep_types=None,
) -> Review:
    """Step 2: find the personal details and save the review for the page.

    An approved review of the same raw text is kept as it is, so the
    decisions of the person who approved it stay.
    """
    try:
        old = load_review(out_dir, patient_id)
        if old.status in (APPROVED, DONE) and old.raw_text == raw_text:
            logger.info("[%s] Step 2: using the approved review", patient_id)
            return old
    except ReviewError:
        pass
    step("removing personal details")
    logger.info("[%s] Step 2: Finding personal details...", patient_id)
    spans = find_personal_details(
        raw_text,
        model=config.pii.model,
        fallback_model=config.pii.fallback_model,
        min_score=config.pii.min_score,
    )
    keep = config.pii.keep_types if keep_types is None else keep_types
    review = Review.from_spans(source, patient_id, raw_text, spans, keep_types=keep)
    save_review(review, out_dir)
    logger.info("[%s] Personal details found: %d", patient_id, len(spans))
    return review


def write_clean_files(review: Review, out_dir: str | Path) -> Path:
    """clean/<patient_id>.txt, plus a .docx for a Word source. Returns the .txt path."""
    text, _ = clean_text(review)
    folder = Path(out_dir) / "clean"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{review.patient_id}.txt"
    path.write_text(text, encoding="utf-8")
    if Path(review.source).suffix.lower() == ".docx":
        write_docx(text, folder / f"{review.patient_id}.docx")
    return path


def code_review(
    review: Review,
    config: Config,
    excel_path: str | Path,
    step: Callable[[str], None] = lambda label: None,
) -> list[dict]:
    """Steps 3 to 5 on an approved review: symptoms, HPO codes, Excel rows."""
    patient_id = review.patient_id
    if review.status not in (APPROVED, DONE):
        raise ReviewError(f"{patient_id}: the personal details are not approved")
    if leaks(review):
        raise ReviewError(f"{patient_id}: a removed value is still in the clean text")

    # Startup guard: the obo on disk, the ChromaDB index and config must all
    # agree on the HPO release before we produce any codes.
    version = check_obo_version(config.paths.hpo_obo, config.hpo.release)
    logger.info("[%s] HPO release verified: %s", patient_id, version)

    excel_path = str(excel_path)
    out_dir = os.path.dirname(excel_path) or "output"
    write_clean_files(review, out_dir)
    safe_text, _ = clean_text(review)

    # Step 3: Extract symptoms
    step("finding symptoms")
    logger.info("[%s] Step 3: Extracting symptoms...", patient_id)
    symptoms = extract_symptoms(
        safe_text,
        provider=config.llm.provider,
        model=config.llm.model,
        ollama_base_url=config.llm.ollama_base_url,
    )
    logger.info("[%s] Symptoms extracted: %d", patient_id, len(symptoms))

    # Step 4 & 5: Match HPO codes (includes ChromaDB search + LLM judge)
    step("matching HPO codes")
    logger.info("[%s] Steps 4-5: Matching HPO codes...", patient_id)
    matches = match_hpo(
        symptoms,
        provider=config.llm.provider,
        model=config.llm.model,
        ollama_base_url=config.llm.ollama_base_url,
        chroma_path=config.paths.chroma_db,
        obo_path=config.paths.hpo_obo,
        embedding_model=config.hpo_index.embedding_model,
        on_symptom=lambda n, total: step(f"matching HPO codes · symptom {n} of {total}"),
    )
    logger.info("[%s] HPO codes matched: %d", patient_id, len(matches))

    # Step 6: Write output
    # Where each finding comes up in the interview (position %, time).
    timeline = mention_rows(matches, safe_text)
    add_timeline(matches, timeline)

    step("writing the Excel file")
    logger.info("[%s] Step 6: Writing output...", patient_id)
    write_excel(
        patient_id,
        matches,
        excel_path,
        fmt=config.output.format,
        hpo_release=version,
        propagate_ancestors=config.output.propagate_ancestors,
        obo_path=config.paths.hpo_obo,
        interview=interview_details(review.source, config.patient.id_prefix),
    )
    write_mentions(excel_path, patient_id, timeline)

    return matches


def derive_patient_id(stem: str, prefix: str) -> str:
    """Build the join key from a filename stem and the configured prefix.

    The transcript/audio files are named by bare stem (e.g. "467"); the
    ground truth keys patients as "MGA.467". Prepend the prefix so pipeline
    output joins against the GT. If the stem already carries the prefix, it
    is left untouched so re-runs stay idempotent.
    """
    if not prefix or stem.startswith(prefix):
        return stem
    return f"{prefix}{stem}"


_PATIENT_NUMBER = re.compile(r"^\s*(?:MGA\s*\.?\s*)?(\d{1,4})(?!\d)", re.IGNORECASE)
_FILE_DATE = re.compile(r"(?<!\d)(20\d\d)[-_.]?(\d\d)[-_.]?(\d\d)(?!\d)")


def interview_details(filename: str, prefix: str) -> dict:
    """Patient, interview type and date from Marc's file names, for the Excel.

    One patient can have several files ("MGA.363 interview.docx", "363 interview
    evolution 2026-10-15_11-09-09.docx"), and each file gets its own patient ID.
    The number at the start links the files of one patient. "evolution" marks a
    follow-up, "mail" an email; any other file is an interview. The date comes
    from the name (YYYYMMDD or YYYY-MM-DD), else it stays empty. No other part of
    the name goes to the Excel, because a name can hold a patient's name.
    """
    stem = Path(filename).stem
    lower = stem.lower()
    number = _PATIENT_NUMBER.match(stem)
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


def batch_patient_id(filename: str, prefix: str) -> str:
    """Patient ID for an uploaded file.

    Marc names recordings by patient number ("467.mp3"), which becomes
    "MGA.467" so the Excel joins against his code lists. Any other filename
    can carry a patient name ("martin-jean-2025-04-12.mp3"), which must stay
    out of the Excel, so it gets a short hash of the stem instead. The stem,
    so a recording and its transcript ("a.mp3", "a.txt") share one ID.
    """
    stem = Path(filename).stem
    if stem.isdigit():
        return derive_patient_id(stem, prefix)
    digest = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:8]
    return f"pt-{digest}"


def transcript_cache_path(audio_path: str, config: Config, transcript_dir: str) -> Path:
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
    digest.update(f"|{resolve_backend(t.backend)}|{t.model}|{t.language}".encode("utf-8"))
    return Path(transcript_dir) / "by-audio" / f"{digest.hexdigest()}.txt"


@dataclass
class FilePlan:
    """What run_batch does with one selected file.

    action: "transcribe", "reuse" (saved transcript of the same audio),
    "transcript" (a .txt or .docx, no Whisper) or "skip" (note says why).
    """

    path: str
    name: str
    patient_id: str
    action: str
    note: str = ""


def plan_batch(files: list[str], config: Config, out_dir: str | Path) -> list[FilePlan]:
    """Decide the work for each file, so the user ticks nothing.

    - Hidden files, Word lock files (~$x.docx) and our own *_pseudo.txt
      outputs are left out.
    - Other unsupported files are skipped.
    - Two audio files (or two transcripts) for one patient ID are both
      skipped: they would overwrite each other's transcript.
    - Audio plus a .txt or .docx for one patient: the text is used, the audio skipped.
    - Audio with a saved transcript of the same bytes: reuse it.
    """
    transcript_dir = os.path.join(str(out_dir), "transcripts")
    plans: list[FilePlan] = []
    by_patient: dict[str, list[FilePlan]] = {}
    for path in files:
        name = Path(path).name
        if name.startswith((".", "~$")) or Path(name).stem.endswith("_pseudo"):
            continue
        plan = FilePlan(str(path), name, batch_patient_id(name, config.patient.id_prefix), "")
        plans.append(plan)
        suffix = Path(name).suffix.lower()
        if suffix == ".doc":
            plan.action, plan.note = "skip", "Old Word file. Save it as .docx in Word, then add it again"
        elif suffix not in AUDIO_EXTENSIONS | TEXT_EXTENSIONS:
            plan.action, plan.note = "skip", "Not an audio file or transcript"
        else:
            by_patient.setdefault(plan.patient_id, []).append(plan)

    for patient_id, group in by_patient.items():
        texts = [p for p in group if Path(p.name).suffix.lower() in TEXT_EXTENSIONS]
        audios = [p for p in group if p not in texts]
        if len(texts) > 1 or len(audios) > 1:
            for p in group:
                p.action = "skip"
                p.note = f"Two files give patient {patient_id}. Rename one."
            continue
        for p in texts:
            p.action = "transcript"
        for p in audios:
            if texts:
                p.action, p.note = "skip", f"Uses {texts[0].name} instead"
            elif transcript_cache_path(p.path, config, transcript_dir).exists():
                p.action = "reuse"
            else:
                p.action = "transcribe"
    return plans


def new_results_path(out_dir: Path, now: datetime | None = None) -> Path:
    """A new dated Excel path for one run: results_YYYY-MM-DD_HHMM.xlsx.

    Each run gets its own file, so a rerun never adds rows to an older file
    and an older file that is open in Excel never blocks the save. A second
    run in the same minute gets a "_2" suffix.
    """
    base = f"results_{(now or datetime.now()):%Y-%m-%d_%H%M}"
    path = out_dir / f"{base}.xlsx"
    n = 2
    while path.exists():
        path = out_dir / f"{base}_{n}.xlsx"
        n += 1
    return path


def record_filename_mapping(out_dir: Path, mapping: dict[str, str]) -> None:
    """Merge the patient_id -> filename mapping into out_dir/filename_mapping.json.

    Lets the GP map an Excel row back to the source recording without putting the
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


# Share of one file's time spent before each step starts. Whisper takes most
# of it, so the bar moves on after transcription instead of sitting at 0 %.
STEP_PROGRESS = {
    "transcribing": 0.0,
    "reading the transcript": 0.0,
    "reading the saved transcript": 0.0,
    "removing personal details": 0.6,
    "finding symptoms": 0.7,
    "matching HPO codes": 0.8,
    "writing the Excel file": 0.95,
}


# The same for the two stages of the page: transcribe and find the personal
# details, then (after the review) code.
PREPARE_PROGRESS = {
    "transcribing": 0.0,
    "reading the transcript": 0.0,
    "reading the saved transcript": 0.0,
    "removing personal details": 0.85,
}
CODE_PROGRESS = {"finding symptoms": 0.0, "matching HPO codes": 0.4, "writing the Excel file": 0.9}


@dataclass
class FileResult:
    filename: str
    patient_id: str
    n_codes: int = 0
    matches: list[dict] = field(default_factory=list)
    error: Exception | None = None
    review: Review | None = None


@dataclass
class BatchResult:
    excel_path: Path | None
    files: list[FileResult] = field(default_factory=list)


def run_batch(
    files: list[str],
    config: Config,
    out_dir: str | Path,
    on_progress: Callable[[float, str], None] | None = None,
    retranscribe: frozenset[str] = frozenset(),
    no_review: bool = False,
) -> BatchResult:
    """Process a list of files into one new Excel file in ``out_dir``.

    Used by the CLI-style smoke run and tests. Without ``no_review``, a file
    whose personal details wait for a review fails with NeedsReview. The page
    uses prepare_batch and code_batch instead, with a review in between.

    plan_batch decides the work for each file; skipped files are not run.
    A saved transcript of the same audio is reused, except for the file
    names in ``retranscribe``. A file that fails does not stop the batch:
    its error is kept on its FileResult and the full trace goes to the log.

    on_progress is called with (fraction done, "2/5 · 467.mp3 · removing
    personal details") at each step.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    excel_path = new_results_path(out_dir)
    progress = on_progress or (lambda fraction, message: None)

    runnable = [p for p in plan_batch(files, config, out_dir) if p.action != "skip"]
    results: list[FileResult] = []
    total = len(runnable)
    for i, plan in enumerate(runnable):
        filepath, name = plan.path, plan.name
        result = FileResult(name, plan.patient_id)
        results.append(result)

        def on_step(label: str, i: int = i, name: str = name) -> None:
            done = (i + step_fraction(STEP_PROGRESS, label)) / total
            progress(done, f"{i + 1}/{total} · {name} · {label}")

        try:
            matches = process_recording(
                str(filepath),
                result.patient_id,
                config,
                output_path=str(excel_path),
                transcript_stem=Path(name).stem,
                on_step=on_step,
                reuse_transcript=name not in retranscribe,
                no_review=no_review,
            )
            result.matches = matches
            result.n_codes = len(matches)
        except Exception as e:  # noqa: BLE001 — one bad file must not stop the batch
            logger.exception("Failed processing %s", name)
            result.error = e

    if results:
        record_filename_mapping(out_dir, {r.patient_id: r.filename for r in results})
    progress(1.0, "Finished")
    return BatchResult(excel_path if excel_path.exists() else None, results)



def step_fraction(fractions: dict[str, float], label: str) -> float:
    """Share of one file done at ``label``.

    A label can carry a count after " · " ("matching HPO codes · symptom 12
    of 40"). The bar then moves from that step's share towards the next one.
    """
    base_label, _, detail = label.partition(" · ")
    base = fractions.get(base_label, 0.0)
    count = re.search(r"(\d+) of (\d+)", detail)
    if not count:
        return base
    following = [f for f in fractions.values() if f > base]
    end = min(following, default=1.0)
    done, total = int(count.group(1)) - 1, int(count.group(2))
    return base + (end - base) * done / max(total, 1)


def _stepper(progress, fractions, i, total, name):
    def on_step(label: str) -> None:
        progress((i + step_fraction(fractions, label)) / total, f"{i + 1}/{total} · {name} · {label}")
    return on_step


def prepare_batch(
    files: list[str],
    config: Config,
    out_dir: str | Path,
    on_progress: Callable[[float, str], None] | None = None,
    retranscribe: frozenset[str] = frozenset(),
    keep_types=None,
) -> list[FileResult]:
    """Stage 1 of the page: transcribe each file and find its personal details.

    Each file gets a review in <out_dir>/review/ for the page. No text leaves
    the computer in this stage. A file that fails keeps its error.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    progress = on_progress or (lambda fraction, message: None)
    runnable = [p for p in plan_batch(files, config, out_dir) if p.action != "skip"]
    results: list[FileResult] = []
    for i, plan in enumerate(runnable):
        result = FileResult(plan.name, plan.patient_id)
        results.append(result)
        step = _stepper(progress, PREPARE_PROGRESS, i, len(runnable), plan.name)
        try:
            raw_text = read_transcript(
                plan.path, plan.patient_id, config, out_dir, step,
                transcript_stem=Path(plan.name).stem,
                reuse_transcript=plan.name not in retranscribe,
            )
            review = find_details(raw_text, plan.name, plan.patient_id, config, out_dir, step,
                                  keep_types=keep_types)
            if review.status == DONE:  # coded before: back in the queue, still approved
                review.status = APPROVED
                save_review(review, out_dir)
            result.review = review
        except Exception as e:  # noqa: BLE001 — one bad file must not stop the batch
            logger.exception("Failed processing %s", plan.name)
            result.error = e
    if results:
        record_filename_mapping(out_dir, {r.patient_id: r.filename for r in results})
    progress(1.0, "Finished")
    return results


def approve_clear_files(reviews: list[Review], out_dir: str | Path) -> int:
    """Approve each review with no warning sign (review.warnings). Returns the count.

    The page checks only the doubtful files. A clear file still has its clean
    text written, and the report records it as approved with no check.
    """
    count = 0
    for review in reviews:
        if review.status == NEEDS_REVIEW and not warnings(review) and not leaks(review):
            approve(review, how=AUTO)
            save_review(review, out_dir)
            write_clean_files(review, out_dir)
            count += 1
    return count


def approve_the_rest(patient_ids: list[str], out_dir: str | Path) -> int:
    """"Approve all the rest": approve every review that still waits. Returns the count."""
    count = 0
    for patient_id in patient_ids:
        try:
            review = load_review(out_dir, patient_id)
        except ReviewError:
            continue
        if review.status == NEEDS_REVIEW and not leaks(review):
            approve(review, how=BULK)
            save_review(review, out_dir)
            write_clean_files(review, out_dir)
            count += 1
    return count


def code_batch(
    patient_ids: list[str],
    config: Config,
    out_dir: str | Path,
    on_progress: Callable[[float, str], None] | None = None,
) -> BatchResult:
    """Stage 2 of the page: code the approved reviews into one new Excel file.

    A review that is not approved is not coded. The Excel gets a "Personal
    details" sheet with the counts for each patient (no original values).
    """
    out_dir = Path(out_dir)
    excel_path = new_results_path(out_dir)
    progress = on_progress or (lambda fraction, message: None)
    results: list[FileResult] = []
    coded: list[Review] = []
    for i, patient_id in enumerate(patient_ids):
        result = FileResult(patient_id, patient_id)
        results.append(result)
        try:
            review = load_review(out_dir, patient_id)
            result.filename, result.review = review.source, review
            step = _stepper(progress, CODE_PROGRESS, i, len(patient_ids), review.source)
            result.matches = code_review(review, config, excel_path, step)
            result.n_codes = len(result.matches)
            coded.append(review)
        except Exception as e:  # noqa: BLE001 — one bad file must not stop the batch
            logger.exception("Failed coding %s", patient_id)
            result.error = e
    if excel_path.exists():
        write_personal_details(excel_path, coded)
        for review in coded:
            review.status = DONE
            save_review(review, out_dir)
    progress(1.0, "Finished")
    return BatchResult(excel_path if excel_path.exists() else None, results)


def finish_clean(patient_ids: list[str], out_dir: str | Path, now: datetime | None = None) -> Path | None:
    """The first tool: one ZIP of the approved clean texts plus the counts report.

    Returns the ZIP path, or None when no review is approved.
    """
    out_dir = Path(out_dir)
    reviews = []
    for patient_id in patient_ids:
        try:
            review = load_review(out_dir, patient_id)
        except ReviewError:
            continue
        if review.status in (APPROVED, DONE):
            reviews.append(review)
    if not reviews:
        return None
    stamp = f"{(now or datetime.now()):%Y-%m-%d_%H%M}"
    report = out_dir / f"personal_details_{stamp}.xlsx"
    n = 2
    while report.exists():
        report = out_dir / f"personal_details_{stamp}_{n}.xlsx"
        n += 1
    write_personal_details(report, reviews)
    archive = report.with_name(report.stem.replace("personal_details", "clean_texts") + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for review in reviews:
            txt = write_clean_files(review, out_dir)
            z.write(txt, f"clean/{txt.name}")
            docx_path = txt.with_suffix(".docx")
            if docx_path.exists() and Path(review.source).suffix.lower() == ".docx":
                z.write(docx_path, f"clean/{docx_path.name}")
            review.status = DONE
            save_review(review, out_dir)
        z.write(report, report.name)
    return archive
