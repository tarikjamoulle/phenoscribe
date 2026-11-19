"""The CLI starts fast: importing it loads no model library."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("heavy", ["torch", "transformers"])
def test_cli_import_does_not_load_a_model_library(heavy):
    result = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", "import phenoscribe.cli"],
        capture_output=True, text=True, check=True,
    )
    # Each -X importtime line ends with "| <module name>".
    loaded = {line.rsplit("|", 1)[-1].strip() for line in result.stderr.splitlines()}
    assert "phenoscribe.cli" in loaded
    assert heavy not in loaded
