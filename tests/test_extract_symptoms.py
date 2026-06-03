"""Tests for the symptom extraction parser.

Robinson issue #6: the schema must carry negation and the frequency/onset/
severity modifiers, and the parser must read them back reliably.
"""

from phenoscribe.extract_symptoms import _parse_response, _coerce_bool


def test_parser_reads_new_fields():
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
    # Older / sloppy output without the new keys must still parse, not crash.
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
