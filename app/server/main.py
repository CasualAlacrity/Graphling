from fastapi import FastAPI

from server.routes.auth import router as auth_router
from server.routes.ledger import router as ledger_router

app = FastAPI(title="ALICE Ledger API")

app.include_router(auth_router)
app.include_router(ledger_router)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
