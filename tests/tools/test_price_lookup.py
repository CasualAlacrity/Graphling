"""Covers price_lookup (tools/uexcorp/price_lookup_tool.py) and the cross-catalogue
resolution under it (catalog_resolution.py). The stock-filter cases come from the old
commodity lookup's tests (2026-10-06); the resolution cases from the response matrix
(Hull A and FS-9 every run, "Cat" never resolving, 2026-10-17)."""
import copy
from types import SimpleNamespace

import pytest

from harness import world
from harness.fakes import install_fakes
from tools.uexcorp import catalog_resolution
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.price_lookup_tool import PriceLookupTool
from tools.uexcorp.reference_cache import CachedCommodity, CachedItem, CachedVehicle


def _tool():
    return PriceLookupTool(client=UEXCorpClient(api_key="fake", bearer_token="fake"))


# ---- price_lookup against the harness world -----------------------------------------

async def test_the_cheapest_place_to_buy_skips_an_out_of_stock_terminal():
    with install_fakes():
        result = await _tool()._arun(what="Agricium", action="buy")

    assert result["best"]["terminal_id"] == world.PORT_TRESSLER.id
    assert result["best"]["price_you_pay_to_acquire"] == 26.10
    assert result["price_unit"] == "aUEC per SCU"


async def test_a_buy_answer_carries_only_the_buy_price():
    with install_fakes():
        result = await _tool()._arun(what="Laranite", action="buy")

    assert result["best"]["terminal_id"] == world.BAIJINI_POINT.id
    assert "price_you_receive_when_selling" not in result["best"]
    assert "location" in result["best"]


async def test_nothing_in_stock_says_so(monkeypatch):
    rows = copy.deepcopy(world.COMMODITY_PRICE_ROWS)
    for row in rows[world.AGRICIUM.id]:
        row["status_buy"] = 1
    monkeypatch.setattr(world, "COMMODITY_PRICE_ROWS", rows)

    with install_fakes():
        result = await _tool()._arun(what="Agricium", action="buy")

    assert isinstance(result, str)
    assert "Agricium" in result


async def test_a_ship_is_bought_not_sold():
    with install_fakes():
        bought = await _tool()._arun(what="Cutlass Black", action="buy")
        sold = await _tool()._arun(what="Cutlass Black", action="sell")

    assert bought["kind"] == "ship"
    assert bought["price_unit"] == "aUEC"
    # Ships have no in-game resale market, so a sale finds nothing to sell.
    assert isinstance(sold, str)


# ---- resolution across catalogues ------------------------------------------------------

def _cache():
    return SimpleNamespace(
        commodities=[CachedCommodity(id=1, name="Laranite", code="LARA", id_parent=0, is_raw=False,
                                     is_refined=True, ids_star_systems=[], ids_planets=[], ids_moons=[],
                                     ids_orbits=[], ids_poi=[], is_buyable=1),
                     CachedCommodity(id=2, name="Quantainium", code="QUAN", id_parent=0, is_raw=False,
                                     is_refined=True, ids_star_systems=[], ids_planets=[], ids_moons=[],
                                     ids_orbits=[], ids_poi=[], is_buyable=0)],
        items=[CachedItem(id=10 + n, name=name, slug=name.lower()) for n, name in enumerate([
            "Hull A Dusk Livery", "Hull A Empyrean Livery", "Hull A Horizon Livery",
            "FS-9 LMG", 'FS-9 "Finish Line" LMG', 'FS-9 "Stoneface" LMG', "FS-9 Magazine (75 Cap)",
            "Pyro RYT Multi-Tool",
        ])],
        vehicles=[CachedVehicle(id=100, name="Hull A", name_full="MISC Hull A", scu=64, is_concept=0),
                  CachedVehicle(id=101, name="Caterpillar", name_full="Drake Caterpillar", scu=576, is_concept=0)],
    )


def _sold(*names, action="buy"):
    """A fetch_rows where only these names have a terminal dealing in them for `action`."""
    key = {"buy": "price_buy", "sell": "price_sell", "rent": "price_rent"}[action]

    async def fetch(candidate):
        if candidate.name in names:
            return [{key: 1000, "id_terminal": 1, "terminal_name": "Somewhere"}]
        return []
    return fetch


async def test_hull_a_is_the_ship_not_a_livery_nobody_sells():
    resolution = await catalog_resolution.resolve("Hull A", "buy", _cache(), _sold("Hull A"))

    assert resolution.chosen.kind == "ship"
    assert resolution.chosen.name == "Hull A"
    assert resolution.understood_as == "the Hull A ship"


async def test_fs9_means_the_lmg_not_its_skins_or_magazine():
    everything = _sold("FS-9 LMG", 'FS-9 "Finish Line" LMG', 'FS-9 "Stoneface" LMG', "FS-9 Magazine (75 Cap)",
                       action="sell")
    resolution = await catalog_resolution.resolve("FS-9", "sell", _cache(), everything)

    assert resolution.chosen.name == "FS-9 LMG"


async def test_cat_rents_a_caterpillar():
    resolution = await catalog_resolution.resolve("Cat", "rent", _cache(), _sold("Caterpillar", action="rent"))

    assert resolution.chosen.name == "Caterpillar"


async def test_slang_resolves_commodities_too():
    resolution = await catalog_resolution.resolve("quant", "sell", _cache(), _sold("Quantainium", action="sell"))

    assert resolution.chosen.name == "Quantainium"


async def test_if_the_named_thing_is_sold_nowhere_that_is_the_answer():
    resolution = await catalog_resolution.resolve("Hull A", "buy", _cache(), _sold())

    assert resolution.chosen is None
    assert resolution.message == "No terminal currently sells the Hull A ship."


async def test_a_genuine_tie_asks_with_a_few_options():
    ties = _sold("Hull A Dusk Livery", "Hull A Empyrean Livery", "Hull A Horizon Livery")
    resolution = await catalog_resolution.resolve("Hull A Livery", "buy", _cache(), ties)

    assert resolution.chosen is None
    assert resolution.message.endswith("Which one?")
    options = resolution.message.split("could be ")[1]
    assert options.count("Livery") <= catalog_resolution.MAX_OPTIONS


@pytest.mark.parametrize(("action", "kinds"), [
    ("buy", {"commodity", "item", "ship"}),
    ("sell", {"commodity", "item"}),
    ("rent", {"ship"}),
])
def test_each_action_only_looks_where_it_can(action, kinds):
    assert set(catalog_resolution._KINDS_FOR_ACTION[action]) == kinds



async def test_a_loose_fuzzy_match_is_never_answered_about():
    """'Cheapest size 3 quantum drive' fuzzy-matched Quantainium and was told nobody sells it."""
    resolution = await catalog_resolution.resolve("size 3 quantum drive", "buy", _cache(), _sold())

    assert resolution.chosen is None
    assert "Quantainium" not in resolution.message
    assert resolution.message.startswith("Couldn't find anything matching")


async def test_when_the_meant_thing_has_no_buyer_say_so_instead_of_switching():
    """Only the magazine has a buyer; the pilot meant the LMG."""
    only_the_magazine = _sold("FS-9 Magazine (75 Cap)", action="sell")
    resolution = await catalog_resolution.resolve("FS-9", "sell", _cache(), only_the_magazine)

    assert resolution.chosen is None
    assert resolution.message == "No terminal currently buys FS-9 LMG."
