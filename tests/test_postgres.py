"""Retrieval on PostgreSQL + pgvector. Runs only when TEST_DATABASE_URL is set (CI does)."""

from pathlib import Path

import pytest
from sqlalchemy import text

from inboxdraft.db import drop_all, init_db, make_engine, make_session_factory
from inboxdraft.embeddings import HashingEmbedder
from inboxdraft.ingest import ingest_directory
from inboxdraft.retrieve import search

KNOWLEDGE = Path(__file__).resolve().parents[1] / "knowledge"

pytestmark = pytest.mark.pg


def test_pgvector_ingest_and_search(pg_url: str) -> None:
    engine = make_engine(pg_url)
    embedder = HashingEmbedder()
    drop_all(engine)
    init_db(engine, embedder.dim)
    factory = make_session_factory(engine)
    with factory() as session:
        report = ingest_directory(session, embedder, KNOWLEDGE)
        session.commit()
        assert len(report.added) == 10
        column_type = session.execute(
            text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
            )
        ).scalar_one()
        assert column_type == f"vector({embedder.dim})"
        hits = search(
            session, embedder, "what do you charge for a basic self assessment tax return", k=5
        )
        assert "02-fees.md" in {h.chunk.document.path for h in hits}
        assert 0.0 <= hits[0].score <= 1.0001
        index_exists = session.execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = 'chunks_embedding_idx'")
        ).scalar_one_or_none()
        assert index_exists == 1
    drop_all(engine)
