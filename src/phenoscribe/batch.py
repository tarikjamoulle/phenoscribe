"""Many files in one run: plan the work, then run the steps of pipeline.py for each file.

Two ways to run a batch:

- run_batch (CLI and smoke run): each file from start to codes, in one go.
- the page, in two stages with a review between them: prepare_batch
  (transcribe, find the personal details), then code_batch (the HPO codes) or
  finish_clean (the clean texts only).

A file that fails never stops the batch: its error stays on its FileResult and
the full trace goes to the log.
"""

import logging
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from phenoscribe.config import Config
from phenoscribe.findings import Finding
from phenoscribe.naming import (
    batch_patient_id,
    dated_path,
    new_results_path,
    record_filename_mapping,
    transcript_cache_path,
)
from phenoscribe.output import ResultsWorkbook, write_personal_details
from phenoscribe.pipeline import (
    NeedsReview,
    code_review,
    find_details,
    process_recording,
    read_transcript,
    write_clean_files,
)
from phenoscribe.review import (
    APPROVED,
    AUTO,
    BULK,
    DONE,
    NEEDS_REVIEW,
    Review,
    ReviewError,
    approve,
    blocking_warnings,
    leaks,
    load_review,
    reopen_if_blocked,
    save_review,
)
from phenoscribe.transcribe import AUDIO_EXTENSIONS, TEXT_EXTENSIONS

logger = logging.getLogger(__name__)

Progress = Callable[[float, str], None]


@dataclass
class FilePlan:
    """What the batch does with one selected file.

    action: "transcribe", "reuse" (saved transcript of the same audio),
    "transcript" (a .txt or .docx, no Whisper) or "skip" (note says why).
    """

    path: str
    name: str
    patient_id: str
    action: str
    note: str = ""


@dataclass
class FileResult:
    filename: str
    patient_id: str
    n_codes: int = 0
    matches: list[Finding] = field(default_factory=list)
    error: Exception | None = None
    review: Review | None = None


@dataclass
class BatchResult:
    excel_path: Path | None
    files: list[FileResult] = field(default_factory=list)
    skipped: list[FilePlan] = field(default_factory=list)  # plan_batch "skip", with the note


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
    transcript_dir = Path(out_dir) / "transcripts"
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


# --- progress ---------------------------------------------------------------------------

# The share of one file's time spent before each step starts. Whisper takes
# most of it, so the bar moves on after transcription instead of sitting at 0 %.
# The three tables are different on purpose: run_batch spreads all the steps
# of a file over the bar, and each stage of the page spreads its own steps.
STEP_PROGRESS = {
    "transcribing": 0.0,
    "reading the transcript": 0.0,
    "reading the saved transcript": 0.0,
    "removing personal details": 0.6,
    "finding the speakers": 0.65,
    "finding symptoms": 0.7,
    "matching HPO codes": 0.8,
    "writing the Excel file": 0.95,
}
PREPARE_PROGRESS = {
    "transcribing": 0.0,
    "reading the transcript": 0.0,
    "reading the saved transcript": 0.0,
    "removing personal details": 0.85,
}
CODE_PROGRESS = {
    "finding the speakers": 0.0,
    "finding symptoms": 0.1,
    "matching HPO codes": 0.4,
    "writing the Excel file": 0.9,
}


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


def _stepper(progress: Progress, fractions: dict[str, float], i: int, total: int, name: str):
    """The on_step of file ``i``: "2/5 · 001.mp3 · removing personal details" at its place on the bar."""
    def on_step(label: str) -> None:
        progress((i + step_fraction(fractions, label)) / total, f"{i + 1}/{total} · {name} · {label}")
    return on_step


def _no_progress(fraction: float, message: str) -> None:
    pass


# --- the runs -----------------------------------------------------------------------------


def run_batch(
    files: list[str],
    config: Config,
    out_dir: str | Path,
    on_progress: Progress | None = None,
    retranscribe: frozenset[str] = frozenset(),
    no_review: bool = False,
) -> BatchResult:
    """Process a list of files into one new Excel file in ``out_dir``.

    The engine of `phenoscribe process` and the smoke run. Without ``no_review``, a file
    whose personal details wait for a review fails with NeedsReview.

    plan_batch decides the work for each file; skipped files are not run.
    A saved transcript of the same audio is reused, except for the file
    names in ``retranscribe``.

    on_progress is called with (fraction done, "2/5 · 001.mp3 · removing
    personal details") at each step.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    book = ResultsWorkbook.for_config(new_results_path(out_dir), config)
    progress = on_progress or _no_progress

    plans = plan_batch(files, config, out_dir)
    runnable = [p for p in plans if p.action != "skip"]
    results: list[FileResult] = []
    for i, plan in enumerate(runnable):
        result = FileResult(plan.name, plan.patient_id)
        results.append(result)
        try:
            result.matches = process_recording(
                plan.path, plan.patient_id, config, book,
                on_step=_stepper(progress, STEP_PROGRESS, i, len(runnable), plan.name),
                reuse_transcript=plan.name not in retranscribe,
                no_review=no_review,
            )
            result.n_codes = len(result.matches)
        except NeedsReview as e:
            logger.info("%s", e)
            result.error = e
        except Exception as e:  # one bad file must not stop the batch
            logger.exception("Failed processing %s", plan.name)
            result.error = e

    if results:
        record_filename_mapping(out_dir, {r.patient_id: r.filename for r in results})
    _finish_workbook(book, load_reviews(out_dir, [r.patient_id for r in results if r.error is None]))
    progress(1.0, "Finished")
    skipped = [p for p in plans if p.action == "skip"]
    return BatchResult(book.path if book.path.exists() else None, results, skipped)


def _finish_workbook(book: ResultsWorkbook, coded: list[Review]) -> None:
    """The "Personal details" sheet with the counts of each coded file, when the workbook exists."""
    if book.path.exists():
        book.add_personal_details(coded)
        book.save()


def prepare_batch(
    files: list[str],
    config: Config,
    out_dir: str | Path,
    on_progress: Progress | None = None,
    retranscribe: frozenset[str] = frozenset(),
    keep_types=None,
) -> list[FileResult]:
    """Stage 1 of the page: transcribe each file and find its personal details.

    Each file gets a review in <out_dir>/review/ for the page. No text leaves
    the computer in this stage.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    progress = on_progress or _no_progress
    runnable = [p for p in plan_batch(files, config, out_dir) if p.action != "skip"]
    results: list[FileResult] = []
    for i, plan in enumerate(runnable):
        result = FileResult(plan.name, plan.patient_id)
        results.append(result)
        step = _stepper(progress, PREPARE_PROGRESS, i, len(runnable), plan.name)
        try:
            raw_text = read_transcript(
                plan.path, plan.patient_id, config, out_dir, step,
                reuse_transcript=plan.name not in retranscribe,
            )
            review = find_details(raw_text, plan.name, plan.patient_id, config, out_dir, step,
                                  keep_types=keep_types)
            if review.status == DONE:  # coded before: back in the queue, still approved
                review.status = APPROVED
                save_review(review, out_dir)
            if reopen_if_blocked(review):  # approved with no check, and a name can be left
                save_review(review, out_dir)
            result.review = review
        except Exception as e:  # one bad file must not stop the batch
            logger.exception("Failed processing %s", plan.name)
            result.error = e
    if results:
        record_filename_mapping(out_dir, {r.patient_id: r.filename for r in results})
    progress(1.0, "Finished")
    return results


def load_reviews(out_dir: str | Path, patient_ids: list[str]) -> list[Review]:
    """The readable reviews of ``patient_ids``, in that order. A missing or damaged file is left out."""
    reviews = []
    for patient_id in patient_ids:
        try:
            reviews.append(load_review(out_dir, patient_id))
        except ReviewError:
            continue
    return reviews


def approve_unblocked(reviews: list[Review], out_dir: str | Path, how: str) -> int:
    """Approve each waiting review with no blocking warning sign and no leak. Returns the count.

    A review with a blocking sign (review.blocking_warnings: a detail can still
    be in the clean text) waits for a person. "Unsure about" (the value is
    removed already) and "No personal detail found" do not stop a file: the
    same rule as batch mode. ``how`` goes to the report. An approved file gets
    its clean text.
    """
    count = 0
    for review in reviews:
        if review.status == NEEDS_REVIEW and not blocking_warnings(review) and not leaks(review):
            approve(review, how=how)
            save_review(review, out_dir)
            write_clean_files(review, out_dir)
            count += 1
    return count


def approve_clear_files(reviews: list[Review], out_dir: str | Path) -> int:
    """After stage 1, the page approves the files with no warning sign that blocks."""
    return approve_unblocked(reviews, out_dir, AUTO)


def approve_the_rest(patient_ids: list[str], out_dir: str | Path) -> int:
    """"Approve all the rest": every waiting file with no warning sign that blocks."""
    return approve_unblocked(load_reviews(out_dir, patient_ids), out_dir, BULK)


def code_batch(
    patient_ids: list[str],
    config: Config,
    out_dir: str | Path,
    on_progress: Progress | None = None,
) -> BatchResult:
    """Stage 2 of the page: code the approved reviews into one new Excel file.

    A review that is not approved is not coded. The Excel gets a "Personal
    details" sheet with the counts for each patient (no original values).
    """
    out_dir = Path(out_dir)
    book = ResultsWorkbook.for_config(new_results_path(out_dir), config)
    progress = on_progress or _no_progress
    results: list[FileResult] = []
    coded: list[Review] = []
    for i, patient_id in enumerate(patient_ids):
        result = FileResult(patient_id, patient_id)
        results.append(result)
        try:
            review = load_review(out_dir, patient_id)
            result.filename, result.review = review.source, review
            step = _stepper(progress, CODE_PROGRESS, i, len(patient_ids), review.source)
            result.matches = code_review(review, config, book, step)
            result.n_codes = len(result.matches)
            coded.append(review)
        except Exception as e:  # one bad file must not stop the batch
            logger.exception("Failed coding %s", patient_id)
            result.error = e
    _finish_workbook(book, coded)
    for review in coded:
        review.status = DONE
        save_review(review, out_dir)
    progress(1.0, "Finished")
    return BatchResult(book.path if book.path.exists() else None, results)


def finish_clean(patient_ids: list[str], out_dir: str | Path, now: datetime | None = None) -> Path | None:
    """The removal-only tool: one ZIP of the approved clean texts plus the counts report.

    Returns the ZIP path, or None when no review is approved.
    """
    out_dir = Path(out_dir)
    reviews = [r for r in load_reviews(out_dir, patient_ids) if r.status in (APPROVED, DONE)]
    if not reviews:
        return None
    report = dated_path(out_dir, "personal_details", ".xlsx", now)
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
