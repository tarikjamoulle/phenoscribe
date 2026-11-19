"""The steps for one file: read it, remove the personal details, then code the approved review.

batch.py runs these steps over many files.
"""

import logging
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from phenoscribe.config import Config
from phenoscribe.extract_symptoms import extract_symptoms
from phenoscribe.findings import Finding
from phenoscribe.hpo_index import check_obo_version
from phenoscribe.match_hpo import flag_unstable, match_hpo
from phenoscribe.naming import interview_details, transcript_cache_path
from phenoscribe.output import ResultsWorkbook, write_docx
from phenoscribe.pii import detect_personal_details
from phenoscribe.review import (
    APPROVED,
    CHECKED,
    DONE,
    NEEDS_REVIEW,
    NO_REVIEW,
    Review,
    ReviewError,
    approve,
    blocking_warnings,
    clean_text,
    leaks,
    load_review,
    removed_values,
    save_review,
)
from phenoscribe.speakers import SpeakerLabelError, check_speakers, label_speakers
from phenoscribe.timeline import add_timeline, mention_rows
from phenoscribe.transcribe import AUDIO_EXTENSIONS, read_input

logger = logging.getLogger(__name__)

Step = Callable[[str], None]


def _no_step(label: str) -> None:
    pass


class NeedsReview(Exception):
    """The personal details of a file wait for a person to approve them."""


def process_recording(
    input_path: str,
    patient_id: str,
    config: Config,
    book: ResultsWorkbook,
    on_step: Step | None = None,
    reuse_transcript: bool = False,
    no_review: bool = False,
) -> list[Finding]:
    """Process one recording or transcript into the rows of ``book``.

    Steps: transcribe (or read the text), find the personal details, then on
    an approved review extract the symptoms, match the HPO codes and write the
    rows. The folder of the workbook holds the transcripts, reviews and clean
    texts.

    The LLM calls read only an approved review. A review approved in the page
    for the same transcript is used as it is. Otherwise the review is saved
    for the page and NeedsReview is raised, unless ``no_review`` approves the
    detector output without a person (CLI --no-review).

    on_step gets a plain label ("transcribing", ...) as each step starts.
    reuse_transcript reads the transcript saved for the same audio bytes
    (naming.transcript_cache_path) instead of running Whisper again.
    """
    step = on_step or _no_step
    out_dir = book.path.parent

    raw_text = read_transcript(input_path, patient_id, config, out_dir, step, reuse_transcript)
    review = find_details(raw_text, Path(input_path).name, patient_id, config, out_dir, step)
    if no_review and review.status == NEEDS_REVIEW:
        # Batch mode stops a file only when a detail can still be in the text
        # (review.blocking_warnings: leak check, a word after a title, and
        # "Unsure about" when only one model ran).
        blocking = blocking_warnings(review)
        if blocking:
            save_review(review, out_dir)
            raise NeedsReview(
                f"{patient_id}: stopped, not sent to the AI provider. A name or a place can "
                f"still be in the clean text ({'; '.join(s.text for s in blocking)}). Check the file in the page."
            )
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
    findings = code_review(review, config, book, step)
    review.status = DONE
    save_review(review, out_dir)
    return findings


def read_transcript(
    input_path: str,
    patient_id: str,
    config: Config,
    out_dir: str | Path,
    step: Step,
    reuse_transcript: bool = False,
) -> str:
    """Step 1: the raw transcript of one file (Whisper, a saved transcript, or the text file).

    The transcript of an audio file is kept under the hash of its bytes
    (naming.transcript_cache_path), and every transcript as
    transcripts/<patient_id>.txt.
    """
    transcript_dir = Path(out_dir) / "transcripts"
    is_audio = Path(input_path).suffix.lower() in AUDIO_EXTENSIONS
    cache = transcript_cache_path(input_path, config, transcript_dir) if is_audio else None

    if cache is not None and reuse_transcript and cache.exists():
        step("reading the saved transcript")
        logger.info("[%s] Step 1: Reusing the transcript of the same audio: %s", patient_id, cache)
        raw_text = cache.read_text(encoding="utf-8")
    else:
        step("transcribing" if is_audio else "reading the transcript")
        logger.info("[%s] Step 1: Transcribing...", patient_id)
        t = config.transcription
        raw_text = read_input(input_path, backend=t.backend, model_name=t.model,
                              language=t.language, device=t.device)
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(raw_text, encoding="utf-8")

    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_dir / f"{patient_id}.txt"
    transcript_path.write_text(raw_text, encoding="utf-8")
    logger.info("[%s] Transcript saved: %s (%d chars)", patient_id, transcript_path, len(raw_text))
    return raw_text


def find_details(
    raw_text: str,
    source: str,
    patient_id: str,
    config: Config,
    out_dir: str | Path,
    step: Step = _no_step,
    keep_types=None,
) -> Review:
    """Step 2: find the personal details and save the review for the page.

    An approved review of the same raw text is kept as it is, so the
    decisions of the person who approved it stay.
    """
    language = config.transcription.language
    try:
        old = load_review(out_dir, patient_id)
        # Another language runs other models: find the details again.
        if old.status in (APPROVED, DONE) and old.raw_text == raw_text and (old.language or "fr") == language:
            logger.info("[%s] Step 2: using the approved review", patient_id)
            return old
    except ReviewError:
        pass
    step("removing personal details")
    logger.info("[%s] Step 2: Finding personal details...", patient_id)
    spans, ran = detect_personal_details(
        raw_text,
        models=config.pii.models_for(language),
        fallback_model=config.pii.fallback_for(language),
        min_score=config.pii.min_score,
    )
    keep = config.pii.keep_types if keep_types is None else keep_types
    review = Review.from_spans(source, patient_id, raw_text, spans, keep_types=keep)
    review.language = language
    review.models = ran
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


def code_review(review: Review, config: Config, book: ResultsWorkbook, step: Step = _no_step) -> list[Finding]:
    """Steps 3 to 6 on an approved review: speakers, symptoms, HPO codes, the rows of ``book``."""
    patient_id = review.patient_id
    _check_gate(review, config)

    # The obo on disk, the ChromaDB index and config must all agree on the HPO
    # release before any code is written.
    version = check_obo_version(config.paths.hpo_obo, config.hpo.release)
    logger.info("[%s] HPO release verified: %s", patient_id, version)

    out_dir = book.path.parent
    write_clean_files(review, out_dir)
    safe_text, _ = clean_text(review)
    language = review.language or "fr"  # a review file without a language is French

    # Step 3a: who speaks. The labeled text is saved next to the clean text.
    labeled = ""
    if config.llm.speaker_labels:
        step("finding the speakers")
        logger.info("[%s] Step 3a: Labeling the speakers...", patient_id)
        labeled = find_speakers(safe_text, patient_id, config, out_dir, language)

    # Step 3b: the symptoms, from the labeled text when there is one.
    step("finding symptoms")
    logger.info("[%s] Step 3: Extracting symptoms...", patient_id)
    symptoms = extract_symptoms(labeled or safe_text, llm=config.llm, runs=config.llm.extraction_runs,
                                language=language)
    logger.info("[%s] Symptoms extracted: %d", patient_id, len(symptoms))

    # Steps 4 and 5: the shortlist search and the LLM judge.
    step("matching HPO codes")
    logger.info("[%s] Steps 4-5: Matching HPO codes...", patient_id)
    findings = match_hpo(
        symptoms,
        llm=config.llm,
        chroma_path=config.paths.chroma_db,
        obo_path=config.paths.hpo_obo,
        embedding_model=config.hpo_index.embedding_model,
        on_symptom=lambda n, total: step(f"matching HPO codes · symptom {n} of {total}"),
    )
    logger.info("[%s] HPO codes matched: %d", patient_id, len(findings))

    # The code's own checks: who says each quote, and which findings are in
    # every extraction run. Both can set needs_review.
    if labeled:
        check_speakers(findings, labeled, language)
        spoken = Counter(f.get("spoken_by") or "no label" for f in findings)
        logger.info("[%s] Spoken by: %s", patient_id, ", ".join(f"{k} {v}" for k, v in sorted(spoken.items())))
    unstable = flag_unstable(findings)
    if config.llm.extraction_runs > 1:
        logger.info("[%s] Findings not in every run: %d", patient_id, unstable)

    # Step 6: where each finding comes up (position %, time), then the rows.
    mentions = mention_rows(findings, safe_text)
    add_timeline(findings, mentions)
    step("writing the Excel file")
    logger.info("[%s] Step 6: Writing output...", patient_id)
    book.add_patient(
        patient_id,
        findings,
        mentions=mentions,
        interview=interview_details(review.source, config.patient.id_prefix),
        hpo_release=version,
        privacy={"language": language, "models": review.models or ["not recorded"]},
    )
    return findings


def _check_gate(review: Review, config: Config) -> None:
    """The last gate before the LLM: an approved review, no leak, and a person's check for a blocking sign.

    A file with a blocking warning sign (a detail can still be in the clean
    text) goes to a cloud provider only after a person checked it.
    """
    if review.status not in (APPROVED, DONE):
        raise ReviewError(f"{review.patient_id}: the personal details are not approved")
    if leaks(review):
        raise ReviewError(f"{review.patient_id}: a removed value is still in the clean text")
    blocking = blocking_warnings(review)
    if blocking and config.llm.is_cloud and review.approved_how != CHECKED:
        raise ReviewError(
            f"{review.patient_id}: not sent to the AI provider. A name or a place can still be in the "
            f"clean text ({'; '.join(s.text for s in blocking)}). Check the file in the page."
        )


def find_speakers(safe_text: str, patient_id: str, config: Config, out_dir: str | Path,
                  language: str = "fr") -> str:
    """The clean text with D:/P: labels, saved as speakers/<patient_id>.txt.

    When the model's copy loses words twice, the file is coded from the plain
    text, with a warning: the "Spoken by" column then stays empty.
    """
    try:
        labeled = label_speakers(safe_text, llm=config.llm, language=language)
    except SpeakerLabelError as e:
        logger.warning("[%s] No speaker labels (%s). Coding the plain text.", patient_id, e)
        return ""
    folder = Path(out_dir) / "speakers"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{patient_id}.txt").write_text(labeled, encoding="utf-8")
    logger.info("[%s] Speaker labels: %d turns", patient_id, len(labeled.splitlines()))
    return labeled
