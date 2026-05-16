"""Tests for SOW parsing — heading detection, chunking, dedupe."""
from __future__ import annotations

from pathlib import Path

from src.scope.sow_ingest import _looks_like_heading, parse_sow

FIXTURE = Path(__file__).parent / "fixtures" / "sows" / "acme_website_sow.txt"


def test_known_section_headings_detected():
    assert _looks_like_heading("Deliverables")
    assert _looks_like_heading("Out of Scope")
    assert _looks_like_heading("DELIVERABLES")
    assert _looks_like_heading("Timeline:")


def test_sentence_is_not_heading():
    assert not _looks_like_heading("This is a long sentence describing the project deliverables in detail.")
    assert not _looks_like_heading("We will deliver designs.")


def test_parse_sow_extracts_sections():
    chunks = parse_sow(FIXTURE, project_id="P-acme")

    assert len(chunks) > 0
    sections = {c.section for c in chunks}
    # Heading variants should normalize via .title() — "Out of Scope" stays distinct.
    assert "Deliverables" in sections
    assert "Out Of Scope" in sections
    assert "Timeline" in sections


def test_parse_sow_chunks_carry_project_id():
    chunks = parse_sow(FIXTURE, project_id="P-acme")
    assert all(c.project_id == "P-acme" for c in chunks)


def test_parse_sow_chunk_ids_are_unique():
    chunks = parse_sow(FIXTURE, project_id="P-acme")
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))


def test_parse_sow_filters_tiny_paragraphs():
    """Any chunk shorter than MIN_CHUNK_CHARS should be dropped."""
    chunks = parse_sow(FIXTURE, project_id="P-acme")
    assert all(len(c.text) >= 40 for c in chunks)
