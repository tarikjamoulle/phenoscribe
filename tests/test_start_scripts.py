"""Tests for the double-click start scripts."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMMAND = ROOT / "Start Phenoscribe.command"
BAT = ROOT / "Start Phenoscribe.bat"


@pytest.mark.parametrize("script", [COMMAND, BAT], ids=["command", "bat"])
def test_script_runs_setup_then_the_page(script):
    text = script.read_text(encoding="utf-8")
    setup = text.index("run --frozen phenoscribe setup")
    gui = text.index("run --frozen phenoscribe-gui")
    assert setup < gui
    assert "Documents" in text and "PHENOSCRIBE_OUTPUT_DIR" in text


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
