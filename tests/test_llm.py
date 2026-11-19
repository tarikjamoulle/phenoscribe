"""Tests for the LLM layer: retry policy, empty answers, scoped API keys."""

from types import SimpleNamespace
from typing import ClassVar

import anthropic
import httpx
import openai
import pytest
import requests

from phenoscribe import llm
from phenoscribe.config import LLMConfig

_REQUEST = httpx.Request("POST", "https://api.example.test/v1")


def _status_error(cls, status: int):
    return cls("error", response=httpx.Response(status, request=_REQUEST), body=None)


class _FakeOpenAI:
    """Stands in for openai.OpenAI: each create() call takes the next outcome."""

    outcomes: ClassVar[list] = []
    calls: ClassVar[list] = []
    keys: ClassVar[list] = []

    def __init__(self, api_key):
        _FakeOpenAI.keys.append(api_key)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        _FakeOpenAI.calls.append(kwargs)
        outcome = _FakeOpenAI.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        message = SimpleNamespace(content=outcome)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def fake_openai(monkeypatch):
    _FakeOpenAI.outcomes, _FakeOpenAI.calls, _FakeOpenAI.keys = [], [], []
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    return _FakeOpenAI


def _call():
    return llm.llm_call("system", "user", LLMConfig(provider="openai", model="m"))


@pytest.mark.parametrize("error", [
    openai.APIConnectionError(request=_REQUEST),
    openai.APITimeoutError(request=_REQUEST),
    _status_error(openai.RateLimitError, 429),
    _status_error(openai.InternalServerError, 503),
])
def test_transient_error_is_retried_once(fake_openai, error):
    fake_openai.outcomes = [error, "answer"]
    assert _call() == "answer"
    assert len(fake_openai.calls) == 2


def test_transient_error_twice_raises(fake_openai):
    fake_openai.outcomes = [openai.APIConnectionError(request=_REQUEST)] * 2
    with pytest.raises(openai.APIConnectionError):
        _call()
    assert len(fake_openai.calls) == 2


@pytest.mark.parametrize("error", [
    _status_error(openai.AuthenticationError, 401),
    _status_error(openai.BadRequestError, 400),
    ValueError("bad"),
])
def test_permanent_error_is_raised_without_retry(fake_openai, error):
    fake_openai.outcomes = [error, "answer"]
    with pytest.raises(type(error)):
        _call()
    assert len(fake_openai.calls) == 1


def test_unknown_provider_raises_without_a_call(fake_openai):
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        llm.llm_call("system", "user", LLMConfig(provider="mistral"))
    assert fake_openai.calls == []


def test_empty_message_content_raises_a_clear_error(fake_openai):
    fake_openai.outcomes = [None]
    with pytest.raises(RuntimeError, match="returned no text"):
        _call()


@pytest.mark.parametrize("error, transient", [
    (anthropic.APIConnectionError(request=_REQUEST), True),
    (_status_error(anthropic.InternalServerError, 529), True),
    (_status_error(anthropic.AuthenticationError, 401), False),
    (requests.ConnectionError(), True),
    (requests.Timeout(), True),
    (requests.HTTPError(response=SimpleNamespace(status_code=502)), True),
    (requests.HTTPError(response=SimpleNamespace(status_code=404)), False),
])
def test_transient_classification_of_other_providers(error, transient):
    assert llm._is_transient(error) is transient


def test_use_api_key_applies_inside_the_block_only(fake_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    fake_openai.outcomes = ["a", "b"]
    with llm.use_api_key("openai", "pasted-key"):
        _call()
    _call()
    assert fake_openai.keys == ["pasted-key", "env-key"]


def test_use_api_key_for_another_provider_leaves_the_env_key(fake_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    fake_openai.outcomes = ["a"]
    with llm.use_api_key("anthropic", "pasted-key"):
        _call()
    assert fake_openai.keys == ["env-key"]


def test_use_api_key_resets_after_an_error(fake_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    with pytest.raises(RuntimeError), llm.use_api_key("openai", "pasted-key"):
        raise RuntimeError
    fake_openai.outcomes = ["a"]
    _call()
    assert fake_openai.keys == ["env-key"]
