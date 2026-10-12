"""The TTS gateway's HTTP surface: POST /tts {"text": ...} -> audio bytes in ALICE's voice.
Routing and fallback live in server/tts_service.py."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from db.models import User
from server import tts_service
from server.config import settings
from server.dependencies import get_current_user
from server.limits import DailyCounter, RateLimiter

router = APIRouter(tags=["tts"])

# A spoken reply is a few hundred characters. This stops a broken client from spending
# a whole ElevenLabs month (or minutes of GPU) on one request.
MAX_CHARS = 2000

# Runaway guards per pilot (server/limits.py): one spoken reply per turn, and a daily
# character total far above a long session -- the expensive failure is a loop feeding
# ElevenLabs, if it's ever the fallback.
rate_limiter = RateLimiter(settings.tts_requests_per_minute, 60)
daily_chars = DailyCounter()


class SpeakRequest(BaseModel):
    text: str


@router.post("/tts")
async def speak(request: SpeakRequest, user: User = Depends(get_current_user)):
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    if len(text) > MAX_CHARS:
        raise HTTPException(status_code=413, detail=f"text is longer than {MAX_CHARS} characters")
    retry_after = rate_limiter.retry_after_s(user.id)
    if retry_after is not None:
        raise HTTPException(status_code=429, detail="Too many speech requests -- slow down a moment.",
                            headers={"Retry-After": str(max(1, round(retry_after)))})
    if daily_chars.total(user.id) + len(text) > settings.tts_daily_chars_per_user:
        raise HTTPException(status_code=429, detail="Today's speech limit is reached; ALICE will talk again tomorrow.")
    daily_chars.add(user.id, len(text))

    try:
        speech = await tts_service.speak(text, user.id)
    except tts_service.NoBackendAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(content=speech.audio, media_type=speech.media_type,
                    headers={"X-Alice-TTS-Backend": speech.backend})
