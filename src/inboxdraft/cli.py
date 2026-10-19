"""Command line: ingest, import, process, serve, evals, demo, reset, status."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import func, select

from inboxdraft import __version__
from inboxdraft.config import Settings, get_settings
from inboxdraft.db import drop_all, drop_index_tables, init_db, make_engine, make_session_factory
from inboxdraft.ingest import ingest_directory
from inboxdraft.mail.importer import import_emails
from inboxdraft.mail.reader import fetch_imap, read_inbox_dir
from inboxdraft.models import Enquiry
from inboxdraft.pipeline import Deps, build_deps, process_new


def _deps(settings: Settings) -> Deps:
    deps = build_deps(settings)
    print(
        f"model: {deps.llm.name} ({settings.draft_model} / {settings.fast_model}); index: {deps.embedder.name}"
    )
    return deps


def _factory(settings: Settings, deps: Deps):
    engine = make_engine(settings.database_url)
    init_db(engine, deps.embedder.dim)
    return engine, make_session_factory(engine)


def cmd_init_db(args: argparse.Namespace, settings: Settings) -> None:
    deps = _deps(settings)
    _factory(settings, deps)
    print("database ready")


def cmd_ingest(args: argparse.Namespace, settings: Settings) -> None:
    deps = _deps(settings)
    _, factory = _factory(settings, deps)
    directory = Path(args.path or settings.knowledge_dir)
    if not directory.is_dir():
        sys.exit(f"knowledge folder not found: {directory}")
    with factory() as session:
        report = ingest_directory(session, deps.embedder, directory)
        session.commit()
    print(report.summary())


def cmd_import(args: argparse.Namespace, settings: Settings) -> None:
    deps = _deps(settings)
    _, factory = _factory(settings, deps)
    emails = []
    inbox = Path(args.path or settings.inbox_dir)
    if inbox.is_dir():
        emails += read_inbox_dir(inbox)
    if args.imap:
        emails += fetch_imap(settings)
    with factory() as session:
        report = import_emails(session, emails)
        session.commit()
    print(report.summary())


def cmd_process(args: argparse.Namespace, settings: Settings) -> None:
    deps = _deps(settings)
    _, factory = _factory(settings, deps)
    with factory() as session:
        done = process_new(session, deps, limit=args.limit)
    for enquiry in done:
        print(
            f"#{enquiry.id:<4} {enquiry.status:<10} {enquiry.category or '':<12} {enquiry.subject[:60]}"
        )
    print(f"{len(done)} enquiry(ies) processed")


def cmd_serve(args: argparse.Namespace, settings: Settings, deps: Deps | None = None) -> None:
    import uvicorn

    from inboxdraft.web.app import create_app

    app = create_app(settings=settings, deps=deps)
    print(f"review UI on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def cmd_demo(args: argparse.Namespace, settings: Settings) -> None:
    """init-db, ingest, import, process, serve: the whole thing in one command."""
    deps = _deps(settings)
    _, factory = _factory(settings, deps)
    with factory() as session:
        report = ingest_directory(session, deps.embedder, Path(settings.knowledge_dir))
        print("ingest:", report.summary())
        inbox = Path(settings.inbox_dir)
        imported = import_emails(session, read_inbox_dir(inbox) if inbox.is_dir() else [])
        print("import:", imported.summary())
        session.commit()
        done = process_new(session, deps)
        print(f"process: {len(done)} drafted")
    if not args.no_serve:
        cmd_serve(args, settings, deps)


def cmd_evals(args: argparse.Namespace, settings: Settings) -> None:
    from inboxdraft.evals import load_cases, run_evals

    deps = _deps(settings)
    _, factory = _factory(settings, deps)
    cases = load_cases(Path(args.cases))
    if args.limit:
        cases = cases[: args.limit]
    with factory() as session:
        report = ingest_directory(session, deps.embedder, Path(settings.knowledge_dir))
        print("ingest:", report.summary())
        session.commit()
        if args.offline:
            from inboxdraft.evals_offline import run_offline

            out = Path(args.out if args.out != "evals/report.md" else "evals/offline-report.md")
            offline = run_offline(session, deps.embedder, cases, settings.top_k, out)
            print(
                f"privacy: {offline.leaked} of {offline.pii_items} personal details left in model text\n"
                f"retrieval: every required fact in top {offline.k} for "
                f"{offline.retrieved_all} of {offline.eligible} enquiries; "
                f"facts {offline.facts_found} of {offline.facts}\n"
                f"report written to {out}"
            )
            return
        summary = run_evals(
            session,
            deps,
            cases,
            judge=not args.no_judge,
            report_path=Path(args.out),
            results_path=Path(args.results),
            labels_path=Path(args.labels),
        )
        session.commit()
    print(summary.as_text())
    print(f"report written to {args.out}")


def cmd_reset(args: argparse.Namespace, settings: Settings) -> None:
    if not args.yes:
        sys.exit("refusing without --yes")
    engine = make_engine(settings.database_url)
    if args.index_only:
        drop_index_tables(engine)
        print("document index dropped; run `inboxdraft ingest`")
    else:
        drop_all(engine)
        print("all tables dropped")


def cmd_status(args: argparse.Namespace, settings: Settings) -> None:
    print(f"inboxdraft {__version__}")
    print(
        f"provider: {settings.resolved_llm_provider} ({settings.draft_model} / {settings.fast_model})"
    )
    print(f"embeddings: {settings.embeddings_provider} ({settings.embedding_model})")
    print(f"database: {settings.database_url}")
    print(
        f"sending: {'SMTP ' + settings.smtp_host if settings.smtp_host else 'outbox folder ' + settings.outbox_dir}"
    )
    engine = make_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            rows = session.execute(
                select(Enquiry.status, func.count())
                .where(Enquiry.source == "inbox")
                .group_by(Enquiry.status)
            ).all()
        print("enquiries: " + (", ".join(f"{s} {n}" for s, n in rows) or "none"))
    except Exception as exc:
        print(f"enquiries: (no database yet: {exc.__class__.__name__})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inboxdraft", description=__doc__)
    parser.add_argument("--version", action="version", version=f"inboxdraft {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create tables").set_defaults(func=cmd_init_db)

    p = sub.add_parser("ingest", help="index the knowledge folder")
    p.add_argument("--path", help="folder with .md/.txt/.pdf/.docx (default: KNOWLEDGE_DIR)")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("import", help="import enquiries from .eml files and/or IMAP")
    p.add_argument("--path", help="folder with .eml files (default: INBOX_DIR)")
    p.add_argument("--imap", action="store_true", help="also fetch unseen messages over IMAP")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("process", help="draft replies for new enquiries")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("serve", help="run the review UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("demo", help="ingest + import + process + serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-serve", action="store_true")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("evals", help="run the evaluation set and write evals/report.md")
    p.add_argument("--cases", default="evals/cases.jsonl")
    p.add_argument("--out", default="evals/report.md")
    p.add_argument("--results", default="evals/results.jsonl")
    p.add_argument("--labels", default="evals/labels.csv")
    p.add_argument("--limit", type=int)
    p.add_argument("--no-judge", action="store_true", help="skip the LLM judge (rules only)")
    p.add_argument(
        "--offline",
        action="store_true",
        help="privacy and retrieval checks only: no model, no key (evals/offline-report.md)",
    )
    p.set_defaults(func=cmd_evals)

    p = sub.add_parser("reset", help="drop tables")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--index-only", action="store_true", help="drop only the document index")
    p.set_defaults(func=cmd_reset)

    sub.add_parser("status", help="show configuration and counts").set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args, get_settings())


if __name__ == "__main__":
    main()
