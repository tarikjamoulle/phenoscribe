"""Configuration loader for Phenoscribe.

The dataclass defaults are the values of config.yaml, so a missing file and
the shipped file give the same Config.
"""

from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import urlparse

import yaml

from phenoscribe.embeddings import EMBEDDING_MODELS
from phenoscribe.llm import PROVIDERS


class ConfigError(ValueError):
    """config.yaml has invalid YAML, an unknown key or an invalid value."""


# The model of each cloud provider when config.yaml names none for it. An
# Ollama model is whatever the local server has, so it has no default.
DEFAULT_MODELS = {"openai": "gpt-4o", "anthropic": "claude-sonnet-4-6"}

# The interview languages of the page, by code.
LANGUAGE_NAMES = {"fr": "French", "en": "English", "nl": "Dutch", "de": "German", "es": "Spanish", "it": "Italian"}


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = DEFAULT_MODELS["openai"]
    ollama_base_url: str = "http://localhost:11434"
    # Label each turn of the clean text D (doctor) or P (patient) before the
    # extraction, so the code can check who says each quote (speakers.py).
    speaker_labels: bool = True
    # Extract the symptoms this many times. A finding that is not in every
    # run gets "needs review" (match_hpo.flag_unstable).
    extraction_runs: int = 2

    @property
    def is_cloud(self) -> bool:
        """True when the LLM runs outside this computer.

        Only Ollama on this computer (localhost, 127.0.0.1 or ::1) is local. An
        Ollama server elsewhere and an unknown provider count as a cloud provider,
        so the privacy gate fails closed.
        """
        if self.provider != "ollama":
            return True
        return (urlparse(self.ollama_base_url).hostname or "") not in ("localhost", "127.0.0.1", "::1")


@dataclass
class TranscriptionConfig:
    backend: str = "auto"  # auto | faster-whisper | mlx (Apple Silicon)
    model: str = "large-v3"
    language: str = "fr"
    device: str = "cpu"  # only used by faster-whisper


def _default_pii_models() -> dict[str, list[str]]:
    # The numbers of scripts/eval_pii.py on the synthetic test set decide this
    # map (README, "Personal details"). The multilingual GLiNER model runs for
    # every language, because a Belgian interview mixes languages.
    gliner = "urchade/gliner_multi_pii-v1"
    return {
        "fr": ["Anonym-IA/V2-camembert-ner-pii-french", gliner],
        "en": ["StanfordAIMI/stanford-deidentifier-base", gliner],
        # OpenMed Dutch is not used: its training data (ai4privacy) is non-commercial.
        # DEDUCE (GPL-3.0) runs only when the optional extra is installed
        # (uv sync --extra dutch-rules); without it Dutch gets GLiNER only.
        "nl": [gliner, "deduce"],
        "default": [gliner],  # de, es, it: the page offers them too
    }


@dataclass
class PIIConfig:
    # The NER models used for local redaction before any LLM call, for each
    # language (transcription.language). "default" serves any other language.
    # The spans of all models of a language are kept. See pii.py.
    models: dict[str, list[str]] = field(default_factory=_default_pii_models)
    # Offline fallback for French only: a general 4-label French
    # NER (PER/LOC/ORG/MISC). Used if the first French model cannot be loaded.
    fallback_model: str = "Jean-Baptiste/camembert-ner"
    # Drop NER detections below this confidence.
    min_score: float = 0.6
    # Optional types to keep in the text (DATE, AGE, GENDER, JOB). Names,
    # places, phones, emails and ID numbers are always removed.
    keep_types: list[str] = field(default_factory=list)

    def __post_init__(self):
        # A config that names some languages keeps the default models of the others.
        self.models = {**_default_pii_models(), **(self.models or {})}

    def models_for(self, language: str) -> list[str]:
        """The models for a language, or the "default" models for a language not in the map."""
        return list(self.models.get(language) or self.models.get("default") or [])

    def fallback_for(self, language: str) -> str | None:
        """The fallback model replaces the first model, for French only."""
        return self.fallback_model if language == "fr" else None

    def all_models(self) -> list[str]:
        """Every model of every language, once, in map order (for phenoscribe setup)."""
        seen: list[str] = []
        for language in self.models:
            for name in self.models_for(language):
                if name not in seen:
                    seen.append(name)
        return seen


@dataclass
class OutputConfig:
    format: str = "detailed"  # detailed | semicolon | purl
    # True-path rule: also emit the ancestor closure of the predicted terms
    # so downstream tools (Exomiser, LIRICAL, Phen2Gene, Phenomizer) get an
    # annotation-propagated set. ON by default — the true-path rule is the
    # definition of what an HPO annotation means, so a default run must honor
    # it. The leaf list stays the human-facing view; the closure lands on a
    # separate "Ancestor Closure" sheet. Set false to suppress that sheet.
    propagate_ancestors: bool = True


@dataclass
class HpoConfig:
    # The one pinned HPO release. Must match the data-version: header of the
    # on-disk obo and the tag `phenoscribe setup` downloads (v2026-02-16).
    release: str = "hp/releases/2026-02-16"


@dataclass
class PathsConfig:
    chroma_db: str = "data/chroma_db"
    hpo_obo: str = "data/hpo/hp.obo"


@dataclass
class PatientConfig:
    # Prefix prepended to a numeric filename stem to form the patient ID, so the
    # output joins against a ground truth keyed "P.001" when the files are named
    # "001". An empty string disables the prefix.
    id_prefix: str = ""


@dataclass
class HpoIndexConfig:
    # embedding model for the HPO vector index: default (all-MiniLM-L6-v2) or
    # sapbert (cambridgeltl/SapBERT-from-PubMedBERT-fulltext).
    embedding_model: str = "default"


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    pii: PIIConfig = field(default_factory=PIIConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    patient: PatientConfig = field(default_factory=PatientConfig)
    hpo: HpoConfig = field(default_factory=HpoConfig)
    hpo_index: HpoIndexConfig = field(default_factory=HpoIndexConfig)


# The allowed values of the settings that take a choice.
CHOICES = {
    ("llm", "provider"): tuple(PROVIDERS),
    ("transcription", "backend"): ("auto", "faster-whisper", "mlx"),
    ("output", "format"): ("detailed", "semicolon", "purl"),
    ("hpo_index", "embedding_model"): EMBEDDING_MODELS,
}

_SECTIONS = {f.name: f.default_factory for f in fields(Config)}


def load_config(path: str | Path = "config.yaml") -> Config:
    """Load the configuration from a YAML file. A missing file gives the defaults.

    Raises ConfigError, with one line that names the problem, for invalid
    YAML, an unknown section or key, and a value outside its choices.
    """
    config_path = Path(path)
    if not config_path.exists():
        return Config()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML ({' '.join(str(e).split())})") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: the file must hold sections such as llm: and output:")
    sections = {name: _section(path, name, values) for name, values in raw.items()}
    config = Config(**sections)
    _check_choices(config, path)
    return config


def _section(path, name: str, values):
    if name not in _SECTIONS:
        raise ConfigError(f"{path}: unknown section {name}; known sections: {', '.join(_SECTIONS)}")
    cls = _SECTIONS[name]
    values = values or {}
    if not isinstance(values, dict):
        raise ConfigError(f"{path}: the section {name} must hold keys")
    known = [f.name for f in fields(cls)]
    for key in values:
        if key not in known:
            raise ConfigError(f"{path}: unknown key {name}.{key}; known keys: {', '.join(known)}")
    return cls(**values)


def _check_choices(config: Config, path) -> None:
    for (section, key), allowed in CHOICES.items():
        value = getattr(getattr(config, section), key)
        if value not in allowed:
            raise ConfigError(
                f"{path}: invalid value {section}.{key}: {value!r}; use one of: {', '.join(allowed)}"
            )
