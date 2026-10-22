"""Pipeline orchestrator — chains all processing steps."""

import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from phenoscribe.config import Config
from phenoscribe.extract_symptoms import extract_symptoms
from phenoscribe.hpo_index import check_obo_version
from phenoscribe.match_hpo import match_hpo
from phenoscribe.output import write_excel
from phenoscribe.pii import pseudonymize
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
    skip_pii: bool = False,
    transcript_stem: str | None = None,
    on_step: Callable[[str], None] | None = None,
    reuse_transcript: bool = False,
) -> list[dict]:
    """Process a single recording through the full pipeline.

    Steps:
    1. Transcribe audio (or read text)
    2. Pseudonymize PII
    3. Extract symptoms (LLM Call 1)
    4. Match HPO codes (ChromaDB + LLM Call 2)
    5. Write Excel output

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

    Returns:
        List of HPO matches.
    """
    step = on_step or (lambda label: None)

    # Startup guard: the obo on disk, the ChromaDB index and config must all
    # agree on the HPO release before we produce any codes.
    version = check_obo_version(config.paths.hpo_obo, config.hpo.release)
    logger.info("[%s] HPO release verified: %s", patient_id, version)

    out = output_path or config.output.path
    out_dir = os.path.dirname(out) or "output"
    transcript_dir = os.path.join(out_dir, "transcripts")

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

    # Step 2: Pseudonymize (optional)
    if skip_pii:
        logger.warning(
            "[%s] Step 2: SKIPPED — PII pseudonymization disabled. "
            "Raw text (may contain patient identifiers) will be sent to the LLM.",
            patient_id,
        )
        safe_text = raw_text
        pii_mapping: dict = {}
    else:
        step("removing personal details")
        logger.info("[%s] Step 2: Pseudonymizing PII...", patient_id)
        safe_text, pii_mapping = pseudonymize(
            raw_text,
            model=config.pii.model,
            fallback_model=config.pii.fallback_model,
            min_score=config.pii.min_score,
        )
        logger.info("[%s] PII entities replaced: %d", patient_id, len(pii_mapping))

        # Save pseudonymized transcript
        pseudo_dir = os.path.join(out_dir, "pseudo")
        os.makedirs(pseudo_dir, exist_ok=True)
        pseudo_path = os.path.join(pseudo_dir, f"{patient_id}.txt")
        with open(pseudo_path, "w", encoding="utf-8") as f:
            f.write(safe_text)
        logger.info("[%s] Pseudonymized transcript saved: %s", patient_id, pseudo_path)

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
    )
    logger.info("[%s] HPO codes matched: %d", patient_id, len(matches))

    # Step 6: Write output
    step("writing the Excel file")
    logger.info("[%s] Step 6: Writing output...", patient_id)
    write_excel(
        patient_id,
        matches,
        out,
        fmt=config.output.format,
        hpo_release=version,
        propagate_ancestors=config.output.propagate_ancestors,
        obo_path=config.paths.hpo_obo,
    )

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
    "transcript" (a .txt, no Whisper) or "skip" (note says why).
    """

    path: str
    name: str
    patient_id: str
    action: str
    note: str = ""


def plan_batch(files: list[str], config: Config, out_dir: str | Path) -> list[FilePlan]:
    """Decide the work for each file, so the user ticks nothing.

    - Hidden files and our own *_pseudo.txt outputs are left out.
    - Other unsupported files are skipped.
    - Two audio files (or two transcripts) for one patient ID are both
      skipped: they would overwrite each other's transcript.
    - Audio plus a .txt for one patient: the .txt is used, the audio skipped.
    - Audio with a saved transcript of the same bytes: reuse it.
    """
    transcript_dir = os.path.join(str(out_dir), "transcripts")
    plans: list[FilePlan] = []
    by_patient: dict[str, list[FilePlan]] = {}
    for path in files:
        name = Path(path).name
        if name.startswith(".") or Path(name).stem.endswith("_pseudo"):
            continue
        plan = FilePlan(str(path), name, batch_patient_id(name, config.patient.id_prefix), "")
        plans.append(plan)
        if Path(name).suffix.lower() not in AUDIO_EXTENSIONS | TEXT_EXTENSIONS:
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


@dataclass
class FileResult:
    filename: str
    patient_id: str
    n_codes: int = 0
    matches: list[dict] = field(default_factory=list)
    error: Exception | None = None


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
) -> BatchResult:
    """Process a list of files into one new Excel file in ``out_dir``.

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
            progress(i / total, f"{i + 1}/{total} · {name} · {label}")

        try:
            matches = process_recording(
                str(filepath),
                result.patient_id,
                config,
                output_path=str(excel_path),
                transcript_stem=Path(name).stem,
                on_step=on_step,
                reuse_transcript=name not in retranscribe,
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

