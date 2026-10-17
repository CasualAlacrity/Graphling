"""Covers tools/uplink_tool.py's failure_message: a failed tool says what actually failed,
and never lets the model claim it worked (response matrix rows 52-54 and 80, 2026-10-16)."""
import httpx
import pytest

from tools.uplink_tool import failure_message


def _status_error(status, body):
    request = httpx.Request("POST", "https://api.test/trade-runs")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(status, json=body, request=request))


@pytest.mark.parametrize(("error", "says"), [
    (_status_error(503, {"detail": "UEX data unavailable until tomorrow"}), "UEX data unavailable until tomorrow"),
    (_status_error(401, {"detail": "expired"}), "login"),
    (_status_error(500, {"detail": "oops"}), "error (500)"),
    (httpx.ConnectTimeout("slow"), "too long"),
    (httpx.ConnectError("refused"), "couldn't reach"),
    (RuntimeError("a bug"), "inside ALICE while running start_trade_run"),
])
def test_says_what_failed_and_that_nothing_happened(error, says):
    message = failure_message("start_trade_run", error)

    assert says in message
    assert "UEX pricing API" not in message
    assert "don't say or imply that anything was found, recorded or changed" in message
