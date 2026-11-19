"""The prompts and the English interviews: the frozen prompt text, the answer words of the
speaker check, and a live extraction.

French and English have their own prompt examples. Any other language runs
the English rules with no examples, and is not validated.
"""

import hashlib

import pytest
from conftest import FIXTURES, finding

from phenoscribe import extract_symptoms as es
from phenoscribe import pipeline, speakers
from phenoscribe.config import Config
from phenoscribe.match_hpo import JUDGE_SYSTEM_PROMPT
from phenoscribe.output import ResultsWorkbook
from phenoscribe.review import Review, approve
from phenoscribe.speakers import check_speakers, speaker_prompt

SAMPLE_EN = FIXTURES / "sample_en.txt"

# The prompts that the benchmarks measured. A prompt change needs a new benchmark
# run (CONTRIBUTING.md), then a new checksum here.
PROMPT_SHA256 = {
    "extraction fr": "f9ea9c44b7eb88c477332cdc9a410704dbb96334ea647ad943d26692191f3752",
    "speakers fr": "061699fe14ab07542bf1b767d3fcb34803f0ef8961888a6fa4d1b06539d89557",
    "extraction en": "054934105557b76617b6c0315bb5232745dc6f2923632e83c17674e2f5d1213b",
    "speakers en": "3144a8f7f4efab190877c00633dbb2522fedae80ed899593bf07b8712bf69877",
    "judge": "4ae042001c2415991e6711c90e0e2eb651d54b0fda28e95dac43bd2f68073af4",
}
PROMPTS = {
    "extraction fr": lambda: es.system_prompt("fr"),
    "speakers fr": lambda: speaker_prompt("fr"),
    "extraction en": lambda: es.system_prompt("en"),
    "speakers en": lambda: speaker_prompt("en"),
    "judge": lambda: JUDGE_SYSTEM_PROMPT,
}


@pytest.mark.parametrize("name", PROMPT_SHA256)
def test_each_prompt_is_the_benchmarked_text(name):
    assert hashlib.sha256(PROMPTS[name]().encode("utf-8")).hexdigest() == PROMPT_SHA256[name]


@pytest.mark.parametrize("prompt", [es.system_prompt("en"), speaker_prompt("en")], ids=["extraction", "speakers"])
def test_the_english_prompts_hold_no_french(prompt):
    for french in ("French", "je n'ai", "ne...pas", "Oui", "fièvre", "genoux"):
        assert french not in prompt


def test_an_unsupported_language_gets_the_generic_rules_and_a_log_line(caplog):
    prompt = es.system_prompt("de")
    assert "You read German medical interview transcripts" in prompt
    assert "negation cues include" not in prompt and "Examples:" not in prompt
    assert "not validated" in caplog.text
    assert "Example input" not in speaker_prompt("de")


ENGLISH_TURNS = (
    "D: Any palpitations?\n"
    "P: Yes, in the evening.\n"
    "D: And any chest pain?\n"
    "P: No.\n"
    "D: Last time you mentioned some nausea.\n"
    "D: Shall we check your blood pressure?\n"
)


def test_an_english_answer_confirms_the_doctors_question():
    palpitations = finding("Palpitations", source="confirmed", verbatim="Any palpitations?")
    chest_pain = finding("Chest pain", source="confirmed", verbatim="any chest pain", negated=True)
    nausea = finding("Nausea", source="doctor", verbatim="some nausea")
    check_speakers([palpitations, chest_pain, nausea], ENGLISH_TURNS, "en")

    assert (palpitations["spoken_by"], palpitations["source"]) == ("patient answer", "confirmed")
    assert palpitations["patient_verbatim"] == "Yes, in the evening."
    assert (chest_pain["spoken_by"], chest_pain["patient_verbatim"]) == ("patient answer", "No.")
    assert chest_pain["negated"] is True  # the model's negation stays
    assert nausea["spoken_by"] == "doctor"


def test_an_unsupported_language_has_no_answer_words():
    palpitations = finding("Palpitations", source="confirmed", verbatim="Any palpitations?")
    check_speakers([palpitations], ENGLISH_TURNS, "de")
    assert palpitations["spoken_by"] == "doctor" and palpitations["needs_review"]


@pytest.mark.real_speaker_pass
def test_code_review_passes_the_review_language_to_every_prompt(tmp_path, monkeypatch):
    prompts = []

    def fake_llm_call(system_prompt, user_prompt, **kwargs):
        prompts.append(system_prompt)
        return "P: " + user_prompt if system_prompt.startswith("You read an") else "[]"

    monkeypatch.setattr(es, "llm_call", fake_llm_call)
    monkeypatch.setattr(speakers, "llm_call", fake_llm_call)
    review = Review.from_spans("a.txt", "A", "I am tired all the time.", [])
    review.language, review.models = "en", ["m1", "m2"]
    config = Config()
    config.llm.extraction_runs = 1
    pipeline.code_review(approve(review), config, ResultsWorkbook.for_config(tmp_path / "r.xlsx", config))
    assert prompts and all("English medical interview" in p for p in prompts)


@pytest.mark.live
def test_live_english_extraction_finds_present_and_denied_findings(live_llm):
    """One live extraction of the synthetic English interview (tests/fixtures/sample_en.txt)."""
    findings = es.extract_symptoms(SAMPLE_EN.read_text(encoding="utf-8"), live_llm, language="en")

    def find(*words):
        return next((f for f in findings if any(w in f["clinical_term"].lower() for w in words)), None)

    for words, negated in ((("fatigue", "exhaust", "tired"), False), (("headache",), False),
                           (("palpitation",), False), (("fever",), True), (("chest pain",), True),
                           (("cough",), True), (("dyspnea", "breath"), False)):
        found = find(*words)
        assert found is not None, f"missing {words[0]}: {findings}"
        assert found["negated"] is negated, found
    assert find("palpitation")["source"] == "confirmed"
