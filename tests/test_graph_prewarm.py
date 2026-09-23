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


async def test_prewarm_is_a_noop_for_a_non_ollama_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setattr(graph, "llm", _RaisesIfCalled())
    monkeypatch.setattr(graph, "classifier_llm", _RaisesIfCalled())

    await graph.prewarm()  # would raise via the fakes above if it actually called through


async def test_prewarm_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("PREWARM_OLLAMA", "false")
    monkeypatch.setattr(graph, "llm", _RaisesIfCalled())
    monkeypatch.setattr(graph, "classifier_llm", _RaisesIfCalled())

    await graph.prewarm()


async def test_prewarm_calls_both_models_when_enabled(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("PREWARM_OLLAMA", "true")
    fake_llm = _RecordingModel()
    fake_classifier = _RecordingModel()
    monkeypatch.setattr(graph, "llm", fake_llm)
    monkeypatch.setattr(graph, "classifier_llm", fake_classifier)

    await graph.prewarm()

    assert len(fake_llm.calls) == 1
    assert len(fake_classifier.calls) == 1
    # Each call is tagged so a prewarm run is identifiable in LangSmith rather than
    # looking like a real, confusing pilot interaction.
    assert fake_llm.calls[0][1]["tags"] == ["warmup"]
    assert fake_classifier.calls[0][1]["tags"] == ["warmup"]


async def test_prewarm_defaults_to_enabled_when_unset(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("PREWARM_OLLAMA", raising=False)
    fake_llm = _RecordingModel()
    fake_classifier = _RecordingModel()
    monkeypatch.setattr(graph, "llm", fake_llm)
    monkeypatch.setattr(graph, "classifier_llm", fake_classifier)

    await graph.prewarm()

    assert len(fake_llm.calls) == 1
    assert len(fake_classifier.calls) == 1


async def test_prewarm_swallows_a_failure_instead_of_raising(monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    class _Explodes:
        async def ainvoke(self, *args, **kwargs):
            raise ConnectionError("ollama not reachable")

    monkeypatch.setattr(graph, "llm", _Explodes())
    monkeypatch.setattr(graph, "classifier_llm", _RecordingModel())

    await graph.prewarm()  # must not raise

    assert "Prewarm failed" in capsys.readouterr().out
