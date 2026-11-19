"""Who says each quote (speakers.py): the speaker pass, its copy check, and the code's own check.

The model labels each turn D or P from the text alone, and the code then finds
each quote of a finding in the labeled text. A finding whose words are only the
doctor's is "Doctor only".
"""

import pytest
from conftest import finding

from phenoscribe import pipeline, speakers
from phenoscribe.config import Config, LLMConfig
from phenoscribe.findings import finding_status
from phenoscribe.output import ResultsWorkbook
from phenoscribe.review import AUTO, Review, approve
from phenoscribe.speakers import (
    SpeakerLabelError,
    check_speakers,
    copy_similarity,
    label_speakers,
    parse_turns,
    speaker_at,
    split_chunks,
)

TEXT = "parle-moi de tes pieds froids ils bleuissent dès novembre dès novembre bon et l'été ça va mieux en juillet"
LABELED = (
    "D: parle-moi de tes pieds froids\n"
    "P: ils bleuissent dès novembre\n"
    "D: dès novembre bon et l'été\n"
    "P: ça va mieux en juillet"
)


def test_parse_turns_gives_the_text_and_the_speaker_spans():
    text, turns = parse_turns(LABELED)
    assert text.splitlines() == [
        "parle-moi de tes pieds froids", "ils bleuissent dès novembre",
        "dès novembre bon et l'été", "ça va mieux en juillet"]
    assert [t[0] for t in turns] == ["doctor", "patient", "doctor", "patient"]
    start = text.index("ils bleuissent")
    assert speaker_at(turns, start, start + 10) == "patient"
    assert speaker_at(turns, 0, 5) == "doctor"


def test_parse_turns_accepts_other_label_spellings():
    text, turns = parse_turns("Docteur : bonjour\npatient: bonjour\nsuite sans label\nP : oui")
    assert [t[0] for t in turns] == ["doctor", "patient", "patient"]
    assert text.splitlines()[2] == "suite sans label"  # continues the patient turn


def test_copy_similarity_sees_lost_words():
    assert copy_similarity(TEXT, LABELED) == 1.0
    assert copy_similarity(TEXT, "D: parle-moi de tes pieds\nP: en juillet") < 0.9
    assert copy_similarity("", "D: x") == 1.0


def test_split_chunks_cuts_at_a_sentence_end():
    text = "Une phrase. " * 100
    chunks = split_chunks(text, size=200)
    assert "".join(c + " " for c in chunks).split() == text.split()
    assert all(c.endswith(".") for c in chunks)
    assert split_chunks("court", size=200) == ["court"]


def test_label_speakers_sends_chunks_with_context_and_checks_the_copy(monkeypatch):
    calls = []

    def fake_call(system_prompt, user_prompt, **kwargs):
        calls.append((user_prompt, kwargs["temperature"]))
        body = user_prompt.split("Label this part:\n")[-1]
        return "```\n" + "\n".join(f"{'DP'[i % 2]}: {line}" for i, line in enumerate(body.split(". "))) + "\n```"

    monkeypatch.setattr(speakers, "llm_call", fake_call)
    monkeypatch.setattr(speakers, "CHUNK_CHARS", 60)
    text = "Tu dors bien. Non pas du tout. Et la fatigue. Oui tout le temps. Des vertiges. Non."
    labeled = label_speakers(text, LLMConfig())
    assert len(calls) == 2 and all(t == 0.0 for _, t in calls)
    assert calls[1][0].startswith("The last turns before this part")
    assert copy_similarity(text, labeled) == 1.0
    assert labeled.splitlines()[0] == "D: Tu dors bien"


def test_label_speakers_asks_again_then_fails_on_a_bad_copy(monkeypatch):
    answers = iter(["D: rien", "P: rien du tout"])
    monkeypatch.setattr(speakers, "llm_call", lambda **k: next(answers))
    with pytest.raises(SpeakerLabelError):
        label_speakers(TEXT, LLMConfig())


LABELED_CHECK = (
    "D: ton cœur bat vite des palpitations\n"
    "P: Oui, le soir\n"
    "D: la dernière fois tu avais des nausées\n"
    "P: je bâille dès dix heures fatigué\n"
    "D: et ta jambe gauche faiblit sur le vélo\n"
    "P: Oui, j'ai l'impression.\n"
    "D: et de la fièvre\n"
    "P: Non.\n"
    "D: des sueurs la nuit\n"
)


@pytest.mark.parametrize("match, spoken_by, source, review", [
    (finding("Palpitations", source="confirmed", verbatim="le soir", mentions=["des palpitations", "le soir"]),
     "patient", "confirmed", False),
    # "Oui, le soir" answers the question.
    (finding("Palpitations", source="patient", verbatim="ton cœur bat vite des palpitations"),
     "patient answer", "confirmed", False),
    # The next patient turn is another subject.
    (finding("Nausea", source="doctor", verbatim="tu avais des nausées"), "doctor", "doctor", False),
    (finding("Fatigue", source="doctor", verbatim="je bâille dès dix heures fatigué"), "patient", "confirmed", True),
    (finding("Vertigo", source="patient", verbatim="la tête qui tourne"), "not found", "patient", True),
    (finding("Muscle weakness", source="doctor", verbatim="jambe gauche faiblit sur le vélo"),
     "patient answer", "confirmed", False),
    (finding("Fever", source="doctor", verbatim="de la fièvre", negated=True), "patient answer", "confirmed", False),
    # No patient turn after it.
    (finding("Night sweats", source="doctor", verbatim="des sueurs la nuit"), "doctor", "doctor", False),
], ids=["patient words", "doctor question answered", "doctor only", "contradiction", "lost quote",
        "yes answer", "no answer", "no reply"])
def test_check_speakers_corrects_the_source_from_the_turns(match, spoken_by, source, review):
    check_speakers([match], LABELED_CHECK)
    assert (match["spoken_by"], match["source"], bool(match.get("needs_review"))) == (spoken_by, source, review)


def test_a_doctor_only_finding_has_its_status():
    match = finding("Nausea", source="doctor", verbatim="tu avais des nausées")
    check_speakers([match], LABELED_CHECK)
    assert finding_status(match) == "Doctor only"


def test_a_yes_answer_becomes_the_verbatim():
    match = finding("Muscle weakness", source="doctor", verbatim="jambe gauche faiblit sur le vélo")
    check_speakers([match], LABELED_CHECK)
    assert match["patient_verbatim"] == "Oui, j'ai l'impression."


def test_a_no_answer_becomes_the_verbatim_of_an_absent_finding():
    match = finding("Fever", source="doctor", verbatim="de la fièvre", negated=True)
    check_speakers([match], LABELED_CHECK)
    assert match["patient_verbatim"] == "Non." and match["negated"]
    assert finding_status(match) == "Absent"


@pytest.mark.real_speaker_pass
def test_pipeline_codes_the_labeled_text_and_saves_it(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "label_speakers", lambda text, **k: LABELED)
    monkeypatch.setattr(pipeline, "extract_symptoms",
                        lambda text, **k: sent.append((text, k["runs"])) or [])
    monkeypatch.setattr(pipeline, "match_hpo", lambda symptoms, **k: [])
    review = Review.from_spans("001.txt", "P.001", TEXT, [])
    approve(review, how=AUTO)
    out = tmp_path / "results.xlsx"
    pipeline.code_review(review, Config(), ResultsWorkbook.for_config(out, Config()))
    assert sent == [(LABELED, 2)]
    assert (tmp_path / "speakers" / "P.001.txt").read_text(encoding="utf-8") == LABELED


@pytest.mark.real_speaker_pass
def test_pipeline_falls_back_to_the_plain_text(tmp_path, monkeypatch):
    def bad(text, **k):
        raise SpeakerLabelError("lost words")

    monkeypatch.setattr(pipeline, "label_speakers", bad)
    assert pipeline.find_speakers(TEXT, "P.001", Config(), tmp_path) == ""
    assert not (tmp_path / "speakers").exists()
