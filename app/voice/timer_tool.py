import asyncio
import threading
import time
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from tools.uplink_tool import UplinkTool
from voice.audio_output import play_audio
from voice.tts import synthesize

# Simple in-memory, single-process state — this only ever runs inside the voice loop's
# own process, so there's no reason for DB persistence: timers are short-lived (minutes)
# and losing one on a restart just means restating the duration.
_timers: dict[str, dict] = {}


async def _speak(text: str) -> None:
    # A timer notification isn't part of any measured turn, so the first-chunk timing
    # synthesize() now also returns (docs/voice-stage-timing.md) is discarded here.
    audio, _first_chunk_ms = await synthesize(text)
    play_audio(audio)


def _notify_when_done(label: str, duration_seconds: int, started_at: datetime) -> None:
    time.sleep(duration_seconds)
    # Guard against a timer that was restarted (same label, new started_at) while this
    # thread was sleeping — without this check, the stale thread would still fire its
    # own notification on top of the new one.
    current = _timers.get(label)
    if current is None or current["started_at"] != started_at:
        return
    del _timers[label]
    asyncio.run(_speak(f"{label} timer's done."))


def _spoken_duration(total_seconds: int) -> str:
    """"23 minutes", "1 minute 30 seconds" -- not "23:00", which the model repeats verbatim
    and which reads aloud as a clock time rather than a length of time."""
    minutes, seconds = divmod(max(int(total_seconds), 0), 60)
    parts = []
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds or not parts:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return " ".join(parts)


def _match_label(label: str) -> str | None:
    return next(
        (existing for existing in _timers if fuzz.partial_ratio(label.lower(), existing.lower()) >= 60),
        None,
    )


class StartTimerArgs(BaseModel):
    label: str = Field(
        description="A short name for what this timer is for, e.g. 'cargo loading'. "
                    "Used to identify it later if the pilot asks how much time is left, "
                    "especially if more than one timer ends up running at once."
    )
    duration_seconds: int = Field(
        description="How long to wait, in total seconds — convert whatever the pilot "
                    "said into one number, e.g. '8 minutes 33 seconds' becomes 513."
    )


class StartTimerTool(UplinkTool):
    name: str = "start_timer"
    description: str = (
        "Start a countdown timer and speak a notification out loud once it finishes — "
        "call this when the pilot mentions a wait with a duration attached, e.g. 'the "
        "loading will take 8 minutes', 'set a timer for 5 minutes', or 'let me know "
        "when the cargo's done, it's about 8 minutes 33 seconds'. Needs a length of time — "
        "a money amount like an 'autoload fee of 19,350' is never a timer. "
        "Not specific to trade runs — usable for any timed wait the pilot mentions."
    )
    args_schema: type[BaseModel] = StartTimerArgs
    progress_label: str = "Starting a timer."

    async def _arun(self, label: str, duration_seconds: int, *args: Any, **kwargs: Any) -> Any:
        started_at = datetime.now(UTC)
        _timers[label] = {"started_at": started_at, "duration_seconds": duration_seconds}
        threading.Thread(
            target=_notify_when_done, args=(label, duration_seconds, started_at), daemon=True
        ).start()
        return f"Timer '{label}' set for {_spoken_duration(duration_seconds)}."


class CheckTimerArgs(BaseModel):
    label: str | None = Field(
        default=None,
        description="Which timer to check, if the pilot named one and more than one "
                    "timer might be running. Leave unset if there's only one, or the "
                    "pilot didn't specify."
    )


class CheckTimerTool(UplinkTool):
    name: str = "check_timer"
    description: str = (
        "Report how much time is left on a running timer — call this when the pilot "
        "asks something like 'how much longer', 'how much time is left', or 'is the "
        "timer done yet'."
    )
    args_schema: type[BaseModel] = CheckTimerArgs
    progress_label: str = "Checking the timer."

    async def _arun(self, label: str | None = None, *args: Any, **kwargs: Any) -> Any:
        if label:
            matched_label = _match_label(label)
            if matched_label is None:
                return f"No running timer found matching '{label}'."
        else:
            if len(_timers) > 1:
                names = ", ".join(_timers)
                return f"More than one timer is running — which one? ({names})"
            if not _timers:
                return "No timers are currently running."
            matched_label = next(iter(_timers))

        entry = _timers[matched_label]
        elapsed = (datetime.now(UTC) - entry["started_at"]).total_seconds()
        remaining = entry["duration_seconds"] - elapsed
        if remaining <= 0:
            return f"The '{matched_label}' timer has already finished."

        return f"{_spoken_duration(int(remaining))} remaining on the '{matched_label}' timer."


class CancelTimerArgs(BaseModel):
    label: str | None = Field(
        default=None,
        description="Which timer to cancel, if the pilot named one. Leave unset to cancel the "
                    "only running timer; with several running, the pilot must say which."
    )


class CancelTimerTool(UplinkTool):
    name: str = "cancel_timer"
    description: str = (
        "Cancel a running timer without it announcing — call this when the pilot says to "
        "cancel, stop, or forget a timer, or says a timer was set by mistake or is wrong."
    )
    args_schema: type[BaseModel] = CancelTimerArgs
    progress_label: str = "Cancelling the timer."

    async def _arun(self, label: str | None = None, *args: Any, **kwargs: Any) -> Any:
        if not _timers:
            return "No timers are currently running."
        if label:
            matched_label = _match_label(label)
            if matched_label is None:
                return f"No running timer found matching '{label}'."
        elif len(_timers) > 1:
            return f"More than one timer is running — which one? ({', '.join(_timers)})"
        else:
            matched_label = next(iter(_timers))
        # Removing the entry is the whole cancellation: _notify_when_done's sleeping thread
        # sees the label gone when it wakes and returns without speaking.
        del _timers[matched_label]
        return f"Cancelled the '{matched_label}' timer."
