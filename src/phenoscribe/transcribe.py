"""Step 1: the text of an input file. Whisper (mlx or faster-whisper) for audio; a .txt or .docx is read."""

import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}
TEXT_EXTENSIONS = {".txt", ".docx"}

# Default HF repo when caller passes a bare faster-whisper model name to the mlx backend.
# distil-* variants are English-only: on French audio they give English nonsense.
_MLX_MODEL_ALIASES = {
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "distil-large-v3": "mlx-community/distil-whisper-large-v3",  # English only
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
}

_faster_whisper_cache: dict[str, object] = {}


class EmptyTranscriptError(ValueError):
    """The file holds no text to code."""


def read_text_file(path: Path) -> str:
    """The text of a .txt file: UTF-8 (with or without BOM), else Windows-1252."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        logger.info("%s is not UTF-8: read as Windows-1252", path.name)
        return data.decode("cp1252", errors="replace")


def resolve_backend(backend: str) -> str:
    """Turn "auto" into the backend this machine can run.

    "auto" is mlx when mlx-whisper imports (Apple Silicon only) and the
    `ffmpeg` command exists, because mlx-whisper decodes audio with it.
    Otherwise faster-whisper, which decodes with the PyAV wheel and needs no
    system program. Any other value is returned unchanged.
    """
    if backend != "auto":
        return backend
    try:
        import mlx_whisper  # noqa: F401  (Apple Silicon only; the import is the check)
    except ImportError:
        return "faster-whisper"
    return "mlx" if shutil.which("ffmpeg") else "faster-whisper"


def download_speech_model(
    backend: str, model_name: str, local_files_only: bool = False
) -> str:
    """Put the Whisper model for ``backend`` in the Hugging Face cache. Returns its folder.

    With ``local_files_only=True`` nothing downloads, and a model that is not
    cached yet raises FileNotFoundError. `phenoscribe setup` uses that as its
    "already present" check.
    """
    if resolve_backend(backend) == "mlx":
        from huggingface_hub import snapshot_download  # slow to import: only setup needs it

        repo = _MLX_MODEL_ALIASES.get(model_name, model_name)
        return snapshot_download(repo, local_files_only=local_files_only)
    from faster_whisper import download_model  # slow to import: only setup needs it

    return download_model(model_name, local_files_only=local_files_only)


def read_docx(path) -> str:
    """The text of a Word file: headers and footers first, then the body in order.

    Headers and footers often hold the patient name, so they go through the
    personal-detail removal too. A table row becomes one line, cells joined
    by " | ". Formatting and images are dropped.
    """
    from docx import Document  # slow to import: only a Word file needs it
    from docx.table import Table

    def table_lines(table) -> list[str]:
        rows = []
        for row in table.rows:
            cells = []
            for cell in row.cells:
                if cell.text not in cells:  # a merged cell repeats
                    cells.append(cell.text)
            rows.append(" | ".join(cells))
        return rows

    def block_lines(container) -> list[str]:
        """Paragraphs and tables of a body, header or footer, in document order."""
        out = []
        for block in container.iter_inner_content():
            out += table_lines(block) if isinstance(block, Table) else [block.text]
        return out

    doc = Document(str(path))
    lines: list[str] = []
    seen_parts = set()
    for section in doc.sections:
        for part in (section.header, section.footer):
            if part.is_linked_to_previous or id(part.part) in seen_parts:
                continue
            seen_parts.add(id(part.part))
            lines += [line for line in block_lines(part) if line.strip()]
    lines += block_lines(doc)
    return "\n".join(lines).strip()


def read_input(
    input_path: str,
    *,
    backend: str = "faster-whisper",
    model_name: str = "large-v3",
    language: str = "fr",
    device: str = "cpu",
) -> str:
    """The text of one input file, stripped: a .txt or .docx as it is, an audio file through Whisper.

    The Whisper settings (see transcribe_audio) apply to audio only. Raises
    EmptyTranscriptError when the file has no text, and ValueError for an
    unsupported file type.
    """
    path = Path(input_path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        logger.info("Reading Word file: %s", path.name)
        text = read_docx(path)
    elif suffix in TEXT_EXTENSIONS:
        logger.info("Reading text file: %s", path.name)
        text = read_text_file(path)
    elif suffix in AUDIO_EXTENSIONS:
        text = transcribe_audio(path, backend, model_name, language, device)
    else:
        raise ValueError(
            f"Unsupported file type '{suffix}'. "
            f"Supported: {AUDIO_EXTENSIONS | TEXT_EXTENSIONS}"
        )
    text = text.strip()
    if not text:
        raise EmptyTranscriptError(f"{path.name}: the file has no text")
    return text


def transcribe_audio(path: Path, backend: str, model_name: str, language: str, device: str) -> str:
    """The Whisper transcript of an audio file, with a [mm:ss] marker before each segment.

    backend: "faster-whisper" (CPU/CUDA), "mlx" (Apple Silicon) or "auto"
    (resolve_backend). model_name: a Whisper model name; for mlx also an HF
    repo, and a bare name such as "large-v3" maps to its mlx-community repo.
    device: cpu | cuda | auto, for faster-whisper only.
    """
    backend = resolve_backend(backend)
    if backend == "mlx":
        return _transcribe_mlx(path, model_name, language)
    if backend == "faster-whisper":
        return _transcribe_faster_whisper(path, model_name, language, device)
    raise ValueError(
        f"Unknown transcription backend '{backend}'. "
        "Use 'auto', 'faster-whisper' or 'mlx'."
    )


def _get_faster_whisper_model(model_name: str, device: str):
    """The faster-whisper model, loaded once for each name and device."""
    from faster_whisper import WhisperModel  # slow to import: only audio needs it

    key = f"{model_name}:{device}"
    if key not in _faster_whisper_cache:
        logger.info("Loading Whisper model '%s' on %s...", model_name, device)
        _faster_whisper_cache[key] = WhisperModel(model_name, device=device)
    return _faster_whisper_cache[key]


def timestamp(seconds: float) -> str:
    """"[mm:ss]", or "[h:mm:ss]" from one hour on, like the Word transcripts."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"[{hours}:{minutes:02d}:{secs:02d}]" if hours else f"[{minutes:02d}:{secs:02d}]"


def timed_text(segments) -> str:
    """One line for each Whisper segment, with its start time: "[01:23] text".

    The times let the app place each symptom in the interview. The
    personal-detail removal ignores them (text.TIMESTAMP_PATTERN).
    """
    lines = []
    for start, text in segments:
        text = text.strip()
        if text:
            lines.append(f"{timestamp(start)} {text}")
    return "\n".join(lines)


def _transcribe_faster_whisper(
    path: Path, model_name: str, language: str, device: str
) -> str:
    logger.info("Transcribing audio (faster-whisper): %s", path.name)
    start = time.time()

    model = _get_faster_whisper_model(model_name, device)
    segments, info = model.transcribe(str(path), language=language)
    text = timed_text((segment.start, segment.text) for segment in segments)

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
        import mlx_whisper  # Apple Silicon only, slow to import
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
    text = timed_text((seg["start"], seg["text"]) for seg in result.get("segments") or [])
    if not text:  # no segment list: keep the plain text
        text = result["text"].strip()

    elapsed = time.time() - start
    # mlx-whisper does not surface audio duration on the result dict; log raw timing.
    logger.info("Transcription complete: took %.1fs", elapsed)
    return text
