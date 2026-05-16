"""Soft-pedal validator.

CLAUDE.md §9: "Status update honesty rule: If health == red, status update
prompt forbids softening adjectives. A validator scans for soft-pedalling
words (e.g. 'minor', 'slight', 'smooth') in red sections and rejects."

Word list is conservative: terms that DOWNPLAY severity (not common words
like 'just' or 'only' which have legit non-softening uses).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Words that, on their own, downplay project trouble. Word-boundary matched.
SOFT_PEDAL_WORDS: frozenset[str] = frozenset(
    {
        "minor",
        "slight",
        "slightly",
        "smooth",
        "smoothly",
        "tiny",
        "modest",
        "trivial",
        "negligible",
        "minimal",
    }
)

# Multi-word phrases. Substring-matched (case-insensitive).
SOFT_PEDAL_PHRASES: frozenset[str] = frozenset(
    {
        "nothing major",
        "no big deal",
        "no worries",
        "all good",
        "kind of stuck",
        "sort of stuck",
    }
)


class SoftPedalError(Exception):
    """Raised when a red status update keeps soft-pedalling after retries."""

    def __init__(self, offenders: list[str], text: str):
        self.offenders = offenders
        self.text = text
        super().__init__(
            f"Status update contains forbidden soft-pedal language: {offenders}"
        )


@dataclass(frozen=True)
class HonestyReport:
    is_honest: bool
    offenders: list[str]


def find_soft_pedal_offenders(text: str) -> list[str]:
    """Return ordered, deduplicated list of soft-pedal terms found in `text`."""
    text_lower = text.lower()
    found: list[str] = []
    for word in SOFT_PEDAL_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", text_lower) and word not in found:
            found.append(word)
    for phrase in SOFT_PEDAL_PHRASES:
        if phrase in text_lower and phrase not in found:
            found.append(phrase)
    return found


def check_honesty(text: str) -> HonestyReport:
    offenders = find_soft_pedal_offenders(text)
    return HonestyReport(is_honest=not offenders, offenders=offenders)
