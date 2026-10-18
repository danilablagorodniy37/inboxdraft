"""The real providers, exercised against stub clients: request shape and response parsing."""

from __future__ import annotations

import json
import types

import pytest

from inboxdraft.config import Settings
from inboxdraft.llm import AnthropicLLM, LLMError, OpenAILLM, Usage, get_llm

SCHEMA = {"type": "object", "properties": {"category": {"type": "string"}}}


def _anthropic_stub(monkeypatch, blocks: list[object], seen: dict) -> None:
    class Messages:
        def create(self, **kwargs):
            seen.update(kwargs)
            return types.SimpleNamespace(
                content=blocks, usage=types.SimpleNamespace(input_tokens=12, output_tokens=5)
            )

    class Client:
        def __init__(self, api_key=None):
            seen["api_key"] = api_key
            self.messages = Messages()

    monkeypatch.setattr("anthropic.Anthropic", Client)


def test_anthropic_forces_the_tool_and_parses_its_input(monkeypatch) -> None:
    seen: dict = {}
    block = types.SimpleNamespace(type="tool_use", input={"category": "pricing"})
    _anthropic_stub(monkeypatch, [types.SimpleNamespace(type="text", text="..."), block], seen)
    llm = AnthropicLLM("test-key")
    result, usage = llm.structured(
        model="claude-haiku-5-5",
        system="sys",
        user="usr",
        tool_name="classify_enquiry",
        description="d",
        schema=SCHEMA,
        max_tokens=50,
    )
    assert result == {"category": "pricing"}
    assert usage == Usage("claude-haiku-5-5", 12, 5)
    assert seen["api_key"] == "test-key"
    assert seen["model"] == "claude-haiku-5-5" and seen["max_tokens"] == 50
    assert seen["system"] == "sys"
    assert seen["messages"] == [{"role": "user", "content": "usr"}]
    assert seen["tools"] == [
        {"name": "classify_enquiry", "description": "d", "input_schema": SCHEMA}
    ]
    assert seen["tool_choice"] == {"type": "tool", "name": "classify_enquiry"}


def test_anthropic_without_tool_use_block_raises(monkeypatch) -> None:
    _anthropic_stub(monkeypatch, [types.SimpleNamespace(type="text", text="no")], {})
    with pytest.raises(LLMError):
        AnthropicLLM("k").structured(
            model="m", system="s", user="u", tool_name="t", description="d", schema=SCHEMA
        )


def test_openai_function_call_is_parsed(monkeypatch) -> None:
    pytest.importorskip("openai")
    seen: dict = {}

    class Completions:
        def create(self, **kwargs):
            seen.update(kwargs)
            call = types.SimpleNamespace(
                function=types.SimpleNamespace(arguments=json.dumps({"category": "other"}))
            )
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(tool_calls=[call]))],
                usage=types.SimpleNamespace(prompt_tokens=7, completion_tokens=3),
            )

    class Client:
        def __init__(self, api_key=None):
            self.chat = types.SimpleNamespace(completions=Completions())

    monkeypatch.setattr("openai.OpenAI", Client)
    result, usage = OpenAILLM("k").structured(
        model="gpt-x", system="s", user="u", tool_name="t", description="d", schema=SCHEMA
    )
    assert result == {"category": "other"} and usage == Usage("gpt-x", 7, 3)
    assert seen["tool_choice"] == {"type": "function", "function": {"name": "t"}}
    assert seen["tools"][0]["function"]["parameters"] == SCHEMA


def test_get_llm_picks_the_provider_from_the_keys(monkeypatch) -> None:
    assert get_llm(Settings(_env_file=None, llm_provider="auto")).name == "fake"
    _anthropic_stub(monkeypatch, [], {})
    anthropic = get_llm(Settings(_env_file=None, llm_provider="auto", anthropic_api_key="k"))
    assert anthropic.name == "anthropic"
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER"):
        get_llm(Settings(_env_file=None, llm_provider="nope"))
