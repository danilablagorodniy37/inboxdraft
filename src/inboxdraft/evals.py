"""Evaluation harness: run the synthetic enquiries through the pipeline and score the results.

Measured, per run:
- classification accuracy (category) and escalation decisions (should a person look first)
- missed escalations: enquiries that needed a person but got a ready draft (the dangerous case)
- facts check: a ready draft contains the facts a correct reply must contain, and none it must not
- LLM judge (fast model): would the practice manager send the draft as is
- unsupported figures caught by the guard, and how many reached a ready draft (must be 0)
- time per draft (p50 / p95) and cost per enquiry at the configured prices

Everything is written to evals/report.md and evals/results.jsonl. Numbers from the fake model are
only a smoke test of the harness; the README reports a run with a real model.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from inboxdraft.config import PRICES_NOTE
from inboxdraft.cost import cost_usd, format_usd
from inboxdraft.draft import strip_citations
from inboxdraft.llm import Usage
from inboxdraft.models import STATUS_DRAFTED, STATUS_ESCALATED, Enquiry
from inboxdraft.pipeline import Deps, process_enquiry

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["send_as_is", "minor_edit", "rewrite"]},
        "invented_facts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Fees, dates, policies or claims in the draft that no source supports.",
        },
        "reason": {"type": "string"},
    },
    "required": ["verdict", "invented_facts", "reason"],
}

JUDGE_SYSTEM = """You are the practice manager at {firm}. A colleague drafted a reply to a client enquiry using only the SOURCES. Decide whether you would send it.

- send_as_is: correct facts, answers the question, tone fine; you would press Send without edits.
- minor_edit: facts correct but wording, length or tone needs a small change.
- rewrite: a fact is wrong or unsupported, the question is not answered, or the reply promises something the sources do not.

The REFERENCE is one acceptable answer, not the only one. FACTS lists what a correct reply must contain. List every claim in the draft that the sources do not support as invented_facts."""


@dataclass(frozen=True)
class EvalCase:
    id: str
    from_name: str
    from_email: str
    subject: str
    body: str
    category: str
    needs_human: bool
    in_kb: bool
    must_include: list[str]
    must_not_include: list[str]
    reference: str
    demo: bool = False

    @property
    def expect_escalation(self) -> bool:
        return self.needs_human or not self.in_kb


@dataclass
class CaseResult:
    id: str
    category_expected: str
    category: str | None
    category_ok: bool
    expect_escalation: bool
    escalated: bool
    escalation_ok: bool
    missed_escalation: bool
    facts_ok: bool | None
    missing_facts: list[str]
    forbidden_present: list[str]
    judge_verdict: str | None
    invented_facts: list[str]
    unsupported_figures: list[str]
    confidence: float | None
    draft_ms: int
    cost_usd: float | None
    judge_cost_usd: float | None
    draft: str


@dataclass
class EvalSummary:
    run_at: str
    provider: str
    draft_model: str
    fast_model: str
    embedder: str
    n: int
    category_accuracy: float
    escalation_accuracy: float
    missed_escalations: int
    unnecessary_escalations: int
    ready_drafts: int
    facts_ok_rate: float | None
    send_as_is_rate: float | None
    minor_edit_rate: float | None
    rewrite_rate: float | None
    invented_fact_drafts: int
    figures_caught: int
    figures_in_ready_drafts: int
    p50_ms: int
    p95_ms: int
    mean_cost_usd: float | None
    total_cost_usd: float | None
    judge_cost_usd: float | None
    human_labels: int
    human_judge_agreement: float | None
    results: list[CaseResult] = field(default_factory=list)

    def as_text(self) -> str:
        def pct(value: float | None) -> str:
            return f"{value * 100:.0f}%" if value is not None else "n/a"

        lines = [
            f"evals: {self.n} enquiries, {self.provider} ({self.draft_model} / {self.fast_model}), index {self.embedder}",
            f"  category accuracy        {pct(self.category_accuracy)}",
            f"  escalation decisions     {pct(self.escalation_accuracy)} correct, "
            f"{self.missed_escalations} missed, {self.unnecessary_escalations} unnecessary",
            f"  ready drafts             {self.ready_drafts}",
            f"  facts complete (rules)   {pct(self.facts_ok_rate)}",
            f"  judge: send as is        {pct(self.send_as_is_rate)}  minor edit {pct(self.minor_edit_rate)}  rewrite {pct(self.rewrite_rate)}",
            f"  drafts with invented facts (judge)  {self.invented_fact_drafts}",
            f"  unsupported figures      {self.figures_caught} caught, {self.figures_in_ready_drafts} in ready drafts",
            f"  time per enquiry         p50 {self.p50_ms} ms, p95 {self.p95_ms} ms",
            f"  cost per enquiry         {format_usd(self.mean_cost_usd)} (total {format_usd(self.total_cost_usd)}, judge {format_usd(self.judge_cost_usd)})",
        ]
        if self.human_labels:
            lines.append(
                f"  human labels             {self.human_labels}, agreement with judge {pct(self.human_judge_agreement)}"
            )
        return "\n".join(lines)


def load_cases(path: Path) -> list[EvalCase]:
    cases = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw = json.loads(line)
            cases.append(
                EvalCase(
                    id=str(raw["id"]),
                    from_name=raw.get("from_name", ""),
                    from_email=raw.get("from_email", ""),
                    subject=raw.get("subject", ""),
                    body=raw["body"],
                    category=raw["category"],
                    needs_human=bool(raw.get("needs_human", False)),
                    in_kb=bool(raw.get("in_kb", True)),
                    must_include=list(raw.get("must_include", [])),
                    must_not_include=list(raw.get("must_not_include", [])),
                    reference=raw.get("reference", ""),
                    demo=bool(raw.get("demo", False)),
                )
            )
    return cases


def load_labels(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    labels: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            verdict = (row.get("human_verdict") or "").strip()
            if verdict:
                labels[row["id"].strip()] = verdict
    return labels


def judge(
    deps: Deps, case: EvalCase, enquiry: Enquiry, draft_text: str
) -> tuple[dict[str, Any], Usage]:
    settings = deps.settings
    sources = (
        "\n\n".join(f"[{s.rank}] {s.chunk.label}\n{s.chunk.text.strip()}" for s in enquiry.sources)
        or "(none)"
    )
    facts = "\n".join(f"- {fact}" for fact in case.must_include) or "- (none listed)"
    user = (
        f"ENQUIRY\nSubject: {case.subject}\n\n{case.body}\n\nSOURCES\n{sources}\n\n"
        f"DRAFT\n{draft_text}\n\nREFERENCE\n{case.reference}\n\nFACTS\n{facts}"
    )
    return deps.llm.structured(
        model=settings.fast_model,
        system=JUDGE_SYSTEM.format(firm=settings.firm_name),
        user=user,
        tool_name="judge_draft",
        description="Record the verdict on the draft.",
        schema=JUDGE_SCHEMA,
        max_tokens=400,
    )


def run_evals(
    session: Session,
    deps: Deps,
    cases: list[EvalCase],
    *,
    judge: bool = True,
    report_path: Path | None = None,
    results_path: Path | None = None,
    labels_path: Path | None = None,
) -> EvalSummary:
    settings = deps.settings
    use_judge = judge
    session.execute(delete(Enquiry).where(Enquiry.source == "eval"))
    session.flush()

    results: list[CaseResult] = []
    judge_costs: list[float | None] = []
    for case in cases:
        enquiry = Enquiry(
            source="eval",
            eval_case_id=case.id,
            message_id=f"<eval-{case.id}@inboxdraft.local>",
            from_name=case.from_name,
            from_email=case.from_email,
            to_email=settings.reply_from,
            subject=case.subject,
            body=case.body,
        )
        session.add(enquiry)
        session.flush()
        process_enquiry(session, enquiry, deps)
        session.commit()

        escalated = enquiry.status == STATUS_ESCALATED
        ready = enquiry.status == STATUS_DRAFTED
        draft_text = strip_citations(enquiry.draft_text or "")
        facts_ok: bool | None = None
        missing: list[str] = []
        forbidden: list[str] = []
        verdict: str | None = None
        invented: list[str] = []
        judge_cost: float | None = None
        if ready:
            lowered = draft_text.lower()
            missing = [f for f in case.must_include if f.lower() not in lowered]
            forbidden = [f for f in case.must_not_include if f.lower() in lowered]
            facts_ok = not missing and not forbidden
            if use_judge:
                verdict_raw, usage = judge_fn(deps, case, enquiry, draft_text)
                verdict = str(verdict_raw.get("verdict", "rewrite"))
                invented = [str(x) for x in verdict_raw.get("invented_facts", [])]
                judge_cost = cost_usd(usage, settings.model_prices)
        judge_costs.append(judge_cost)
        results.append(
            CaseResult(
                id=case.id,
                category_expected=case.category,
                category=enquiry.category,
                category_ok=enquiry.category == case.category,
                expect_escalation=case.expect_escalation,
                escalated=escalated,
                escalation_ok=escalated == case.expect_escalation,
                missed_escalation=case.expect_escalation and not escalated,
                facts_ok=facts_ok,
                missing_facts=missing,
                forbidden_present=forbidden,
                judge_verdict=verdict,
                invented_facts=invented,
                unsupported_figures=list(enquiry.unsupported_figures or []),
                confidence=enquiry.confidence,
                draft_ms=enquiry.draft_ms or 0,
                cost_usd=enquiry.cost_usd,
                judge_cost_usd=judge_cost,
                draft=draft_text,
            )
        )

    labels = load_labels(labels_path) if labels_path else {}
    summary = _summarise(deps, results, labels, judge_costs)
    if results_path:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        with results_path.open("w", encoding="utf-8") as handle:
            for result in results:
                handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report(summary), encoding="utf-8")
    return summary


judge_fn = judge  # indirection so tests can monkeypatch the judge without touching the model


def _summarise(
    deps: Deps, results: list[CaseResult], labels: dict[str, str], judge_costs: list[float | None]
) -> EvalSummary:
    n = len(results)
    ready = [r for r in results if not r.escalated]
    judged = [r for r in ready if r.judge_verdict]
    times = sorted(r.draft_ms for r in results) or [0]
    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    known_judge = [c for c in judge_costs if c is not None]
    labelled = [(r, labels[r.id]) for r in judged if r.id in labels]

    def rate(count: int, total: int) -> float | None:
        return count / total if total else None

    def p(values: list[int], q: float) -> int:
        if not values:
            return 0
        index = min(len(values) - 1, max(0, round(q * (len(values) - 1))))
        return int(values[index])

    settings = deps.settings
    return EvalSummary(
        run_at=datetime.now(UTC).strftime("%d %b %Y %H:%M UTC"),
        provider=deps.llm.name,
        draft_model=settings.draft_model if deps.llm.name != "fake" else "fake",
        fast_model=settings.fast_model if deps.llm.name != "fake" else "fake",
        embedder=deps.embedder.name,
        n=n,
        category_accuracy=rate(sum(r.category_ok for r in results), n) or 0.0,
        escalation_accuracy=rate(sum(r.escalation_ok for r in results), n) or 0.0,
        missed_escalations=sum(r.missed_escalation for r in results),
        unnecessary_escalations=sum(1 for r in results if r.escalated and not r.expect_escalation),
        ready_drafts=len(ready),
        facts_ok_rate=rate(sum(1 for r in ready if r.facts_ok), len(ready)),
        send_as_is_rate=rate(
            sum(1 for r in judged if r.judge_verdict == "send_as_is"), len(judged)
        ),
        minor_edit_rate=rate(
            sum(1 for r in judged if r.judge_verdict == "minor_edit"), len(judged)
        ),
        rewrite_rate=rate(sum(1 for r in judged if r.judge_verdict == "rewrite"), len(judged)),
        invented_fact_drafts=sum(1 for r in judged if r.invented_facts),
        figures_caught=sum(1 for r in results if r.unsupported_figures),
        figures_in_ready_drafts=sum(1 for r in ready if r.unsupported_figures),
        p50_ms=p(times, 0.5),
        p95_ms=p(times, 0.95),
        mean_cost_usd=statistics.mean(costs) if costs else None,
        total_cost_usd=sum(costs) if costs else None,
        judge_cost_usd=sum(known_judge) if known_judge else None,
        human_labels=len(labelled),
        human_judge_agreement=rate(
            sum(1 for r, label in labelled if r.judge_verdict == label), len(labelled)
        ),
        results=results,
    )


def render_report(summary: EvalSummary) -> str:
    def pct(value: float | None) -> str:
        return f"{value * 100:.0f}%" if value is not None else "n/a"

    lines = [
        "# Evaluation report",
        "",
        f"Run: {summary.run_at}. Model: {summary.provider} ({summary.draft_model} drafts, "
        f"{summary.fast_model} classification and judge). Index: {summary.embedder}. "
        f"Enquiries: {summary.n}, all synthetic.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Category classified correctly | {pct(summary.category_accuracy)} |",
        f"| Escalation decision correct | {pct(summary.escalation_accuracy)} "
        f"({summary.missed_escalations} missed, {summary.unnecessary_escalations} unnecessary) |",
        f"| Ready drafts (no escalation) | {summary.ready_drafts} of {summary.n} |",
        f"| Ready drafts with all required facts (rules) | {pct(summary.facts_ok_rate)} |",
        f"| Judge: send as is / minor edit / rewrite | {pct(summary.send_as_is_rate)} / "
        f"{pct(summary.minor_edit_rate)} / {pct(summary.rewrite_rate)} |",
        f"| Ready drafts with invented facts (judge) | {summary.invented_fact_drafts} |",
        f"| Unsupported figures caught by the guard | {summary.figures_caught} |",
        f"| Unsupported figures in ready drafts | {summary.figures_in_ready_drafts} |",
        f"| Time per enquiry, p50 / p95 | {summary.p50_ms} ms / {summary.p95_ms} ms |",
        f"| Cost per enquiry (classify + draft) | {format_usd(summary.mean_cost_usd)} |",
        f"| Total cost, drafts / judge | {format_usd(summary.total_cost_usd)} / {format_usd(summary.judge_cost_usd)} |",
    ]
    if summary.human_labels:
        lines.append(
            f"| Human labels / agreement with judge | {summary.human_labels} / {pct(summary.human_judge_agreement)} |"
        )
    lines += [
        "",
        f"Prices: {PRICES_NOTE}",
        "",
        "Emails sent during this run: 0. The pipeline has no code path to a mail server; see "
        "tests/test_no_send_without_human.py.",
        "",
        "## Per enquiry",
        "",
        "| id | expected | got | escalation | facts | judge | figures flagged | ms | cost |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in summary.results:
        escalation = (
            "expected, done"
            if r.expect_escalation and r.escalated
            else "MISSED"
            if r.missed_escalation
            else "unnecessary"
            if r.escalated
            else "none"
        )
        facts = (
            "n/a"
            if r.facts_ok is None
            else (
                "ok"
                if r.facts_ok
                else "missing " + "; ".join(r.missing_facts + r.forbidden_present)
            )
        )
        lines.append(
            f"| {r.id} | {r.category_expected} | {r.category or ''}{'' if r.category_ok else ' (x)'} | {escalation} | "
            f"{facts} | {r.judge_verdict or ''} | {', '.join(r.unsupported_figures)} | {r.draft_ms} | "
            f"{format_usd(r.cost_usd)} |"
        )
    return "\n".join(lines) + "\n"
