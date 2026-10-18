from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from inboxdraft.mail.importer import import_emails
from inboxdraft.mail.reader import read_inbox_dir
from inboxdraft.models import STATUS_DRAFTED, STATUS_ESCALATED, STATUS_NEW, Enquiry
from inboxdraft.pipeline import Deps, process_enquiry, process_new

INBOX = Path(__file__).resolve().parents[1] / "inbox"


def _import(session: Session) -> list[Enquiry]:
    report = import_emails(session, read_inbox_dir(INBOX))
    session.commit()
    return report.added


def test_import_is_idempotent(indexed: Session) -> None:
    first = _import(indexed)
    assert len(first) == 8
    second = import_emails(indexed, read_inbox_dir(INBOX))
    assert second.added == [] and second.skipped == 8


def test_process_new_drafts_everything_and_stores_no_token_map(
    indexed: Session, deps: Deps
) -> None:
    _import(indexed)
    done = process_new(indexed, deps)
    assert len(done) == 8
    statuses = {e.status for e in done}
    assert STATUS_NEW not in statuses
    assert statuses <= {STATUS_DRAFTED, STATUS_ESCALATED}
    for enquiry in done:
        assert enquiry.scrubbed_body is not None
        assert enquiry.from_email not in enquiry.scrubbed_body
        assert enquiry.llm_calls == 2
        assert enquiry.cost_usd == 0.0  # fake model is priced at zero
        assert enquiry.draft_ms is not None
        assert enquiry.sources, "every processed enquiry keeps its retrieved sources"
    columns = {c.name for c in Enquiry.__table__.columns}
    assert not any("mapping" in c or "token_map" in c for c in columns)


def test_pricing_enquiry_gets_a_ready_draft_with_the_fee(indexed: Session, deps: Deps) -> None:
    enquiry = Enquiry(
        from_name="Sarah Patel",
        from_email="sarah@example.com",
        subject="Tax return cost",
        body="How much do you charge for a basic Self Assessment tax return? Thanks, Sarah Patel",
    )
    indexed.add(enquiry)
    indexed.flush()
    process_enquiry(indexed, enquiry, deps)
    assert enquiry.status == STATUS_DRAFTED, enquiry.escalation_reasons
    assert enquiry.category == "pricing"
    assert enquiry.draft_text.startswith("Dear Sarah,")
    assert "£180" in enquiry.draft_text
    assert enquiry.unsupported_figures == []
    assert enquiry.citations, "the fake model cites the source it quoted"
    cited = enquiry.sources[enquiry.citations[0] - 1].chunk
    assert "£180" in cited.text


def test_complaint_is_escalated_not_drafted(indexed: Session, deps: Deps) -> None:
    enquiry = Enquiry(
        from_name="Martin Blake",
        from_email="martin@example.com",
        subject="Penalty because of YOUR delay",
        body="You filed late and I have a £100 penalty. This is unacceptable. Martin",
    )
    indexed.add(enquiry)
    indexed.flush()
    process_enquiry(indexed, enquiry, deps)
    assert enquiry.status == STATUS_ESCALATED
    assert enquiry.needs_human is True
    assert any("Needs a person" in r for r in enquiry.escalation_reasons)


def test_question_outside_the_documents_gets_a_holding_reply(indexed: Session, deps: Deps) -> None:
    enquiry = Enquiry(
        from_name="Jessica Lane",
        from_email="jessica@example.com",
        subject="Trainee position",
        body="I would like to apply for a trainee role, CV attached. Yours faithfully, Jessica Lane",
    )
    indexed.add(enquiry)
    indexed.flush()
    process_enquiry(indexed, enquiry, deps)
    assert enquiry.status == STATUS_ESCALATED
    assert "Not covered by the documents" in enquiry.escalation_reasons
    assert enquiry.draft_text.startswith("Dear Jessica,")
    assert "colleague" in enquiry.draft_text
    assert "Model's attempt" in (enquiry.model_notes or "")


def test_unsupported_figure_escalates(indexed: Session, deps: Deps, monkeypatch) -> None:
    original = deps.llm.structured

    def with_invented_price(**kwargs):
        result, usage = original(**kwargs)
        if kwargs["tool_name"] == "draft_reply":
            result["reply"] = result["reply"].replace("£180", "£150")
        return result, usage

    monkeypatch.setattr(deps.llm, "structured", with_invented_price)
    enquiry = Enquiry(
        from_name="Sarah Patel",
        from_email="sarah@example.com",
        subject="Tax return cost",
        body="How much do you charge for a basic Self Assessment tax return?",
    )
    indexed.add(enquiry)
    indexed.flush()
    process_enquiry(indexed, enquiry, deps)
    assert enquiry.status == STATUS_ESCALATED
    assert enquiry.unsupported_figures == ["£150"]
    assert any("Figures not found" in r for r in enquiry.escalation_reasons)


def test_reprocessing_replaces_sources(indexed: Session, deps: Deps) -> None:
    _import(indexed)
    enquiry = indexed.execute(select(Enquiry).limit(1)).scalar_one()
    process_enquiry(indexed, enquiry, deps)
    first = [s.chunk_id for s in enquiry.sources]
    process_enquiry(indexed, enquiry, deps)
    indexed.commit()
    assert [s.chunk_id for s in enquiry.sources] == first
    assert len(enquiry.sources) == deps.settings.top_k
