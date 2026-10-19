"""The part of the evaluation that needs no model and no key: privacy and retrieval.

    inboxdraft evals --offline

Both checks run the production code on the 50 enquiries in `evals/cases.jsonl`:

- Privacy: the enquiry goes through the same `pii.Scrubber` as in the pipeline. The check then
  looks for the sender's name, email address and every phone number in the text the model would
  see. Phone numbers are found with a deliberately loose pattern that is independent of the
  scrubber's own rules, so a gap in those rules shows up as a leak instead of hiding.
- Retrieval: the scrubbed subject and body are embedded and searched exactly as the pipeline does.
  For every enquiry the documents can answer, the check asks whether each required fact (a fee, a
  deadline, a document name) is in the top-k chunks the draft would be written from. A fact that
  is not retrieved cannot be quoted, however good the model is.
"""

from __future__ import annotations

import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from inboxdraft import pii
from inboxdraft.embeddings import Embedder
from inboxdraft.evals import EvalCase
from inboxdraft.retrieve import search

LOOSE_PHONE_RE = re.compile(r"\+?\(?\d[\d\s().-]{8,}\d")


@dataclass
class OfflineResult:
    id: str
    category: str
    pii_items: int
    leaked: list[str]
    eligible: bool
    facts: list[str]
    missing_facts: list[str]
    top_sources: list[str]
    search_ms: float

    @property
    def retrieved_all(self) -> bool:
        return self.eligible and not self.missing_facts


@dataclass
class OfflineSummary:
    n: int
    k: int
    embedder: str
    pii_items: int
    leaked: int
    eligible: int
    retrieved_all: int
    facts: int
    facts_found: int
    search_p50_ms: float
    search_p95_ms: float
    results: list[OfflineResult] = field(default_factory=list)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def personal_details(case: EvalCase) -> list[str]:
    """What must not reach the model: name parts, the sender's email, every phone number."""
    items = [part for part in (case.from_name or "").split() if len(part) > 1]
    if case.from_email:
        items.append(case.from_email)
    text = f"{case.subject}\n{case.body}"
    items += [
        m.group(0).strip() for m in LOOSE_PHONE_RE.finditer(text) if len(_digits(m.group(0))) >= 10
    ]
    return items


def leaks(details: list[str], scrubbed: str) -> list[str]:
    found = []
    scrubbed_digits = _digits(scrubbed)
    for item in details:
        if any(ch.isalpha() for ch in item):
            if re.search(rf"(?<![\w\[]){re.escape(item)}(?![\w\]])", scrubbed, re.IGNORECASE):
                found.append(item)
        elif _digits(item) and _digits(item) in scrubbed_digits:
            found.append(item)
    return found


def run_offline(
    session: Session, embedder: Embedder, cases: list[EvalCase], k: int, report_path: Path
) -> OfflineSummary:
    results: list[OfflineResult] = []
    for case in cases:
        scrubber = pii.Scrubber(from_name=case.from_name, from_email=case.from_email)
        subject, body = scrubber.scrub(case.subject), scrubber.scrub(case.body)
        details = personal_details(case)
        leaked = leaks(details, f"{subject}\n{body}")

        started = time.perf_counter()
        hits = search(session, embedder, f"{subject}\n{body}", k=k)
        search_ms = (time.perf_counter() - started) * 1000
        context = "\n".join(h.chunk.text for h in hits).lower()
        eligible = case.in_kb and not case.needs_human and bool(case.must_include)
        missing = [f for f in case.must_include if f.lower() not in context] if eligible else []
        results.append(
            OfflineResult(
                id=case.id,
                category=case.category,
                pii_items=len(details),
                leaked=leaked,
                eligible=eligible,
                facts=list(case.must_include) if eligible else [],
                missing_facts=missing,
                top_sources=[h.label for h in hits[:2]],
                search_ms=search_ms,
            )
        )

    times = sorted(r.search_ms for r in results)
    summary = OfflineSummary(
        n=len(results),
        k=k,
        embedder=getattr(embedder, "name", type(embedder).__name__),
        pii_items=sum(r.pii_items for r in results),
        leaked=sum(len(r.leaked) for r in results),
        eligible=sum(1 for r in results if r.eligible),
        retrieved_all=sum(1 for r in results if r.retrieved_all),
        facts=sum(len(r.facts) for r in results),
        facts_found=sum(len(r.facts) - len(r.missing_facts) for r in results),
        search_p50_ms=statistics.median(times) if times else 0.0,
        search_p95_ms=times[max(0, round(0.95 * len(times)) - 1)] if times else 0.0,
        results=results,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(to_markdown(summary), encoding="utf-8")
    return summary


def to_markdown(s: OfflineSummary) -> str:
    def pct(a: int, b: int) -> str:
        return f"{a} of {b} ({100 * a / b:.0f}%)" if b else "n/a"

    lines = [
        "# Offline evaluation: privacy and retrieval",
        "",
        "Generated by `inboxdraft evals --offline`: production code, no model, no API key. "
        f"{s.n} enquiries from `evals/cases.jsonl`, index `{s.embedder}`, top {s.k} chunks.",
        "",
        "| Check | Result |",
        "|---|---|",
        f"| Personal details in the enquiries (names, emails, phone numbers) | {s.pii_items} |",
        f"| Of those, left in the text a model would see | **{s.leaked}** |",
        f"| Enquiries the documents answer, with required facts | {s.eligible} |",
        f"| Every required fact in the top {s.k} chunks | {pct(s.retrieved_all, s.eligible)} |",
        f"| Required facts retrieved | {pct(s.facts_found, s.facts)} |",
        f"| Search time per enquiry, p50 / p95 | {s.search_p50_ms:.0f} ms / {s.search_p95_ms:.0f} ms |",
        "",
        "Enquiries that need a person first, or that the documents do not cover, are not scored for "
        "retrieval: the right answer for them is an escalation, which the model-graded run checks.",
        "",
        "## Per enquiry",
        "",
        "| Id | Category | Details / leaked | Required facts | Missing from top chunks | Top sources |",
        "|---|---|---|---|---|---|",
    ]
    for r in s.results:
        facts = ", ".join(r.facts) if r.eligible else "not scored"
        missing = ", ".join(r.missing_facts) if r.missing_facts else ("-" if r.eligible else "")
        leaked = ", ".join(r.leaked) if r.leaked else "0"
        lines.append(
            f"| {r.id} | {r.category} | {r.pii_items} / {leaked} | {facts} | {missing} | "
            f"{'; '.join(r.top_sources)} |"
        )
    return "\n".join(lines) + "\n"
