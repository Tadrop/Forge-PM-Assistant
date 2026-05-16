"""Honesty validator unit tests — word + phrase matching, false-positive avoidance."""
from __future__ import annotations

from src.drafters.honesty import (
    SOFT_PEDAL_PHRASES,
    SOFT_PEDAL_WORDS,
    check_honesty,
    find_soft_pedal_offenders,
)


def test_finds_basic_soft_words():
    text = "We had a minor delay on deliverables this week."
    offenders = find_soft_pedal_offenders(text)
    assert "minor" in offenders


def test_finds_phrase_offenders():
    text = "There's nothing major to flag here."
    offenders = find_soft_pedal_offenders(text)
    assert "nothing major" in offenders


def test_word_boundary_avoids_false_positives():
    """'tiny' is forbidden, but a substring like 'destiny' should not trigger."""
    text = "Aligning destiny is part of the discovery phase."
    offenders = find_soft_pedal_offenders(text)
    assert "tiny" not in offenders


def test_case_insensitive():
    text = "There were MINOR issues this week."
    assert "minor" in find_soft_pedal_offenders(text)


def test_multiple_offenders_deduped_and_ordered():
    text = "A minor slip and a slight delay; nothing major really."
    offenders = find_soft_pedal_offenders(text)
    assert "minor" in offenders
    assert "slight" in offenders
    assert "nothing major" in offenders
    # No duplicates.
    assert len(offenders) == len(set(offenders))


def test_clean_text_returns_empty():
    text = "Two stuck blockers on dependencies; we are escalating to leadership today."
    assert find_soft_pedal_offenders(text) == []


def test_check_honesty_reports_is_honest():
    clean = check_honesty("All due dates missed; we have 3 stuck blockers.")
    assert clean.is_honest is True
    assert clean.offenders == []

    bad = check_honesty("Just a slight delay.")
    assert bad.is_honest is False
    assert "slight" in bad.offenders


def test_word_list_is_lowercased():
    """Single-word entries should all be lowercase — the matcher lowercases input."""
    for word in SOFT_PEDAL_WORDS:
        assert word == word.lower()
    for phrase in SOFT_PEDAL_PHRASES:
        assert phrase == phrase.lower()
