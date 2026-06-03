"""Pipeline orchestrator — chains all processing steps."""

import logging
import os
from pathlib import Path

from phenoscribe.config import Config
from phenoscribe.extract_symptoms import extract_symptoms
from phenoscribe.hpo_index import check_obo_version
from phenoscribe.match_hpo import match_hpo
from phenoscribe.output import write_excel
from phenoscribe.pii import pseudonymize
from phenoscribe.transcribe import transcribe, transcribe_segments, AUDIO_EXTENSIONS

logger = logging.getLogger(__name__)


def process_recording(
    input_path: str,
    patient_id: str,
    config: Config,
    output_path: str | None = None,
    skip_transcription: bool = False,
    skip_pii: bool = False,
    transcript_stem: str | None = None,
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

    Returns:
        List of HPO matches.
    """
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
        logger.info("[%s] Step 1: Skipped (reading saved transcript)", patient_id)
        raw_text = Path(transcript_path).read_text(encoding="utf-8").strip()
    else:
        # Step 1: Transcribe (with optional diarization)
        is_audio = Path(input_path).suffix.lower() in AUDIO_EXTENSIONS
        use_diarization = config.diarization.enabled and is_audio

        if use_diarization:
            if config.transcription.backend == "mlx":
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
            logger.info("[%s] Step 1: Transcribing...", patient_id)
            raw_text = transcribe(
                input_path,
                backend=config.transcription.backend,
                model_name=config.transcription.model,
                language=config.transcription.language,
                device=config.transcription.device,
            )

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
        logger.info("[%s] Step 2: Pseudonymizing PII...", patient_id)
        safe_text, pii_mapping = pseudonymize(raw_text)
        logger.info("[%s] PII entities replaced: %d", patient_id, len(pii_mapping))

        # Save pseudonymized transcript
        pseudo_dir = os.path.join(out_dir, "pseudo")
        os.makedirs(pseudo_dir, exist_ok=True)
        pseudo_path = os.path.join(pseudo_dir, f"{patient_id}.txt")
        with open(pseudo_path, "w", encoding="utf-8") as f:
            f.write(safe_text)
        logger.info("[%s] Pseudonymized transcript saved: %s", patient_id, pseudo_path)

    # Step 3: Extract symptoms
    logger.info("[%s] Step 3: Extracting symptoms...", patient_id)
    symptoms = extract_symptoms(
        safe_text,
        provider=config.llm.provider,
        model=config.llm.model,
        ollama_base_url=config.llm.ollama_base_url,
    )
    logger.info("[%s] Symptoms extracted: %d", patient_id, len(symptoms))

    # Step 4 & 5: Match HPO codes (includes ChromaDB search + LLM judge)
    logger.info("[%s] Steps 4-5: Matching HPO codes...", patient_id)
    matches = match_hpo(
        symptoms,
        provider=config.llm.provider,
        model=config.llm.model,
        ollama_base_url=config.llm.ollama_base_url,
        chroma_path=config.paths.chroma_db,
        obo_path=config.paths.hpo_obo,
    )
    logger.info("[%s] HPO codes matched: %d", patient_id, len(matches))

    # Step 6: Write output
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
