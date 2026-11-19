"""The leak check (review.possibly_left): a first name or a town left in the clean text."""

from importlib.resources import files

import pytest

from phenoscribe.pii import Span
from phenoscribe.review import Review, first_names, possibly_left, towns


def _left(text, spans=(), kept=()):
    """The words that possibly_left finds, after the removal of ``spans`` (value, category)."""
    found = []
    for value, category in spans:
        start = text.index(value)
        found.append(Span(start, start + len(value), value, category, 0.95))
    review = Review.from_spans("a.txt", "A", text, found)
    review.kept = list(kept)
    return [word for word, _ in possibly_left(review)]


@pytest.mark.parametrize("text, words", [
    ("Elle habite à Namur avec Claire.", ["Namur", "Claire"]),
    ("Ik woon in Geel bij Hilde.", ["Geel", "Hilde"]),
    ("I live in Ghent with my son Pierre.", ["Ghent", "Pierre"]),
    ("Hij heet Jean-Luc, zegt hij.", ["Jean-Luc"]),
])
def test_a_first_name_or_a_town_in_the_clean_text_is_a_sign(text, words):
    assert _left(text) == words


@pytest.mark.parametrize("text", [
    "Elle a la peau rose et boit de l'eau de spa.",  # lower case: words
    "Mijn ogen zijn geel, ik eet een peer.",
    "Claire est venue. Rose aussi.",  # sentence start: the capital says nothing
    "D: Bonjour.\nP: Pierre m'a amené.",  # after a speaker label
    "[00:12] Namur, c'est loin.",  # after a timestamp
    "On parle de maladie de Crohn, du syndrome de Gilbert et de Addison's disease.",
    "Ze heeft de ziekte van Lyme.",
    "I saw her in May, on a Monday, before Christmas.",
])
def test_words_that_look_like_a_name_give_no_sign(text):
    assert _left(text) == []


def test_a_capital_after_a_removed_name_is_a_family_name():
    assert _left("Mijn dokter is Hilde Wouters.", [("Hilde", "PERSON")]) == ["Wouters"]


def test_a_capital_after_a_particle_is_a_family_name():
    assert _left("Ik ken mevrouw Van den Broeck niet.") == ["Broeck"]


def test_a_word_a_person_put_back_gives_no_sign():
    assert _left("Elle habite à Namur.", kept=["Namur"]) == []


def test_removed_words_are_not_in_the_clean_text():
    assert _left("Elle habite à Namur avec Claire.", [("Namur", "LOCATION"), ("Claire", "PERSON")]) == []


@pytest.mark.parametrize("text, words", [
    ("Elle habite à Liège.", ["Liège"]),
    ("Ze woont in Sint-Niklaas, vroeger in Knokke-Heist.", ["Sint-Niklaas", "Knokke-Heist"]),
    ("P: Claire Dubois, j'habite ici.", ["Claire"]),  # a full name at the start of a turn
    ("Ik ken mevrouw van den Broeck.", ["Broeck"]),  # a Dutch particle in lower case
])
def test_the_leak_check_finds_accents_hyphens_and_full_names(text, words):
    assert _left(text) == words


@pytest.mark.parametrize("text", [
    "Je suis malade depuis Noël.",
    "Je tousse. Le Covid m'a fatiguée. Du Dafalgan le soir. La Belgique est petite.",
])
def test_an_article_at_the_start_of_a_sentence_is_no_particle(text):
    assert _left(text) == []


@pytest.mark.parametrize("name", ["first_names_be.txt", "towns_be.txt"])
def test_the_word_lists_name_their_source_and_licence(name):
    header = (files("phenoscribe") / "resources" / name).read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("# Statbel") and "CC BY 4.0" in header


def test_the_word_lists_hold_names_and_towns_of_three_languages():
    assert {"claire", "rose", "hilde"} <= first_names()
    assert {"namur", "geel", "spa", "gent", "ghent"} <= towns()
