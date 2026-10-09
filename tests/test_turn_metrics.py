"""Covers the new per-stage timing pieces in turn_metrics.py (docs/voice-stage-timing.md):
TurnMetricsCallback bucketing LangChain start/end events into classify_ms/respond_ms/
decline_ms/tool_ms by metadata["langgraph_node"] (the mechanism LangGraph itself attaches,
confirmed empirically against the real graph before writing this), and TurnTimings'
JSON round-trip. Usage/cost capture (the same callback's other half) is covered in
tests/test_turn_usage.py."""
import uuid

from turn_metrics import TurnMetricsCallback, TurnTimings


def _run_id() -> uuid.UUID:
    return uuid.uuid4()


async def test_classify_node_llm_call_recorded_as_classify_ms():
    callback = TurnMetricsCallback()
    run_id = _run_id()

    await callback.on_chat_model_start({}, [], run_id=run_id, metadata={"langgraph_node": "classify_topic"})
    await callback.on_llm_end(None, run_id=run_id)

    assert callback.timings.classify_ms is not None
    assert callback.timings.classify_ms >= 0
    assert callback.timings.respond_ms == []
    assert callback.timings.decline_ms is None


async def test_decline_node_llm_call_recorded_as_decline_ms():
    callback = TurnMetricsCallback()
    run_id = _run_id()

    await callback.on_chat_model_start({}, [], run_id=run_id, metadata={"langgraph_node": "decline"})
    await callback.on_llm_end(None, run_id=run_id)

    assert callback.timings.decline_ms is not None
    assert callback.timings.classify_ms is None


async def test_respond_node_calls_accumulate_in_order():
    # A turn can round-trip respond <-> tools more than once -- each gets its own run_id
    # and must land as its own entry, not overwrite the previous one.
    callback = TurnMetricsCallback()
    first_id, second_id = _run_id(), _run_id()

    await callback.on_chat_model_start({}, [], run_id=first_id, metadata={"langgraph_node": "respond"})
    await callback.on_llm_end(None, run_id=first_id)
    await callback.on_chat_model_start({}, [], run_id=second_id, metadata={"langgraph_node": "respond"})
    await callback.on_llm_end(None, run_id=second_id)

    assert len(callback.timings.respond_ms) == 2


async def test_llm_call_with_no_langgraph_node_is_ignored():
    # No metadata at all (e.g. a non-LangGraph call using the same callback by mistake)
    # must not crash and must not land in any bucket.
    callback = TurnMetricsCallback()
    run_id = _run_id()

    await callback.on_chat_model_start({}, [], run_id=run_id)
    await callback.on_llm_end(None, run_id=run_id)

    assert callback.timings.classify_ms is None
    assert callback.timings.respond_ms == []
    assert callback.timings.decline_ms is None


async def test_llm_error_clears_pending_state_without_recording():
    callback = TurnMetricsCallback()
    run_id = _run_id()

    await callback.on_chat_model_start({}, [], run_id=run_id, metadata={"langgraph_node": "respond"})
    await callback.on_llm_error(RuntimeError("boom"), run_id=run_id)

    assert callback.timings.respond_ms == []
    assert run_id not in callback._llm_start
    assert run_id not in callback._llm_node


async def test_llm_end_for_unknown_run_id_is_a_noop():
    # on_llm_end firing for a run_id this handler never saw on_chat_model_start for
    # (shouldn't happen, but must not crash) -- defensive, matches the None-check in
    # the implementation.
    callback = TurnMetricsCallback()

    await callback.on_llm_end(None, run_id=_run_id())

    assert callback.timings.respond_ms == []


async def test_tool_call_recorded_with_its_name():
    callback = TurnMetricsCallback()
    run_id = _run_id()

    await callback.on_tool_start({"name": "commodity_price_lookup"}, "{}", run_id=run_id)
    await callback.on_tool_end("result", run_id=run_id)

    assert len(callback.timings.tool_ms) == 1
    name, duration_ms = callback.timings.tool_ms[0]
    assert name == "commodity_price_lookup"
    assert duration_ms >= 0


async def test_multiple_tool_calls_each_recorded_separately():
    callback = TurnMetricsCallback()
    first_id, second_id = _run_id(), _run_id()

    await callback.on_tool_start({"name": "commodity_price_lookup"}, "{}", run_id=first_id)
    await callback.on_tool_end("result", run_id=first_id)
    await callback.on_tool_start({"name": "best_route"}, "{}", run_id=second_id)
    await callback.on_tool_end("result", run_id=second_id)

    names = [name for name, _ in callback.timings.tool_ms]
    assert names == ["commodity_price_lookup", "best_route"]


async def test_tool_error_clears_pending_state_without_recording():
    callback = TurnMetricsCallback()
    run_id = _run_id()

    await callback.on_tool_start({"name": "best_route"}, "{}", run_id=run_id)
    await callback.on_tool_error(RuntimeError("boom"), run_id=run_id)

    assert callback.timings.tool_ms == []
    assert run_id not in callback._tool_start
    assert run_id not in callback._tool_name


def test_turn_timings_json_round_trips():
    timings = TurnTimings(
        whisper_ms=120.5, classify_ms=80.0, decline_ms=None,
        respond_ms=[1200.0, 300.0], tool_ms=[("commodity_price_lookup", 450.0)],
        graph_ms=2500.0, tts_first_chunk_ms=600.0, tts_total_ms=1800.0, round_trip_ms=4500.0,
    )

    round_tripped = TurnTimings.model_validate_json(timings.model_dump_json())

    assert round_tripped == timings


def test_summary_line_only_includes_set_fields():
    timings = TurnTimings()
    assert timings.summary_line() == "ms "

    timings.classify_ms = 80.0
    assert timings.summary_line() == "ms classify=80"
