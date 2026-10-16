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


# A POST only because the id list can run to 161 terminals; it never calls UEX. JSON
# object keys are strings, so the client turns them back into ids.
@router.post("/routes-from-terminals/cached", response_model=dict[int, list[dict]])
async def get_cached_routes_from_terminals(terminal_ids: list[int], user: User = Depends(get_current_user)):
    return await uex_cache_service.get_cached_routes_from_terminals(terminal_ids)


# Superseded by the batch route above; kept while 0.2.6 and older clients still ask.
@router.get("/routes-from-terminal/{terminal_id}/cached", response_model=list[dict] | None)
async def get_cached_routes_from_terminal(terminal_id: int, user: User = Depends(get_current_user)):
    return await uex_cache_service.get_cached_routes_from_terminal(terminal_id)


@router.post("/routes-from-terminal/{terminal_id}/fetch", response_model=list[dict])
async def fetch_routes_from_terminal(terminal_id: int, user: User = Depends(get_current_user)):
    return await uex_cache_service.fetch_routes_from_terminal(terminal_id)


@router.get("/item-prices/{item_id}", response_model=list[dict])
async def get_item_prices(item_id: int, user: User = Depends(get_current_user)):
    return await uex_cache_service.get_item_price_rows(item_id)


@router.get("/vehicle-purchase-prices/{vehicle_id}", response_model=list[dict])
async def get_vehicle_purchase_prices(vehicle_id: int, user: User = Depends(get_current_user)):
    return await uex_cache_service.get_vehicle_purchase_price_rows(vehicle_id)


@router.get("/vehicle-rental-prices/{vehicle_id}", response_model=list[dict])
async def get_vehicle_rental_prices(vehicle_id: int, user: User = Depends(get_current_user)):
    return await uex_cache_service.get_vehicle_rental_price_rows(vehicle_id)


@router.get("/orbit-distances/{orbit_id}", response_model=list[dict])
async def get_orbit_distances(orbit_id: int, star_system_id: int, user: User = Depends(get_current_user)):
    return await uex_cache_service.get_orbit_distance_rows(orbit_id, star_system_id)
