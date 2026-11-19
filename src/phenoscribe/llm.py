"""One call interface for the LLM providers: OpenAI, Anthropic and Ollama."""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:  # config imports PROVIDERS from this module
    from phenoscribe.config import LLMConfig

logger = logging.getLogger(__name__)

# A local model needs minutes for a long interview.
OLLAMA_TIMEOUT_SECONDS = 600
RETRY_DELAY_SECONDS = 2
# The environment variable that holds the key of each cloud provider.
API_KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

_api_key_override: ContextVar[dict[str, str] | None] = ContextVar(
    "_api_key_override", default=None
)


@contextmanager
def use_api_key(provider: str, api_key: str):
    """Make `api_key` available to LLM calls for this provider, scoped to the with-block.

    Used by the GUI to keep a user-pasted key out of `os.environ` (where it would
    persist across requests and leak to other users of the same process).
    """
    if not api_key:
        yield
        return
    token = _api_key_override.set({provider: api_key})
    try:
        yield
    finally:
        _api_key_override.reset(token)


def _resolve_api_key(provider: str) -> str:
    override = _api_key_override.get()
    if override and override.get(provider):
        return override[provider]
    return os.environ.get(API_KEY_ENV[provider], "")


def missing_key_variable(provider: str) -> str | None:
    """The environment variable to set when a cloud provider has no key, else None."""
    if provider in API_KEY_ENV and not _resolve_api_key(provider):
        return API_KEY_ENV[provider]
    return None


def llm_call(system_prompt: str, user_prompt: str, llm: LLMConfig, temperature: float = 0.1) -> str:
    """The answer of the LLM of ``llm`` to a system and a user prompt.

    A temperature of 0 gives the most repeatable answer (speaker labels,
    symptom extraction); the HPO judge keeps the default. A transient failure
    (connection, timeout, rate limit, 5xx) gets one more try.
    """
    call = PROVIDERS.get(llm.provider)
    if call is None:
        raise ValueError(f"Unknown LLM provider: {llm.provider}")
    try:
        return call(system_prompt, user_prompt, llm, temperature)
    except Exception as e:
        if not _is_transient(e):
            raise
        logger.warning("LLM call failed, retrying once: %s", e)
    time.sleep(RETRY_DELAY_SECONDS)
    return call(system_prompt, user_prompt, llm, temperature)


def _is_transient(error: Exception) -> bool:
    """True for a failure that a second try can fix: connection, timeout, rate limit, 5xx."""
    import anthropic  # the SDKs are slow to import: only a failed call needs them here
    import openai

    if isinstance(error, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return _is_transient_status(error.response.status_code)
    if isinstance(error, (openai.APIConnectionError, anthropic.APIConnectionError)):
        return True  # includes the SDK timeouts
    if isinstance(error, (openai.APIStatusError, anthropic.APIStatusError)):
        return _is_transient_status(error.status_code)
    return False


def _is_transient_status(status: int) -> bool:
    return status == 429 or status >= 500


def _require_text(text: str | None, provider: str) -> str:
    if text is None:
        raise RuntimeError(f"The {provider} model returned no text.")
    return text


def _call_openai(system_prompt: str, user_prompt: str, llm: LLMConfig, temperature: float) -> str:
    from openai import OpenAI  # slow to import: only a call needs it

    client = OpenAI(api_key=_resolve_api_key("openai"))
    response = client.chat.completions.create(
        model=llm.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
    )
    return _require_text(response.choices[0].message.content, "openai")


def _call_anthropic(system_prompt: str, user_prompt: str, llm: LLMConfig, temperature: float) -> str:
    from anthropic import Anthropic  # slow to import: only a call needs it

    client = Anthropic(api_key=_resolve_api_key("anthropic"))
    response = client.messages.create(
        model=llm.model,
        max_tokens=16000,  # a long interview gives a long symptom list
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=temperature,
    )
    return _require_text(response.content[0].text if response.content else None, "anthropic")


def _call_ollama(system_prompt: str, user_prompt: str, llm: LLMConfig, temperature: float) -> str:
    response = requests.post(
        f"{llm.ollama_base_url}/api/chat",
        json={
            "model": llm.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        },
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return _require_text(response.json()["message"]["content"], "ollama")


# The call function of each provider.
PROVIDERS = {"openai": _call_openai, "anthropic": _call_anthropic, "ollama": _call_ollama}
