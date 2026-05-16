"""Ingest SOWs into the vector store.

Pipeline:
    1. Read text from .pdf / .txt / .md
    2. Walk paragraphs; promote short Title-Case / ALL-CAPS lines to "section"
    3. Drop paragraphs shorter than MIN_CHUNK_CHARS (boilerplate, headers)
    4. Embed each chunk
    5. Upsert with namespace=project_id

CLAUDE.md §6 Step 1: "Get the 18 active project SOWs from Mei BEFORE building
the scope creep detector." This module is the loader for that handoff.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from .embeddings import Embedder
from .models import SowChunk
from .sow_store import SowStore

logger = logging.getLogger(__name__)

MIN_CHUNK_CHARS = 40
MAX_CHUNK_CHARS = 1500

KNOWN_SECTIONS = {
    "scope of work",
    "scope",
    "deliverables",
    "out of scope",
    "exclusions",
    "timeline",
    "milestones",
    "acceptance criteria",
    "assumptions",
    "responsibilities",
    "fees",
    "payment terms",
    "change orders",
}


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return False
    if stripped.lower().rstrip(":") in KNOWN_SECTIONS:
        return True
    # All caps or Title Case + ends without sentence punctuation
    if stripped.endswith((".", "!", "?")):
        return False
    if stripped.isupper():
        return True
    words = stripped.split()
    if len(words) <= 8 and all(w[:1].isupper() or not w[:1].isalpha() for w in words):
        return True
    return False


def _split_pdf_pages(path: Path) -> list[str]:
    from pypdf import PdfReader  # type: ignore

    reader = PdfReader(str(path))
    return [page.extract_text() or "" for page in reader.pages]


def _split_text_pages(text: str) -> list[str]:
    # Plain-text SOWs treated as a single page.
    return [text]


def _chunk_page(page_text: str) -> list[tuple[str, str]]:
    """Yield (section, chunk_text) pairs for one page.

    Section state carries across paragraphs until the next heading.
    """
    pairs: list[tuple[str, str]] = []
    current_section = "General"
    paragraphs = re.split(r"\n\s*\n", page_text)

    for para in paragraphs:
        lines = [ln for ln in para.splitlines() if ln.strip()]
        if not lines:
            continue

        # If the first line is a heading, update section and use the rest as text.
        if _looks_like_heading(lines[0]):
            current_section = lines[0].strip().rstrip(":").title()
            body = "\n".join(lines[1:]).strip()
            if len(body) >= MIN_CHUNK_CHARS:
                pairs.append((current_section, body[:MAX_CHUNK_CHARS]))
        else:
            body = "\n".join(lines).strip()
            if len(body) >= MIN_CHUNK_CHARS:
                pairs.append((current_section, body[:MAX_CHUNK_CHARS]))

    return pairs


def parse_sow(path: Path, project_id: str) -> list[SowChunk]:
    """Read an SOW file and return chunks with section + page metadata."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = _split_pdf_pages(path)
    elif suffix in {".txt", ".md"}:
        pages = _split_text_pages(path.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"Unsupported SOW format: {suffix}")

    chunks: list[SowChunk] = []
    for page_num, page_text in enumerate(pages, start=1):
        for section, text in _chunk_page(page_text):
            chunk_id = hashlib.sha1(
                f"{project_id}|{page_num}|{section}|{text[:120]}".encode()
            ).hexdigest()[:24]
            chunks.append(
                SowChunk(
                    chunk_id=chunk_id,
                    project_id=project_id,
                    section=section,
                    page=page_num if suffix == ".pdf" else None,
                    text=text,
                )
            )
    logger.info(
        "Parsed SOW path=%s project=%s chunks=%d", path, project_id, len(chunks)
    )
    return chunks


def ingest_sow(
    path: Path,
    project_id: str,
    *,
    embedder: Embedder,
    store: SowStore,
) -> int:
    """Parse + embed + upsert a single SOW. Returns the number of chunks indexed."""
    chunks = parse_sow(path, project_id)
    if not chunks:
        logger.warning("SOW had zero usable chunks path=%s", path)
        return 0
    vectors = embedder.embed_documents([c.text for c in chunks])
    store.upsert(project_id, chunks, vectors)
    return len(chunks)
