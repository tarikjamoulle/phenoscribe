"""Tests for the release ZIP that .github/workflows/release.yml builds."""

import io
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _release_zip() -> zipfile.ZipFile:
    # The same command as the release workflow, written to memory.
    data = subprocess.run(
        ["git", "archive", "--format=zip", "--prefix=Phenoscribe/", "HEAD"],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout
    return zipfile.ZipFile(io.BytesIO(data))


def test_zip_holds_the_app_and_both_start_scripts():
    names = set(_release_zip().namelist())
    for name in (
        "Start Phenoscribe.bat",
        "Start Phenoscribe.command",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "config.yaml",
        "src/phenoscribe/gui.py",
        "src/phenoscribe/resources/hpo_icpc.csv",
    ):
        assert f"Phenoscribe/{name}" in names


def test_command_file_is_executable_in_the_zip():
    info = _release_zip().getinfo("Phenoscribe/Start Phenoscribe.command")
    assert (info.external_attr >> 16) & 0o111


def test_bat_keeps_windows_line_endings_in_the_zip():
    data = _release_zip().read("Phenoscribe/Start Phenoscribe.bat")
    assert data.count(b"\r\n") == data.count(b"\n")
