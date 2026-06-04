"""Tests for local PII pseudonymisation.

The redaction and label-mapping tests stub the NER pipeline so they run
offline and deterministically. One test loads the real configured model to
guard against drift between config.yaml, the docs, and the model pii.py loads.
"""

import re

import pytest

from phenoscribe import pii
from phenoscribe.config import PIIConfig, load_config


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
    out, mapping = pii.pseudonymize(text)

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
    out, mapping = pii.pseudonymize(text)

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
    out, mapping = pii.pseudonymize(text)

    assert mapping["PERSON_1"] == "Martin"
    assert mapping["ORGANIZATION_1"] == "Saint-Pierre"
    assert mapping["LOCATION_1"] == "Liege"


def test_same_value_gets_stable_pseudonym(monkeypatch):
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
    out, mapping = pii.pseudonymize(text)

    assert out.count("PERSON_1") == 2
    assert "Dupont" not in out
    assert list(mapping.keys()) == ["PERSON_1"]


def test_low_confidence_dropped(monkeypatch):
    text = "Peut-etre Machin, on ne sait pas."
    _install_fake_ner(monkeypatch, lambda t: [_span(t, "Machin", "NOM_PERSONNE", score=0.3)])
    out, mapping = pii.pseudonymize(text, min_score=0.6)
    assert "Machin" in out
    assert mapping == {}


# --- subword/offset mapping (privacy: no leaked fragments) -------------------

def test_subword_spans_replace_full_entity_no_leftover(monkeypatch):
    """NER subword spans must map back to full character offsets.

    Real CamemBERT NER with aggregation_strategy="simple" can return spans
    that cover only the first subword token of a multi-subword or accented
    name, and that butt directly against an adjacent entity. The pseudonymiser
    must still replace each entity fully: no placeholder may sit next to a
    leftover letter, and no fragment of the original name may survive.
    """
    text = "Mme Héloïse au CHU Erasme Bruxelles voit le gynéco de l'X."

    # Deliberately truncated / boundary-misaligned spans, the way the model's
    # tokenizer reports them for accented and multi-subword entities.
    def entities(t):
        def trunc(sub, n, label):
            start = t.index(sub)
            return {"entity_group": label, "word": sub[:n],
                    "start": start, "end": start + n, "score": 0.99}
        return [
            trunc("Héloïse", 2, "NOM_PERSONNE"),   # only "Hé" -> would leak "loïse"
            trunc("Erasme", 3, "NOM_SOCIETE"),
            # two adjacent locations
            trunc("Erasme Bruxelles", len("Erasme"), "VILLE"),
            trunc("Bruxelles", 4, "VILLE"),
            trunc("gynéco", 1, "JOBTITLE"),        # only "g" -> would leak "ynéco"
            trunc("X", 1, "NOM_SOCIETE"),
        ]

    _install_fake_ner(monkeypatch, entities)
    out, _ = pii.pseudonymize(text)

    # A placeholder (CATEGORY_N) must never be glued to a leftover letter on
    # either side. Match the whole placeholder token and check its neighbours.
    placeholder = r"(?:PERSON|LOCATION|ORGANIZATION|JOB)_\d+"
    assert not re.search(r"[A-Za-zÀ-ÿ]" + placeholder, out), out  # letter before
    assert not re.search(placeholder + r"[A-Za-zÀ-ÿ]", out), out  # letter after

    # The original name fragments must be gone entirely.
    for frag in ("Héloïse", "loïse", "gynéco", "ynéco", "Bruxelles", "Erasme"):
        assert frag not in out, (frag, out)


# --- no-drift guard: config vs code vs docs ----------------------------------

def test_config_default_matches_pii_module_default():
    """config.py PIIConfig default must equal the pii.py module default."""
    assert PIIConfig().model == pii.DEFAULT_MODEL
    assert PIIConfig().fallback_model == pii.FALLBACK_MODEL


def test_yaml_config_matches_pii_module_default():
    """The shipped config.yaml must point at the model pii.py defaults to."""
    cfg = load_config("config.yaml")
    assert cfg.pii.model == pii.DEFAULT_MODEL
    assert cfg.pii.fallback_model == pii.FALLBACK_MODEL


@pytest.mark.network
def test_configured_model_actually_loads():
    """No drift between docs/config and the model the code loads.

    Loads the configured default and asserts the resolved model name is one
    of the two we document. Skips if the hub is unreachable so the offline
    suite never fails on this.
    """
    cfg = load_config("config.yaml")
    try:
        resolved = pii.resolve_model(cfg.pii.model, cfg.pii.fallback_model)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"NER model could not be loaded (offline?): {exc}")
    assert resolved in (cfg.pii.model, cfg.pii.fallback_model)
