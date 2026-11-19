"""Tests for the double-click start scripts."""

import re
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT as ROOT

COMMAND = ROOT / "Start Phenoscribe.command"
BAT = ROOT / "Start Phenoscribe.bat"


@pytest.mark.parametrize("script", [COMMAND, BAT], ids=["command", "bat"])
def test_script_runs_setup_then_the_page(script):
    text = script.read_text(encoding="utf-8")
    setup = text.index("run --frozen --no-dev phenoscribe setup")
    gui = text.index("run --frozen --no-dev phenoscribe-gui")
    assert setup < gui
    assert "Documents" in text and "PHENOSCRIBE_OUTPUT_DIR" in text


def test_both_scripts_install_the_same_pinned_uv_version():
    pins = {re.search(r"astral\.sh/uv/([\d.]+)/install\.", s.read_text(encoding="utf-8")).group(1)
            for s in (COMMAND, BAT)}
    assert len(pins) == 1


def test_bat_asks_windows_for_the_documents_folder():
    assert "GetFolderPath('MyDocuments')" in BAT.read_text(encoding="utf-8")


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


def test_bat_has_windows_line_endings():
    # cmd.exe can miss a goto label in a file with LF-only line endings.
    data = BAT.read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n")
