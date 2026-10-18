"""One enquiry in, one reviewed-by-nobody-yet draft out.

    scrub PII -> classify -> retrieve -> draft -> figures guard -> restore PII -> status

Nothing in this module sends email. The only sender call in the code base is the approve route
in web/app.py, and tests/test_no_send_without_human.py checks that this stays true.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from inboxdraft import pii
from inboxdraft.classify import classify
from inboxdraft.config import Settings
from inboxdraft.cost import cost_usd, sum_costs
from inboxdraft.db import utcnow
from inboxdraft.draft import draft_reply, holding_reply
from inboxdraft.embeddings import Embedder, get_embedder
from inboxdraft.guard import unsupported_figures
from inboxdraft.llm import LLM, get_llm
from inboxdraft.models import STATUS_DRAFTED, STATUS_ESCALATED, STATUS_NEW, Enquiry, EnquirySource
from inboxdraft.retrieve import search


@dataclass
class Deps:
    settings: Settings
    llm: LLM
    embedder: Embedder


def build_deps(settings: Settings) -> Deps:
    return Deps(settings=settings, llm=get_llm(settings), embedder=get_embedder(settings))


def process_enquiry(session: Session, enquiry: Enquiry, deps: Deps) -> Enquiry:
    settings = deps.settings
    started = time.perf_counter()

    # 1. PII out. The map lives in `scrubber.mapping` for this function call only.
    scrubber = pii.Scrubber(from_name=enquiry.from_name, from_email=enquiry.from_email)
    subject = scrubber.scrub(enquiry.subject)
    body = scrubber.scrub(enquiry.body)
    has_name = "[NAME_1]" in scrubber.mapping
    from_line = (
        ("[NAME_1] [NAME_2]" if "[NAME_2]" in scrubber.mapping else "[NAME_1]") if has_name else ""
    )
    from_line = f"{from_line} <[EMAIL_1]>".strip() if "[EMAIL_1]" in scrubber.mapping else from_line

    # 2. classify (fast model)
    classification = classify(deps.llm, settings, subject=subject, body=body)

    # 3. retrieve (only scrubbed text is embedded)
    hits = search(session, deps.embedder, f"{subject}\n{body}", k=settings.top_k)

    # 4. draft (draft model)
    draft = draft_reply(
        deps.llm, settings, subject=subject, body=body, from_line=from_line, hits=hits
    )

    # 5. figures guard, on the draft with placeholders still in place
    figures = unsupported_figures(draft.reply, [h.chunk.text for h in hits], f"{subject}\n{body}")

    reasons: list[str] = []
    if classification.needs_human:
        reasons.append(
            f"Needs a person first: {classification.reason or 'flagged by the classifier'}"
        )
    if not hits:
        reasons.append("The knowledge base is empty: run ingest")
    if not draft.answered_from_sources:
        reasons.append("Not covered by the documents")
    elif draft.confidence < settings.confidence_threshold:
        reasons.append(
            f"Confidence {draft.confidence:.2f} is below the threshold {settings.confidence_threshold:.2f}"
        )
    if figures:
        reasons.append("Figures not found in any source: " + ", ".join(figures))

    reply_with_tokens = draft.reply
    notes = draft.notes
    if not draft.answered_from_sources:
        notes = "\n\n".join(p for p in [draft.notes, "Model's attempt:\n" + draft.reply] if p)
        reply_with_tokens = holding_reply(settings, has_name=has_name)

    # 6. PII back in, then drop the map
    restored = _tidy_leftovers(pii.restore(reply_with_tokens, scrubber.mapping))
    usages = [classification.usage, draft.usage]
    costs = [cost_usd(u, settings.model_prices) for u in usages]

    enquiry.scrubbed_subject = subject
    enquiry.scrubbed_body = body
    enquiry.category = classification.category
    enquiry.needs_human = classification.needs_human
    enquiry.classification_reason = classification.reason
    enquiry.draft_text = restored
    enquiry.model_notes = notes or None
    enquiry.citations = draft.citations
    enquiry.confidence = draft.confidence
    enquiry.answered_from_sources = draft.answered_from_sources
    enquiry.unsupported_figures = figures
    enquiry.escalation_reasons = reasons
    enquiry.status = STATUS_ESCALATED if reasons else STATUS_DRAFTED
    enquiry.draft_model = draft.usage.model
    enquiry.fast_model = classification.usage.model
    enquiry.llm_calls = len(usages)
    enquiry.input_tokens = sum(u.input_tokens for u in usages)
    enquiry.output_tokens = sum(u.output_tokens for u in usages)
    enquiry.cost_usd = sum_costs(costs)
    enquiry.draft_ms = int((time.perf_counter() - started) * 1000)
    enquiry.drafted_at = utcnow()
    enquiry.edited = False
    enquiry.sources = [
        EnquirySource(chunk_id=hit.chunk.id, rank=index, score=hit.score)
        for index, hit in enumerate(hits, start=1)
    ]
    session.add(enquiry)
    session.flush()
    del scrubber  # the token map goes with it
    return enquiry


def process_new(session: Session, deps: Deps, *, limit: int | None = None) -> list[Enquiry]:
    query = (
        select(Enquiry)
        .where(Enquiry.status == STATUS_NEW, Enquiry.source == "inbox")
        .order_by(Enquiry.received_at.asc().nulls_last(), Enquiry.id.asc())
    )
    if limit:
        query = query.limit(limit)
    enquiries = list(session.execute(query).scalars())
    for enquiry in enquiries:
        process_enquiry(session, enquiry, deps)
        session.commit()
    return enquiries


def _tidy_leftovers(text: str) -> str:
    """A placeholder the map does not know (the model invented [NAME_3]) must not go out."""
    text = pii.TOKEN_RE.sub("", text)
    return text.replace("Dear ,", "Hello,").replace("Dear  ,", "Hello,").strip() + "\n"
