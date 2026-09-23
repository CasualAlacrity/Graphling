from fastapi import APIRouter, Depends

from db.models import User
from server import wiki_cache_service
from server.dependencies import get_current_user
from tools.starcitizenwiki.models import LocationPosition, ShipSpeed

router = APIRouter(prefix="/wiki-cache", tags=["wiki-cache"])


@router.get("/ship-speed", response_model=ShipSpeed | None)
async def get_ship_speed(ship_name: str, user: User = Depends(get_current_user)):
    return await wiki_cache_service.get_ship_speed(ship_name)


@router.get("/locations", response_model=list[LocationPosition])
async def get_locations(user: User = Depends(get_current_user)):
    return await wiki_cache_service.get_locations()
