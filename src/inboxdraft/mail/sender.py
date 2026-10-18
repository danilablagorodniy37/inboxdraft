"""Send an approved reply. This is the only module that imports smtplib.

`send_reply` is called from exactly one place: the approve route in web/app.py, which runs only
when a person presses the button. tests/test_no_send_without_human.py enforces both facts.

Without SMTP_HOST the message is written to OUTBOX_DIR as an .eml file instead, so the demo
and the tests never need a mail server.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from inboxdraft.config import Settings
from inboxdraft.models import Enquiry


class SendError(RuntimeError):
    pass


@dataclass(frozen=True)
class SendResult:
    via: str  # smtp | outbox
    message_id: str
    path: str | None = None


def build_message(settings: Settings, enquiry: Enquiry, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((settings.firm_name, settings.reply_from))
    message["To"] = (
        formataddr((enquiry.from_name, enquiry.from_email))
        if enquiry.from_name
        else enquiry.from_email
    )
    message["Subject"] = enquiry.reply_subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(
        domain=settings.reply_from.split("@")[-1] or "inboxdraft.local"
    )
    if enquiry.message_id:
        message["In-Reply-To"] = enquiry.message_id
        references = " ".join(filter(None, [enquiry.references_header, enquiry.message_id]))
        message["References"] = references
    message.set_content(body)
    return message


def send_reply(settings: Settings, enquiry: Enquiry, body: str) -> SendResult:
    if not enquiry.from_email:
        raise SendError("the enquiry has no sender address")
    message = build_message(settings, enquiry, body)
    message_id = str(message["Message-ID"])
    if not settings.smtp_host:
        outbox = Path(settings.outbox_dir)
        outbox.mkdir(parents=True, exist_ok=True)
        path = outbox / f"{enquiry.id:05d}-reply.eml"
        path.write_bytes(bytes(message))
        return SendResult(via="outbox", message_id=message_id, path=str(path))
    try:
        if settings.smtp_ssl:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=30
            )
        else:
            client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        with client:
            client.ehlo()
            if settings.smtp_starttls and not settings.smtp_ssl:
                client.starttls()
                client.ehlo()
            if settings.smtp_user:
                client.login(settings.smtp_user, settings.smtp_password or "")
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise SendError(f"SMTP failed: {exc}") from exc
    return SendResult(via="smtp", message_id=message_id)
