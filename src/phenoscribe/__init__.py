"""Phenoscribe — Automated HPO phenotype coding from patient interviews."""

from importlib.metadata import PackageNotFoundError, version

# The version lives in pyproject.toml only; the installed metadata carries it.
try:
    __version__ = version("phenoscribe")
except PackageNotFoundError:  # a source tree on PYTHONPATH with no install
    __version__ = "0.0.0"
