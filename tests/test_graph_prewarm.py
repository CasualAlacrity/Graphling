"""Covers graph.prewarm's gating logic and failure handling — not the actual warmup
call itself, which just replays the real persona/classifier prompts through the real
bound models (nothing to fake there beyond the models themselves).
"""
import graph


class _RaisesIfCalled:
    async def ainvoke(self, *args, **kwargs):
        raise AssertionError("should not have been called")


class _RecordingModel:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, messages, config=None):
        self.calls.append((messages, config))
        return "ok"


def _fake_targets(**models):
    """Builds a _PREWARM_TARGETS-shaped list from name->fake-model kwargs, paired with
    the real (cheap, local, no-network) templates — only the model .ainvoke() call is
    ever faked here, never template rendering."""
    templates = {
        "persona": graph.PERSONA_TEMPLATE, "classifier": graph.CLASSIFY_TEMPLATE, "reject": graph.REJECT_TEMPLATE,
    }
    return [(name, model, templates[name]) for name, model in models.items()]


async def test_prewarm_is_a_noop_for_a_non_ollama_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setattr(graph, "_PREWARM_TARGETS", _fake_targets(persona=_RaisesIfCalled()))
    monkeypatch.setattr(graph, "_warmed", set())

    await graph.prewarm()  # would raise via the fake above if it actually called through


async def test_prewarm_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("PREWARM_OLLAMA", "false")
    monkeypatch.setattr(graph, "_PREWARM_TARGETS", _fake_targets(persona=_RaisesIfCalled()))
    monkeypatch.setattr(graph, "_warmed", set())

    await graph.prewarm()


async def test_prewarm_calls_every_target_when_enabled(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("PREWARM_OLLAMA", "true")
    fake_persona = _RecordingModel()
    fake_classifier = _RecordingModel()
    fake_reject = _RecordingModel()
    monkeypatch.setattr(
        graph, "_PREWARM_TARGETS", _fake_targets(persona=fake_persona, classifier=fake_classifier, reject=fake_reject)
    )
    monkeypatch.setattr(graph, "_warmed", set())

    await graph.prewarm()

    assert len(fake_persona.calls) == 1
    assert len(fake_classifier.calls) == 1
    assert len(fake_reject.calls) == 1
    # Each call is tagged so a prewarm run is identifiable in LangSmith rather than
    # looking like a real, confusing pilot interaction.
    assert fake_persona.calls[0][1]["tags"] == ["warmup"]
    assert fake_classifier.calls[0][1]["tags"] == ["warmup"]
    assert fake_reject.calls[0][1]["tags"] == ["warmup"]


async def test_prewarm_defaults_to_enabled_when_unset(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("PREWARM_OLLAMA", raising=False)
    fake_persona = _RecordingModel()
    fake_classifier = _RecordingModel()
    monkeypatch.setattr(graph, "_PREWARM_TARGETS", _fake_targets(persona=fake_persona, classifier=fake_classifier))
    monkeypatch.setattr(graph, "_warmed", set())

    await graph.prewarm()

    assert len(fake_persona.calls) == 1
    assert len(fake_classifier.calls) == 1


async def test_prewarm_swallows_a_failure_instead_of_raising(monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    class _Explodes:
        async def ainvoke(self, *args, **kwargs):
            raise ConnectionError("ollama not reachable")

    monkeypatch.setattr(graph, "_PREWARM_TARGETS", _fake_targets(persona=_Explodes(), classifier=_RecordingModel()))
    monkeypatch.setattr(graph, "_warmed", set())

    await graph.prewarm()  # must not raise

    assert "Prewarm failed" in capsys.readouterr().out


async def test_prewarm_only_warms_each_target_once(monkeypatch):
    """A target that failed stays un-warmed and gets retried on a later call; a target
    that already succeeded is skipped, so a second prewarm() call (or a caller that
    invokes it defensively more than once) doesn't waste a duplicate warm-up."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    fake_persona = _RecordingModel()
    fake_classifier = _RecordingModel()
    monkeypatch.setattr(graph, "_PREWARM_TARGETS", _fake_targets(persona=fake_persona, classifier=fake_classifier))
    monkeypatch.setattr(graph, "_warmed", set())

    await graph.prewarm()
    await graph.prewarm()

    assert len(fake_persona.calls) == 1
    assert len(fake_classifier.calls) == 1
