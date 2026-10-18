"""Read Markdown, text, PDF and DOCX files and split them into section-sized chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".pdf", ".docx"}
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    section: str
    text: str


def load_text(path: Path) -> tuple[str, str]:
    """Return (title, plain text) for a supported file."""
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        text = path.read_text(encoding="utf-8")
        title = _first_heading(text) or _title_from_name(path)
        return title, text
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
        return _title_from_name(path), "\n\n".join(pages)
    if suffix == ".docx":
        import docx

        document = docx.Document(str(path))
        lines: list[str] = []
        for paragraph in document.paragraphs:
            style = (paragraph.style.name or "") if paragraph.style is not None else ""
            text = paragraph.text.strip()
            if not text:
                lines.append("")
            elif style.startswith("Heading"):
                level = "".join(ch for ch in style if ch.isdigit()) or "2"
                lines.append(f"{'#' * min(int(level), 6)} {text}")
            else:
                lines.append(text)
        text = "\n".join(lines)
        return _first_heading(text) or _title_from_name(path), text
    raise ValueError(f"unsupported file type: {path.suffix}")


def chunk_text(text: str, *, max_chars: int = 1200) -> list[TextChunk]:
    """Split by Markdown headings, then by paragraphs when a section is long.

    The section label is the heading path ("Fees > Payroll"), which is embedded together with
    the chunk text and shown to the model and the reviewer as the citation.
    """
    sections = _split_sections(text)
    chunks: list[TextChunk] = []
    for section, body in sections:
        for piece in _split_long(body, max_chars):
            chunks.append(TextChunk(ordinal=len(chunks), section=section, text=piece))
    return chunks


def _split_sections(text: str) -> list[tuple[str, str]]:
    stack: list[tuple[int, str]] = []
    current: list[str] = []
    sections: list[tuple[str, str]] = []

    def flush() -> None:
        body = "\n".join(current).strip()
        if body:
            label = " > ".join(name for level, name in stack if level > 1)
            sections.append((label, body))
        current.clear()

    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, match.group(2).strip()))
        else:
            current.append(line.rstrip())
    flush()
    return sections


def _split_long(body: str, max_chars: int) -> list[str]:
    if len(body) <= max_chars:
        return [body]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    pieces: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(candidate) > max_chars and buffer:
            pieces.append(buffer)
            buffer = paragraph
        else:
            buffer = candidate
    if buffer:
        pieces.append(buffer)
    return pieces


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            return match.group(2).strip()
    return None


def _title_from_name(path: Path) -> str:
    stem = re.sub(r"^\d+[-_.\s]*", "", path.stem)
    return stem.replace("-", " ").replace("_", " ").strip().capitalize() or path.name
