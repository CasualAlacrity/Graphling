import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from server import llm_gateway_service, prompt_service
from server.routes.auth import router as auth_router
from server.routes.ledger import router as ledger_router
from server.routes.llm import router as llm_router
from server.routes.tts import router as tts_router
from server.routes.uex_cache import router as uex_cache_router
from server.routes.wiki_cache import router as wiki_cache_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One probe before serving, so the digest pin is enforced from the first request
    # rather than up to PROBE_INTERVAL_S later, then keep probing in the background.
    await llm_gateway_service.probe_once()
    probe_task = asyncio.create_task(llm_gateway_service.probe_forever())
    # Prompts before the first request too: the gateway can't expand a placeholder
    # until they're loaded (Hub, else the snapshot baked into the image).
    await prompt_service.refresh()
    prompt_task = asyncio.create_task(prompt_service.refresh_forever())
    yield
    for task in (probe_task, prompt_task):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="ALICE Ledger API", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(ledger_router)
app.include_router(llm_router)
app.include_router(tts_router)
app.include_router(uex_cache_router)
app.include_router(wiki_cache_router)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
