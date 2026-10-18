"""The guarantee the README makes: no code path sends an email without a person pressing Send.

Two checks. Static: `smtplib` is imported in exactly one module and `send_reply` is called from
exactly one function, the approve route. Dynamic: running the whole pipeline over the demo inbox
and the evals never calls the sender; pressing the button calls it once.
"""

from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inboxdraft.mail import sender
from inboxdraft.mail.importer import import_emails
from inboxdraft.mail.reader import read_inbox_dir
from inboxdraft.models import STATUS_DRAFTED, Enquiry
from inboxdraft.pipeline import Deps, process_new
from inboxdraft.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "inboxdraft"
INBOX = ROOT / "inbox"


class _CallFinder(ast.NodeVisitor):
    """Collect the enclosing function of every call to `name`."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.stack: list[str] = []
        self.found: set[str] = set()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if called == self.name:
            self.found.add(self.stack[-1] if self.stack else "<module>")
        self.generic_visit(node)


def _calls_of(name: str) -> dict[str, set[str]]:
    """{module: {enclosing function names}} for every call to `name` in the package."""
    found: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        finder = _CallFinder(name)
        finder.visit(ast.parse(path.read_text(encoding="utf-8")))
        if finder.found:
            found[path.relative_to(SRC).as_posix()] = finder.found
    return found


def test_smtplib_is_imported_only_by_the_sender_module() -> None:
    importers = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(n == "smtplib" or n.startswith("smtplib.") for n in names):
                importers.append(path.relative_to(SRC).as_posix())
    assert importers == ["mail/sender.py"]


def test_send_reply_is_called_only_from_the_approve_route() -> None:
    assert _calls_of("send_reply") == {"web/app.py": {"approve_and_send"}}


def test_pipeline_and_evals_never_send(indexed: Session, deps: Deps, monkeypatch, tmp_path) -> None:
    sent: list[object] = []
    monkeypatch.setattr(sender, "send_reply", lambda *a, **k: sent.append(a))
    import_emails(indexed, read_inbox_dir(INBOX))
    indexed.commit()
    process_new(indexed, deps)

    from inboxdraft.evals import load_cases, run_evals

    cases = load_cases(ROOT / "evals" / "cases.jsonl")[:6]
    run_evals(
        indexed, deps, cases, report_path=tmp_path / "r.md", results_path=tmp_path / "r.jsonl"
    )
    assert sent == []
    assert not Path(deps.settings.outbox_dir).exists()


def test_approve_button_sends_exactly_once(
    indexed: Session, deps: Deps, session_factory, monkeypatch
) -> None:
    import inboxdraft.web.app as web

    calls: list[tuple] = []

    def spy(settings, enquiry, body):
        calls.append((enquiry.id, body))
        return sender.SendResult(via="spy", message_id="<spy@test>")

    monkeypatch.setattr(web, "send_reply", spy)
    import_emails(indexed, read_inbox_dir(INBOX))
    indexed.commit()
    done = process_new(indexed, deps)
    ready = next(e for e in done if e.status == STATUS_DRAFTED)

    app = create_app(settings=deps.settings, deps=deps, session_factory=session_factory)
    with TestClient(app) as client:
        page = client.get(f"/enquiries/{ready.id}")
        assert page.status_code == 200 and calls == []
        client.post(f"/enquiries/{ready.id}/save", data={"draft_text": "edited but not sent"})
        assert calls == []
        response = client.post(
            f"/enquiries/{ready.id}/approve", data={"draft_text": "Final text [1]"}
        )
        assert response.status_code == 200
        assert calls == [(ready.id, "Final text")]
        again = client.post(f"/enquiries/{ready.id}/approve", data={"draft_text": "Final text"})
        assert again.status_code == 409 and len(calls) == 1

    with session_factory() as check:
        stored = check.get(Enquiry, ready.id)
        assert stored.status == "sent"
        assert stored.final_text == "Final text"
        assert stored.sent_via == "spy"
