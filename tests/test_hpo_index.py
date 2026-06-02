"""Tests for OBO synonym-scope parsing and scope-filtered embedding text.

Robinson issue #9: the parser dropped the synonym scope tag, so a chatty
RELATED or BROAD synonym was embedded the same as an EXACT one. The parser now
keeps the scope and build_enriched_text embeds only EXACT and NARROW by default.
"""

from phenoscribe.hpo_index import (
    EMBEDDED_SCOPES,
    build_enriched_text,
    parse_obo,
)


def _term(scopes_and_texts, name="Term", definition=""):
    return {
        "id": "HP:0000001",
        "name": name,
        "definition": definition,
        "synonyms": [{"text": t, "scope": s} for s, t in scopes_and_texts],
        "parents": [],
        "is_obsolete": False,
    }


def test_parse_synonym_line_with_scope_and_type(tmp_path):
    obo = tmp_path / "mini.obo"
    obo.write_text(
        "format-version: 1.2\n\n"
        "[Term]\n"
        "id: HP:0100785\n"
        "name: Insomnia\n"
        'synonym: "Difficulty staying or falling asleep" EXACT layperson [orcid]\n'
        'synonym: "Inability to sleep" EXACT []\n'
        'synonym: "Hydrocele" BROAD []\n'
        'synonym: "Loose association" RELATED layperson []\n'
        'synonym: "No scope token here"\n'
    )
    terms = parse_obo(str(obo))
    syns = terms[0]["synonyms"]

    by_text = {s["text"]: s["scope"] for s in syns}
    assert by_text["Difficulty staying or falling asleep"] == "EXACT"
    assert by_text["Inability to sleep"] == "EXACT"
    assert by_text["Hydrocele"] == "BROAD"
    assert by_text["Loose association"] == "RELATED"
    # OBO spec: a synonym with no explicit scope defaults to RELATED.
    assert by_text["No scope token here"] == "RELATED"


def test_real_insomnia_synonyms_carry_scope():
    obo = "/Users/tarikjamoulle/projects/hpo_identifier/data/hpo/hp.obo"
    terms = parse_obo(obo)
    insomnia = next(t for t in terms if t["id"] == "HP:0100785")
    assert insomnia["synonyms"], "Insomnia should have synonyms"
    # Every synonym must carry a valid OBO scope.
    for s in insomnia["synonyms"]:
        assert s["scope"] in {"EXACT", "NARROW", "BROAD", "RELATED"}
    # In this release both Insomnia synonyms are EXACT.
    assert all(s["scope"] == "EXACT" for s in insomnia["synonyms"])


def test_build_enriched_text_excludes_broad_and_related():
    term = _term(
        [
            ("EXACT", "Testicular hydrocele"),
            ("NARROW", "Congenital hydrocele"),
            ("BROAD", "Hydrocele"),
            ("RELATED", "Scrotal swelling"),
        ],
        name="Hydrocele testis",
        definition="Fluid around the testis.",
    )
    text = build_enriched_text(term)

    assert "Testicular hydrocele" in text
    assert "Congenital hydrocele" in text
    assert "Hydrocele," not in text  # the BROAD bare "Hydrocele" is dropped
    assert "Hydrocele testis" in text  # the name still leads
    assert "Scrotal swelling" not in text
    assert "Fluid around the testis." in text


def test_build_enriched_text_scope_override_restores_all():
    term = _term([("BROAD", "Hydrocele"), ("RELATED", "Scrotal swelling")])
    default_text = build_enriched_text(term)
    assert "Synonyms:" not in default_text  # nothing in EXACT/NARROW

    all_scopes = frozenset({"EXACT", "NARROW", "BROAD", "RELATED"})
    widened = build_enriched_text(term, all_scopes)
    assert "Hydrocele" in widened
    assert "Scrotal swelling" in widened


def test_default_embedded_scopes_are_exact_and_narrow():
    assert EMBEDDED_SCOPES == frozenset({"EXACT", "NARROW"})
