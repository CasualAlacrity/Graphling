"""The LLM gateway's HTTP surface -- Ollama's native /api/chat, mounted under /llm so the
client's ChatOllama only needs base_url=<ALICE_API_URL>/llm (docs/llm-gateway.md). All
routing/fallback logic lives in server/llm_gateway_service.py."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from db.models import User
from server import llm_gateway_service, prompt_service
from server.config import settings
from server.dependencies import get_current_user
from server.limits import RateLimiter

router = APIRouter(prefix="/llm", tags=["llm"])

# History is already windowed client-side (HISTORY_TURNS); a real turn is well under
# 100 KB. This only stops a broken or hostile client from shipping megabytes per call.
MAX_BODY_BYTES = 512 * 1024

# Per pilot, a sliding minute (server/limits.py). A normal turn is 2-3 calls.
rate_limiter = RateLimiter(settings.llm_requests_per_minute, 60)


@router.post("/api/chat")
async def chat(request: Request, user: User = Depends(get_current_user)):
    retry_after = rate_limiter.retry_after_s(user.id)
    if retry_after is not None:
        raise HTTPException(status_code=429, detail="Too many requests -- slow down a moment.",
                            headers={"Retry-After": str(max(1, round(retry_after)))})
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request body too large")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Body is not valid JSON") from exc

    # Before routing, so whichever backend answers sees the real prompt text.
    try:
        payload = prompt_service.expand(payload)
    except prompt_service.UnknownPrompt as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        relay = await llm_gateway_service.open_chat(payload, user.id)
    except llm_gateway_service.UnknownRole as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except llm_gateway_service.NoBackendAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return StreamingResponse(relay.body, status_code=relay.status_code, media_type=relay.media_type)
