"""Reading the input files (transcribe.py): text, Word and audio through the Whisper backends."""

import sys
import types
from unittest.mock import patch

import pytest
from docx import Document

from phenoscribe import transcribe
from phenoscribe.text import TIMESTAMP_PATTERN
from phenoscribe.transcribe import EmptyTranscriptError, read_docx, read_input, resolve_backend, timed_text


def test_text_file_passes_through(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("bonjour", encoding="utf-8")
    assert read_input(str(note)) == "bonjour"


def test_text_file_with_bom_loses_the_bom(tmp_path):
    note = tmp_path / "note.txt"
    note.write_bytes("\ufeffbonjour".encode("utf-8"))
    assert read_input(str(note)) == "bonjour"


def test_windows_1252_text_file_is_read(tmp_path):
    note = tmp_path / "note.txt"
    note.write_bytes("fièvre à 39°".encode("cp1252"))
    assert read_input(str(note)) == "fièvre à 39°"


@pytest.mark.parametrize("content", ["", "  \n\t \n"])
def test_text_file_without_text_fails_early(tmp_path, content):
    note = tmp_path / "note.txt"
    note.write_text(content, encoding="utf-8")
    with pytest.raises(EmptyTranscriptError, match="the file has no text"):
        read_input(str(note))


def test_unsupported_extension_raises(tmp_path):
    bogus = tmp_path / "thing.xyz"
    bogus.touch()
    with pytest.raises(ValueError, match="Unsupported file type"):
        read_input(str(bogus))


def test_unknown_backend_raises(tmp_path):
    audio = tmp_path / "fake.mp3"
    audio.touch()
    with pytest.raises(ValueError, match="Unknown transcription backend"):
        read_input(str(audio), backend="cloud", model_name="x", language="fr")


def test_faster_whisper_dispatch(tmp_path):
    audio = tmp_path / "fake.mp3"
    audio.touch()
    with patch(
        "phenoscribe.transcribe._transcribe_faster_whisper", return_value="hello"
    ) as fake_fw, patch("phenoscribe.transcribe._transcribe_mlx") as fake_mlx:
        result = read_input(
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
        result = read_input(
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
        assert read_input(str(audio), backend="auto") == "bonjour"
    fake_fw.assert_called_once()


def test_timed_text_puts_the_start_time_before_each_segment():
    text = timed_text([(0.0, " Bonjour docteur."), (83.6, " J'ai mal "), (90.0, "  "),
                       (3725.2, "Merci.")])
    assert text == "[00:00] Bonjour docteur.\n[01:23] J'ai mal\n[1:02:05] Merci."
    # The personal-detail removal sees each marker as a timestamp.
    assert len(TIMESTAMP_PATTERN.findall(text)) == 3


def test_mlx_and_faster_whisper_both_give_timed_text(tmp_path, monkeypatch):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    fake_mlx = types.SimpleNamespace(transcribe=lambda *a, **k: {
        "text": "Bonjour. Ça va.", "segments": [{"start": 0.0, "text": " Bonjour."},
                                                {"start": 4.2, "text": " Ça va."}]})
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx)
    assert transcribe._transcribe_mlx(audio, "large-v3", "fr") == "[00:00] Bonjour.\n[00:04] Ça va."

    segment = types.SimpleNamespace(start=61.0, text=" Oui.")
    model = types.SimpleNamespace(
        transcribe=lambda *a, **k: (iter([segment]), types.SimpleNamespace(duration=62.0)))
    monkeypatch.setattr(transcribe, "_get_faster_whisper_model", lambda *a: model)
    assert transcribe._transcribe_faster_whisper(audio, "large-v3", "fr", "cpu") == "[01:01] Oui."


def test_read_docx_keeps_headers_paragraphs_and_tables_in_order(tmp_path):
    doc = Document()
    doc.sections[0].header.paragraphs[0].text = "Dossier de Mme Lambert"
    header_table = doc.sections[0].header.add_table(rows=1, cols=1, width=doc.sections[0].page_width)
    header_table.cell(0, 0).text = "Patient: Claire Lambert"
    doc.add_paragraph("Premier paragraphe.")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Téléphone", "0472 12 34 56"
    doc.add_paragraph("Dernier paragraphe.")
    path = tmp_path / "chemin avec espaces é" / "004.docx"
    path.parent.mkdir()
    doc.save(str(path))
    assert read_docx(path) == (
        "Dossier de Mme Lambert\nPatient: Claire Lambert\nPremier paragraphe.\nTéléphone | 0472 12 34 56\nDernier paragraphe."
    )
