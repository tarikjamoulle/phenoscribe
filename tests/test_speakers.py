"""Who says each quote: the speaker pass, its copy check, and the code's own check.

No diarization: the model labels each turn D or P from the text alone, and
the code then finds each quote of a finding in the labeled text. A finding
whose words are only the doctor's is "Doctor only".
"""

import openpyxl
import pytest

from phenoscribe import pipeline, speakers
from phenoscribe.extract_symptoms import merge_runs
from phenoscribe.match_hpo import finding_status, flag_unstable, runs_label
from phenoscribe.output import write_excel
from phenoscribe.speakers import (
    SpeakerLabelError,
    check_speakers,
    copy_similarity,
    label_speakers,
    parse_turns,
    speaker_at,
    split_chunks,
)

# The real pass, taken before the conftest fixture stubs it for each test.
REAL_FIND_SPEAKERS = pipeline.find_speakers

TEXT = "dis moi le principal problème que tu as je fais des malaises des malaises oui explique j'ai des vertiges"
LABELED = (
    "D: dis moi le principal problème que tu as\n"
    "P: je fais des malaises\n"
    "D: des malaises oui explique\n"
    "P: j'ai des vertiges"
)


def test_parse_turns_gives_the_text_and_the_speaker_spans():
    text, turns = parse_turns(LABELED)
    assert text.splitlines() == [
        "dis moi le principal problème que tu as", "je fais des malaises",
        "des malaises oui explique", "j'ai des vertiges"]
    assert [t[0] for t in turns] == ["doctor", "patient", "doctor", "patient"]
    start = text.index("je fais")
    assert speaker_at(turns, start, start + 10) == "patient"
    assert speaker_at(turns, 0, 5) == "doctor"


def test_parse_turns_accepts_other_label_spellings():
    text, turns = parse_turns("Docteur : bonjour\npatient: bonjour\nsuite sans label\nP : oui")
    assert [t[0] for t in turns] == ["doctor", "patient", "patient"]
    assert text.splitlines()[2] == "suite sans label"  # continues the patient turn


def test_copy_similarity_sees_lost_words():
    assert copy_similarity(TEXT, LABELED) == 1.0
    assert copy_similarity(TEXT, "D: dis moi le principal problème\nP: des vertiges") < 0.9
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
    labeled = label_speakers(text)
    assert len(calls) == 2 and all(t == 0.0 for _, t in calls)
    assert calls[1][0].startswith("The last turns before this part")
    assert copy_similarity(text, labeled) == 1.0
    assert labeled.splitlines()[0] == "D: Tu dors bien"


def test_label_speakers_asks_again_then_fails_on_a_bad_copy(monkeypatch):
    answers = iter(["D: rien", "P: rien du tout"])
    monkeypatch.setattr(speakers, "llm_call", lambda **k: next(answers))
    with pytest.raises(SpeakerLabelError):
        label_speakers(TEXT)


def _match(term, source, verbatim, mentions=None, code="HP:1", negated=False):
    return {"hpo_id": code, "hpo_term": term, "clinical_term": term.lower(), "source": source,
            "patient_verbatim": verbatim, "mentions": mentions or [], "negated": negated}


def test_check_speakers_corrects_the_source_from_the_turns():
    labeled = (
        "D: est-ce que tu as des palpitations\n"
        "P: Oui, le soir\n"
        "D: la dernière fois tu avais des nausées\n"
        "P: je suis tout le temps fatigué\n"
        "D: et tu as aussi moins de force dans cette jambe-là\n"
        "P: Oui, j'ai l'impression.\n"
        "D: et de la fièvre\n"
        "P: Non.\n"
        "D: des sueurs la nuit\n"
    )
    confirmed = _match("Palpitations", "confirmed", "le soir", ["des palpitations", "le soir"])
    doctor_words = _match("Palpitations", "patient", "est-ce que tu as des palpitations")
    doctor_only = _match("Nausea", "doctor", "tu avais des nausées")
    contradiction = _match("Fatigue", "doctor", "je suis tout le temps fatigué")
    lost = _match("Vertigo", "patient", "la tête qui tourne")
    yes_answer = _match("Muscle weakness", "doctor", "moins de force dans cette jambe-là")
    no_answer = _match("Fever", "doctor", "de la fièvre", negated=True)
    no_reply = _match("Night sweats", "doctor", "des sueurs la nuit")
    matches = [confirmed, doctor_words, doctor_only, contradiction, lost, yes_answer, no_answer, no_reply]
    check_speakers(matches, labeled)

    assert [(m["spoken_by"], m["source"], bool(m.get("needs_review"))) for m in matches] == [
        ("patient", "confirmed", False),
        ("patient answer", "confirmed", False),  # "Oui, le soir" answers the question
        ("doctor", "doctor", False),  # the next patient turn is another subject
        ("patient", "confirmed", True),
        ("not found", "patient", True),
        ("patient answer", "confirmed", False),
        ("patient answer", "confirmed", False),
        ("doctor", "doctor", False),  # no patient turn after it
    ]
    assert finding_status(doctor_only) == "Doctor only"
    assert yes_answer["patient_verbatim"] == "Oui, j'ai l'impression."
    assert no_answer["patient_verbatim"] == "Non." and no_answer["negated"]
    assert finding_status(no_answer) == "Absent"


def test_merge_runs_joins_the_same_finding_and_counts_the_runs():
    run1 = [{"clinical_term": "fatigue", "negated": False, "patient_verbatim": "", "mentions": ["a"]},
            {"clinical_term": "fever", "negated": True, "patient_verbatim": "pas de fièvre", "mentions": []}]
    run2 = [{"clinical_term": "Fatigue", "negated": False, "patient_verbatim": "fatigué", "mentions": ["a", "b"]},
            {"clinical_term": "vertigo", "negated": False, "patient_verbatim": "", "mentions": []}]
    merged = {m["clinical_term"].lower(): m for m in merge_runs([run1, run2])}
    assert merged["fatigue"]["runs_found"] == [1, 2] and merged["fatigue"]["n_runs"] == 2
    assert merged["fatigue"]["mentions"] == ["a", "b"]
    assert merged["fatigue"]["patient_verbatim"] == "fatigué"
    assert merged["fever"]["runs_found"] == [1]
    assert merged["vertigo"]["runs_found"] == [2]
    assert merge_runs([run1])[0]["n_runs"] == 1


def test_flag_unstable_joins_the_runs_on_the_code():
    a = dict(_match("Fatigue", "patient", "", code="HP:1"), runs_found=[1], n_runs=2)
    b = dict(_match("Chronic fatigue", "patient", "", code="HP:1"), runs_found=[2], n_runs=2)
    c = dict(_match("Vertigo", "patient", "", code="HP:2"), runs_found=[2], n_runs=2)
    one_run = dict(_match("Fever", "patient", "", code="HP:3"), runs_found=[1], n_runs=1)
    assert flag_unstable([a, b, c, one_run]) == 1
    assert [runs_label(m) for m in (a, b, c, one_run)] == ["2/2", "2/2", "1/2", ""]
    assert [bool(m.get("needs_review")) for m in (a, b, c, one_run)] == [False, False, True, False]


def test_excel_has_the_spoken_by_and_runs_columns(tmp_path):
    m = dict(_match("Fatigue", "patient", "fatigué", code="HP:0012378"),
             spoken_by="patient", runs_found=[1, 2], n_runs=2)
    path = tmp_path / "r.xlsx"
    write_excel("MGA.001", [m], str(path), fmt="detailed")
    rows = list(openpyxl.load_workbook(path).active.iter_rows(values_only=True))
    head = rows[0]
    assert head[-3:] == ("Source", "Spoken by", "Runs")
    assert rows[1][-3:] == ("patient", "patient", "2/2")


def test_pipeline_codes_the_labeled_text_and_saves_it(tmp_path, monkeypatch):
    from phenoscribe.config import Config
    from phenoscribe.review import AUTO, Review, approve

    sent = []
    monkeypatch.setattr(pipeline, "check_obo_version", lambda *a: "hp/releases/2026-02-16")
    monkeypatch.setattr(pipeline, "label_speakers", lambda text, **k: LABELED)
    monkeypatch.setattr(pipeline, "find_speakers", REAL_FIND_SPEAKERS)
    monkeypatch.setattr(pipeline, "extract_symptoms",
                        lambda text, **k: sent.append((text, k["runs"])) or [])
    monkeypatch.setattr(pipeline, "match_hpo", lambda symptoms, **k: [])
    review = Review.from_spans("467.txt", "MGA.467", TEXT, [])
    approve(review, how=AUTO)
    out = tmp_path / "results.xlsx"
    pipeline.code_review(review, Config(), out)
    assert sent == [(LABELED, 2)]
    assert (tmp_path / "speakers" / "MGA.467.txt").read_text(encoding="utf-8") == LABELED


def test_pipeline_falls_back_to_the_plain_text(tmp_path, monkeypatch):
    from phenoscribe.config import Config

    def bad(text, **k):
        raise SpeakerLabelError("lost words")

    monkeypatch.setattr(pipeline, "label_speakers", bad)
    assert REAL_FIND_SPEAKERS(TEXT, "MGA.467", Config(), tmp_path) == ""
    assert not (tmp_path / "speakers").exists()
