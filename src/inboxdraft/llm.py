"""Model providers behind one small interface: a structured (tool-use) call.

Every call returns a dict matching the given JSON schema plus token usage. The fake provider
is rule-based and deterministic, so the tests, CI and the no-key demo run without a model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from inboxdraft.config import Settings


@dataclass(frozen=True)
class Usage:
    model: str
    input_tokens: int
    output_tokens: int


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    name: str

    def structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        tool_name: str,
        description: str,
        schema: dict[str, Any],
        max_tokens: int = 1024,
    ) -> tuple[dict[str, Any], Usage]: ...


class AnthropicLLM:
    """Forces the tool where the model allows it. Some models reject a forced `tool_choice`
    ("type tool and any are not supported for this model"); for those the call is repeated with
    `tool_choice: auto` and an explicit instruction, and the model is remembered so later calls
    go straight to that mode. Tokens of every attempt are counted, so the cost line stays true."""

    name = "anthropic"
    _auto_only: set[str] = set()

    def __init__(self, api_key: str | None) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)

    def structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        tool_name: str,
        description: str,
        schema: dict[str, Any],
        max_tokens: int = 1024,
    ) -> tuple[dict[str, Any], Usage]:
        tools = [{"name": tool_name, "description": description, "input_schema": schema}]
        tokens_in = tokens_out = 0
        forced = model not in self._auto_only
        for _attempt in range(3):
            if forced:
                choice = {"type": "tool", "name": tool_name}
                prompt = system
            else:
                choice = {"type": "auto"}
                prompt = f"{system}\n\nAnswer only by calling the `{tool_name}` tool, exactly once."
            try:
                response = self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=prompt,
                    messages=[{"role": "user", "content": user}],
                    tools=tools,
                    tool_choice=choice,
                )
            except self._anthropic.BadRequestError as e:
                if forced and "tool_choice" in str(e):
                    self._auto_only.add(model)
                    forced = False
                    continue
                raise
            tokens_in += response.usage.input_tokens
            tokens_out += response.usage.output_tokens
            for block in response.content:
                if getattr(block, "type", "") == "tool_use":
                    return dict(block.input), Usage(model, tokens_in, tokens_out)
            if forced:
                break
        raise LLMError(f"{model} returned no tool_use block")


class OpenAILLM:
    name = "openai"

    def __init__(self, api_key: str | None) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)

    def structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        tool_name: str,
        description: str,
        schema: dict[str, Any],
        max_tokens: int = 1024,
    ) -> tuple[dict[str, Any], Usage]:
        response = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": description,
                        "parameters": schema,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": tool_name}},
            max_completion_tokens=max_tokens,
        )
        choice = response.choices[0]
        calls = choice.message.tool_calls or []
        if not calls:
            raise LLMError(f"{model} returned no tool call")
        usage = response.usage
        return (
            json.loads(calls[0].function.arguments),
            Usage(
                model,
                usage.prompt_tokens if usage else 0,
                usage.completion_tokens if usage else 0,
            ),
        )


_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "my",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "you",
        "your",
        "please",
        "thanks",
        "thank",
        "hello",
        "hi",
        "dear",
        "regards",
        "kind",
        "could",
        "would",
        "can",
        "do",
        "does",
        "about",
        "me",
        "us",
        "am",
        "also",
        "just",
        "need",
        "want",
        "like",
    ]
)
_WORD_RE = re.compile(r"[a-z0-9£]+")
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")

_NEEDS_HUMAN = (
    "complain",
    "unhappy",
    "disappointed",
    "unacceptable",
    "refund",
    "penalty",
    "solicitor",
    "letter from hmrc",
    "hmrc letter",
    "investigation",
    "enquiry into",
    "my tax bill",
    "how much tax will",
    "wrong",
    "mistake",
)
_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("complaint", ("complain", "unhappy", "disappointed", "unacceptable", "mistake", "wrong")),
    ("appointment", ("appointment", "meeting", "book", "reschedule", "cancel", "slot")),
    (
        "documents",
        ("document", "bring", "upload", "portal", "p60", "p45", "statements", "paperwork"),
    ),
    (
        "deadlines",
        ("deadline", "due date", "by when", "31 january", "filing date", "when do i need"),
    ),
    ("pricing", ("how much", "cost", "fee", "price", "charge", "quote", "£")),
    (
        "services",
        (
            "do you offer",
            "do you do",
            "do you handle",
            "payroll",
            "vat",
            "bookkeeping",
            "cis",
            "formation",
            "limited company",
            "xero",
        ),
    ),
]


def _words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2}


class FakeLLM:
    """Rule-based stand-in. Classifies by keywords and drafts from the best-matching source."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        tool_name: str,
        description: str,
        schema: dict[str, Any],
        max_tokens: int = 1024,
    ) -> tuple[dict[str, Any], Usage]:
        self.calls.append({"model": model, "tool": tool_name, "user": user})
        if tool_name == "classify_enquiry":
            result: dict[str, Any] = self._classify(user)
        elif tool_name == "draft_reply":
            result = self._draft(user, system)
        elif tool_name == "judge_draft":
            result = self._judge(user)
        else:
            raise LLMError(f"fake model has no rule for tool {tool_name}")
        usage = Usage("fake", (len(system) + len(user)) // 4, len(json.dumps(result)) // 4)
        return result, usage

    # -- rules -------------------------------------------------------------------------
    @staticmethod
    def _classify(user: str) -> dict[str, Any]:
        text = user.lower()
        needs_human = any(k in text for k in _NEEDS_HUMAN)
        category = "other"
        for name, keys in _CATEGORY_RULES:
            if any(k in text for k in keys):
                category = name
                break
        return {
            "category": category,
            "needs_human": needs_human,
            "reason": "keyword rule (fake model)",
        }

    @staticmethod
    def _sources(user: str) -> list[tuple[str, str]]:
        _, _, tail = user.partition("\nSOURCES\n")
        found = re.findall(r"\[(\d+)\] (.+?)\n(.*?)(?=\n\[\d+\] |\Z)", tail, flags=re.S)
        return [(label, body.strip()) for _, label, body in found]

    @staticmethod
    def _signature(system: str) -> str:
        match = re.search(r"sign-off:\n(.*?)\n6\.", system, flags=re.S)
        return match.group(1).strip() if match else "Kind regards"

    @classmethod
    def _draft(cls, user: str, system: str) -> dict[str, Any]:
        head, _, _ = user.partition("\nSOURCES\n")
        enquiry_words = _words(head)
        greeting = "Dear [NAME_1]," if "[NAME_1]" in head else "Hello,"
        signature = cls._signature(system)
        sources = cls._sources(user)

        best_index, best_overlap = -1, 0
        for index, (label, body) in enumerate(sources):
            overlap = len(enquiry_words & _words(label + " " + body))
            if overlap > best_overlap:
                best_index, best_overlap = index, overlap
        if best_index < 0 or best_overlap < 3:
            return {
                "reply": (
                    f"{greeting}\n\nThank you for your email. I do not have this information to "
                    "hand, so I have passed your question to a colleague who will come back to "
                    f"you shortly.\n\n{signature}"
                ),
                "citations": [],
                "confidence": 0.2,
                "answered_from_sources": False,
                "notes": "No source covered the question (fake model).",
            }

        _, body = sources[best_index]
        sentences: list[str] = []
        for line in body.splitlines():
            line = _BULLET_RE.sub("", line).strip()
            sentences += [s.strip() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip()]
        ranked = sorted(
            range(len(sentences)), key=lambda i: -len(enquiry_words & _words(sentences[i]))
        )[:2]
        chosen = []
        for i in sorted(ranked):
            sentence = sentences[i]
            sentence = sentence[0].upper() + sentence[1:]
            chosen.append(sentence if sentence.endswith((".", "!", "?")) else sentence + ".")
        reply = (
            f"{greeting}\n\nThank you for your email. {' '.join(chosen)} [{best_index + 1}]\n\n"
            "If you have any other questions, just reply to this email and we will be happy to "
            f"help.\n\n{signature}"
        )
        return {
            "reply": reply,
            "citations": [best_index + 1],
            "confidence": 0.85,
            "answered_from_sources": True,
            "notes": "",
        }

    @staticmethod
    def _judge(user: str) -> dict[str, Any]:
        draft = user.partition("\nDRAFT\n")[2].partition("\nREFERENCE\n")[0].lower()
        facts_block = user.partition("\nFACTS\n")[2].partition("\n\n")[0]
        facts = re.findall(r"^- (.+)$", facts_block, flags=re.M)
        missing = [f for f in facts if f.lower() not in draft and f != "(none listed)"]
        verdict = "send_as_is" if not missing else "minor_edit"
        return {"verdict": verdict, "invented_facts": [], "reason": "fact check (fake model)"}


def get_llm(settings: Settings) -> LLM:
    provider = settings.resolved_llm_provider
    if provider == "anthropic":
        return AnthropicLLM(settings.anthropic_api_key)
    if provider == "openai":
        return OpenAILLM(settings.openai_api_key)
    if provider == "fake":
        return FakeLLM()
    raise ValueError(f"unknown LLM_PROVIDER: {provider}")
