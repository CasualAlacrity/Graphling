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
# own process, never Chainlit, so there's no reason for DB persistence: timers are
# short-lived (minutes) and losing one on a restart just means restating the duration.
_timers: dict[str, dict] = {}


async def _speak(text: str) -> None:
    audio = await synthesize(text)
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
        "autoload will take 8 minutes', 'set a timer for 5 minutes', or 'let me know "
        "when the cargo's done, it's about 8 minutes 33 seconds'. Not specific to trade "
        "runs — usable for any timed wait the pilot mentions."
    )
    args_schema: type[BaseModel] = StartTimerArgs
    progress_label: str = "Starting a timer."

    async def _arun(self, label: str, duration_seconds: int, *args: Any, **kwargs: Any) -> Any:
        started_at = datetime.now(UTC)
        _timers[label] = {"started_at": started_at, "duration_seconds": duration_seconds}
        threading.Thread(
            target=_notify_when_done, args=(label, duration_seconds, started_at), daemon=True
        ).start()
        minutes, seconds = divmod(duration_seconds, 60)
        return f"Timer '{label}' set for {minutes}:{seconds:02d}."


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
            matched_label = next(
                (existing for existing in _timers if fuzz.partial_ratio(label.lower(), existing.lower()) >= 60),
                None,
            )
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

        minutes, seconds = divmod(int(remaining), 60)
        return f"{minutes}:{seconds:02d} remaining on the '{matched_label}' timer."
