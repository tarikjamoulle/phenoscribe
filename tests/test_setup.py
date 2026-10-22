"""Tests for `phenoscribe setup`: each part downloads once, then is skipped."""

from pathlib import Path

import pytest

from phenoscribe import hpo_index, pii, transcribe
from phenoscribe.cli import main
from phenoscribe.hpo_index import index_ready, obo_ready, obo_url, seed_pinned_index

RELEASE = "hp/releases/2026-02-16"

MINI_OBO = (
    "format-version: 1.2\n"
    f"data-version: {RELEASE}\n\n"
    "[Term]\n"
    "id: HP:0001945\n"
    "name: Fever\n"
    'synonym: "Pyrexia" EXACT []\n'
)


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
    monkeypatch.setattr(pii, "download_model", hub.fetcher("pii"))
    return hub, obo_downloads


def _run_setup(monkeypatch, capsys, config, *extra):
    monkeypatch.setattr("sys.argv", ["phenoscribe", "setup", "--config", str(config), *extra])
    main()
    return capsys.readouterr().out


def test_first_run_fetches_every_part(tmp_path, monkeypatch, capsys, fake_downloads):
    hub, obo_downloads = fake_downloads
    out = _run_setup(monkeypatch, capsys, _write_config(tmp_path))

    assert len(obo_downloads) == 1
    assert hub.downloads == ["speech", "pii"]
    assert "Downloading the HPO ontology" in out
    assert "Building the search index" in out
    assert "Downloading the speech model (3 GB, one time only)..." in out
    assert "Downloading the privacy model" in out
    assert "Setup done." in out


def test_second_run_does_nothing(tmp_path, monkeypatch, capsys, fake_downloads):
    hub, obo_downloads = fake_downloads
    config = _write_config(tmp_path)
    _run_setup(monkeypatch, capsys, config)

    seeded = []
    monkeypatch.setattr(hpo_index, "seed_pinned_index", lambda *a, **k: seeded.append(a))
    out = _run_setup(monkeypatch, capsys, config)

    assert len(obo_downloads) == 1
    assert seeded == []
    assert hub.downloads == ["speech", "pii"]
    for part in ("HPO ontology", "Search index", "Speech model", "Privacy model"):
        assert f"{part}: ready." in out
    assert "Downloading" not in out and "Building" not in out


def test_no_models_skips_the_model_downloads(tmp_path, monkeypatch, capsys, fake_downloads):
    hub, _ = fake_downloads
    out = _run_setup(monkeypatch, capsys, _write_config(tmp_path), "--no-models")

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


def test_index_ready_needs_the_same_release_and_embedding(tmp_path):
    obo = tmp_path / "hp.obo"
    obo.write_text(MINI_OBO, encoding="utf-8")
    chroma = str(tmp_path / "chroma")
    assert not index_ready(chroma, RELEASE)

    seed_pinned_index(str(obo), chroma, RELEASE)
    assert index_ready(chroma, RELEASE)
    assert not index_ready(chroma, "hp/releases/2099-01-01")
    assert not index_ready(chroma, RELEASE, embedding_model="sapbert")
