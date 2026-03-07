"""LLM abstraction layer — supports OpenAI, Anthropic, and Ollama."""

import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)


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

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
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

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
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
