import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server import llm_gateway_service, prompt_service, topic_decision_service, uex_route_prefetch
from server.routes.auth import router as auth_router
from server.routes.ledger import router as ledger_router
from server.routes.llm import router as llm_router
from server.routes.tts import router as tts_router
from server.routes.uex_cache import router as uex_cache_router
from server.routes.wiki_cache import router as wiki_cache_router
from tools.uexcorp.client import UexCallBudgetExhausted


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
    prefetch_task = asyncio.create_task(uex_route_prefetch.prefetch_forever())
    topic_warm_task = asyncio.create_task(topic_decision_service.warm_forever())
    yield
    for task in (probe_task, prompt_task, prefetch_task, topic_warm_task):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="ALICE Ledger API", lifespan=lifespan)


# Any cache route asked for something UEX would have to supply, with today's UEX calls
# spent and nothing cached to fall back on (server/uex_budget.py, price_cache.py).
@app.exception_handler(UexCallBudgetExhausted)
async def uex_budget_exhausted(request: Request, exc: UexCallBudgetExhausted) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": f"UEX data unavailable until tomorrow: {exc}"})


app.include_router(auth_router)
app.include_router(ledger_router)
app.include_router(llm_router)
app.include_router(tts_router)
app.include_router(uex_cache_router)
app.include_router(wiki_cache_router)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
