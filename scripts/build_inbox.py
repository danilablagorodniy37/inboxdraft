"""Write the demo inbox: one .eml per eval case marked "demo": true.

    python scripts/build_inbox.py            # writes inbox/*.eml

The files are committed, so this only needs re-running when evals/cases.jsonl changes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "cases.jsonl"
INBOX = ROOT / "inbox"
TO = ("Wrenfield Accounting", "hello@wrenfield.example")
START = datetime(2026, 10, 16, 8, 40, tzinfo=UTC)


def main() -> None:
    INBOX.mkdir(exist_ok=True)
    for old in INBOX.glob("*.eml"):
        old.unlink()
    cases = [
        json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    demo = [c for c in cases if c.get("demo")]
    for index, case in enumerate(demo):
        message = EmailMessage()
        message["From"] = formataddr((case["from_name"], case["from_email"]))
        message["To"] = formataddr(TO)
        message["Subject"] = case["subject"]
        message["Date"] = format_datetime(START + timedelta(hours=5 * index, minutes=7 * index))
        message["Message-ID"] = f"<{case['id']}.demo@wrenfield.example>"
        message.set_content(case["body"])
        path = INBOX / f"{index + 1:02d}-{case['id']}.eml"
        path.write_bytes(bytes(message))
        print(path.name)
    print(f"{len(demo)} emails written to {INBOX}")


if __name__ == "__main__":
    main()
