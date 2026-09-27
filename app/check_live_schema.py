"""Live schema-drift check against the real UEX Corp and Star Citizen Wiki APIs.

Not part of the pytest suite — it needs real network access and real API keys, and
correctness there is the offline fake-client harness's job (docs/todo.md Phase 3).
This is the other half: a fast tripwire for "did UEX or the wiki quietly rename or
drop a field one of our Pydantic models depends on," independent of whether the
prices themselves look reasonable that day.

Not scheduled anywhere yet. Built now, on purpose, ahead of needing it — see
docs/todo.md's Phase 3 section for why (active development already surfaces drift
by hand; this earns its keep once things are stable enough to go quiet). The
intended shape once it is scheduled: run weekly, and on any drift reported here, the
scheduled agent drafts a fix for review — never an unsupervised auto-merge.

Run from the repo root:
    cd app && ../.venv/bin/python check_live_schema.py
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.reference_cache import UexReferenceCache
from tools.uexcorp.trade_data import UEXTradeData, UEXTradeRoute
from tools.uexcorp.vehicle_rental_tool import VehicleRentalData

# Old, game-launch-era entities, picked specifically because they're unlikely to ever
# be removed outright the way a brand-new ship/commodity might be — a failure here
# should mean "UEX/the wiki changed shape," not "this thing just doesn't exist anymore."
KNOWN_COMMODITY = "Laranite"
KNOWN_VEHICLE = "Cutlass Black"
KNOWN_SHIP = "Freelancer"


def _fail(problems: list[str], label: str, exc: Exception) -> None:
    problems.append(f"{label}: {exc!r}")


async def check_uex_reference_cache(client: UEXCorpClient) -> tuple[list[str], UexReferenceCache | None]:
    """build_uex_cache() already runs every reference-endpoint row through its own
    model_validate — a drift raises right there, so this just needs to call it for
    real and sanity-check nothing came back suspiciously empty."""
    problems: list[str] = []
    try:
        cache = await client.build_uex_cache()
    except Exception as exc:
        _fail(problems, "build_uex_cache()", exc)
        return problems, None

    for label, rows in [
        ("commodities", cache.commodities), ("star_systems", cache.star_systems),
        ("orbits", cache.orbits), ("terminals", cache.terminals), ("moons", cache.moons),
        ("items", cache.items), ("vehicles", cache.vehicles),
        ("refinery_yields", cache.refinery_yields), ("poi", cache.poi),
        ("commodity_statuses", cache.commodity_statuses),
    ]:
        if not rows:
            problems.append(f"{label}: parsed cleanly but came back empty — endpoint may have changed shape")
    return problems, cache


async def check_commodity_prices(client: UEXCorpClient, cache: UexReferenceCache) -> list[str]:
    problems: list[str] = []
    match = next((c for c in cache.commodities if c.name == KNOWN_COMMODITY), None)
    if match is None:
        problems.append(f"{KNOWN_COMMODITY} not found in the reference cache — can't check live prices")
        return problems
    try:
        rows = await client.get_commodity_prices(match.id)
        [UEXTradeData.model_validate(row) for row in rows]
        if not rows:
            problems.append(f"get_commodity_prices({KNOWN_COMMODITY}): parsed but returned no rows")
    except Exception as exc:
        _fail(problems, f"get_commodity_prices({KNOWN_COMMODITY})", exc)
    return problems


async def check_terminal_prices(client: UEXCorpClient, cache: UexReferenceCache) -> list[str]:
    problems: list[str] = []
    if not cache.terminals:
        return problems  # already reported by the reference-cache check
    terminal = cache.terminals[0]
    try:
        rows = await client.get_terminal_prices(terminal.id)
        [UEXTradeData.model_validate(row) for row in rows]
        # An empty result is a legitimate outcome for some terminals (nothing tradeable
        # right now), so it isn't flagged the way a commodity/vehicle-keyed empty is.
    except Exception as exc:
        _fail(problems, f"get_terminal_prices({terminal.name})", exc)
    return problems


async def check_item_prices(client: UEXCorpClient, cache: UexReferenceCache) -> list[str]:
    problems: list[str] = []
    if not cache.items:
        return problems  # already reported by the reference-cache check
    item = cache.items[0]
    try:
        rows = await client.get_item_prices(item.id)
        [UEXTradeData.model_validate(row) for row in rows]
    except Exception as exc:
        _fail(problems, f"get_item_prices({item.name})", exc)
    return problems


async def check_commodity_routes(client: UEXCorpClient, cache: UexReferenceCache) -> list[str]:
    problems: list[str] = []
    match = next((c for c in cache.commodities if c.name == KNOWN_COMMODITY), None)
    if match is None:
        return problems  # already reported by check_commodity_prices
    try:
        rows = await client.get_commodity_routes(commodity_id=match.id)
        [UEXTradeRoute.model_validate(row) for row in rows]
        if not rows:
            problems.append(f"get_commodity_routes({KNOWN_COMMODITY}): parsed but returned no rows")
    except Exception as exc:
        _fail(problems, f"get_commodity_routes({KNOWN_COMMODITY})", exc)
    return problems


async def check_vehicle_prices(client: UEXCorpClient, cache: UexReferenceCache) -> list[str]:
    problems: list[str] = []
    match = next((v for v in cache.vehicles if v.name == KNOWN_VEHICLE), None)
    if match is None:
        problems.append(f"{KNOWN_VEHICLE} not found in the reference cache — can't check live prices")
        return problems

    try:
        rows = await client.get_vehicle_purchase_prices(match.id)
        [UEXTradeData.model_validate(row) for row in rows]
        if not rows:
            problems.append(f"get_vehicle_purchase_prices({KNOWN_VEHICLE}): parsed but returned no rows")
    except Exception as exc:
        _fail(problems, f"get_vehicle_purchase_prices({KNOWN_VEHICLE})", exc)

    try:
        rows = await client.get_vehicle_rental_prices(match.id)
        [VehicleRentalData.model_validate(row) for row in rows]
        # Not every ship is rentable — an empty result here is legitimate, not flagged.
    except Exception as exc:
        _fail(problems, f"get_vehicle_rental_prices({KNOWN_VEHICLE})", exc)

    return problems


async def check_wiki(scw_client: StarCitizenWikiClient) -> list[str]:
    problems: list[str] = []
    try:
        ship_speed = await scw_client.fetch_ship_speed_from_wiki(KNOWN_SHIP)
        if ship_speed is None:
            problems.append(f"fetch_ship_speed_from_wiki({KNOWN_SHIP}): parsed but found no match")
    except Exception as exc:
        _fail(problems, f"fetch_ship_speed_from_wiki({KNOWN_SHIP})", exc)

    try:
        locations = await scw_client.fetch_locations_from_wiki()
        if not locations:
            problems.append("fetch_locations_from_wiki(): parsed but returned no rows")
    except Exception as exc:
        _fail(problems, "fetch_locations_from_wiki()", exc)

    return problems


async def main() -> int:
    load_dotenv()
    uex_client = UEXCorpClient(
        api_key=os.getenv("UEXCORP_API_KEY"),
        bearer_token=os.getenv("UEXCORP_BEARER_TOKEN"),
    )
    scw_client = StarCitizenWikiClient()

    all_problems: list[str] = []

    print("Checking UEX reference cache (commodities, terminals, vehicles, ...)...")
    reference_problems, cache = await check_uex_reference_cache(uex_client)
    all_problems += reference_problems

    if cache is not None:
        print(f"Checking live commodity prices ({KNOWN_COMMODITY})...")
        all_problems += await check_commodity_prices(uex_client, cache)

        print("Checking live terminal prices...")
        all_problems += await check_terminal_prices(uex_client, cache)

        print("Checking live item prices...")
        all_problems += await check_item_prices(uex_client, cache)

        print(f"Checking live commodity routes ({KNOWN_COMMODITY})...")
        all_problems += await check_commodity_routes(uex_client, cache)

        print(f"Checking live vehicle prices ({KNOWN_VEHICLE})...")
        all_problems += await check_vehicle_prices(uex_client, cache)
    else:
        print("Skipping price/route checks — the reference cache itself failed to build.")

    print(f"Checking wiki ship speed/locations ({KNOWN_SHIP})...")
    all_problems += await check_wiki(scw_client)

    print()
    if all_problems:
        print(f"SCHEMA DRIFT DETECTED — {len(all_problems)} issue(s):")
        for problem in all_problems:
            print(f"  - {problem}")
        return 1

    print("All UEX/wiki endpoints still parse cleanly. No drift detected.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
