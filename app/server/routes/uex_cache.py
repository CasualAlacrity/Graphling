from fastapi import APIRouter, Depends

from db.models import User
from server import uex_cache_service
from server.dependencies import get_current_user
from tools.uexcorp.reference_cache import UexReferenceCache

router = APIRouter(prefix="/uex-cache", tags=["uex-cache"])


@router.get("/reference", response_model=UexReferenceCache)
async def get_reference(user: User = Depends(get_current_user)):
    return await uex_cache_service.get_reference_cache()


@router.get("/commodity-prices/{commodity_id}", response_model=list[dict])
async def get_commodity_prices(commodity_id: int, user: User = Depends(get_current_user)):
    return await uex_cache_service.get_commodity_price_rows(commodity_id)


@router.get("/terminal-prices/{terminal_id}", response_model=list[dict])
async def get_terminal_prices(terminal_id: int, user: User = Depends(get_current_user)):
    return await uex_cache_service.get_terminal_price_rows(terminal_id)


@router.get("/commodity-routes/{commodity_id}", response_model=list[dict])
async def get_commodity_routes(commodity_id: int, user: User = Depends(get_current_user)):
    return await uex_cache_service.get_commodity_route_rows(commodity_id)


@router.get("/routes-from-terminal/{terminal_id}/cached", response_model=list[dict] | None)
async def get_cached_routes_from_terminal(terminal_id: int, user: User = Depends(get_current_user)):
    return await uex_cache_service.get_cached_routes_from_terminal(terminal_id)


@router.post("/routes-from-terminal/{terminal_id}/fetch", response_model=list[dict])
async def fetch_routes_from_terminal(terminal_id: int, user: User = Depends(get_current_user)):
    return await uex_cache_service.fetch_routes_from_terminal(terminal_id)
