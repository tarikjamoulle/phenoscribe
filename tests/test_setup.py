"""Tests for `phenoscribe setup`: each part downloads once, then is skipped."""

from pathlib import Path

import pytest
from conftest import MINI_OBO

from phenoscribe import hpo_index, pii, transcribe
from phenoscribe.cli import main
from phenoscribe.config import Config
from phenoscribe.hpo_index import DEFAULT_HPO_RELEASE as RELEASE
from phenoscribe.hpo_index import index_ready, obo_ready, obo_url, seed_pinned_index


def _write_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "paths:\n"
        f"  hpo_obo: '{tmp_path / 'hpo' / 'hp.obo'}'\n"
        f"  chroma_db: '{tmp_path / 'chroma'}'\n"
        f"hpo:\n  release: '{RELEASE}'\n",
        encoding="utf-8",
    )
    return config


class FakeHub:
    """Stands in for the Hugging Face cache: a model is cached after one download."""

    def __init__(self):
        self.cached: set[str] = set()
        self.downloads: list[str] = []

    def fetcher(self, name):
        def download(*args, local_files_only=False):
            if local_files_only:
                if name not in self.cached:
                    raise FileNotFoundError(name)
            else:
                self.downloads.append(name)
                self.cached.add(name)
            return f"/cache/{name}"

        return download

    def by_model(self, model, local_files_only=False):
        """A privacy model download, keyed by the model name."""
        return self.fetcher(model)(local_files_only=local_files_only)


# The default map (config.pii.models): every model of every language, once.
PII_MODELS = [m for m in Config().pii.all_models() if m != pii.DEDUCE]


@pytest.fixture
def fake_downloads(monkeypatch):
    """Fake the network: the obo comes from MINI_OBO, the models from FakeHub."""
    hub = FakeHub()
    obo_downloads = []

    def fake_download_obo(obo_path, release):
        obo_downloads.append(obo_path)
        Path(obo_path).parent.mkdir(parents=True, exist_ok=True)
        Path(obo_path).write_text(MINI_OBO, encoding="utf-8")

    monkeypatch.setattr(hpo_index, "download_obo", fake_download_obo)
    monkeypatch.setattr(transcribe, "download_speech_model", hub.fetcher("speech"))
    monkeypatch.setattr(pii, "download_model", hub.by_model)
    return hub, obo_downloads


def _run_setup(capsys, config, *extra):
    main(["setup", "--config", str(config), *extra])
    return capsys.readouterr().out


def test_first_run_fetches_every_part(tmp_path, capsys, fake_downloads, index_model):
    hub, obo_downloads = fake_downloads
    out = _run_setup(capsys, _write_config(tmp_path))

    assert len(obo_downloads) == 1
    assert hub.downloads == ["speech", *PII_MODELS]
    assert "Downloading the HPO ontology" in out
    assert "Building the search index" in out
    assert "Downloading the speech model" in out
    for model in PII_MODELS:
        assert f"Downloading the privacy model {model} ({pii.size_text(model)}, one time only)..." in out
    assert "Setup done." in out


def test_second_run_does_nothing(tmp_path, monkeypatch, capsys, fake_downloads, index_model):
    hub, obo_downloads = fake_downloads
    config = _write_config(tmp_path)
    _run_setup(capsys, config)

    seeded = []
    monkeypatch.setattr(hpo_index, "seed_pinned_index", lambda *a, **k: seeded.append(a))
    out = _run_setup(capsys, config)

    assert len(obo_downloads) == 1
    assert seeded == []
    assert hub.downloads == ["speech", *PII_MODELS]
    for part in ("HPO ontology", "Search index", "Speech model", *(f"Privacy model {m}" for m in PII_MODELS)):
        assert f"{part}: ready." in out
    assert "Downloading" not in out and "Building" not in out


def test_no_models_skips_the_model_downloads(tmp_path, capsys, fake_downloads, index_model):
    hub, _ = fake_downloads
    out = _run_setup(capsys, _write_config(tmp_path), "--no-models")

    assert hub.downloads == []
    assert "skipped (--no-models)" in out
    assert index_ready(str(tmp_path / "chroma"), RELEASE)


def test_obo_url_uses_the_release_tag():
    assert obo_url(RELEASE) == (
        "https://github.com/obophenotype/human-phenotype-ontology/releases/"
        "download/v2026-02-16/hp.obo"
    )


def test_obo_ready_is_false_for_a_missing_or_other_release(tmp_path):
    obo = tmp_path / "hp.obo"
    assert not obo_ready(str(obo), RELEASE)
    obo.write_text(MINI_OBO, encoding="utf-8")
    assert obo_ready(str(obo), RELEASE)
    assert not obo_ready(str(obo), "hp/releases/2099-01-01")


def test_index_ready_needs_the_same_release_and_embedding(tmp_path, index_model):
    obo = tmp_path / "hp.obo"
    obo.write_text(MINI_OBO, encoding="utf-8")
    chroma = str(tmp_path / "chroma")
    assert not index_ready(chroma, RELEASE)

    seed_pinned_index(str(obo), chroma, RELEASE)
    assert index_ready(chroma, RELEASE)
    assert not index_ready(chroma, "hp/releases/2099-01-01")
    assert not index_ready(chroma, RELEASE, embedding_model="sapbert")


def test_an_index_without_the_completion_mark_is_not_ready(tmp_path, monkeypatch, index_model):
    # An interrupted seed stops before the last step, which sets the mark.
    obo = tmp_path / "hp.obo"
    obo.write_text(MINI_OBO, encoding="utf-8")
    chroma = str(tmp_path / "chroma")
    monkeypatch.setattr(
        hpo_index.chromadb.api.models.Collection.Collection, "modify", lambda *a, **k: None
    )
    seed_pinned_index(str(obo), chroma, RELEASE)
    assert not index_ready(chroma, RELEASE)


def test_setup_never_downloads_deduce(tmp_path, capsys, fake_downloads, index_model):
    # DEDUCE is a package of the optional extra, not a model in the cache.
    hub, _ = fake_downloads
    config = _write_config(tmp_path)
    config.write_text(config.read_text() + "pii:\n  models:\n    nl: ['deduce']\n", encoding="utf-8")
    _run_setup(capsys, config)
    assert "deduce" not in hub.downloads
