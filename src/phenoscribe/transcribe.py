"""Transcription module — faster-whisper, mlx-whisper, or passthrough for text."""

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}
TEXT_EXTENSIONS = {".txt"}

# Default HF repo when caller passes a bare faster-whisper model name to the mlx backend.
# NOTE: distil-* variants are English-only — do not use them on non-English audio
# (verified 2026-06-03: distil-large-v3 produced English-soup gibberish on French input).
_MLX_MODEL_ALIASES = {
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "distil-large-v3": "mlx-community/distil-whisper-large-v3",  # English only
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
}

_faster_whisper_cache: dict[str, object] = {}


def transcribe(
    input_path: str,
    *,
    backend: str = "faster-whisper",
    model_name: str = "large-v3",
    language: str = "fr",
    device: str = "cpu",
) -> str:
    """Transcribe an audio file or read a text file.

    Args:
        input_path: Path to audio file (.wav, .mp3, .m4a, .ogg, .flac) or text file (.txt).
        backend: "faster-whisper" (CPU/CUDA) or "mlx" (Apple Silicon Metal).
        model_name: Whisper model name. For mlx, also accepts an HF repo path directly;
            bare names like "large-v3" are mapped to the matching mlx-community repo.
        language: Audio language code.
        device: Device hint for faster-whisper (cpu | cuda | auto). Ignored by mlx.

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

    if backend == "mlx":
        return _transcribe_mlx(path, model_name, language)
    if backend == "faster-whisper":
        return _transcribe_faster_whisper(path, model_name, language, device)
    raise ValueError(
        f"Unknown transcription backend '{backend}'. Use 'faster-whisper' or 'mlx'."
    )


def transcribe_segments(
    input_path: str,
    model_name: str = "large-v3",
    language: str = "fr",
    device: str = "cpu",
) -> tuple[str, list[dict]]:
    """Transcribe an audio file and return text with segment timestamps.

    Always uses faster-whisper — the diarization alignment downstream expects
    its segment format. The mlx backend does not support diarization yet.
    """
    path = Path(input_path)
    suffix = path.suffix.lower()

    if suffix not in AUDIO_EXTENSIONS:
        raise ValueError(
            f"transcribe_segments requires audio input, got '{suffix}'. "
            f"Supported: {AUDIO_EXTENSIONS}"
        )

    logger.info("Transcribing audio with segments: %s", path.name)
    start = time.time()

    model = _get_faster_whisper_model(model_name, device)
    segments_iter, info = model.transcribe(str(path), language=language)

    segments_list = []
    text_parts = []
    for segment in segments_iter:
        text_parts.append(segment.text.strip())
        segments_list.append(
            {
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip(),
            }
        )

    elapsed = time.time() - start
    logger.info(
        "Transcription complete: %.1fs audio, took %.1fs (%.1fx realtime), %d segments",
        info.duration,
        elapsed,
        info.duration / elapsed if elapsed > 0 else 0,
        len(segments_list),
    )

    return " ".join(text_parts), segments_list


def _get_faster_whisper_model(model_name: str, device: str):
    """Lazily import faster-whisper and cache the loaded model."""
    from faster_whisper import WhisperModel

    key = f"{model_name}:{device}"
    if key not in _faster_whisper_cache:
        logger.info("Loading Whisper model '%s' on %s...", model_name, device)
        _faster_whisper_cache[key] = WhisperModel(model_name, device=device)
    return _faster_whisper_cache[key]


def _transcribe_faster_whisper(
    path: Path, model_name: str, language: str, device: str
) -> str:
    logger.info("Transcribing audio (faster-whisper): %s", path.name)
    start = time.time()

    model = _get_faster_whisper_model(model_name, device)
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


def _transcribe_mlx(path: Path, model_name: str, language: str) -> str:
    try:
        import mlx_whisper
    except ImportError as e:
        raise ImportError(
            "mlx-whisper is not installed. It only ships on Apple Silicon Macs. "
            "Install with `uv sync` on an M-series Mac, or switch the transcription "
            "backend back to 'faster-whisper' in config.yaml."
        ) from e

    repo = _MLX_MODEL_ALIASES.get(model_name, model_name)
    logger.info("Transcribing audio (mlx-whisper, %s): %s", repo, path.name)
    start = time.time()

    result = mlx_whisper.transcribe(str(path), path_or_hf_repo=repo, language=language)
    text = result["text"].strip()

    elapsed = time.time() - start
    # mlx-whisper does not surface audio duration on the result dict; log raw timing.
    logger.info("Transcription complete: took %.1fs", elapsed)
    return text
