"""Configuration loader for Phenoscribe."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o"
    ollama_base_url: str = "http://localhost:11434"


@dataclass
class TranscriptionConfig:
    backend: str = "faster-whisper"  # faster-whisper | mlx (Apple Silicon)
    model: str = "large-v3"
    language: str = "fr"
    device: str = "cpu"  # only used by faster-whisper


@dataclass
class OutputConfig:
    format: str = "semicolon"
    path: str = "output/results.xlsx"
    # True-path rule: also emit the ancestor closure of the predicted terms
    # so downstream tools (Exomiser, LIRICAL, Phen2Gene, Phenomizer) get an
    # annotation-propagated set. Off by default; the leaf list stays the
    # human-facing view.
    propagate_ancestors: bool = False


@dataclass
class PathsConfig:
    chroma_db: str = "data/chroma_db"
    jobs_db: str = "data/jobs.db"
    hpo_obo: str = "data/hpo/hp.obo"


@dataclass
class DiarizationConfig:
    enabled: bool = False
    num_speakers: int = 2


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    diarization: DiarizationConfig = field(default_factory=DiarizationConfig)


def load_config(path: str = "config.yaml") -> Config:
    """Load configuration from a YAML file."""
    config_path = Path(path)
    if not config_path.exists():
        return Config()

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    return Config(
        llm=LLMConfig(**raw.get("llm", {})),
        transcription=TranscriptionConfig(**raw.get("transcription", {})),
        output=OutputConfig(**raw.get("output", {})),
        paths=PathsConfig(**raw.get("paths", {})),
        diarization=DiarizationConfig(**raw.get("diarization", {})),
    )
