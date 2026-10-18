"""Store parsed emails as enquiries, once each."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from inboxdraft.mail.reader import ParsedEmail
from inboxdraft.models import Enquiry


@dataclass
class ImportReport:
    added: list[Enquiry] = field(default_factory=list)
    skipped: int = 0

    def summary(self) -> str:
        return f"{len(self.added)} new enquiry(ies), {self.skipped} already imported"


def import_emails(
    session: Session, emails: list[ParsedEmail], *, source: str = "inbox"
) -> ImportReport:
    report = ImportReport()
    for parsed in emails:
        message_id = parsed.message_id or _synthetic_id(parsed)
        exists = session.execute(
            select(Enquiry.id).where(Enquiry.message_id == message_id)
        ).scalar_one_or_none()
        if exists:
            report.skipped += 1
            continue
        enquiry = Enquiry(
            source=source,
            message_id=message_id,
            in_reply_to=parsed.in_reply_to,
            references_header=parsed.references,
            from_name=parsed.from_name,
            from_email=parsed.from_email,
            to_email=parsed.to_email,
            subject=parsed.subject,
            body=parsed.body,
            received_at=parsed.date,
        )
        session.add(enquiry)
        report.added.append(enquiry)
    session.flush()
    return report


def _synthetic_id(parsed: ParsedEmail) -> str:
    digest = hashlib.sha1(
        f"{parsed.from_email}|{parsed.subject}|{parsed.body}".encode()
    ).hexdigest()
    return f"<{digest}@inboxdraft.local>"
