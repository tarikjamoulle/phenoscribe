"""One file through the pipeline (pipeline.py): the input, the gate before the LLM and the
privacy models of each language. The detector and the LLM steps are stubs."""

import pytest
from docx import Document

from phenoscribe import pipeline
from phenoscribe.config import Config
from phenoscribe.naming import transcript_cache_path
from phenoscribe.output import ResultsWorkbook
from phenoscribe.pii import Span
from phenoscribe.review import DONE, NEEDS_REVIEW, add_value, approve, load_review, save_review
from phenoscribe.transcribe import EmptyTranscriptError

TEXT = "Bonjour madame Dupont, je suis fatiguée."


@pytest.fixture
def sent(monkeypatch):
    """Stub the detector (finds "Dupont") and both LLM steps; record the text the LLM gets."""
    texts = []

    def detect(text, **k):
        start = text.find("Dupont")
        return ([Span(start, start + 6, "Dupont", "PERSON", 0.95)] if start >= 0 else []), k["models"]

    monkeypatch.setattr(pipeline, "detect_personal_details", detect)
    monkeypatch.setattr(pipeline, "extract_symptoms", lambda text, **k: texts.append(text) or [])
    monkeypatch.setattr(pipeline, "match_hpo", lambda symptoms, **k: [])
    return texts


def _transcript(tmp_path, text=TEXT, name="004.txt"):
    path = tmp_path / "in" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def _book(tmp_path):
    return ResultsWorkbook(tmp_path / "out" / "results.xlsx")


# --- the gate before the LLM -----------------------------------------------------------


def test_no_llm_call_before_approval(tmp_path, sent):
    with pytest.raises(pipeline.NeedsReview):
        pipeline.process_recording(_transcript(tmp_path), "P.004", Config(), book=_book(tmp_path))
    assert sent == []
    assert load_review(tmp_path / "out", "P.004").status == NEEDS_REVIEW


def test_the_llm_gets_only_the_approved_clean_text(tmp_path, sent):
    out_dir = tmp_path / "out"
    source = _transcript(tmp_path)
    with pytest.raises(pipeline.NeedsReview):
        pipeline.process_recording(source, "P.004", Config(), book=_book(tmp_path))
    review = load_review(out_dir, "P.004")
    add_value(review, "fatiguée", "PERSON")  # the person removes one more word
    save_review(approve(review), out_dir)

    pipeline.process_recording(source, "P.004", Config(), book=_book(tmp_path))
    assert sent == ["Bonjour madame PERSON_1, je suis PERSON_2."]
    assert load_review(out_dir, "P.004").status == DONE


def test_no_review_approves_and_records_it(tmp_path, sent):
    source = _transcript(tmp_path)
    pipeline.process_recording(source, "P.004", Config(), book=_book(tmp_path), no_review=True)
    assert sent == ["Bonjour madame PERSON_1, je suis fatiguée."]
    assert load_review(tmp_path / "out", "P.004").approved_how == "no review (CLI)"


def test_a_run_without_no_review_asks_for_a_review_of_an_unchecked_file(tmp_path, sent):
    source = _transcript(tmp_path)
    pipeline.process_recording(source, "P.004", Config(), book=_book(tmp_path), no_review=True)
    with pytest.raises(pipeline.NeedsReview):
        pipeline.process_recording(source, "P.004", Config(), book=_book(tmp_path))


@pytest.mark.parametrize("language, first, fallback", [
    ("fr", "Anonym-IA/V2-camembert-ner-pii-french", "Jean-Baptiste/camembert-ner"),
    ("nl", "urchade/gliner_multi_pii-v1", None),
    ("en", "StanfordAIMI/stanford-deidentifier-base", None),
    ("de", "urchade/gliner_multi_pii-v1", None),
])
def test_the_language_picks_the_privacy_models(tmp_path, monkeypatch, language, first, fallback):
    seen = {}
    monkeypatch.setattr(pipeline, "detect_personal_details", lambda text, **k: seen.update(k) or ([], []))
    config = Config()
    config.transcription.language = language
    pipeline.find_details("Bonjour.", "a.txt", "P.1", config, tmp_path)
    assert seen["models"][0] == first
    assert seen["models"] == config.pii.models_for(language)
    assert seen["fallback_model"] == fallback


# --- the input ---------------------------------------------------------------------------


@pytest.fixture
def no_detector(monkeypatch):
    monkeypatch.setattr(pipeline, "detect_personal_details",
                        lambda *a, **k: pytest.fail("the detector runs on an empty file"))


@pytest.mark.parametrize("text", ["", " \n\t\n"])
def test_an_empty_transcript_fails_before_the_detector(tmp_path, no_detector, text):
    with pytest.raises(EmptyTranscriptError, match=r"004\.txt: the file has no text"):
        pipeline.process_recording(_transcript(tmp_path, text), "P.004", Config(), book=_book(tmp_path))


def test_an_empty_word_file_fails_before_the_detector(tmp_path, no_detector):
    path = tmp_path / "004.docx"
    Document().save(str(path))
    with pytest.raises(EmptyTranscriptError, match=r"004\.docx: the file has no text"):
        pipeline.process_recording(str(path), "P.004", Config(), book=_book(tmp_path))


def test_a_windows_1252_transcript_reaches_the_detector(tmp_path, monkeypatch):
    path = tmp_path / "004.txt"
    path.write_bytes("Je suis fatiguée, fièvre à 39°.".encode("cp1252"))
    seen = []
    monkeypatch.setattr(pipeline, "detect_personal_details", lambda text, **k: seen.append(text) or ([], []))
    with pytest.raises(pipeline.NeedsReview):
        pipeline.process_recording(str(path), "P.004", Config(), book=_book(tmp_path))
    assert seen == ["Je suis fatiguée, fièvre à 39°."]


def test_process_recording_reuses_and_saves_the_audio_transcript(tmp_path, sent, monkeypatch):
    audio = tmp_path / "in" / "001.mp3"
    audio.parent.mkdir()
    audio.write_bytes(b"audio")
    whisper = []
    monkeypatch.setattr(pipeline, "read_input", lambda path, **k: whisper.append(path) or "bonjour")

    for reuse in (True, True, False):
        pipeline.process_recording(str(audio), "P.001", Config(), _book(tmp_path), reuse_transcript=reuse,
                                   no_review=True)

    assert len(whisper) == 2  # the second run reused the saved transcript
    cache = transcript_cache_path(str(audio), Config(), str(tmp_path / "out" / "transcripts"))
    assert cache.read_text(encoding="utf-8") == "bonjour"
