"""Nearest-neighbour search over the document index.

PostgreSQL: pgvector cosine distance in SQL (with an HNSW index). SQLite: NumPy over all chunks,
which is fine for a knowledge base of a few hundred chunks.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from inboxdraft.embeddings import Embedder
from inboxdraft.models import Chunk


class IndexMismatch(RuntimeError):
    pass


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float  # cosine similarity, 1.0 is identical

    @property
    def label(self) -> str:
        return self.chunk.label


def embedding_text(section: str, body: str) -> str:
    """What gets embedded for a chunk: the heading path and the text."""
    return f"{section}\n{body}" if section else body


def search(session: Session, embedder: Embedder, query: str, k: int = 5) -> list[Hit]:
    query_vec = embedder.embed_query(query)
    if session.get_bind().dialect.name == "postgresql":
        return _search_pgvector(session, query_vec, k)
    return _search_numpy(session, query_vec, k)


def _search_pgvector(session: Session, query_vec: list[float], k: int) -> list[Hit]:
    literal = "[" + ",".join(f"{x:.8f}" for x in query_vec) + "]"
    rows = session.execute(
        text(
            "SELECT id, 1 - (embedding <=> CAST(:q AS vector)) AS score "
            "FROM chunks WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"
        ),
        {"q": literal, "k": k},
    ).all()
    return _hits(session, [(int(row.id), float(row.score)) for row in rows])


def _search_numpy(session: Session, query_vec: list[float], k: int) -> list[Hit]:
    rows = session.execute(
        select(Chunk.id, Chunk.embedding).where(Chunk.embedding.is_not(None))
    ).all()
    if not rows:
        return []
    ids = [int(row.id) for row in rows]
    matrix = np.asarray([row.embedding for row in rows], dtype=np.float32)
    query = np.asarray(query_vec, dtype=np.float32)
    if matrix.shape[1] != query.shape[0]:
        raise IndexMismatch(
            f"index has {matrix.shape[1]}-dim vectors but the embedder returns {query.shape[0]}: "
            "re-run `inboxdraft ingest`"
        )
    norms = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(query) or 1.0)
    scores = matrix @ query / np.where(norms == 0, 1.0, norms)
    order = np.argsort(-scores)[:k]
    return _hits(session, [(ids[i], float(scores[i])) for i in order])


def _hits(session: Session, ranked: list[tuple[int, float]]) -> list[Hit]:
    if not ranked:
        return []
    chunks = session.execute(
        select(Chunk)
        .options(selectinload(Chunk.document))
        .where(Chunk.id.in_([i for i, _ in ranked]))
    ).scalars()
    by_id = {chunk.id: chunk for chunk in chunks}
    return [Hit(chunk=by_id[i], score=score) for i, score in ranked if i in by_id]
