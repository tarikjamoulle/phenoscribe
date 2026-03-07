"""Pipeline orchestrator — chains all processing steps."""

import logging

from phenoscribe.config import Config
from phenoscribe.extract_symptoms import extract_symptoms
from phenoscribe.match_hpo import match_hpo
from phenoscribe.output import write_excel
from phenoscribe.pii import pseudonymize
from phenoscribe.transcribe import transcribe

logger = logging.getLogger(__name__)


def process_recording(
    input_path: str,
    patient_id: str,
    config: Config,
    output_path: str | None = None,
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

    Returns:
        List of HPO matches.
    """
    out = output_path or config.output.path

    # Step 1: Transcribe
    logger.info("[%s] Step 1: Transcribing...", patient_id)
    raw_text = transcribe(
        input_path,
        model_name=config.transcription.model,
        language=config.transcription.language,
        device=config.transcription.device,
    )
    logger.info("[%s] Transcript: %d chars", patient_id, len(raw_text))

    # Step 2: Pseudonymize
    logger.info("[%s] Step 2: Pseudonymizing PII...", patient_id)
    safe_text, pii_mapping = pseudonymize(raw_text)
    logger.info("[%s] PII entities replaced: %d", patient_id, len(pii_mapping))

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
    )
    logger.info("[%s] HPO codes matched: %d", patient_id, len(matches))

    # Step 6: Write output
    logger.info("[%s] Step 6: Writing output...", patient_id)
    write_excel(patient_id, matches, out, fmt=config.output.format)

    return matches
