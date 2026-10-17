from voice import timer_tool
from voice.timer_tool import CancelTimerTool, CheckTimerTool, StartTimerTool, _spoken_duration


def test_spoken_duration_reads_as_a_length_of_time_not_a_clock():
    assert _spoken_duration(23 * 60) == "23 minutes"
    assert _spoken_duration(60) == "1 minute"
    assert _spoken_duration(90) == "1 minute 30 seconds"
    assert _spoken_duration(1) == "1 second"


async def test_start_timer_confirms_in_words(monkeypatch):
    monkeypatch.setattr(timer_tool, "_timers", {})
    monkeypatch.setattr(timer_tool.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())

    message = await StartTimerTool()._arun(label="waiting period", duration_seconds=23 * 60)

    assert message == "Timer 'waiting period' set for 23 minutes."


async def test_cancel_timer_removes_it_so_it_never_announces(monkeypatch):
    timers = {"cargo loading": {"duration_seconds": 1175}}
    monkeypatch.setattr(timer_tool, "_timers", timers)

    message = await CancelTimerTool()._arun(label="cargo loading")

    assert timers == {}
    assert "Cancelled" in message


async def test_cancel_timer_asks_which_when_several_are_running(monkeypatch):
    monkeypatch.setattr(timer_tool, "_timers", {"a": {}, "b": {}})

    assert "which one" in await CancelTimerTool()._arun()


async def test_check_timer_with_none_running(monkeypatch):
    monkeypatch.setattr(timer_tool, "_timers", {})

    assert "No timers" in await CheckTimerTool()._arun()
