"""LLM abstraction layer — supports OpenAI, Anthropic, and Ollama."""

import json
import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar

import requests

logger = logging.getLogger(__name__)

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


def _resolve_api_key(provider: str, env_var: str) -> str:
    override = _api_key_override.get()
    if override and override.get(provider):
        return override[provider]
    return os.environ.get(env_var, "")


def llm_call(
    system_prompt: str,
    user_prompt: str,
    provider: str = "openai",
    model: str = "gpt-4o",
    ollama_base_url: str = "http://localhost:11434",
) -> str:
    """Call an LLM with a system + user prompt. Returns the response text.

    Args:
        system_prompt: System instructions.
        user_prompt: User message.
        provider: One of "openai", "anthropic", "ollama".
        model: Model name for the provider.
        ollama_base_url: Base URL for Ollama (only used if provider is "ollama").

    Returns:
        The LLM's response text.
    """
    for attempt in range(2):
        try:
            if provider == "openai":
                return _call_openai(system_prompt, user_prompt, model)
            elif provider == "anthropic":
                return _call_anthropic(system_prompt, user_prompt, model)
            elif provider == "ollama":
                return _call_ollama(system_prompt, user_prompt, model, ollama_base_url)
            else:
                raise ValueError(f"Unknown LLM provider: {provider}")
        except Exception as e:
            if attempt == 0:
                logger.warning("LLM call failed (attempt 1), retrying: %s", e)
                time.sleep(2)
            else:
                raise


def _call_openai(system_prompt: str, user_prompt: str, model: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=_resolve_api_key("openai", "OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
    )
    return response.choices[0].message.content


def _call_anthropic(system_prompt: str, user_prompt: str, model: str) -> str:
    from anthropic import Anthropic

    client = Anthropic(api_key=_resolve_api_key("anthropic", "ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.1,
    )
    return response.content[0].text


def _call_ollama(
    system_prompt: str, user_prompt: str, model: str, base_url: str
) -> str:
    response = requests.post(
        f"{base_url}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.1},
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]
