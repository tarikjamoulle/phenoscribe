"""Tests for the release ZIP that .github/workflows/release.yml builds."""

import io
import shutil
import subprocess
import zipfile
from functools import cache

import pytest
from conftest import REPO_ROOT as ROOT

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(),
    reason="needs git and a git checkout of the repository",
)

# What `uv run --frozen --no-dev phenoscribe setup` and the app need, plus the user docs.
NEEDED = (
    "Start Phenoscribe.bat",
    "Start Phenoscribe.command",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "config.yaml",
    "README.md",
    "INSTALL.md",
    "LICENSE",
    "install-images/page.png",
    "src/phenoscribe/cli.py",
    "src/phenoscribe/gui/__init__.py",
    "src/phenoscribe/resources/hpo_icpc.csv",
    "src/phenoscribe/resources/first_names_be.txt",
    "src/phenoscribe/resources/towns_be.txt",
)
# Folders for maintainers only (.gitattributes export-ignore).
LEFT_OUT = ("tests/", "benchmarks/", "scripts/dev/", ".github/")


@cache
def _release_zip() -> zipfile.ZipFile:
    # The release workflow command, written to memory. --worktree-attributes
    # applies the .gitattributes of the checkout, so an uncommitted rule counts too.
    data = subprocess.run(
        ["git", "archive", "--worktree-attributes", "--format=zip", "--prefix=Phenoscribe/", "HEAD"],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout
    return zipfile.ZipFile(io.BytesIO(data))


@pytest.mark.parametrize("name", NEEDED)
def test_zip_holds_what_setup_and_the_app_need(name):
    assert f"Phenoscribe/{name}" in _release_zip().namelist()


@pytest.mark.parametrize("folder", LEFT_OUT)
def test_zip_leaves_out_the_maintainer_folders(folder):
    assert not any(n.startswith(f"Phenoscribe/{folder}") for n in _release_zip().namelist())


def test_command_file_is_executable_in_the_zip():
    info = _release_zip().getinfo("Phenoscribe/Start Phenoscribe.command")
    assert (info.external_attr >> 16) & 0o111


def test_bat_keeps_windows_line_endings_in_the_zip():
    data = _release_zip().read("Phenoscribe/Start Phenoscribe.bat")
    assert data.count(b"\r\n") == data.count(b"\n")
