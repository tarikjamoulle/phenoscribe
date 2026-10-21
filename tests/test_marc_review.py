"""Marc's 2026-10 review of results.xlsx, pinned as facts about the HPO release.

Each flagged row in tests/fixtures/marc_review_2026-10.json names codes. These
tests check every named code against the pinned hp.obo, so the reply to Marc
and the replay script (scripts/validation/replay_marc_review.py) rest on the
real term names.
"""

import json
import os
from pathlib import Path

import pytest

from phenoscribe.hpo_index import parse_obo

FIXTURE = Path(__file__).parent / "fixtures" / "marc_review_2026-10.json"
OBO = "data/hpo/hp.obo"

pytestmark = pytest.mark.skipif(not os.path.isfile(OBO), reason=f"needs {OBO}")


@pytest.fixture(scope="module")
def names() -> dict[str, str]:
    return {t["id"]: t["name"] for t in parse_obo(OBO)}


@pytest.fixture(scope="module")
def review() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_every_named_code_exists(names, review):
    for row in review["rows"]:
        for code in [row["tool_code"], row["marc_code"], *row["accept"]]:
            if code:
                assert code in names, f"{row['patient_id']}: {code} not in {OBO}"


def test_reviewer_codes_that_differ_name_another_term(names, review):
    """Where Marc's code and ours disagree on the same meaning, the obo decides."""
    by_term = {r["clinical_term"]: r for r in review["rows"]}
    assert names["HP:0030157"] == "Flank pain"
    assert names[by_term["flank pain"]["marc_code"]] == "Microscopic hematuria"
    assert names[by_term["white tongue"]["marc_code"]] == "Impairment in personality functioning"
    assert names["HP:5200290"] == "Exploding head syndrome"
