"""Figures guard: every material number in a draft must appear in a source or in the enquiry.

A model that is told "use only the sources" still sometimes writes "£120" when the price list
says £150, or invents "within 5 working days". This check is deterministic and cheap, and the
pipeline escalates any draft that fails it, so an unsupported figure never reaches a ready draft.

"Material" means: a currency amount, a percentage, a clock time, or any number of 10 or more.
Small bare numbers ("two of our three accountants", "1.") are ignored to avoid noise.
"""

from __future__ import annotations

import contextlib
import re
from decimal import Decimal, InvalidOperation

FIGURE_RE = re.compile(
    r"(?P<time>\b\d{1,2}[:.]\d{2}\s?(?:am|pm)?\b)"
    r"|(?P<money>£\s?\d[\d,]*(?:\.\d+)?)"
    r"|(?P<percent>\d[\d,]*(?:\.\d+)?\s?%)"
    r"|(?P<number>\b\d[\d,]*(?:\.\d+)?\b)",
    re.IGNORECASE,
)
CITATION_RE = re.compile(r"\[\d+\]")
TOKEN_RE = re.compile(r"\[[A-Z]+_\d+\]")


def _canonical(kind: str, raw: str) -> str | None:
    raw = raw.strip()
    if kind == "time":
        digits = re.sub(r"[^\d]", "", raw)
        return f"{int(digits[:-2])}:{digits[-2:]}"
    text = raw.replace("£", "").replace("%", "").replace(",", "").strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if kind == "number" and value < 10:
        return None
    normalised = format(value.normalize(), "f")
    return normalised


def figures(text: str, *, material_only: bool = True) -> dict[str, str]:
    """Map canonical figure -> first raw occurrence."""
    cleaned = TOKEN_RE.sub(" ", CITATION_RE.sub(" ", text or ""))
    found: dict[str, str] = {}
    for match in FIGURE_RE.finditer(cleaned):
        kind = match.lastgroup or "number"
        raw = match.group(0)
        canonical = _canonical(kind, raw)
        if canonical is None and not material_only:
            canonical = raw
        if canonical is not None and canonical not in found:
            found[canonical] = raw
    return found


def allowed_figures(sources: list[str], enquiry: str) -> set[str]:
    allowed: set[str] = set()
    for text in [*sources, enquiry]:
        cleaned = TOKEN_RE.sub(" ", text or "")
        for match in FIGURE_RE.finditer(cleaned):
            kind = match.lastgroup or "number"
            canonical = _canonical(kind, match.group(0))
            if canonical is not None:
                allowed.add(canonical)
            # a bare number in a source also licenses "£number" and "number%" in the draft
            bare = re.sub(r"[£%,\s]", "", match.group(0))
            with contextlib.suppress(InvalidOperation):
                allowed.add(format(Decimal(bare).normalize(), "f"))
    return allowed


def unsupported_figures(draft: str, sources: list[str], enquiry: str = "") -> list[str]:
    """Raw figures in the draft that no source (and not the enquiry) contains."""
    allowed = allowed_figures(sources, enquiry)
    return [raw for canonical, raw in figures(draft).items() if canonical not in allowed]
