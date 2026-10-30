"""Shared test setup."""

import os
import urllib.request

import pytest


@pytest.fixture(autouse=True, scope="session")
def github_token_for_hpotk():
    """Send the CI job token with hpo-toolkit's GitHub API call.

    hpo-toolkit asks the GitHub API for the newest HPO tag with no token.
    Shared CI runners (macOS) hit the anonymous limit of 60 calls an hour and
    get HTTP 403. With GITHUB_TOKEN set (CI only), the call is authenticated.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        yield
        return

    from hpotk.store import _github

    real_urlopen = _github.urlopen

    def urlopen_with_token(url, *args, **kwargs):
        if isinstance(url, str) and url.startswith("https://api.github.com/"):
            url = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        return real_urlopen(url, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_github, "urlopen", urlopen_with_token)
        yield


@pytest.fixture(autouse=True)
def no_speaker_pass(monkeypatch):
    """The pipeline tests stub the LLM steps; the speaker pass is one of them.

    Without this, every test that runs code_review would call the model to
    label the speakers. A test of the pass itself patches find_speakers again.
    """
    from phenoscribe import pipeline

    monkeypatch.setattr(pipeline, "find_speakers", lambda *a, **k: "")
