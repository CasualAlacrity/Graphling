"""Covers server/prompt_service.py — parsing Hub/snapshot manifests, expanding the
client's placeholders, and refresh's source fallback — plus that the client's
placeholder (prompt_loader) is exactly what the server looks for.

The real committed snapshot is used throughout: it's the file the server image actually
falls back on, so a manifest shape the parser can't read should fail here first.
"""
import pytest
from fastapi.testclient import TestClient

import prompt_loader
from server import llm_gateway_service as gateway
from server import prompt_service
from server.dependencies import get_current_user
from server.main import app


def _manifest(*messages, variables=()):
    return {"kwargs": {"messages": [
        {"id": ["langchain", "prompts", "chat", kind],
         "kwargs": {"prompt": {"kwargs": {
             "input_variables": list(variables), "template_format": "f-string", "template": text,
         }}}}
        for kind, text in messages
    ]}}


@pytest.fixture
def loaded(monkeypatch):
    store = prompt_service._Store()
    store.messages = prompt_service.load_snapshot()
    monkeypatch.setattr(prompt_service, "store", store)
    return store


def test_snapshot_has_every_prompt_as_plain_text():
    loaded = prompt_service.load_snapshot()
    assert set(loaded) == set(prompt_service.PROMPT_NAMES)
    persona = loaded["alice-persona"]
    assert persona[0]["role"] == "system"
    assert "You are ALICE" in persona[0]["content"]


def test_manifest_roles_and_escaped_braces():
    messages = prompt_service.messages_from_manifest(_manifest(
        ("SystemMessagePromptTemplate", 'Reply as JSON: {{"line": ...}}'),
        ("HumanMessagePromptTemplate", "example"),
    ))
    assert messages == [
        {"role": "system", "content": 'Reply as JSON: {"line": ...}'},
        {"role": "user", "content": "example"},
    ]


def test_manifest_with_variables_is_rejected():
    with pytest.raises(ValueError, match="template variables"):
        prompt_service.messages_from_manifest(
            _manifest(("SystemMessagePromptTemplate", "Hi {pilot}"), variables=["pilot"])
        )


def test_client_placeholder_matches_what_the_server_expands():
    for name in prompt_service.PROMPT_NAMES:
        match = prompt_service.PLACEHOLDER.match(prompt_loader.placeholder(name))
        assert match is not None and match.group(1) == name


def test_client_sends_only_a_placeholder_through_the_gateway(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "alice")
    messages = prompt_loader.prompt_messages("alice-persona")
    assert len(messages) == 1
    assert messages[0].content == "{{alice-prompt:alice-persona}}"


def test_expand_swaps_the_placeholder_and_keeps_the_rest(loaded):
    payload = {"model": "alice-chat", "messages": [
        {"role": "system", "content": prompt_loader.placeholder("alice-persona")},
        {"role": "user", "content": "Where's Laranite cheap?"},
    ]}
    expanded = prompt_service.expand(payload)
    assert expanded["messages"][:-1] == loaded.messages["alice-persona"]
    assert expanded["messages"][-1] == payload["messages"][-1]
    assert expanded["model"] == "alice-chat"
    # The caller's payload isn't mutated.
    assert payload["messages"][0]["content"] == "{{alice-prompt:alice-persona}}"


def test_expand_ignores_a_placeholder_outside_a_system_message(loaded):
    # A pilot saying the placeholder text out loud must not pull the prompt into a
    # user message.
    payload = {"messages": [{"role": "user", "content": "{{alice-prompt:alice-persona}}"}]}
    assert prompt_service.expand(payload) == payload


def test_expand_rejects_an_unknown_prompt(loaded):
    payload = {"messages": [{"role": "system", "content": "{{alice-prompt:not-a-prompt}}"}]}
    with pytest.raises(prompt_service.UnknownPrompt):
        prompt_service.expand(payload)


async def test_refresh_without_a_hub_key_loads_the_snapshot(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setattr(prompt_service, "store", prompt_service._Store())
    await prompt_service.refresh()
    assert set(prompt_service.store.sources.values()) == {"snapshot"}


async def test_hub_failure_keeps_the_prompt_already_loaded(monkeypatch, loaded):
    monkeypatch.setenv("LANGSMITH_API_KEY", "test")
    loaded.messages["alice-persona"] = [{"role": "system", "content": "the live one"}]

    def _hub_down(name):
        raise ConnectionError("hub down")

    monkeypatch.setattr(prompt_service, "_pull_from_hub", _hub_down)
    await prompt_service.refresh()
    assert loaded.messages["alice-persona"] == [{"role": "system", "content": "the live one"}]


async def test_hub_success_replaces_the_prompt(monkeypatch, loaded):
    monkeypatch.setenv("LANGSMITH_API_KEY", "test")
    monkeypatch.setattr(
        prompt_service, "_pull_from_hub", lambda name: [{"role": "system", "content": f"new {name}"}],
    )
    await prompt_service.refresh()
    assert loaded.messages["alice-persona"] == [{"role": "system", "content": "new alice-persona"}]
    assert loaded.sources["alice-persona"] == "hub"


def test_gateway_route_expands_before_routing(monkeypatch, loaded):
    seen = {}

    async def _fake_open_chat(payload, user_id):
        seen["messages"] = payload["messages"]
        raise gateway.NoBackendAvailable("stop here")

    monkeypatch.setattr(gateway, "open_chat", _fake_open_chat)
    app.dependency_overrides[get_current_user] = lambda: type("U", (), {"id": "u1"})()
    try:
        response = TestClient(app).post("/llm/api/chat", json={
            "model": "alice-chat",
            "messages": [{"role": "system", "content": prompt_loader.placeholder("topic-classification")}],
        })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert seen["messages"] == loaded.messages["topic-classification"]


def test_gateway_route_rejects_an_unknown_prompt(loaded):
    app.dependency_overrides[get_current_user] = lambda: type("U", (), {"id": "u2"})()
    try:
        response = TestClient(app).post("/llm/api/chat", json={
            "model": "alice-chat", "messages": [{"role": "system", "content": "{{alice-prompt:nope}}"}],
        })
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 400
