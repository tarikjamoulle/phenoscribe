"""Tests for the benchmark fetch script's checksum machinery.

No network here. The downloads hit external URLs; what we test is the pure
checksum logic: a stable tree digest, recording a missing checksum, verifying a
matching one, failing on a mismatch, and --strict refusing an unrecorded
artefact. That is what makes the benchmark corpora reproducible.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "fetch_benchmark_data",
    Path(__file__).parent.parent / "scripts" / "fetch_benchmark_data.py",
)
fetch = importlib.util.module_from_spec(_spec)
sys.modules["fetch_benchmark_data"] = fetch
_spec.loader.exec_module(fetch)


def test_repo_ships_hpoa_checksum():
    """The pinned phenotype.hpoa checksum is vendored, keyed to the release."""
    sums = fetch.load_checksums()
    key = f"phenotype.hpoa@v{fetch.HPO_RELEASE}"
    assert key in sums
    assert len(sums[key]) == 64  # a full SHA-256 hex digest


def test_sha256_tree_is_order_independent(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    for root in (a, b):
        (root / "Text").mkdir(parents=True)
        (root / "Annotations").mkdir(parents=True)
        (root / "Text" / "1").write_text("alpha")
        (root / "Annotations" / "1").write_text("0:5\tHP:0000001\talpha")
    # Same content under two different locations -> same digest.
    assert fetch.sha256_tree(a) == fetch.sha256_tree(b)
    # A content change moves the digest.
    (b / "Text" / "1").write_text("beta")
    assert fetch.sha256_tree(a) != fetch.sha256_tree(b)


def _isolate_checksums(tmp_path, monkeypatch):
    cs = tmp_path / "CHECKSUMS.txt"
    monkeypatch.setattr(fetch, "CHECKSUMS_PATH", cs)
    return cs


def test_verify_records_missing_then_verifies(tmp_path, monkeypatch):
    _isolate_checksums(tmp_path, monkeypatch)
    # First call: no record -> records it (non-strict).
    fetch.verify_or_record("artefact", "deadbeef", strict=False)
    assert fetch.load_checksums()["artefact"] == "deadbeef"
    # Second call with the same digest verifies cleanly (no raise).
    fetch.verify_or_record("artefact", "deadbeef", strict=False)


def test_verify_fails_on_mismatch(tmp_path, monkeypatch):
    _isolate_checksums(tmp_path, monkeypatch)
    fetch.record_checksum("artefact", "aaaa")
    with pytest.raises(SystemExit):
        fetch.verify_or_record("artefact", "bbbb", strict=False)


def test_strict_fails_when_unrecorded(tmp_path, monkeypatch):
    _isolate_checksums(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        fetch.verify_or_record("never-seen", "cafe", strict=True)
