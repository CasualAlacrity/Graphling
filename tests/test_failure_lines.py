import httpx

from voice import failure_lines
from voice.failure_lines import failure_line


def _status_error(status):
    request = httpx.Request("GET", "https://example.test/x")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(status, request=request))


def test_unreachable_server_is_named():
    assert failure_line(httpx.ConnectError("down")) == failure_lines.UNREACHABLE
    assert failure_line(httpx.ReadTimeout("slow")) == failure_lines.UNREACHABLE


def test_rejected_login_points_at_signing_in():
    assert failure_line(_status_error(401)) == failure_lines.LOGIN_REJECTED


def test_busy_statuses_say_to_retry():
    for status in (429, 502, 503, 504):
        assert failure_line(_status_error(status)) == failure_lines.BUSY


def test_anything_else_is_generic():
    assert failure_line(_status_error(500)) == failure_lines.GENERIC
    assert failure_line(KeyError("data")) == failure_lines.GENERIC


def test_no_line_claims_anything_was_recorded():
    for line in (failure_lines.UNREACHABLE, failure_lines.LOGIN_REJECTED, failure_lines.BUSY, failure_lines.GENERIC):
        assert "didn't go through" in line
