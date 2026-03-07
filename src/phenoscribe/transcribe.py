"""Transcription module — faster-whisper for audio, passthrough for text."""

import logging
import time
from pathlib import Path

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}
TEXT_EXTENSIONS = {".txt"}

_model_cache: dict[str, WhisperModel] = {}


def _get_model(model_name: str, device: str) -> WhisperModel:
    """Get or create a cached WhisperModel."""
    key = f"{model_name}:{device}"
    if key not in _model_cache:
        logger.info("Loading Whisper model '%s' on %s...", model_name, device)
        _model_cache[key] = WhisperModel(model_name, device=device)
    return _model_cache[key]


def transcribe(
    input_path: str,
    model_name: str = "large-v3",
    language: str = "fr",
    device: str = "cpu",
) -> str:
    """Transcribe an audio file or read a text file.

    Args:
        input_path: Path to audio file (.wav, .mp3, .m4a, .ogg) or text file (.txt).
        model_name: Whisper model name (only used for audio).
        language: Audio language code (only used for audio).
        device: Device for Whisper inference: cpu, cuda, or auto.

    Returns:
        Transcript text.
    """
    path = Path(input_path)
    suffix = path.suffix.lower()

    if suffix in TEXT_EXTENSIONS:
        logger.info("Reading text file: %s", path.name)
        return path.read_text(encoding="utf-8").strip()

    if suffix not in AUDIO_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{suffix}'. "
            f"Supported: {AUDIO_EXTENSIONS | TEXT_EXTENSIONS}"
        )

    logger.info("Transcribing audio: %s", path.name)
    start = time.time()

    model = _get_model(model_name, device)
    segments, info = model.transcribe(str(path), language=language)
    text = " ".join(segment.text.strip() for segment in segments)

    elapsed = time.time() - start
    logger.info(
        "Transcription complete: %.1fs audio, took %.1fs (%.1fx realtime)",
        info.duration,
        elapsed,
        info.duration / elapsed if elapsed > 0 else 0,
    )

    return text
