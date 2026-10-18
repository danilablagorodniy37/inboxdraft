"""Replace personal details with placeholders before any text reaches a model.

    "Hi, it's Sarah Patel, 07700 900123, GU1 4AB"  ->  "Hi, it's [NAME_1] [NAME_2], [PHONE_1], [POSTCODE_1]"

The map from placeholder to original value is returned to the caller, used once to restore the
finished draft, and never written to the database or to logs. Rules are regular expressions,
deliberately conservative: a false placeholder costs a little clarity, a missed detail costs a
leak. Dates (including dates of birth) are not tokenised; add a rule if your inbox carries them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(
    r"(?<![\w.-])(?:\+\d{1,3}[\s-]?(?:\(0\)\s?)?|\(?0)\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?![\w-])"
)
NINO_RE = re.compile(r"\b[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b", re.IGNORECASE)
UTR_RE = re.compile(r"(?<!\d)\d{5}\s?\d{5}(?!\d)")
POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", re.IGNORECASE)
ADDRESS_RE = re.compile(
    r"\b\d{1,4}[A-Za-z]?\s+(?:[A-Z][a-z]+\s+){1,3}"
    r"(?:Road|Street|Lane|Avenue|Close|Drive|Way|Court|Place|Crescent|Gardens|Terrace|Square|"
    r"Hill|Park|Row|Rd|St|Ave)\b\.?"
)
_SIGNOFFS = (
    r"kind regards|best regards|warm regards|regards|best wishes|many thanks|thanks so much|"
    r"thanks|thank you|cheers|best|yours sincerely|yours faithfully|all the best|ta"
)
SIGNOFF_RE = re.compile(rf"^\s*(?:{_SIGNOFFS})\s*[,.!]?\s*$", re.IGNORECASE)
NAME_WORD = rf"[A-Z][A-Za-z'{chr(0x2019)}-]+"  # straight and curly apostrophes
NAME_LINE_RE = re.compile(rf"^\s*{NAME_WORD}(?:\s+{NAME_WORD}){{0,3}}\s*$")
INLINE_SIGNOFF_RE = re.compile(
    rf"^(.*?\b(?i:{_SIGNOFFS}),?\s+)({NAME_WORD}(?:\s+{NAME_WORD}){{0,2}})\s*$"
)
TOKEN_RE = re.compile(r"\[?\b(EMAIL|PHONE|NINO|UTR|POSTCODE|ADDRESS|NAME)_(\d+)\b\]?")

_NAME_STOPWORDS = frozenset({"The", "Our", "Your", "Team", "Ltd", "Limited", "LLP", "Accounts"})


@dataclass
class Scrubber:
    """Stateful: the same value gets the same placeholder across subject and body."""

    from_name: str | None = None
    from_email: str | None = None
    mapping: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)
    _reverse: dict[tuple[str, str], str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.from_email:
            self._token("EMAIL", self.from_email.strip())
        for part in _name_parts(self.from_name):
            self._token("NAME", part)

    def _token(self, kind: str, original: str) -> str:
        key = (kind, original)
        if key in self._reverse:
            return self._reverse[key]
        self._counters[kind] = self._counters.get(kind, 0) + 1
        token = f"[{kind}_{self._counters[kind]}]"
        self.mapping[token] = original
        self._reverse[key] = token
        return token

    def _sub(self, kind: str, pattern: re.Pattern[str], text: str) -> str:
        return pattern.sub(lambda m: self._token(kind, m.group(0)), text)

    def _known_names(self) -> set[str]:
        return {orig for tok, orig in self.mapping.items() if tok.startswith("[NAME_")}

    def _tokenise_words(self, text: str) -> str:
        return " ".join(w if w in _NAME_STOPWORDS else self._token("NAME", w) for w in text.split())

    def scrub(self, text: str | None) -> str:
        if not text:
            return ""
        text = self._sub("EMAIL", EMAIL_RE, text)
        text = self._sub("PHONE", PHONE_RE, text)
        text = self._sub("NINO", NINO_RE, text)
        text = self._sub("UTR", UTR_RE, text)
        text = self._sub("POSTCODE", POSTCODE_RE, text)
        text = self._sub("ADDRESS", ADDRESS_RE, text)
        text = self._scrub_signature(text)
        text = self._scrub_known_names(text)
        return text

    def _scrub_signature(self, text: str) -> str:
        """Names on their own line after a sign-off, inline after a sign-off, or on a line that
        contains a name we already know from the From header."""
        known = self._known_names()
        out: list[str] = []
        pending = 0  # lines still to inspect after a sign-off
        for line in text.splitlines():
            stripped = line.strip()
            if SIGNOFF_RE.match(line):
                pending = 3
                out.append(line)
                continue
            inline = INLINE_SIGNOFF_RE.match(line)
            if inline:
                out.append(inline.group(1) + self._tokenise_words(inline.group(2)))
                pending = 0
                continue
            if stripped and NAME_LINE_RE.match(line) and not TOKEN_RE.search(line):
                words = stripped.split()
                looks_like_name = (pending or any(w in known for w in words)) and not all(
                    w in _NAME_STOPWORDS for w in words
                )
                if looks_like_name:
                    indent = line[: len(line) - len(line.lstrip())]
                    out.append(indent + self._tokenise_words(stripped))
                    pending = 0  # the first name line is the name; what follows is a title
                    continue
            if pending and stripped:
                pending -= 1
            out.append(line)
        return "\n".join(out) + ("\n" if text.endswith("\n") else "")

    def _scrub_known_names(self, text: str) -> str:
        names = [(orig, tok) for tok, orig in self.mapping.items() if tok.startswith("[NAME_")]
        for original, token in sorted(names, key=lambda item: -len(item[0])):
            text = re.sub(rf"(?<![\w\[]){re.escape(original)}(?![\w\]])", token, text)
        return text


def _name_parts(full_name: str | None) -> list[str]:
    if not full_name:
        return []
    cleaned = re.sub(r"[\"'<>]", " ", full_name)
    parts = [p.strip(".,") for p in cleaned.split()]
    return [p for p in parts if len(p) >= 2 and p[0].isalpha() and p not in _NAME_STOPWORDS]


def restore(text: str | None, mapping: dict[str, str]) -> str:
    """Put the original values back into a draft. Tolerates placeholders without brackets."""
    if not text:
        return ""

    def replace(match: re.Match[str]) -> str:
        token = f"[{match.group(1)}_{match.group(2)}]"
        return mapping.get(token, match.group(0))

    return TOKEN_RE.sub(replace, text)


def contains_any(text: str, values: list[str]) -> list[str]:
    """Which of the given values appear verbatim in the text (used by tests and evals)."""
    return [v for v in values if v and v in text]
