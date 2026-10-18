from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inboxdraft.config import Settings
from inboxdraft.mail.importer import import_emails
from inboxdraft.mail.reader import read_inbox_dir
from inboxdraft.models import STATUS_DRAFTED, STATUS_ESCALATED, Enquiry
from inboxdraft.pipeline import Deps, process_new
from inboxdraft.web.app import create_app

INBOX = Path(__file__).resolve().parents[1] / "inbox"


def _client(deps: Deps, session_factory) -> TestClient:
    return TestClient(
        create_app(settings=deps.settings, deps=deps, session_factory=session_factory)
    )


def test_empty_inbox_page_and_health(deps: Deps, session_factory) -> None:
    with _client(deps, session_factory) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "No enquiries yet" in page.text
        assert "DEMO DATA" in page.text
        assert client.get("/health").json()["provider"] == "fake"


def test_import_process_and_review_flow(indexed: Session, deps: Deps, session_factory) -> None:
    with _client(deps, session_factory) as client:
        assert client.post("/import", follow_redirects=False).status_code == 303
        assert client.post("/process", follow_redirects=False).status_code == 303
        page = client.get("/")
        assert page.text.count("<tr onclick") == 8
        drafted = client.get("/?status=drafted")
        assert "drafted" in drafted.text

    indexed.expire_all()
    ready = (
        indexed.execute(
            select(Enquiry).where(Enquiry.status == STATUS_DRAFTED).order_by(Enquiry.id)
        )
        .scalars()
        .first()
    )
    assert ready is not None
    with _client(deps, session_factory) as client:
        page = client.get(f"/enquiries/{ready.id}")
        assert page.status_code == 200
        assert "What the model saw" in page.text
        assert '<mark class="tok">[NAME_1]</mark>' in page.text
        model_view = page.text.split("What the model saw")[1].split("</details>")[0]
        assert ready.from_email not in model_view and ready.from_name not in model_view
        assert "Sources" in page.text and "[1]" in page.text

        saved = client.post(f"/enquiries/{ready.id}/save", data={"draft_text": "Changed."})
        assert "Draft saved." in saved.text and "edited" in saved.text

        escalated = client.post(
            f"/enquiries/{ready.id}/escalate",
            data={"draft_text": "Changed.", "note": "needs Helen"},
        )
        assert "Passed to a colleague." in escalated.text
        assert "needs Helen" in escalated.text
    with session_factory() as check:
        assert check.get(Enquiry, ready.id).status == STATUS_ESCALATED


def test_approve_writes_to_outbox_when_smtp_is_not_configured(
    indexed: Session, deps: Deps, session_factory
) -> None:
    import_emails(indexed, read_inbox_dir(INBOX))
    indexed.commit()
    ready = next(e for e in process_new(indexed, deps) if e.status == STATUS_DRAFTED)
    with _client(deps, session_factory) as client:
        response = client.post(
            f"/enquiries/{ready.id}/approve", data={"draft_text": ready.draft_text}
        )
        assert response.status_code == 200
        assert "Sent via outbox" in response.text
    outbox = Path(deps.settings.outbox_dir)
    files = list(outbox.glob("*.eml"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    assert f"To: {ready.from_name}" in raw or ready.from_email in raw
    assert "In-Reply-To: " + ready.message_id in raw
    assert "Subject: Re: " in raw


def test_basic_auth_when_password_is_set(settings: Settings, deps: Deps, session_factory) -> None:
    locked = settings.model_copy(update={"app_password": "secret-for-tests"})
    deps.settings = locked
    with _client(deps, session_factory) as client:
        assert client.get("/").status_code == 401
        assert client.get("/", auth=("staff", "wrong")).status_code == 401
        assert client.get("/", auth=("staff", "secret-for-tests")).status_code == 200
        assert client.get("/health").status_code == 200
