"""Review UI. A person reads the enquiry, the draft and the sources, then edits, sends or escalates.

The approve route is the only place in the code base that calls `send_reply`.
"""

import re
import secrets
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from inboxdraft import __version__
from inboxdraft.config import Settings, get_settings
from inboxdraft.cost import format_usd
from inboxdraft.db import init_db, make_engine, make_session_factory, utcnow
from inboxdraft.draft import strip_citations
from inboxdraft.mail.importer import import_emails
from inboxdraft.mail.reader import fetch_imap, read_inbox_dir
from inboxdraft.mail.sender import SendError, send_reply
from inboxdraft.models import (
    STATUS_DRAFTED,
    STATUS_ESCALATED,
    STATUS_NEW,
    STATUS_SENT,
    STATUSES,
    Enquiry,
    EnquirySource,
)
from inboxdraft.pipeline import Deps, build_deps, process_enquiry, process_new

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
_TOKEN_RE = re.compile(r"\[[A-Z]+_\d+\]")


def _highlight_tokens(text: str | None) -> Markup:
    safe = str(escape(text or ""))
    return Markup(_TOKEN_RE.sub(lambda m: f'<mark class="tok">{m.group(0)}</mark>', safe))


def _fmt_dt(value: datetime | None) -> str:
    return value.strftime("%d %b %Y %H:%M") if value else ""


def _pct(value: float | None) -> str:
    return f"{round(value * 100)}%" if value is not None else "n/a"


templates.env.filters["tokens"] = _highlight_tokens
templates.env.filters["dt"] = _fmt_dt
templates.env.filters["usd"] = format_usd
templates.env.filters["pct"] = _pct


def create_app(
    settings: Settings | None = None,
    deps: Deps | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.deps = deps or build_deps(settings)
        if session_factory is None:
            engine = make_engine(settings.database_url)
            init_db(engine, app.state.deps.embedder.dim)
            app.state.session_factory = make_session_factory(engine)
        else:
            app.state.session_factory = session_factory
        yield

    app = FastAPI(
        title="inboxdraft", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    basic = HTTPBasic(auto_error=False)

    def require_auth(
        request: Request, credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)]
    ) -> None:
        password = request.app.state.settings.app_password
        if not password:
            return
        ok = (
            credentials is not None
            and secrets.compare_digest(credentials.username.encode(), b"staff")
            and secrets.compare_digest(credentials.password.encode(), password.encode())
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sign in",
                headers={"WWW-Authenticate": "Basic realm=inboxdraft"},
            )

    def get_session(request: Request) -> Iterator[Session]:
        session = request.app.state.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    SessionDep = Annotated[Session, Depends(get_session)]
    Auth = Depends(require_auth)

    def context(request: Request, session: Session, **extra: object) -> dict:
        counts = dict.fromkeys(STATUSES, 0)
        rows = session.execute(
            select(Enquiry.status, func.count())
            .where(Enquiry.source == "inbox")
            .group_by(Enquiry.status)
        ).all()
        for row_status, count in rows:
            counts[row_status] = count
        s: Settings = request.app.state.settings
        d: Deps = request.app.state.deps
        return {
            "request": request,
            "settings": s,
            "counts": counts,
            "provider": d.llm.name,
            "draft_model": s.draft_model if d.llm.name != "fake" else "fake model (no API key)",
            "embedder": d.embedder.name,
            "version": __version__,
            **extra,
        }

    def load_enquiry(session: Session, enquiry_id: int) -> Enquiry:
        enquiry = session.execute(
            select(Enquiry)
            .options(selectinload(Enquiry.sources).selectinload(EnquirySource.chunk))
            .where(Enquiry.id == enquiry_id)
        ).scalar_one_or_none()
        if enquiry is None:
            raise HTTPException(status_code=404, detail="No such enquiry")
        return enquiry

    def draft_panel(
        request: Request, session: Session, enquiry: Enquiry, **extra: object
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "_draft.html", context(request, session, enquiry=enquiry, **extra)
        )

    # -- pages ---------------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse, dependencies=[Auth])
    def index(request: Request, session: SessionDep, status: str | None = None) -> HTMLResponse:
        query = select(Enquiry).where(Enquiry.source == "inbox")
        if status in STATUSES:
            query = query.where(Enquiry.status == status)
        query = query.order_by(Enquiry.received_at.desc().nulls_last(), Enquiry.id.desc())
        enquiries = list(session.execute(query).scalars())
        return templates.TemplateResponse(
            request,
            "index.html",
            context(request, session, enquiries=enquiries, filter_status=status),
        )

    @app.get("/enquiries/{enquiry_id}", response_class=HTMLResponse, dependencies=[Auth])
    def enquiry_page(request: Request, session: SessionDep, enquiry_id: int) -> HTMLResponse:
        enquiry = load_enquiry(session, enquiry_id)
        return templates.TemplateResponse(
            request, "enquiry.html", context(request, session, enquiry=enquiry)
        )

    # -- actions -------------------------------------------------------------------------
    @app.post("/import", dependencies=[Auth])
    def import_inbox(request: Request, session: SessionDep) -> Response:
        s: Settings = request.app.state.settings
        emails = read_inbox_dir(Path(s.inbox_dir)) if Path(s.inbox_dir).is_dir() else []
        if s.imap_host:
            emails += fetch_imap(s)
        import_emails(session, emails)
        return RedirectResponse("/", status_code=303)

    @app.post("/process", dependencies=[Auth])
    def process_all(request: Request, session: SessionDep) -> Response:
        process_new(session, request.app.state.deps)
        return RedirectResponse("/", status_code=303)

    @app.post("/enquiries/{enquiry_id}/regenerate", dependencies=[Auth])
    def regenerate(request: Request, session: SessionDep, enquiry_id: int) -> Response:
        enquiry = load_enquiry(session, enquiry_id)
        if enquiry.status == STATUS_SENT:
            raise HTTPException(status_code=409, detail="Already sent")
        process_enquiry(session, enquiry, request.app.state.deps)
        session.commit()
        return Response(status_code=204, headers={"HX-Redirect": f"/enquiries/{enquiry_id}"})

    @app.post("/enquiries/{enquiry_id}/save", response_class=HTMLResponse, dependencies=[Auth])
    def save_draft(
        request: Request, session: SessionDep, enquiry_id: int, draft_text: Annotated[str, Form()]
    ) -> HTMLResponse:
        enquiry = load_enquiry(session, enquiry_id)
        if enquiry.status == STATUS_SENT:
            raise HTTPException(status_code=409, detail="Already sent")
        if draft_text != enquiry.draft_text:
            enquiry.draft_text = draft_text
            enquiry.edited = True
        session.flush()
        return draft_panel(request, session, enquiry, flash="Draft saved.")

    @app.post("/enquiries/{enquiry_id}/escalate", response_class=HTMLResponse, dependencies=[Auth])
    def escalate(
        request: Request,
        session: SessionDep,
        enquiry_id: int,
        draft_text: Annotated[str, Form()] = "",
        note: Annotated[str, Form()] = "",
    ) -> HTMLResponse:
        enquiry = load_enquiry(session, enquiry_id)
        if enquiry.status == STATUS_SENT:
            raise HTTPException(status_code=409, detail="Already sent")
        if draft_text and draft_text != enquiry.draft_text:
            enquiry.draft_text = draft_text
            enquiry.edited = True
        reason = (
            f"Escalated by a reviewer: {note.strip()}"
            if note.strip()
            else "Escalated by a reviewer"
        )
        enquiry.escalation_reasons = [*enquiry.escalation_reasons, reason]
        enquiry.status = STATUS_ESCALATED
        session.flush()
        return draft_panel(request, session, enquiry, flash="Passed to a colleague.")

    @app.post("/enquiries/{enquiry_id}/approve", response_class=HTMLResponse, dependencies=[Auth])
    def approve_and_send(
        request: Request, session: SessionDep, enquiry_id: int, draft_text: Annotated[str, Form()]
    ) -> HTMLResponse:
        """The one code path that sends email. It runs only when a person submits this form."""
        enquiry = load_enquiry(session, enquiry_id)
        if enquiry.status == STATUS_SENT:
            raise HTTPException(status_code=409, detail="Already sent")
        if enquiry.status == STATUS_NEW or not draft_text.strip():
            raise HTTPException(status_code=400, detail="Nothing to send yet")
        if draft_text != enquiry.draft_text:
            enquiry.draft_text = draft_text
            enquiry.edited = True
        final_text = strip_citations(draft_text)
        try:
            result = send_reply(request.app.state.settings, enquiry, final_text)
        except SendError as exc:
            session.flush()
            return draft_panel(request, session, enquiry, error=str(exc))
        enquiry.final_text = final_text
        enquiry.status = STATUS_SENT
        enquiry.sent_at = utcnow()
        enquiry.sent_to = enquiry.from_email
        enquiry.sent_via = result.via
        enquiry.sent_message_id = result.message_id
        session.flush()
        where = f" (written to {result.path})" if result.path else ""
        return draft_panel(request, session, enquiry, flash=f"Sent via {result.via}{where}.")

    @app.get("/health")
    def health(request: Request) -> dict:
        d: Deps = request.app.state.deps
        return {
            "status": "ok",
            "provider": d.llm.name,
            "embedder": d.embedder.name,
            "version": __version__,
        }

    @app.get("/_statuses")
    def statuses() -> dict:
        return {"statuses": [STATUS_NEW, STATUS_DRAFTED, STATUS_ESCALATED, STATUS_SENT]}

    return app


app = create_app()
