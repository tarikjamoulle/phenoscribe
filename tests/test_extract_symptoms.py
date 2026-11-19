"""Tests for the symptom extraction parser.

The schema carries negation and the frequency/onset/severity modifiers, and
the parser reads them back.
"""

import pytest

from phenoscribe import extract_symptoms as es
from phenoscribe.config import LLMConfig
from phenoscribe.extract_symptoms import ExtractionParseError, _coerce_bool, _parse_response, merge_runs


def test_parser_reads_negation_and_modifiers():
    response = """[
      {"patient_verbatim": "j'ai mal au ventre", "clinical_term": "abdominal pain",
       "negated": false, "frequency": "frequent", "onset": "adult",
       "severity": "severe", "context": "worse after meals"},
      {"patient_verbatim": "je n'ai pas de fievre", "clinical_term": "fever",
       "negated": true, "frequency": "", "onset": "", "severity": "", "context": ""}
    ]"""

    result = _parse_response(response)

    assert len(result) == 2
    pain, fever = result
    assert pain["clinical_term"] == "abdominal pain"
    assert pain["negated"] is False
    assert pain["frequency"] == "frequent"
    assert pain["onset"] == "adult"
    assert pain["severity"] == "severe"
    assert fever["clinical_term"] == "fever"
    assert fever["negated"] is True


def test_parser_defaults_missing_fields():
    # Output without the optional keys parses with defaults.
    response = '[{"clinical_term": "headache"}]'

    result = _parse_response(response)

    assert len(result) == 1
    item = result[0]
    assert item["negated"] is False
    assert item["frequency"] == ""
    assert item["onset"] == ""
    assert item["severity"] == ""


def test_parser_coerces_string_negated():
    # Some models emit "true"/"yes" strings instead of JSON booleans.
    response = '[{"clinical_term": "cough", "negated": "true"}]'

    result = _parse_response(response)

    assert result[0]["negated"] is True


def test_coerce_bool_variants():
    assert _coerce_bool(True) is True
    assert _coerce_bool("YES") is True
    assert _coerce_bool("absent") is True
    assert _coerce_bool("false") is False
    assert _coerce_bool("") is False
    assert _coerce_bool(None) is False


@pytest.mark.parametrize(
    "response",
    [
        '[\n  {"clinical_term": "malaise", "negated": false},\n  {"patie',  # truncated
        "Sorry, I cannot help with that.",
        '{"clinical_term": "cough"}',
    ],
)
def test_unparseable_response_raises(response):
    """A broken response must fail the job, never pass as zero findings."""
    with pytest.raises(ExtractionParseError, match="symptom extraction"):
        _parse_response(response)


def test_a_cut_answer_is_asked_again_with_no_mentions(monkeypatch):
    prompts = []
    answers = iter([
        '[{"patient_verbatim": "j\'ai mal", "clinical_term": "pain", "mentions": ["j\'ai mal", "enc',
        '[{"patient_verbatim": "j\'ai mal", "clinical_term": "pain", "mentions": []}]',
    ])

    def fake_call(system_prompt, **kwargs):
        prompts.append(system_prompt)
        return next(answers)

    monkeypatch.setattr(es, "llm_call", fake_call)
    found = es.extract_symptoms("[00:00] j'ai mal", LLMConfig())
    assert [f["clinical_term"] for f in found] == ["pain"]
    assert found[0]["mentions"] == ["j'ai mal"]  # the verbatim stands in
    assert es.NO_MENTIONS not in prompts[0] and prompts[1].endswith(es.NO_MENTIONS)


def test_two_cut_answers_fail(monkeypatch):
    monkeypatch.setattr(es, "llm_call", lambda **k: '[{"clinical_term": "pa')
    with pytest.raises(ExtractionParseError):
        es.extract_symptoms("texte", LLMConfig())


def test_the_second_try_uses_a_small_temperature(monkeypatch):
    temperatures = []
    answers = iter(['[{"clinical_term": "pa', '[{"clinical_term": "pain"}]'])

    def fake_call(temperature, **kwargs):
        temperatures.append(temperature)
        return next(answers)

    monkeypatch.setattr(es, "llm_call", fake_call)
    es.extract_symptoms("texte", LLMConfig())
    assert temperatures == [0.0, es.RETRY_TEMPERATURE]


def test_two_looped_answers_keep_the_complete_findings(monkeypatch):
    """Both answers repeat the same "fatigue" line until the output limit."""
    loop = (
        '[\n  {"patient_verbatim": "j\'ai mal", "clinical_term": "pain", "negated": false},\n'
        + '  {"patient_verbatim": "Je suis fatiguée.", "clinical_term": "fatigue", "negated": false},\n' * 50
        + '  {"patient_verbatim": "Je suis'
    )
    monkeypatch.setattr(es, "llm_call", lambda **k: loop)
    found = es.extract_symptoms("texte", LLMConfig())
    assert [f["clinical_term"] for f in found] == ["pain", "fatigue"]


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


def test_the_extractor_keeps_the_mention_list():
    found = _parse_response(
        '[{"patient_verbatim": "j\'ai mal", "clinical_term": "pain", "mentions": ["j\'ai mal", "encore mal"]},'
        ' {"patient_verbatim": "fatiguée", "clinical_term": "fatigue"},'
        ' {"patient_verbatim": "", "clinical_term": "cough", "mentions": "je tousse"}]'
    )
    assert [f["mentions"] for f in found] == [["j'ai mal", "encore mal"], ["fatiguée"], ["je tousse"]]
