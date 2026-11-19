"""Licence rule for the dependencies in pyproject.toml."""


try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: pytest depends on tomli there
    import tomli as tomllib
from conftest import REPO_ROOT as ROOT


def test_deduce_is_never_a_default_dependency():
    # DEDUCE has a GPL-3.0 licence: an optional install only (uv sync --extra dutch-rules).
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert not any(d.lower().startswith("deduce") for d in project["dependencies"])
    assert any(d.lower().startswith("deduce") for d in project["optional-dependencies"]["dutch-rules"])
