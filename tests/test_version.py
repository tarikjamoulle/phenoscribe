"""The version has one source: pyproject.toml. CITATION.cff repeats it."""

import re

import phenoscribe

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: pytest depends on tomli there
    import tomli as tomllib
from conftest import REPO_ROOT as ROOT


def _pyproject_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def test_package_version_comes_from_pyproject():
    assert phenoscribe.__version__ == _pyproject_version()


def test_citation_version_agrees_with_pyproject():
    citation = (ROOT / "CITATION.cff").read_text()
    assert re.search(r"^version: (\S+)$", citation, re.M).group(1) == _pyproject_version()


def test_changelog_has_an_entry_for_the_version():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert f"## [{_pyproject_version()}]" in changelog
