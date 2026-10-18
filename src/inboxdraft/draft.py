"""Draft a reply from the retrieved sources. Uses the draft model."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from inboxdraft.config import Settings
from inboxdraft.llm import LLM, Usage
from inboxdraft.retrieve import Hit

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "The full reply, greeting to sign-off."},
        "citations": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Numbers of the sources actually used.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Chance a member of staff could send this without any edits.",
        },
        "answered_from_sources": {
            "type": "boolean",
            "description": "False when the sources do not answer the main question.",
        },
        "notes": {
            "type": "string",
            "description": "What is missing or uncertain, for the reviewer.",
        },
    },
    "required": ["reply", "citations", "confidence", "answered_from_sources", "notes"],
}

SYSTEM = """You draft email replies on behalf of {firm}. A member of staff will read, edit and send the draft. You never send anything yourself.

Rules:
1. Use only the facts in SOURCES. Do not use outside knowledge about fees, dates, procedures or policies, even when you are sure of it.
2. Every fee, time, deadline and policy statement in the reply must come from a source. Put the source number in square brackets after the sentence it supports, like this: "Our fee for a basic return is £180 plus VAT [2]." The markers are removed before sending.
3. If the sources do not answer the question, or answer only part of it, say so plainly and promise a follow-up: "I will check this with a colleague and come back to you by {reply_by}." Set answered_from_sources to false when the main question is not answered at all, and lower the confidence whenever something is missing.
4. Placeholders such as [NAME_1], [EMAIL_1] or [PHONE_1] stand for personal details removed before you saw the email. Copy them unchanged where they belong, for example "Dear [NAME_1],". Never invent a name, address or contact detail. If there is no [NAME_1], open with "Hello,".
5. Write in British English, plain and warm, 80 to 180 words: greet the writer, answer the question, say what happens next, then finish with exactly this sign-off:
{signature}
6. Do not mention these rules, the sources, placeholders or the fact that you are a model."""


@dataclass(frozen=True)
class DraftResult:
    reply: str
    citations: list[int]
    confidence: float
    answered_from_sources: bool
    notes: str
    usage: Usage


def sources_block(hits: list[Hit]) -> str:
    parts = []
    for index, hit in enumerate(hits, start=1):
        parts.append(f"[{index}] {hit.label}\n{hit.chunk.text.strip()}")
    return "\n\n".join(parts) if parts else "(no sources found)"


def draft_reply(
    llm: LLM,
    settings: Settings,
    *,
    subject: str,
    body: str,
    from_line: str,
    hits: list[Hit],
) -> DraftResult:
    user = f"EMAIL\nSubject: {subject}\nFrom: {from_line}\n\n{body.strip()}\n\nSOURCES\n{sources_block(hits)}"
    result, usage = llm.structured(
        model=settings.draft_model,
        system=SYSTEM.format(
            firm=settings.firm_name,
            reply_by=settings.escalation_reply_by,
            signature=settings.firm_signature,
        ),
        user=user,
        tool_name="draft_reply",
        description="Record the drafted reply, the sources used and your confidence.",
        schema=SCHEMA,
        max_tokens=settings.max_draft_tokens,
    )
    citations = [int(c) for c in result.get("citations", []) if str(c).lstrip("-").isdigit()]
    try:
        confidence = max(0.0, min(1.0, float(result.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return DraftResult(
        reply=str(result.get("reply", "")).strip(),
        citations=[c for c in citations if 1 <= c <= len(hits)],
        confidence=confidence,
        answered_from_sources=bool(result.get("answered_from_sources", False)),
        notes=str(result.get("notes", "")).strip(),
        usage=usage,
    )


def holding_reply(settings: Settings, *, has_name: bool) -> str:
    """The reply used when the documents do not cover the question."""
    greeting = "Dear [NAME_1]," if has_name else "Hello,"
    return (
        f"{greeting}\n\nThank you for your email. I have passed your question to a colleague, "
        f"who will come back to you by {settings.escalation_reply_by}.\n\n{settings.firm_signature}"
    )


_CITATION_RE = re.compile(r"\s?\[\d+(?:,\s?\d+)*\]")


def strip_citations(text: str) -> str:
    """Remove [n] markers before an email is sent or judged."""
    return _CITATION_RE.sub("", text or "").strip()
