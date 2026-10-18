"""ORM models: the document index and the enquiries with their drafts.

What is deliberately *not* stored: the map from PII tokens to the original values. It lives
in memory for one pipeline run and is discarded (see pii.py and pipeline.py).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from inboxdraft.db import Base, EmbeddingType, utcnow

STATUS_NEW = "new"  # imported, not yet processed
STATUS_DRAFTED = "drafted"  # draft ready for a person to review
STATUS_ESCALATED = "escalated"  # a person must look before anything goes out
STATUS_SENT = "sent"  # a person pressed Send
STATUSES = (STATUS_NEW, STATUS_DRAFTED, STATUS_ESCALATED, STATUS_SENT)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(500), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str] = mapped_column(String(200), default="")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Chunk.ordinal"
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType, nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")

    @property
    def label(self) -> str:
        return f"{self.document.title} > {self.section}" if self.section else self.document.title


class Enquiry(Base):
    __tablename__ = "enquiries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20), default="inbox")  # inbox | eval
    eval_case_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # the incoming email
    message_id: Mapped[str | None] = mapped_column(String(500), unique=True, nullable=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(500), nullable=True)
    references_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_name: Mapped[str] = mapped_column(String(300), default="")
    from_email: Mapped[str] = mapped_column(String(300), default="")
    to_email: Mapped[str] = mapped_column(String(300), default="")
    subject: Mapped[str] = mapped_column(String(1000), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # pipeline output
    status: Mapped[str] = mapped_column(String(20), default=STATUS_NEW, index=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False)
    classification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scrubbed_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    scrubbed_body: Mapped[str | None] = mapped_column(Text, nullable=True)  # what the model saw
    draft_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # for the reviewer
    model_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[list[int]] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    answered_from_sources: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    unsupported_figures: Mapped[list[str]] = mapped_column(JSON, default=list)
    escalation_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    draft_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fast_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    draft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drafted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)

    # the human decision
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_to: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sent_via: Mapped[str | None] = mapped_column(String(20), nullable=True)  # smtp | outbox
    sent_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    final_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    sources: Mapped[list[EnquirySource]] = relationship(
        back_populates="enquiry", cascade="all, delete-orphan", order_by="EnquirySource.rank"
    )

    @property
    def reply_subject(self) -> str:
        subject = (self.subject or "").strip()
        return subject if subject.lower().startswith("re:") else f"Re: {subject}"


class EnquirySource(Base):
    __tablename__ = "enquiry_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enquiry_id: Mapped[int] = mapped_column(ForeignKey("enquiries.id", ondelete="CASCADE"))
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"))
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)

    enquiry: Mapped[Enquiry] = relationship(back_populates="sources")
    chunk: Mapped[Chunk] = relationship()
