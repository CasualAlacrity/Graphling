"""Covers the Anthropic prompt-caching switches: anthropic_cache_mode/_control's .env
parsing (llm.py) and _persona_messages' breakpoint placement (graph.py). The request
payload itself was checked against langchain-anthropic's _get_request_payload when this
was written -- breakpoint on system[0] for "system", plus top-level cache_control for
"full" -- not re-asserted here since that's the library's job."""
import pytest
from langchain_core.messages import SystemMessage

import graph
from llm import anthropic_cache_control, anthropic_cache_mode


def test_cache_mode_is_off_for_other_providers(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("ANTHROPIC_CACHE_MODE", "full")
    assert anthropic_cache_mode() == "off"


def test_cache_mode_defaults_to_system(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_CACHE_MODE", raising=False)
    assert anthropic_cache_mode() == "system"


def test_unknown_cache_mode_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_CACHE_MODE", "everything")
    with pytest.raises(ValueError):
        anthropic_cache_mode()


def test_cache_ttl(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_CACHE_TTL", "1h")
    assert anthropic_cache_control() == {"type": "ephemeral", "ttl": "1h"}
    monkeypatch.setenv("ANTHROPIC_CACHE_TTL", "10m")
    with pytest.raises(ValueError):
        anthropic_cache_control()


def test_persona_untouched_when_caching_off(monkeypatch):
    monkeypatch.setattr(graph, "CACHE_MODE", "off")
    messages = graph._persona_messages()
    assert isinstance(messages[-1].content, str)


def test_persona_gets_breakpoint_when_caching_on(monkeypatch):
    monkeypatch.setattr(graph, "CACHE_MODE", "system")
    monkeypatch.delenv("ANTHROPIC_CACHE_TTL", raising=False)
    plain = graph.PERSONA_MESSAGES[-1].content
    last = graph._persona_messages()[-1]
    assert isinstance(last, SystemMessage)
    assert last.content == [{"type": "text", "text": plain, "cache_control": {"type": "ephemeral", "ttl": "5m"}}]
