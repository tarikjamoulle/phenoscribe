"""config.yaml and the Config defaults are one set of values; a bad file gives one clear error."""

import re

import pytest
from conftest import SHIPPED_CONFIG

from phenoscribe.config import Config, ConfigError, load_config


def test_the_shipped_config_equals_the_defaults():
    assert load_config(SHIPPED_CONFIG) == Config()


def test_a_missing_file_gives_the_defaults(tmp_path):
    assert load_config(tmp_path / "none.yaml") == Config()


def _load(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)


@pytest.mark.parametrize("text, message", [
    ("llm:\n  provder: openai\n", "unknown key llm.provder; known keys: provider, model"),
    ("llmm:\n  provider: openai\n", "unknown section llmm; known sections: llm, transcription"),
    ("llm:\n  provider: gemini\n", "invalid value llm.provider: 'gemini'; use one of: openai, anthropic, ollama"),
    ("transcription:\n  backend: whisper\n", "invalid value transcription.backend"),
    ("output:\n  format: csv\n", "invalid value output.format"),
    ("hpo_index:\n  embedding_model: bert\n", "invalid value hpo_index.embedding_model"),
    ("llm: [openai\n", "invalid YAML"),
    ("llm: openai\n", "the section llm must hold keys"),
    ("- llm\n", "the file must hold sections"),
])
def test_a_bad_config_raises_one_clear_line(tmp_path, text, message):
    with pytest.raises(ConfigError, match=re.escape(message)) as error:
        _load(tmp_path, text)
    assert "\n" not in str(error.value)


def test_a_config_with_some_keys_keeps_the_other_defaults(tmp_path):
    config = _load(tmp_path, "llm:\n  provider: anthropic\n  model: claude-sonnet-4-6\n")
    assert config.llm.provider == "anthropic"
    assert config.llm.extraction_runs == Config().llm.extraction_runs
    assert config.output == Config().output


@pytest.mark.parametrize("text", ["", "# only a comment\n", "llm:\n", "llm:\noutput: ~\n"],
                         ids=["empty file", "comment only", "empty section", "null section"])
def test_an_empty_file_or_section_gives_the_defaults(tmp_path, text):
    assert _load(tmp_path, text) == Config()


def test_the_config_picks_the_embedding_model(tmp_path):
    assert _load(tmp_path, "hpo_index:\n  embedding_model: sapbert\n").hpo_index.embedding_model == "sapbert"
