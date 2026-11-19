"""The search index (hpo_index.py): synonym scopes, the embedded text, the exact name
lookup and the index checks.

The parser keeps the synonym scope tag, so build_enriched_text embeds only
EXACT and NARROW synonyms by default. A chatty RELATED or BROAD synonym would
otherwise weigh the same as an EXACT one.
"""

import chromadb
import pytest
from conftest import MINI_OBO

from phenoscribe.hpo_index import (
    DEFAULT_HPO_RELEASE,
    INDEX_SCHEMA,
    IndexMissing,
    build_enriched_text,
    index_ready,
    parse_obo,
    search_hpo,
    seed_chromadb,
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


def test_real_insomnia_synonyms_carry_scope(hpo_obo):
    terms = parse_obo(hpo_obo)
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


# --- exact name lookup and one document for each synonym ---------------------

EXACT_OBO = MINI_OBO + (
    "\n[Term]\n"
    "id: HP:0002094\n"
    "name: Dyspnea\n"
    'synonym: "Shortness of breath" EXACT layperson []\n'
    'synonym: "Breathlessness" NARROW []\n'
    "\n[Term]\n"
    "id: HP:0002527\n"
    "name: Falls\n"
    "\n[Term]\n"
    "id: HP:9999901\n"
    "name: Twin A\n"
    'synonym: "Same name" EXACT []\n'
    "\n[Term]\n"
    "id: HP:9999902\n"
    "name: Twin B\n"
    'synonym: "Same-name" EXACT []\n'
)


@pytest.fixture
def exact_index(tmp_path, index_model):
    """(obo path, index path) of MINI_OBO plus four terms for the name lookup."""
    obo = tmp_path / "exact.obo"
    obo.write_text(EXACT_OBO)
    chroma_path = str(tmp_path / "chroma")
    seed_chromadb(str(obo), chroma_path, embedding_model="default")
    return str(obo), chroma_path


def test_an_exact_synonym_comes_first(exact_index):
    obo, chroma = exact_index
    hits = search_hpo("Shortness  of-breath", k=3, chroma_path=chroma, obo_path=obo)
    assert hits[0]["hpo_id"] == "HP:0002094" and hits[0]["exact"] is True
    assert hits[0]["ambiguous"] is False
    assert len({h["hpo_id"] for h in hits}) == len(hits) == 3  # no repeat


def test_a_singular_finds_the_plural_name(exact_index):
    obo, chroma = exact_index
    assert search_hpo("fall", k=2, chroma_path=chroma, obo_path=obo)[0]["hpo_id"] == "HP:0002527"


def test_a_name_of_two_terms_gives_both_marked_ambiguous(exact_index):
    obo, chroma = exact_index
    hits = search_hpo("same name", k=2, chroma_path=chroma, obo_path=obo)
    assert {h["hpo_id"] for h in hits[:2]} == {"HP:9999901", "HP:9999902"}
    assert all(h["ambiguous"] for h in hits[:2])


def test_without_obo_path_the_search_is_vector_only(exact_index):
    _, chroma = exact_index
    hits = search_hpo("shortness of breath", k=2, chroma_path=chroma)
    assert not any(h.get("exact") for h in hits)


def test_k_unique_terms_although_each_term_has_several_documents(exact_index):
    _, chroma = exact_index
    hits = search_hpo("breath", k=6, chroma_path=chroma)
    ids = [h["hpo_id"] for h in hits]
    assert len(ids) == len(set(ids)) == 6
    assert all("#" not in i for i in ids)


def test_an_index_of_the_old_layout_is_not_ready(exact_index):
    _, chroma = exact_index
    assert index_ready(chroma, DEFAULT_HPO_RELEASE)
    collection = chromadb.PersistentClient(path=chroma).get_collection("hpo_terms")
    meta = dict(collection.metadata)
    meta["index_schema"] = INDEX_SCHEMA - 1
    meta.pop("hnsw:space", None)  # chromadb does not allow a change of the distance
    collection.modify(metadata=meta)
    assert not index_ready(chroma, DEFAULT_HPO_RELEASE)


@pytest.mark.parametrize("make", ["nothing", "empty folder"])
def test_a_missing_index_names_the_setup_command(tmp_path, make):
    chroma = tmp_path / "chroma"
    if make == "empty folder":
        chroma.mkdir()
    with pytest.raises(IndexMissing, match="Run `phenoscribe setup`"):
        search_hpo("fever", chroma_path=str(chroma))
    assert chroma.exists() == (make == "empty folder")  # the search makes no folder
