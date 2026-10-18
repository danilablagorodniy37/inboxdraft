"""Database engine, session factory and the portable embedding column type.

The same models run on SQLite (laptop, one mailbox) and on PostgreSQL + pgvector (shared).
On PostgreSQL the embedding column is a pgvector `vector(dim)`; on SQLite it is JSON and the
nearest-neighbour search runs in NumPy (see retrieve.py).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import JSON, Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

_embedding_dim = 384


def set_embedding_dim(dim: int) -> None:
    """Fix the vector width before tables are created or queried on PostgreSQL."""
    global _embedding_dim
    _embedding_dim = int(dim)


def get_embedding_dim() -> int:
    return _embedding_dim


class EmbeddingType(TypeDecorator):
    """`vector(dim)` on PostgreSQL, JSON everywhere else. Values are plain Python lists."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(get_embedding_dim()))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value]

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value]


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC timestamps: the same value on SQLite and PostgreSQL."""
    return datetime.now(UTC).replace(tzinfo=None)


def make_engine(database_url: str) -> Engine:
    kwargs: dict = {}
    if database_url.startswith("sqlite"):
        path = database_url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(database_url, future=True, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine, embedding_dim: int) -> None:
    """Create tables (and the pgvector extension and index on PostgreSQL)."""
    from inboxdraft import models  # noqa: F401  (registers the tables)

    set_embedding_dim(embedding_dim)
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql" and embedding_dim <= 2000:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks "
                    "USING hnsw (embedding vector_cosine_ops)"
                )
            )


def drop_index_tables(engine: Engine) -> None:
    """Drop the document index. Needed when the embedding model (and so the width) changes."""
    from inboxdraft.models import Chunk, Document, EnquirySource

    Base.metadata.drop_all(
        engine, tables=[EnquirySource.__table__, Chunk.__table__, Document.__table__]
    )


def drop_all(engine: Engine) -> None:
    from inboxdraft import models  # noqa: F401

    Base.metadata.drop_all(engine)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
