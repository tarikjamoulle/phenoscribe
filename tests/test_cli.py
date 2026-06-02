"""Tests for CLI argument resolution helpers."""

import pytest

from phenoscribe.cli import resolve_provider


def test_resolve_canonical_names():
    assert resolve_provider("openai") == "openai"
    assert resolve_provider("anthropic") == "anthropic"
    assert resolve_provider("ollama") == "ollama"


def test_resolve_friendly_aliases():
    assert resolve_provider("claude") == "anthropic"
    assert resolve_provider("gpt") == "openai"


def test_resolve_is_case_insensitive():
    assert resolve_provider("Claude") == "anthropic"
    assert resolve_provider("OPENAI") == "openai"


def test_resolve_unknown_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        resolve_provider("gemini")
