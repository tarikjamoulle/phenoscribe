"""Tests for transcription backend dispatch."""

from unittest.mock import patch

import pytest

from phenoscribe.transcribe import transcribe


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
