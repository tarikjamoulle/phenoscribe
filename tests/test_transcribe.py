"""Tests for transcription backend dispatch."""

import sys
import types
from unittest.mock import patch

import pytest

from phenoscribe.transcribe import resolve_backend, transcribe


def test_text_file_passes_through(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("bonjour", encoding="utf-8")
    assert transcribe(str(note)) == "bonjour"


def test_unsupported_extension_raises(tmp_path):
    bogus = tmp_path / "thing.xyz"
    bogus.touch()
    with pytest.raises(ValueError, match="Unsupported file type"):
        transcribe(str(bogus))


def test_unknown_backend_raises(tmp_path):
    audio = tmp_path / "fake.mp3"
    audio.touch()
    with pytest.raises(ValueError, match="Unknown transcription backend"):
        transcribe(str(audio), backend="cloud", model_name="x", language="fr")


def test_faster_whisper_dispatch(tmp_path):
    audio = tmp_path / "fake.mp3"
    audio.touch()
    with patch(
        "phenoscribe.transcribe._transcribe_faster_whisper", return_value="hello"
    ) as fake_fw, patch("phenoscribe.transcribe._transcribe_mlx") as fake_mlx:
        result = transcribe(
            str(audio),
            backend="faster-whisper",
            model_name="large-v3",
            language="fr",
            device="cpu",
        )
    assert result == "hello"
    fake_fw.assert_called_once()
    fake_mlx.assert_not_called()


def test_mlx_dispatch(tmp_path):
    audio = tmp_path / "fake.mp3"
    audio.touch()
    with patch(
        "phenoscribe.transcribe._transcribe_mlx", return_value="hola"
    ) as fake_mlx, patch("phenoscribe.transcribe._transcribe_faster_whisper") as fake_fw:
        result = transcribe(
            str(audio), backend="mlx", model_name="large-v3", language="es"
        )
    assert result == "hola"
    fake_mlx.assert_called_once()
    fake_fw.assert_not_called()


# --- backend: "auto" ----------------------------------------------------------


def test_auto_is_faster_whisper_when_mlx_does_not_import(monkeypatch):
    # None in sys.modules makes `import mlx_whisper` raise ImportError.
    monkeypatch.setitem(sys.modules, "mlx_whisper", None)
    monkeypatch.setattr("phenoscribe.transcribe.shutil.which", lambda _: "/usr/bin/ffmpeg")
    assert resolve_backend("auto") == "faster-whisper"


def test_auto_is_faster_whisper_when_ffmpeg_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.ModuleType("mlx_whisper"))
    monkeypatch.setattr("phenoscribe.transcribe.shutil.which", lambda _: None)
    assert resolve_backend("auto") == "faster-whisper"


def test_auto_is_mlx_when_mlx_imports_and_ffmpeg_exists(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.ModuleType("mlx_whisper"))
    monkeypatch.setattr("phenoscribe.transcribe.shutil.which", lambda _: "/usr/bin/ffmpeg")
    assert resolve_backend("auto") == "mlx"


def test_explicit_backend_is_unchanged():
    assert resolve_backend("faster-whisper") == "faster-whisper"
    assert resolve_backend("mlx") == "mlx"


def test_auto_dispatches_to_the_resolved_backend(tmp_path, monkeypatch):
    audio = tmp_path / "fake.mp3"
    audio.touch()
    monkeypatch.setattr("phenoscribe.transcribe.resolve_backend", lambda _: "faster-whisper")
    with patch(
        "phenoscribe.transcribe._transcribe_faster_whisper", return_value="bonjour"
    ) as fake_fw:
        assert transcribe(str(audio), backend="auto") == "bonjour"
    fake_fw.assert_called_once()


def test_diarization_without_the_extra_says_how_to_install(monkeypatch, tmp_path):
    from phenoscribe import diarize

    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.setitem(sys.modules, "pyannote.audio", None)
    monkeypatch.setattr(diarize, "_pipeline_cache", None)
    with pytest.raises(ImportError, match="uv sync --extra diarize"):
        diarize.diarize(str(tmp_path / "a.mp3"))


def test_timed_text_puts_the_start_time_before_each_segment():
    from phenoscribe.pii import TIMESTAMP_PATTERN
    from phenoscribe.transcribe import timed_text

    text = timed_text([(0.0, " Bonjour docteur."), (83.6, " J'ai mal "), (90.0, "  "),
                       (3725.2, "Merci.")])
    assert text == "[00:00] Bonjour docteur.\n[01:23] J'ai mal\n[1:02:05] Merci."
    # The personal-detail removal sees each marker as a timestamp.
    assert len(TIMESTAMP_PATTERN.findall(text)) == 3


def test_mlx_and_faster_whisper_both_give_timed_text(tmp_path, monkeypatch):
    import sys
    import types

    from phenoscribe import transcribe as t

    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    fake_mlx = types.SimpleNamespace(transcribe=lambda *a, **k: {
        "text": "Bonjour. Ça va.", "segments": [{"start": 0.0, "text": " Bonjour."},
                                                {"start": 4.2, "text": " Ça va."}]})
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx)
    assert t._transcribe_mlx(audio, "large-v3", "fr") == "[00:00] Bonjour.\n[00:04] Ça va."

    segment = types.SimpleNamespace(start=61.0, text=" Oui.")
    model = types.SimpleNamespace(
        transcribe=lambda *a, **k: (iter([segment]), types.SimpleNamespace(duration=62.0)))
    monkeypatch.setattr(t, "_get_faster_whisper_model", lambda *a: model)
    assert t._transcribe_faster_whisper(audio, "large-v3", "fr", "cpu") == "[01:01] Oui."
