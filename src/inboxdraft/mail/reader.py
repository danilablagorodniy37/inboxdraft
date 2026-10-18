"""Parse .eml files or fetch unseen messages over IMAP."""

from __future__ import annotations

import contextlib
import email
import email.policy
import html
import imaplib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from inboxdraft.config import Settings


@dataclass(frozen=True)
class ParsedEmail:
    message_id: str | None
    in_reply_to: str | None
    references: str | None
    from_name: str
    from_email: str
    to_email: str
    subject: str
    date: datetime | None
    body: str


class _HTMLToText(HTMLParser):
    _block = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table", "blockquote"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"style", "script"}:
            self._skip += 1
        elif tag in self._block:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"style", "script"} and self._skip:
            self._skip -= 1
        elif tag in self._block:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _HTMLToText()
    parser.feed(markup)
    text = html.unescape("".join(parser.parts))
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_eml_bytes(raw: bytes) -> ParsedEmail:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(message, EmailMessage)
    from_pairs = getaddresses([str(message.get("From", ""))])
    from_name, from_email = from_pairs[0] if from_pairs else ("", "")
    to_pairs = getaddresses([str(message.get("To", ""))])
    to_email = to_pairs[0][1] if to_pairs else ""
    body = _body_text(message)
    date = None
    if message.get("Date"):
        try:
            parsed = parsedate_to_datetime(str(message["Date"]))
            date = parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed
        except (TypeError, ValueError):
            date = None
    return ParsedEmail(
        message_id=_clean(message.get("Message-ID")),
        in_reply_to=_clean(message.get("In-Reply-To")),
        references=_clean(message.get("References")),
        from_name=from_name.strip(),
        from_email=from_email.strip().lower(),
        to_email=to_email.strip().lower(),
        subject=str(message.get("Subject", "")).strip(),
        date=date,
        body=body,
    )


def _body_text(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html":
        return html_to_text(content)
    return str(content).strip()


def _clean(value: object) -> str | None:
    text = str(value).strip() if value else ""
    return text or None


def read_inbox_dir(directory: Path) -> list[ParsedEmail]:
    emails = []
    for path in sorted(directory.glob("*.eml")):
        emails.append(parse_eml_bytes(path.read_bytes()))
    return emails


def fetch_imap(settings: Settings) -> list[ParsedEmail]:
    """Fetch unseen messages. Marks them seen afterwards unless IMAP_MARK_SEEN=false."""
    if not (settings.imap_host and settings.imap_user and settings.imap_password):
        raise RuntimeError("IMAP_HOST, IMAP_USER and IMAP_PASSWORD must be set")
    client = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
    try:
        client.login(settings.imap_user, settings.imap_password)
        client.select(settings.imap_folder)
        status, data = client.search(None, "UNSEEN")
        if status != "OK":
            return []
        emails: list[ParsedEmail] = []
        for num in data[0].split():
            status, parts = client.fetch(num, "(BODY.PEEK[])")
            if status != "OK" or not parts:
                continue
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if raw is None:
                continue
            emails.append(parse_eml_bytes(raw))
            if settings.imap_mark_seen:
                client.store(num, "+FLAGS", "\\Seen")
        return emails
    finally:
        with contextlib.suppress(Exception):
            client.logout()
