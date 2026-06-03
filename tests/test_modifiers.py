"""Tests for mapping modifier text to HPO subontology leaves.

The target codes are checked against HPO release hp/releases/2026-02-16.
"""

from phenoscribe.modifiers import (
    map_frequency,
    map_onset,
    map_severity,
)


def test_frequency_exact_and_synonym():
    assert map_frequency("frequent") == ("HP:0040282", "Frequent")
    assert map_frequency("occasional") == ("HP:0040283", "Occasional")
    assert map_frequency("very rare") == ("HP:0040284", "Very rare")
    assert map_frequency("often") == ("HP:0040282", "Frequent")


def test_frequency_longest_cue_wins():
    # "very rare" must not be shadowed by the shorter "rare" substring.
    assert map_frequency("very rare event") == ("HP:0040284", "Very rare")


def test_severity_maps_to_clinical_modifier_leaves():
    assert map_severity("mild") == ("HP:0012825", "Mild")
    assert map_severity("severe") == ("HP:0012828", "Severe")
    assert map_severity("severe headache") == ("HP:0012828", "Severe")


def test_onset_maps_to_onset_leaves():
    assert map_onset("childhood") == ("HP:0011463", "Childhood onset")
    assert map_onset("adult onset") == ("HP:0003581", "Adult onset")
    assert map_onset("since childhood") == ("HP:0011463", "Childhood onset")


def test_empty_and_unmapped_return_none():
    assert map_frequency("") is None
    assert map_frequency(None) is None
    assert map_severity("excruciating") is None
    assert map_onset("last tuesday") is None
