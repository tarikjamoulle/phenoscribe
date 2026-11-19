"""The PII test set (tests/fixtures/pii) is frozen: a changed file fails here."""

import hashlib
import json

import pytest
from conftest import FIXTURES

ROOT = FIXTURES / "pii"
LANGUAGES = ("fr", "nl", "en")


def _checksums() -> dict[str, str]:
    lines = (ROOT / "CHECKSUMS").read_text(encoding="utf-8").splitlines()
    return {name: digest for digest, name in (line.split(maxsplit=1) for line in lines)}


def test_each_file_has_its_frozen_checksum():
    frozen = _checksums()
    files = sorted(str(p.relative_to(ROOT)) for p in ROOT.glob("*/*") if p.suffix in (".txt", ".json"))
    assert files == sorted(frozen)
    for name in files:
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == frozen[name], name


@pytest.mark.parametrize("lang", LANGUAGES)
def test_five_interviews_for_each_language(lang):
    assert len(list((ROOT / lang).glob("*.txt"))) == 5


@pytest.mark.parametrize("path", sorted(ROOT.glob("*/*.txt")), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_each_span_points_at_its_value(path):
    text = path.read_text(encoding="utf-8")
    spans = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert spans["details"]
    for span in spans["details"] + spans["keep"]:
        assert text[span["start"]:span["end"]] == span["value"]
