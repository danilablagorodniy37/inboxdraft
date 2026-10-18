from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inboxdraft.chunking import chunk_text, load_text
from inboxdraft.ingest import ingest_directory
from inboxdraft.models import Chunk, Document
from inboxdraft.pipeline import Deps
from inboxdraft.retrieve import search

KNOWLEDGE = Path(__file__).resolve().parents[1] / "knowledge"


def test_markdown_sections_become_chunks_with_heading_paths() -> None:
    text = "# Fees\n\nIntro line.\n\n## Payroll\n\nPayroll is £25 per month.\n\n### Pensions\n\nSet-up is £150."
    chunks = chunk_text(text)
    sections = [c.section for c in chunks]
    assert sections == ["", "Payroll", "Payroll > Pensions"]
    assert chunks[1].text == "Payroll is £25 per month."


def test_long_sections_split_on_paragraphs() -> None:
    body = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(8))
    chunks = chunk_text("## Long\n\n" + body, max_chars=800)
    assert len(chunks) > 1
    assert all(len(c.text) <= 800 for c in chunks)
    assert all(c.section == "Long" for c in chunks)


def test_load_text_reads_every_demo_document() -> None:
    files = sorted(KNOWLEDGE.glob("*.md"))
    assert len(files) == 10
    for path in files:
        title, text = load_text(path)
        assert title and text


def test_ingest_is_idempotent_and_search_finds_the_fee_section(
    indexed: Session, deps: Deps
) -> None:
    docs = indexed.execute(select(func.count(Document.id))).scalar_one()
    chunks = indexed.execute(select(func.count(Chunk.id))).scalar_one()
    assert docs == 10 and chunks > 20

    report = ingest_directory(indexed, deps.embedder, KNOWLEDGE)
    assert report.added == [] and len(report.unchanged) == 10

    hits = search(
        indexed, deps.embedder, "what do you charge for a basic self assessment tax return", k=5
    )
    # keyword embeddings rank the FAQ ("a basic return with us is from £180") close to the fee list;
    # both are correct sources for this question
    assert hits and "tax return" in hits[0].label.lower() + hits[0].chunk.text.lower()
    assert "02-fees.md" in {h.chunk.document.path for h in hits}
    assert any("£180" in h.chunk.text for h in hits)
    assert hits[0].score >= hits[-1].score


def test_search_on_empty_index_returns_nothing(session: Session, deps: Deps) -> None:
    assert search(session, deps.embedder, "anything", k=3) == []
