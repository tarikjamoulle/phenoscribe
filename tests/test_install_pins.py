"""Tests for the Python pin that every install path (start scripts, CI, Docker) uses."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_python_version_is_pinned_to_312():
    assert (ROOT / ".python-version").read_text().strip() == "3.12"


def test_requires_python_excludes_314():
    # uv.lock has no torch wheel for Python 3.14; uv must never pick it.
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["requires-python"] == ">=3.10,<3.14"
