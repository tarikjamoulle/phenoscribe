"""Speaker diarization module — pyannote-audio for speaker segmentation."""

import logging
import os

logger = logging.getLogger(__name__)

_pipeline_cache = None


def diarize(audio_path: str, num_speakers: int = 2) -> list[dict]:
    """Run speaker diarization on an audio file.

    Requires HF_TOKEN environment variable for pyannote model access.

    Args:
        audio_path: Path to audio file.
        num_speakers: Expected number of speakers.

    Returns:
        List of diarization segments with speaker, start, end.
    """
    global _pipeline_cache

    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        raise RuntimeError(
            "HF_TOKEN environment variable is required for speaker diarization. "
            "Get a token at https://huggingface.co/settings/tokens and accept "
            "the pyannote/speaker-diarization-3.1 model conditions."
        )

    if _pipeline_cache is None:
        from pyannote.audio import Pipeline

        logger.info("Loading pyannote speaker-diarization-3.1 pipeline...")
        _pipeline_cache = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=hf_token,
        )
        logger.info("Diarization pipeline loaded.")

    logger.info("Running diarization on %s (num_speakers=%d)...", audio_path, num_speakers)
    diarization = _pipeline_cache(audio_path, num_speakers=num_speakers)

    segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        segments.append({
            "speaker": speaker,
            "start": turn.start,
            "end": turn.end,
        })

    logger.info("Diarization complete: %d segments, speakers: %s",
                len(segments),
                sorted(set(s["speaker"] for s in segments)))
    return segments


def align_segments(
    whisper_segments: list[dict],
    diarization_segments: list[dict],
) -> list[dict]:
    """Assign a speaker label to each Whisper segment by maximum temporal overlap.

    Args:
        whisper_segments: List of dicts with start, end, text from Whisper.
        diarization_segments: List of dicts with speaker, start, end from diarization.

    Returns:
        List of dicts with speaker, start, end, text.
    """
    aligned = []
    for ws in whisper_segments:
        best_speaker = "SPEAKER_00"
        best_overlap = 0.0

        for ds in diarization_segments:
            overlap_start = max(ws["start"], ds["start"])
            overlap_end = min(ws["end"], ds["end"])
            overlap = max(0.0, overlap_end - overlap_start)

            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = ds["speaker"]

        aligned.append({
            "speaker": best_speaker,
            "start": ws["start"],
            "end": ws["end"],
            "text": ws["text"],
        })

    return aligned


def format_transcript(aligned_segments: list[dict]) -> str:
    """Format aligned segments into a speaker-labeled transcript.

    Merges consecutive segments from the same speaker into single blocks.

    Args:
        aligned_segments: List of dicts with speaker, start, end, text.

    Returns:
        Formatted transcript with [SPEAKER_XX]: prefix per block.
    """
    if not aligned_segments:
        return ""

    blocks = []
    current_speaker = aligned_segments[0]["speaker"]
    current_texts = [aligned_segments[0]["text"]]

    for seg in aligned_segments[1:]:
        if seg["speaker"] == current_speaker:
            current_texts.append(seg["text"])
        else:
            blocks.append(f"[{current_speaker}]: {' '.join(current_texts)}")
            current_speaker = seg["speaker"]
            current_texts = [seg["text"]]

    blocks.append(f"[{current_speaker}]: {' '.join(current_texts)}")

    return "\n\n".join(blocks)
