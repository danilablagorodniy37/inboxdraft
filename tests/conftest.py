"""Fixtures: SQLite in a temp folder, hashing embeddings, the fake model. No network, no keys."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from inboxdraft.config import Settings
from inboxdraft.db import init_db, make_engine, make_session_factory
from inboxdraft.embeddings import HashingEmbedder
from inboxdraft.ingest import ingest_directory
from inboxdraft.llm import FakeLLM
from inboxdraft.pipeline import Deps

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / "knowledge"
INBOX = ROOT / "inbox"
CASES = ROOT / "evals" / "cases.jsonl"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="fake",
        embeddings_provider="hashing",
        database_url=f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}",
        knowledge_dir=str(KNOWLEDGE),
        inbox_dir=str(INBOX),
        outbox_dir=str(tmp_path / "outbox"),
        smtp_host=None,
        app_password=None,
        confidence_threshold=0.6,
    )


@pytest.fixture
def deps(settings: Settings) -> Deps:
    return Deps(settings=settings, llm=FakeLLM(), embedder=HashingEmbedder())


@pytest.fixture
def session_factory(settings: Settings, deps: Deps):
    engine = make_engine(settings.database_url)
    init_db(engine, deps.embedder.dim)
    return make_session_factory(engine)


@pytest.fixture
def session(session_factory) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def indexed(session: Session, deps: Deps) -> Session:
    ingest_directory(session, deps.embedder, KNOWLEDGE)
    session.commit()
    return session


@pytest.fixture
def pg_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url
