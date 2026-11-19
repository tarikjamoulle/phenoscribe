"""The review of personal details (review.py): the clean text, the warning signs and the review file."""

import json

import pytest

from phenoscribe import review as review_module
from phenoscribe.pii import Span
from phenoscribe.review import (
    APPROVED,
    NEEDS_REVIEW,
    Review,
    ReviewError,
    add_value,
    approve,
    blocking_warnings,
    clean_text,
    entries,
    fold,
    leaks,
    load_review,
    possibly_missed,
    review_path,
    save_review,
    set_keep_types,
    set_removed,
    summary_line,
    warning_signs,
)

TEXT = (
    "Bonjour madame Dupont. Mme DUPONT a 54 ans et elle est institutrice. "
    "Le docteur Vandermeulen la suit à Namur. Pas de mariage prévu, Marie dit non."
)


def _span(sub, category, score=0.95, origin="model", text=TEXT):
    start = text.index(sub)
    return Span(start, start + len(sub), sub, category, score, origin)


def _review(**kwargs):
    spans = [
        _span("Dupont", "PERSON", 0.98),
        _span("54 ans", "AGE", 0.8),
        _span("institutrice", "JOB", 0.7),
        _span("Namur", "LOCATION", 0.9),
        _span("Marie", "PERSON", 0.65),
    ]
    return Review.from_spans("004.txt", "P.004", TEXT, spans, **kwargs)


def test_fold_keeps_the_length():
    assert fold("Élodie DUPONT") == "elodie dupont"
    assert len(fold("Œuvre ﬁn İ")) == len("Œuvre ﬁn İ")


def test_a_value_found_once_is_removed_at_every_position_in_any_case():
    text, mapping = clean_text(_review())
    assert "Dupont" not in text and "DUPONT" not in text
    assert text.count("PERSON_1") == 2
    assert mapping["PERSON_1"] == "Dupont"


def test_whole_words_only():
    text, _ = clean_text(_review())
    assert "mariage" in text  # "Marie" does not hit "mariage"
    assert "PERSON_2 dit non" in text


def test_untick_puts_the_value_back_everywhere():
    review = set_removed(_review(), "Dupont", False)
    text, _ = clean_text(review)
    assert text.count("Dupont") == 1 and "DUPONT" in text
    assert [e.removed for e in entries(review) if e.value == "Dupont"] == [False]
    assert set_removed(review, "Dupont", True) and "Dupont" not in clean_text(review)[0]


def test_an_added_word_is_removed_and_can_be_put_back():
    review = add_value(_review(), "vandermeulen", "PERSON")
    assert "Vandermeulen" not in clean_text(review)[0]
    assert possibly_missed(review) == []
    review = set_removed(review, "vandermeulen", False)
    assert "Vandermeulen" in clean_text(review)[0]
    assert review.added == []


def test_kept_types_stay_and_names_still_go():
    review = _review(keep_types=["AGE", "JOB"])
    text, _ = clean_text(review)
    assert "54 ans" in text and "institutrice" in text and "Dupont" not in text
    assert leaks(review) == []
    # One value of a kept type can still be removed.
    text, _ = clean_text(set_removed(review, "institutrice", True))
    assert "institutrice" not in text and "54 ans" in text


def test_names_cannot_be_kept_as_a_type():
    review = set_keep_types(_review(), ["PERSON", "AGE"])
    assert review.keep_types == ["AGE"]
    assert "Dupont" not in clean_text(review)[0]


def test_overlapping_and_repeated_spans():
    text = "Jean Dupont et Dupont."
    spans = [Span(0, 11, "Jean Dupont", "PERSON"), Span(5, 11, "Dupont", "PERSON"),
             Span(15, 21, "Dupont", "PERSON")]
    clean, mapping = clean_text(Review.from_spans("a.txt", "A", text, spans))
    assert clean == "PERSON_1 et PERSON_2."
    assert mapping == {"PERSON_1": "Jean Dupont", "PERSON_2": "Dupont"}


def test_an_elision_is_not_the_value():
    text = "Parce qu'on m'avait dit que M. Dupont m'a vu. Le M est là."
    review = Review.from_spans("a.txt", "A", text, [Span(text.index("M est"), text.index("M est") + 1, "M", "PERSON")])
    clean, _ = clean_text(review)
    assert "m'avait" in clean and "m'a vu" in clean
    assert "Le PERSON_1 est" in clean


def test_regex_only_spans():
    text = "Appelez le 0472 12 34 56 ou le 0472 12 34 56."
    start = text.index("0472")
    span = Span(start, start + 13, "0472 12 34 56", "PHONE", 1.0, "regex")
    clean, _ = clean_text(Review.from_spans("a.txt", "A", text, [span]))
    assert clean == "Appelez le PHONE_1 ou le PHONE_1."


def test_possibly_missed_finds_words_after_a_title_only():
    missed = possibly_missed(_review())
    assert [word for word, _ in missed] == ["Vandermeulen"]
    assert "docteur Vandermeulen" in missed[0][1]


def test_a_title_at_the_end_of_a_sentence_is_not_a_name():
    text = "Il faut trouver un docteur. Oui. Puis M. Lefèvre et Dr Haddad."
    review = Review.from_spans("a.txt", "A", text, [])
    assert [word for word, _ in possibly_missed(review)] == ["Lefèvre", "Haddad"]


def test_summary_line():
    assert summary_line(_review()) == "5 removed: 2 names · 1 place · 1 age · 1 job title"


def test_approve_and_an_edit_after_it():
    review = approve(_review())
    assert review.status == APPROVED and review.approved_at
    add_value(review, "Vandermeulen")
    assert review.status == NEEDS_REVIEW and review.approved_at is None


def test_approve_is_refused_while_a_value_leaks(monkeypatch):
    # The leak check is a safety net: force a clean text that still holds the name.
    monkeypatch.setattr(review_module, "clean_text", lambda r, removed=None: (r.raw_text, {}))
    review = _review()
    assert "Dupont" in leaks(review)
    with pytest.raises(ReviewError, match="Still in the clean text"):
        approve(review)
    assert review.status == NEEDS_REVIEW


def test_a_value_inside_a_word_goes_at_its_detected_position():
    review = Review.from_spans("a.txt", "A", "abcDupontdef", [Span(3, 9, "Dupont", "PERSON")])
    assert clean_text(review)[0] == "abcPERSON_1def"


def test_save_load_and_a_damaged_file(tmp_path):
    review = _review()
    save_review(review, tmp_path)
    assert load_review(tmp_path, "P.004") == review
    review_path(tmp_path, "P.004").write_text("{not json", encoding="utf-8")
    with pytest.raises(ReviewError, match="damaged"):
        load_review(tmp_path, "P.004")


def test_a_long_transcript_loses_every_value():
    text = (TEXT + " ") * 400  # about 60 000 characters, a 2-hour interview
    spans = [Span(i, i + 6, "Dupont", "PERSON") for i in range(len(text)) if text.startswith("Dupont", i)]
    review = Review.from_spans("a.txt", "A", text, spans)
    clean, _ = clean_text(review)
    assert "Dupont" not in clean
    assert leaks(review) == []


# --- doubtful files only ---------------------------------------------------------------


def test_warnings_name_the_reasons_to_check():
    assert [s.text for s in warning_signs(_review())] == [
        "Possibly missed: Vandermeulen",
        "Unsure about: Marie, institutrice",
    ]


def test_a_text_with_no_detail_gets_a_warning():
    nothing = Review.from_spans("a.txt", "A", "Je suis fatiguée.", [])
    assert [s.text for s in warning_signs(nothing)] == ["No personal detail found"]


def test_a_sure_removed_name_gets_no_warning():
    clear = Review.from_spans("a.txt", "A", "Madame Dupont va mieux.", [Span(7, 13, "Dupont", "PERSON", 0.97)])
    assert warning_signs(clear) == []


def test_a_review_file_with_a_reviewed_flag_loads(tmp_path):
    # A review file with a "reviewed" flag and no "approved_how".
    save_review(approve(_review()), tmp_path)
    path = review_path(tmp_path, "P.004")
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["approved_how"]
    data["reviewed"] = False
    path.write_text(json.dumps(data), encoding="utf-8")
    old = load_review(tmp_path, "P.004")
    assert old.status == APPROVED and old.approved_how == "" and old.kept == []
    assert "Dupont" not in clean_text(old)[0]


def test_a_review_file_with_bad_bytes_is_damaged(tmp_path):
    save_review(_review(), tmp_path)
    review_path(tmp_path, "P.004").write_bytes(b"\xff\xfe{")
    with pytest.raises(ReviewError, match="damaged"):
        load_review(tmp_path, "P.004")


def test_separators_and_unicode_case_do_not_hide_a_value():
    text = "Jean Dupont vient. Jean-Dupont repart. ΟΣ et ος."
    spans = [Span(0, 11, "Jean Dupont", "PERSON"), Span(39, 41, "ΟΣ", "PERSON")]
    review = Review.from_spans("a.txt", "A", text, spans)
    clean, _ = clean_text(review)
    assert clean == "PERSON_1 vient. PERSON_1 repart. PERSON_2 et PERSON_2."
    assert leaks(review) == []


def test_a_contraction_is_no_title():
    review = Review.from_spans("a.txt", "A", "Hello, I'm Dr Maes.", [_span("Maes", "PERSON", text="Hello, I'm Dr Maes.")])
    assert possibly_missed(review) == []


# --- titles, accents and particles ---------------------------------------------------


@pytest.mark.parametrize("text, word", [
    ("Ik was gisteren bij dokter Janssens.", "Janssens"),
    ("Ik zag mevrouw Vermeiren.", "Vermeiren"),
    ("I saw Mrs Vermeiren last week.", "Vermeiren"),
    ("Then doctor Lambrecht came.", "Lambrecht"),
])
def test_a_dutch_or_english_title_is_a_title(text, word):
    review = Review.from_spans("a.txt", "A", text, [])
    assert [w for w, _ in possibly_missed(review)] == [word]
    assert blocking_warnings(review)
