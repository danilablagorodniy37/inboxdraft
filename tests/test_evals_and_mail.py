from pathlib import Path

from sqlalchemy.orm import Session

from inboxdraft.cost import cost_usd, format_usd
from inboxdraft.evals import load_cases, run_evals
from inboxdraft.llm import Usage
from inboxdraft.mail.reader import html_to_text, parse_eml_bytes
from inboxdraft.pipeline import Deps

ROOT = Path(__file__).resolve().parents[1]


def test_cases_file_is_well_formed() -> None:
    cases = load_cases(ROOT / "evals" / "cases.jsonl")
    assert len(cases) == 50
    assert len({c.id for c in cases}) == 50
    assert sum(c.demo for c in cases) == 8
    assert sum(c.expect_escalation for c in cases) == 9
    assert {c.category for c in cases} == {
        "pricing",
        "appointment",
        "documents",
        "services",
        "deadlines",
        "complaint",
        "other",
    }


def test_evals_harness_runs_and_writes_a_report(
    indexed: Session, deps: Deps, tmp_path: Path
) -> None:
    cases = load_cases(ROOT / "evals" / "cases.jsonl")[:12]
    summary = run_evals(
        indexed,
        deps,
        cases,
        report_path=tmp_path / "report.md",
        results_path=tmp_path / "results.jsonl",
        labels_path=ROOT / "evals" / "labels.csv",
    )
    assert summary.n == 12
    assert summary.figures_in_ready_drafts == 0
    assert summary.ready_drafts + summary.missed_escalations + summary.unnecessary_escalations <= 12
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| Category classified correctly |" in report
    assert "Emails sent during this run: 0" in report
    assert "| c01 |" in report
    assert len((tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 12
    assert "evals:" in summary.as_text()


def test_cost_from_configured_prices() -> None:
    prices = {"claude-haiku-5-5": (0.10, 0.50)}
    assert cost_usd(Usage("claude-haiku-5-5", 1_000_000, 0), prices) == 0.10
    assert cost_usd(Usage("claude-haiku-5-5", 0, 1_000_000), prices) == 0.50
    assert cost_usd(Usage("unknown-model", 10, 10), prices) is None
    assert (
        format_usd(None) == "n/a" and format_usd(0.0042) == "$0.0042" and format_usd(1.5) == "$1.50"
    )


def test_parse_html_only_email_and_threading_headers() -> None:
    raw = (
        b"From: Grace Li <grace.li@example.com>\r\n"
        b"To: hello@wrenfield.example\r\n"
        b"Subject: Re: Portal\r\n"
        b"Date: Fri, 16 Oct 2026 09:15:00 +0100\r\n"
        b"Message-ID: <abc@example.com>\r\n"
        b"In-Reply-To: <prev@wrenfield.example>\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><body><p>Hi,</p><p>Is the portal <b>secure</b>?</p><style>p{}</style></body></html>"
    )
    parsed = parse_eml_bytes(raw)
    assert parsed.from_name == "Grace Li" and parsed.from_email == "grace.li@example.com"
    assert parsed.body == "Hi,\n\nIs the portal secure?"
    assert parsed.in_reply_to == "<prev@wrenfield.example>"
    assert parsed.date is not None and parsed.date.hour == 8  # converted to UTC
    assert html_to_text("<div>a</div><div>b</div>") == "a\n\nb"


def test_demo_inbox_matches_the_demo_cases() -> None:
    cases = {c.id: c for c in load_cases(ROOT / "evals" / "cases.jsonl") if c.demo}
    files = sorted((ROOT / "inbox").glob("*.eml"))
    assert len(files) == len(cases) == 8
    for path in files:
        parsed = parse_eml_bytes(path.read_bytes())
        case = cases[path.stem.split("-", 1)[1]]
        assert parsed.subject == case.subject and parsed.from_email == case.from_email
        assert parsed.body.strip() == case.body.strip()
