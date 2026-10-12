"""The TTS gateway's HTTP surface: POST /tts {"text": ...} -> audio bytes in ALICE's voice.
Routing and fallback live in server/tts_service.py."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from db.models import User
from server import tts_service
from server.dependencies import get_current_user

router = APIRouter(tags=["tts"])

# A spoken reply is a few hundred characters. This stops a broken client from spending
# a whole ElevenLabs month (or minutes of GPU) on one request.
MAX_CHARS = 2000


class SpeakRequest(BaseModel):
    text: str


@router.post("/tts")
async def speak(request: SpeakRequest, user: User = Depends(get_current_user)):
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    if len(text) > MAX_CHARS:
        raise HTTPException(status_code=413, detail=f"text is longer than {MAX_CHARS} characters")

    try:
        speech = await tts_service.speak(text, user.id)
    except tts_service.NoBackendAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(content=speech.audio, media_type=speech.media_type,
                    headers={"X-Alice-TTS-Backend": speech.backend})
