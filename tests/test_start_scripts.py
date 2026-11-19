"""Tests for the double-click start script."""

import shutil
import subprocess

import pytest
from conftest import REPO_ROOT as ROOT

COMMAND = ROOT / "Start Phenoscribe.command"


def test_script_runs_setup_then_the_page():
    text = COMMAND.read_text(encoding="utf-8")
    setup = text.index("run --frozen --no-dev phenoscribe setup")
    gui = text.index("run --frozen --no-dev phenoscribe-gui")
    assert setup < gui
    assert "Documents" in text and "PHENOSCRIBE_OUTPUT_DIR" in text


def test_script_installs_a_pinned_uv_version():
    assert "https://astral.sh/uv/0." in COMMAND.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs git and a git checkout")
def test_command_file_is_executable_in_git():
    # Finder runs a .command only with the executable bit, and the ZIP
    # carries the mode that git stores.
    out = subprocess.run(
        ["git", "ls-files", "--stage", COMMAND.name],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    assert out.startswith("100755"), out


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")
def test_command_file_is_valid_sh():
    subprocess.run(["sh", "-n", str(COMMAND)], check=True)
