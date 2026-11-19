"""The pinned HPO release: the version guard and the resolution of obsolete codes.

The obo on disk, the index and the config agree on one release. An obsolete
ground-truth code resolves to its replacement, so it stays in search.
"""

import pytest

from phenoscribe.hpo_index import (
    DEFAULT_HPO_RELEASE,
    HpoVersionMismatch,
    build_obsolete_map,
    check_obo_version,
    parse_obo,
    read_obo_terms,
    read_obo_version,
    resolve_obsolete,
)
from phenoscribe.output import write_excel
from phenoscribe.validate import load_codes_from_excel

# A tiny obo with a known obsolete-with-replaced_by term, an obsolete
# consider-only term, and a merged term (alt_id). Lets the resolution logic be
# checked without parsing the full 19k-term release.
OBSOLETE_OBO = """\
format-version: 1.2
data-version: hp/releases/2099-01-01

[Term]
id: HP:0000001
name: Live term
synonym: "alive" EXACT []
def: "A non-obsolete term." [HPO:probinson]

[Term]
id: HP:0000002
name: Replacement target

[Term]
id: HP:0000003
name: obsolete Old term
is_obsolete: true
replaced_by: HP:0000002

[Term]
id: HP:0000004
name: obsolete Consider-only term
is_obsolete: true
consider: HP:0000001
consider: HP:0000002

[Term]
id: HP:0000005
name: Merged survivor
alt_id: HP:0000099

[Typedef]
id: part_of
name: part of
"""


@pytest.fixture
def obsolete_obo(tmp_path) -> str:
    p = tmp_path / "mini.obo"
    p.write_text(OBSOLETE_OBO)
    return str(p)


# --- version header reading ---------------------------------------------------


def test_read_obo_version_real_release(hpo_obo):
    assert read_obo_version(hpo_obo) == DEFAULT_HPO_RELEASE


def test_read_obo_version_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_obo_version(str(tmp_path / "nope.obo"))


def test_read_obo_version_no_header_raises(tmp_path):
    p = tmp_path / "headerless.obo"
    p.write_text("[Term]\nid: HP:0000001\nname: x\n")
    with pytest.raises(ValueError):
        read_obo_version(str(p))


# --- the build/startup guard --------------------------------------------------


def test_check_obo_version_passes_on_match(hpo_obo):
    assert check_obo_version(hpo_obo, DEFAULT_HPO_RELEASE) == DEFAULT_HPO_RELEASE


def test_check_obo_version_raises_on_mismatch(hpo_obo):
    # The config pins 2025-05-06 but the obo on disk is 2026-02-16: the guard
    # raises.
    with pytest.raises(HpoVersionMismatch) as exc:
        check_obo_version(hpo_obo, "hp/releases/2025-05-06")
    assert "2025-05-06" in str(exc.value)
    assert DEFAULT_HPO_RELEASE in str(exc.value)


# --- obsolete / merged id resolution ------------------------------------------


def test_parse_obo_drops_obsolete_terms(obsolete_obo):
    ids = {t["id"] for t in parse_obo(obsolete_obo)}
    assert "HP:0000001" in ids
    assert "HP:0000003" not in ids  # obsolete
    assert "HP:0000004" not in ids  # obsolete


def test_read_obo_terms_keeps_obsolete_terms_flagged(obsolete_obo):
    flags = {t["id"]: t["is_obsolete"] for t in read_obo_terms(obsolete_obo)}
    assert flags["HP:0000003"] is True
    assert flags["HP:0000001"] is False
    assert "part_of" not in flags  # a [Typedef] is not a term


def test_resolve_replaced_by(obsolete_obo):
    m = build_obsolete_map(obsolete_obo)
    assert resolve_obsolete("HP:0000003", m) == "HP:0000002"


def test_resolve_merged_alt_id(obsolete_obo):
    m = build_obsolete_map(obsolete_obo)
    assert resolve_obsolete("HP:0000099", m) == "HP:0000005"


def test_consider_only_term_not_auto_resolved(obsolete_obo):
    # consider: needs human review, so we never rewrite it automatically.
    m = build_obsolete_map(obsolete_obo)
    assert resolve_obsolete("HP:0000004", m) == "HP:0000004"


def test_resolve_passthrough_for_live_id(obsolete_obo):
    m = build_obsolete_map(obsolete_obo)
    assert resolve_obsolete("HP:0000001", m) == "HP:0000001"


def test_resolve_real_obsolete_with_replaced_by(hpo_obo):
    # HP:0000057 "obsolete Clitoromegaly" -> replaced_by HP:0008665 in the
    # 2026-02-16 release. A real obsoleted term with a replacement.
    m = build_obsolete_map(hpo_obo)
    assert resolve_obsolete("HP:0000057", m) == "HP:0008665"


def test_resolve_real_ground_truth_obsolete_code(hpo_obo):
    # HP:0001322 occurs in a manual code list and is obsolete in 2026-02-16,
    # replaced_by HP:0006872. Without resolution it would never
    # match a current prediction.
    m = build_obsolete_map(hpo_obo)
    assert resolve_obsolete("HP:0001322", m) == "HP:0006872"


# --- ground-truth loading resolves obsolete codes -----------------------------


def test_load_codes_resolves_obsolete(tmp_path, obsolete_obo):
    out = tmp_path / "gt.xlsx"
    # Workbook carrying the obsolete code HP:0000003.
    write_excel(
        "PAT.001",
        [{"hpo_id": "HP:0000003", "hpo_term": "Old term", "patient_verbatim": "x"}],
        str(out),
        fmt="detailed",
    )
    obsolete_map = build_obsolete_map(obsolete_obo)

    raw = load_codes_from_excel(str(out))
    resolved = load_codes_from_excel(str(out), obsolete_map)
    assert raw["PAT.001"] == {"HP:0000003"}
    assert resolved["PAT.001"] == {"HP:0000002"}
