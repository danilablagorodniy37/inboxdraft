"""Classify an enquiry: category plus a "needs a person first" flag. Uses the fast model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from inboxdraft.config import Settings
from inboxdraft.llm import LLM, Usage

CATEGORIES = ("pricing", "appointment", "documents", "services", "deadlines", "complaint", "other")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "needs_human": {"type": "boolean"},
        "reason": {"type": "string", "description": "One sentence."},
    },
    "required": ["category", "needs_human", "reason"],
}

SYSTEM = """You classify incoming emails for {firm}, a small accountancy practice.

Categories:
- pricing: fees, quotes, what something costs, payment terms
- appointment: booking, changing or cancelling a meeting or call
- documents: what to send or bring, uploads, the client portal, forms
- services: what the firm does and how a service works (payroll, VAT, bookkeeping, accounts, company formation)
- deadlines: tax and filing dates, when something is due
- complaint: dissatisfaction, errors, disputes about work or fees
- other: anything else, including spam, job applications, sales pitches and requests outside the firm's services

needs_human is true when a member of staff must handle the email before any reply goes out:
complaints and disputes, letters or penalties from HMRC, refunds, legal action, a distressed or angry
writer, or a request for specific advice about the writer's own tax position (how much they owe,
what they should do). Ordinary questions about fees, dates, documents and services do not need a human first.

Personal details in the email have been replaced with placeholders such as [NAME_1] or [PHONE_1]."""


@dataclass(frozen=True)
class Classification:
    category: str
    needs_human: bool
    reason: str
    usage: Usage


def classify(llm: LLM, settings: Settings, *, subject: str, body: str) -> Classification:
    user = f"Subject: {subject}\n\n{body}".strip()
    result, usage = llm.structured(
        model=settings.fast_model,
        system=SYSTEM.format(firm=settings.firm_name),
        user=user,
        tool_name="classify_enquiry",
        description="Record the category of the enquiry and whether a person must handle it first.",
        schema=SCHEMA,
        max_tokens=200,
    )
    category = str(result.get("category", "other"))
    if category not in CATEGORIES:
        category = "other"
    return Classification(
        category=category,
        needs_human=bool(result.get("needs_human", False)),
        reason=str(result.get("reason", "")).strip(),
        usage=usage,
    )
