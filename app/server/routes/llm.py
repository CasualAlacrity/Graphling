"""The LLM gateway's HTTP surface -- Ollama's native /api/chat, mounted under /llm so the
client's ChatOllama only needs base_url=<ALICE_API_URL>/llm (docs/llm-gateway.md). All
routing/fallback logic lives in server/llm_gateway_service.py."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from db.models import User
from server import llm_gateway_service
from server.dependencies import get_current_user

router = APIRouter(prefix="/llm", tags=["llm"])

# History is already windowed client-side (HISTORY_TURNS); a real turn is well under
# 100 KB. This only stops a broken or hostile client from shipping megabytes per call.
MAX_BODY_BYTES = 512 * 1024


@router.post("/api/chat")
async def chat(request: Request, user: User = Depends(get_current_user)):
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request body too large")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Body is not valid JSON") from exc

    try:
        relay = await llm_gateway_service.open_chat(payload, user.id)
    except llm_gateway_service.UnknownRole as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except llm_gateway_service.NoBackendAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return StreamingResponse(relay.body, status_code=relay.status_code, media_type=relay.media_type)
