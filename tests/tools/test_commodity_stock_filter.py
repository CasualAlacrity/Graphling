"""Covers commodity_price_lookup skipping out-of-stock terminals for cheapest_to_buy
(2026-10-06). Found by the agent eval harness: the cheapest-priced terminal was out of
stock (UEX status_buy 1) and ALICE recommended it anyway, because _lookup picked the
minimum price without ever looking at stock.

Runs the real tools against harness/world.py's fixed data (Agricium: Baijini Point is
cheaper but empty, Port Tressler is dearer and in stock) via harness/fakes.py.
"""
import copy

from harness import world
from harness.fakes import install_fakes
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.commodity_tool import CommodityPriceTool
from tools.uexcorp.vehicle_purchase_tool import VehiclePurchaseTool


def _client():
    return UEXCorpClient(api_key="fake", bearer_token="fake")


async def test_cheapest_to_buy_skips_an_out_of_stock_terminal():
    with install_fakes():
        result = await CommodityPriceTool(client=_client())._arun(commodity="Agricium")

    assert result["cheapest_to_buy"]["terminal_id"] == world.PORT_TRESSLER.id
    assert result["cheapest_to_buy"]["price_you_pay_to_acquire"] == 26.10


async def test_the_out_of_stock_terminal_stays_in_all_results():
    # Filtered out of the recommendation, not out of the data -- "what about Baijini?"
    # has to be answerable.
    with install_fakes():
        result = await CommodityPriceTool(client=_client())._arun(commodity="Agricium")

    by_id = {}
    for row in result["all_results"]:
        by_id[row["terminal_id"]] = row
    assert by_id[world.BAIJINI_POINT.id]["status_buy"] == 1
    assert by_id[world.BAIJINI_POINT.id]["in_stock"] is False
    assert by_id[world.PORT_TRESSLER.id]["in_stock"] is True


async def test_in_stock_commodity_is_unaffected():
    with install_fakes():
        result = await CommodityPriceTool(client=_client())._arun(commodity="Laranite")

    assert result["cheapest_to_buy"]["terminal_id"] == world.BAIJINI_POINT.id


async def test_cheapest_to_buy_is_none_when_everything_is_out_of_stock(monkeypatch):
    rows = copy.deepcopy(world.COMMODITY_PRICE_ROWS)
    for row in rows[world.AGRICIUM.id]:
        row["status_buy"] = 1
    monkeypatch.setattr(world, "COMMODITY_PRICE_ROWS", rows)

    with install_fakes():
        result = await CommodityPriceTool(client=_client())._arun(commodity="Agricium")

    assert result["cheapest_to_buy"] is None
    assert len(result["all_results"]) == 2


async def test_vehicle_lookup_is_not_filtered_by_status():
    # Vehicle rows have no inventory status; the fixture's status_buy 1 there must not
    # knock a terminal out. Guards the filter staying opt-in.
    with install_fakes():
        result = await VehiclePurchaseTool(client=_client())._arun(vehicle="Cutlass Black")

    assert result["cheapest_to_buy"]["terminal_id"] == world.BAIJINI_POINT.id


async def test_vehicle_rows_get_no_in_stock_field():
    with install_fakes():
        result = await VehiclePurchaseTool(client=_client())._arun(vehicle="Cutlass Black")

    assert all("in_stock" not in row for row in result["all_results"])


async def test_the_model_gets_only_the_location_phrase_to_name_a_place():
    """Anything else it could build its own place description from is left out: given the
    raw fields, it mixed them into the phrase (2026-10-16 response matrix)."""
    with install_fakes():
        result = await CommodityPriceTool(client=_client())._arun(commodity="Laranite")

    row = result["cheapest_to_buy"]
    for field in ("terminal_name", "star_system_name", "orbit_name", "moon_name", "planet_name"):
        assert field not in row
    assert row["location"]
