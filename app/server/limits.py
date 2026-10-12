"""Runaway guards for the gateways (docs/llm-gateway.md, Limits). Not usage limits: every
threshold sits well above a real session, so they only ever trip on a broken client, a
loop, or abuse -- the goal is still to *observe* real cost, not cap it.

In memory, like the gateway's other state (one server process). A restart forgets the
counts, which is fine for a guard; the daily fallback budget is read from llm_usage
instead, so it survives restarts.
"""
import time
from collections import defaultdict, deque
from datetime import UTC, datetime


class RateLimiter:
    """At most `max_events` per key in any sliding `window_s`-second window."""

    def __init__(self, max_events: int, window_s: float) -> None:
        self.max_events = max_events
        self.window_s = window_s
        self._events: dict[object, deque[float]] = defaultdict(deque)

    def retry_after_s(self, key: object, now: float | None = None) -> float | None:
        """Counts this event and returns None if it's allowed; otherwise returns how many
        seconds until it would be, without counting it."""
        now = time.monotonic() if now is None else now
        events = self._events[key]
        while events and now - events[0] >= self.window_s:
            events.popleft()
        if len(events) >= self.max_events:
            if not events:  # max_events == 0: nothing is ever allowed
                return self.window_s
            return self.window_s - (now - events[0])
        events.append(now)
        return None


class DailyCounter:
    """A per-key running total that resets at UTC midnight."""

    def __init__(self) -> None:
        self._day = None
        self._totals: dict[object, float] = defaultdict(float)

    def total(self, key: object) -> float:
        self._roll_over()
        return self._totals[key]

    def add(self, key: object, amount: float) -> None:
        self._roll_over()
        self._totals[key] += amount

    def _roll_over(self) -> None:
        today = datetime.now(UTC).date()
        if today != self._day:
            self._day = today
            self._totals.clear()
