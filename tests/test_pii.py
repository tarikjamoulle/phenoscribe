"""Tests for the local detection of personal details.

The tests stub the NER pipeline, so they run offline and give the same result
at each run. The clean text comes from review.clean_text, as in the pipeline.
"""

import re

import pytest
from conftest import SHIPPED_CONFIG

from phenoscribe import pii
from phenoscribe.config import Config, PIIConfig, load_config
from phenoscribe.review import Review, clean_text


class _FakeNER:
    """Stand-in for a transformers NER pipeline.

    Returns pre-baked entities whose offsets match the input text. Mimics
    aggregation_strategy="simple": entity_group has no B-/I- prefix.
    """

    def __init__(self, entities_for):
        self._entities_for = entities_for

    def __call__(self, text):
        return self._entities_for(text)


def _install_fake_ner(monkeypatch, entities_for):
    fake = _FakeNER(entities_for)
    monkeypatch.setattr(pii, "_get_ner", lambda *a, **k: (fake, "fake-model"))
    return fake


def _clean(text, **kwargs):
    """(clean text, placeholder -> value), the way the pipeline makes it."""
    spans = pii.find_personal_details(text, **kwargs)
    return clean_text(Review.from_spans("a.txt", "A", text, spans))


def _span(text, sub, label, score=0.99):
    start = text.index(sub)
    return {"entity_group": label, "word": sub, "start": start, "end": start + len(sub), "score": score}


# --- regex layer (deterministic, model-independent) ---------------------------

def test_regex_redacts_email_phone_date_id(monkeypatch):
    _install_fake_ner(monkeypatch, lambda t: [])
    text = (
        "Rendez-vous le 15/03/2023. Email: jean.test@gmail.com, "
        "tel 0498 12 34 56, NISS 85.07.30-033.61."
    )
    out, mapping = _clean(text)

    assert "jean.test@gmail.com" not in out
    assert "0498 12 34 56" not in out
    assert "15/03/2023" not in out
    assert "85.07.30-033.61" not in out
    assert any(k.startswith("EMAIL_") for k in mapping)
    assert any(k.startswith("PHONE_") for k in mapping)
    assert any(k.startswith("DATE_") for k in mapping)
    assert any(k.startswith("ID_") for k in mapping)


# --- label mapping for the French PII model ----------------------------------

def test_pii_model_labels_map_to_categories(monkeypatch):
    text = "Le patient Dupont habite a Bruxelles, secteur Erasme."

    def entities(t):
        return [
            _span(t, "Dupont", "NOM_PERSONNE"),
            _span(t, "Bruxelles", "VILLE"),
            _span(t, "Erasme", "NOM_SOCIETE"),
        ]

    _install_fake_ner(monkeypatch, entities)
    out, mapping = _clean(text)

    assert "Dupont" not in out
    assert "Bruxelles" not in out
    assert mapping["PERSON_1"] == "Dupont"
    assert mapping["LOCATION_1"] == "Bruxelles"
    assert mapping["ORGANIZATION_1"] == "Erasme"


def test_general_model_labels_still_map(monkeypatch):
    """Fallback model uses PER/LOC/ORG/MISC; these must still map."""
    text = "Dr Martin travaille a l hopital Saint-Pierre a Liege."

    def entities(t):
        return [
            _span(t, "Martin", "PER"),
            _span(t, "Saint-Pierre", "ORG"),
            _span(t, "Liege", "LOC"),
        ]

    _install_fake_ner(monkeypatch, entities)
    _, mapping = _clean(text)

    assert mapping["PERSON_1"] == "Martin"
    assert mapping["ORGANIZATION_1"] == "Saint-Pierre"
    assert mapping["LOCATION_1"] == "Liege"


def test_a_repeated_value_gets_one_placeholder(monkeypatch):
    text = "Dupont est arrive. Plus tard, Dupont est reparti."

    def entities(t):
        out = []
        start = 0
        while True:
            idx = t.find("Dupont", start)
            if idx == -1:
                break
            out.append({"entity_group": "NOM_PERSONNE", "word": "Dupont",
                        "start": idx, "end": idx + 6, "score": 0.99})
            start = idx + 6
        return out

    _install_fake_ner(monkeypatch, entities)
    out, mapping = _clean(text)

    assert out.count("PERSON_1") == 2
    assert "Dupont" not in out
    assert list(mapping.keys()) == ["PERSON_1"]


def test_low_confidence_dropped(monkeypatch):
    text = "Peut-etre Machin, on ne sait pas."
    _install_fake_ner(monkeypatch, lambda t: [_span(t, "Machin", "NOM_PERSONNE", score=0.3)])
    out, mapping = _clean(text, min_score=0.6)
    assert "Machin" in out
    assert mapping == {}


# --- subword/offset mapping (privacy: no leaked fragments) -------------------

def test_subword_spans_replace_full_entity_no_leftover(monkeypatch):
    """NER subword spans must map back to full character offsets.

    Real CamemBERT NER with aggregation_strategy="simple" can return spans
    that cover only the first subword token of a multi-subword or accented
    name, and that butt directly against an adjacent entity. The removal
    must still replace each entity fully: no placeholder may sit next to a
    leftover letter, and no fragment of the original name may survive.
    """
    text = "Mme Gaëlle au CHU Erasme Bruxelles voit le gynéco de l'X."

    # Deliberately truncated / boundary-misaligned spans, the way the model's
    # tokenizer reports them for accented and multi-subword entities.
    def entities(t):
        def trunc(sub, n, label):
            start = t.index(sub)
            return {"entity_group": label, "word": sub[:n],
                    "start": start, "end": start + n, "score": 0.99}
        return [
            trunc("Gaëlle", 2, "NOM_PERSONNE"),   # only "Ga" -> would leak "ëlle"
            trunc("Erasme", 3, "NOM_SOCIETE"),
            # two adjacent locations
            trunc("Erasme Bruxelles", len("Erasme"), "VILLE"),
            trunc("Bruxelles", 4, "VILLE"),
            trunc("gynéco", 1, "JOBTITLE"),        # only "g" -> would leak "ynéco"
            trunc("X", 1, "NOM_SOCIETE"),
        ]

    _install_fake_ner(monkeypatch, entities)
    out, _ = _clean(text)

    # A placeholder (CATEGORY_N) must never be glued to a leftover letter on
    # either side. Match the whole placeholder token and check its neighbours.
    placeholder = r"(?:PERSON|LOCATION|ORGANIZATION|JOB)_\d+"
    assert not re.search(r"[A-Za-zÀ-ÿ]" + placeholder, out), out  # letter before
    assert not re.search(placeholder + r"[A-Za-zÀ-ÿ]", out), out  # letter after

    # The original name fragments must be gone entirely.
    for frag in ("Gaëlle", "ëlle", "gynéco", "ynéco", "Bruxelles", "Erasme"):
        assert frag not in out, (frag, out)


# --- spans and filters ---------------------------------------------------------


def test_find_personal_details_gives_value_type_score_and_origin(monkeypatch):
    text = "Mme Dupont, née le 15/03/1970."
    _install_fake_ner(monkeypatch, lambda t: [_span(t, "Dupont", "NOM_PERSONNE", score=0.91)])
    spans = pii.find_personal_details(text)
    assert [(s.value, s.category, s.origin) for s in spans] == [
        ("Dupont", "PERSON", "model"), ("15/03/1970", "DATE", "regex"),
    ]
    assert spans[0].score == pytest.approx(0.91)
    assert all(text[s.start:s.end] == s.value for s in spans)


def test_timestamps_are_hidden_from_the_model_and_stay_in_the_text(monkeypatch):
    text = "[27:25] docteur: Bonjour madame Dupont. [27:32] patient: j'ai 2 enfants."
    seen = []

    def ner(t):
        seen.append(t)
        return [_span(t, "Dupont", "NOM_PERSONNE"), _span(t, "2", "NUM_DOSSIER")]

    _install_fake_ner(monkeypatch, ner)
    spans = pii.find_personal_details(text)
    assert "27" not in seen[0] and len(seen[0]) == len(text)
    assert [s.value for s in spans] == ["Dupont"]  # "2" is too short for an ID
    out, _ = _clean(text)
    assert out.startswith("[27:25] docteur:") and "[27:32]" in out


def test_a_single_letter_is_never_a_name(monkeypatch):
    text = "Elle disait m'avoir vu chez M. Dupont."
    _install_fake_ner(monkeypatch, lambda t: [
        {"entity_group": "NOM_PERSONNE", "word": "m", "start": 12, "end": 13, "score": 0.9},
        _span(t, "Dupont", "NOM_PERSONNE"),
    ])
    assert [s.value for s in pii.find_personal_details(text)] == ["Dupont"]


def test_a_long_number_is_still_an_id(monkeypatch):
    text = "Mon numéro de dossier est 4471209."
    _install_fake_ner(monkeypatch, lambda t: [_span(t, "4471209", "NUM_DOSSIER")])
    assert [s.value for s in pii.find_personal_details(text)] == ["4471209"]


def test_config_default_matches_pii_module_default():
    """config.py PIIConfig default must equal the pii.py module default."""
    assert PIIConfig().models_for("fr")[0] == pii.DEFAULT_MODEL
    assert PIIConfig().models["default"] == [pii.GLINER_MODEL]
    assert PIIConfig().fallback_model == pii.FALLBACK_MODEL


def test_yaml_config_matches_pii_module_default():
    """The shipped config.yaml must point at the models pii.py defaults to."""
    cfg = load_config(SHIPPED_CONFIG)
    assert cfg.pii.models_for("fr")[0] == pii.DEFAULT_MODEL
    assert cfg.pii.fallback_model == pii.FALLBACK_MODEL
    for language in ("fr", "nl", "en", "de"):
        assert pii.GLINER_MODEL in cfg.pii.models_for(language)


def test_the_map_gives_the_models_of_a_language():
    pii_config = Config().pii
    assert pii_config.models_for("nl") == [pii.GLINER_MODEL, pii.DEDUCE]  # DEDUCE: skipped when absent
    assert not any("OpenMed" in m for m in pii_config.all_models())  # non-commercial training data
    assert pii_config.models_for("de") == [pii.GLINER_MODEL]  # not in the map: "default"
    assert pii_config.fallback_for("fr") == pii.FALLBACK_MODEL
    assert pii_config.fallback_for("nl") is None  # the CamemBERT fallback is French only


def test_a_config_with_one_language_keeps_the_other_languages():
    one = PIIConfig(models={"fr": ["Anonym-IA/V2-camembert-ner-pii-french"]})
    assert one.models_for("fr") == ["Anonym-IA/V2-camembert-ner-pii-french"]
    assert one.models_for("en")[0] == "StanfordAIMI/stanford-deidentifier-base"


def test_dutch_runs_without_deduce(monkeypatch):
    # The default Dutch map names DEDUCE; without the optional install it is skipped.
    monkeypatch.setattr(pii, "deduce_installed", lambda: False)
    _install_fake_models(monkeypatch, {pii.GLINER_MODEL: lambda t: [_span(t, "Hilde", "PERSON")]})
    spans, ran = pii.detect_personal_details("Ik ben Hilde.", models=PIIConfig().models_for("nl"))
    assert [s.value for s in spans] == ["Hilde"]
    assert ran == [pii.GLINER_MODEL]


def _loader(monkeypatch, broken="broken"):
    """A fresh model cache whose loader fails for ``broken``. Returns the names it tried."""
    tries = []

    def load(name):
        tries.append(name)
        if name == broken:
            raise OSError(f"cannot load {name}")
        return _FakeNER(lambda t: [])

    monkeypatch.setattr(pii, "_load", load)
    monkeypatch.setattr(pii, "_ner_pipelines", {})
    monkeypatch.setattr(pii, "_load_errors", {})
    return tries


def test_the_fallback_model_is_reported_when_it_ran(monkeypatch):
    _loader(monkeypatch)
    _, ran = pii.detect_personal_details("Bonjour.", models=["broken", "b"], fallback_model="fallback")
    assert ran == ["fallback", "b"]


def test_a_model_that_failed_to_load_is_not_tried_again(monkeypatch):
    tries = _loader(monkeypatch)
    for _ in range(2):
        assert pii._get_ner("broken", "fallback")[1] == "fallback"
    with pytest.raises(OSError):
        pii._get_ner("broken", None)
    assert tries == ["broken", "fallback"]


@pytest.mark.parametrize("length, calls", [(pii.CHUNK_CHARS, 1), (pii.CHUNK_CHARS + 200, 2)])
def test_a_text_longer_than_one_chunk_is_cut(length, calls):
    seen = []
    pii._detect_entities(lambda t: seen.append(t) or [], ("mot. " * length)[:length])
    assert len(seen) == calls
    assert max(len(t) for t in seen) <= pii.CHUNK_CHARS


# --- several models for a language (config.pii.models) -------------------------


def _install_fake_models(monkeypatch, by_name):
    """One fake detector for each model name. A missing name fails to load."""
    def get(name, fallback=None):
        if name not in by_name:
            raise OSError(f"cannot load {name}")
        return _FakeNER(by_name[name]), name
    monkeypatch.setattr(pii, "_get_ner", get)


def test_the_spans_of_all_models_are_kept(monkeypatch):
    text = "Ik ben Hilde, mijn dokter is Mertens in Geel."
    _install_fake_models(monkeypatch, {
        "a": lambda t: [_span(t, "Hilde", "FIRSTNAME")],
        "b": lambda t: [_span(t, "Mertens", "person"), _span(t, "Geel", "CITY")],
    })
    found = [s.value for s in pii.find_personal_details(text, models=["a", "b"])]
    assert found == ["Hilde", "Mertens", "Geel"]


def test_overlapping_spans_of_two_models_merge_into_their_union(monkeypatch):
    # One model stops before the year, the regex starts after the label: the
    # union removes the whole date. Dropping the shorter span left "1991".
    text = "uw geboortedatum 11/12/1991 graag"
    _install_fake_models(monkeypatch, {
        "a": lambda t: [_span(t, "geboortedatum 11/12", "DATEOFBIRTH")],
    })
    (span,) = pii.find_personal_details(text, models=["a"])
    assert span.value == "geboortedatum 11/12/1991"
    assert span.category == "DATE"


def test_a_second_model_that_cannot_load_stops_the_removal(monkeypatch):
    # Fails closed: no fallback for a model after the first one.
    _install_fake_models(monkeypatch, {"a": lambda t: []})
    with pytest.raises(OSError):
        pii.find_personal_details("Bonjour Claire.", models=["a", "missing"])


def test_deduce_is_skipped_when_it_is_not_installed(monkeypatch, caplog):
    _install_fake_models(monkeypatch, {"a": lambda t: [_span(t, "Hilde", "FIRSTNAME")]})
    monkeypatch.setattr(pii, "deduce_installed", lambda: False)
    spans = pii.find_personal_details("Hilde belt op 0494 37 12 08.", models=["a", pii.DEDUCE])
    assert [s.value for s in spans] == ["Hilde", "0494 37 12 08"]
    assert "DEDUCE is not installed" in caplog.text


def test_deduce_tags_map_to_the_categories(monkeypatch):
    if not pii.deduce_installed():
        pytest.skip("DEDUCE is an optional install (uv sync --extra dutch-rules)")
    monkeypatch.delitem(pii._ner_pipelines, pii.DEDUCE, raising=False)
    spans = pii.find_personal_details("Mevrouw Hilde Peeters belde.", models=[pii.DEDUCE], min_score=0.6)
    assert any(s.category == "PERSON" and "Peeters" in s.value for s in spans)


@pytest.mark.parametrize("date", ["4 mei 1962", "29 augustus 1959", "2 March 1984", "March 2, 1984",
                                  "30 June 1956", "14 février 1967", "1er janvier 1950"])
def test_dates_in_french_dutch_and_english(monkeypatch, date):
    _install_fake_ner(monkeypatch, lambda t: [])
    assert [s.value for s in pii.find_personal_details(f"geboren op {date}, ja")] == [date]


@pytest.mark.parametrize("phone", ["+32 470 98 76 54", "083 21 47 65", "015 42 18 77", "09 224 31 50",
                                   "02/345.67.89", "0476 18 42 90", "+33 6 12 34 56 78"])
def test_belgian_fixed_and_mobile_phones(monkeypatch, phone):
    _install_fake_ner(monkeypatch, lambda t: [])
    spans = pii.find_personal_details(f"bel {phone}.")
    assert [(s.value, s.category) for s in spans] == [(phone, "PHONE")]


@pytest.mark.parametrize("text", ["il y a 3 ans", "score 0 sur 10", "en 2019 puis 2020", "10 mg 2 fois"])
def test_counts_are_not_phones(monkeypatch, text):
    _install_fake_ner(monkeypatch, lambda t: [])
    assert pii.find_personal_details(text) == []


def test_a_long_unpunctuated_text_is_cut_at_spaces():
    text = "mot " * 1000
    chunks = pii._split_into_chunks(text, max_chars=1200)
    assert "".join(chunks) == text
    assert max(len(c) for c in chunks) <= 1200


@pytest.mark.parametrize("found, value", [
    ("Dokter Mertens", "Mertens"), ("Mrs Grace Okafor", "Grace Okafor"), ("docteur Lemaire", "Lemaire"),
])
def test_a_title_at_the_edge_of_a_span_stays_in_the_text(monkeypatch, found, value):
    text = f"Ja, {found} zei het."
    _install_fake_ner(monkeypatch, lambda t: [_span(t, found, "PER")])
    assert [s.value for s in pii.find_personal_details(text)] == [value]


def test_a_speaker_label_is_never_a_name(monkeypatch):
    text = "Médecin : bonjour.\nPatient : bonjour."
    _install_fake_ner(monkeypatch, lambda t: [_span(t, "Médecin", "PER", 0.62)])
    assert pii.find_personal_details(text) == []


def test_arts_is_a_family_name_and_stays_removed(monkeypatch):
    # "Arts" is a common Flemish family name and "arts" a Dutch word for doctor.
    text = "Dokter Arts belde."
    _install_fake_ner(monkeypatch, lambda t: [_span(t, "Dokter Arts", "PER")])
    assert [s.value for s in pii.find_personal_details(text)] == ["Arts"]


@pytest.mark.parametrize("found", ["Le patient", "La patiente", "De patiënt", "The patient", "mijn dokter"])
def test_an_article_before_a_title_is_no_name(monkeypatch, found):
    text = f"{found} dit que le genou fait mal."
    _install_fake_ner(monkeypatch, lambda t: [_span(t, found, "PER", 0.97)])
    assert pii.find_personal_details(text) == []


def test_a_number_span_keeps_its_digits(monkeypatch):
    text = "Bel 0476 18 42 90 morgen."
    _install_fake_ner(monkeypatch, lambda t: [_span(t, "0476 18 42 90", "TELEPHONE")])
    assert [s.value for s in pii.find_personal_details(text)] == ["0476 18 42 90"]


def test_a_hit_on_an_apostrophe_only_removes_nothing():
    text = "Depuis trois mois, j'ai des maux de tête."
    at = text.index("'")
    ner = lambda _: [{"entity_group": "TIME", "start": at, "end": at + 1, "score": 0.77}]  # noqa: E731
    assert pii._model_spans(ner, text, min_score=0.6) == []
