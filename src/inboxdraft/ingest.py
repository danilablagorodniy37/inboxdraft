"""Build the document index from a folder of Markdown, text, PDF and DOCX files."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from inboxdraft.chunking import SUPPORTED_SUFFIXES, chunk_text, load_text
from inboxdraft.embeddings import Embedder
from inboxdraft.models import Chunk, Document
from inboxdraft.retrieve import embedding_text


@dataclass
class IngestReport:
    added: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    chunks: int = 0

    def summary(self) -> str:
        return (
            f"{len(self.added)} document(s) indexed, {len(self.unchanged)} unchanged, "
            f"{len(self.removed)} removed, {self.chunks} new chunk(s)"
        )


def ingest_directory(session: Session, embedder: Embedder, directory: Path) -> IngestReport:
    report = IngestReport()
    files = sorted(p for p in directory.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES)
    seen: set[str] = set()
    for path in files:
        rel = path.relative_to(directory).as_posix()
        seen.add(rel)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        existing = session.execute(
            select(Document).where(Document.path == rel)
        ).scalar_one_or_none()
        if existing and existing.sha256 == digest and existing.embedding_model == embedder.name:
            report.unchanged.append(rel)
            continue
        if existing:
            session.delete(existing)
            session.flush()
        title, text = load_text(path)
        chunks = chunk_text(text)
        vectors = embedder.embed_documents([embedding_text(c.section, c.text) for c in chunks])
        document = Document(
            path=rel,
            title=title,
            sha256=digest,
            chunk_count=len(chunks),
            embedding_model=embedder.name,
            chunks=[
                Chunk(ordinal=c.ordinal, section=c.section, text=c.text, embedding=v)
                for c, v in zip(chunks, vectors, strict=True)
            ],
        )
        session.add(document)
        report.added.append(rel)
        report.chunks += len(chunks)
    for document in session.execute(select(Document)).scalars():
        if document.path not in seen:
            session.delete(document)
            report.removed.append(document.path)
    session.flush()
    return report
