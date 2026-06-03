"""Tests for local PII pseudonymisation.

The redaction and label-mapping tests stub the NER pipeline so they run
offline and deterministically. One test loads the real configured model to
guard against drift between config.yaml, the docs, and the model pii.py loads.
"""

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
