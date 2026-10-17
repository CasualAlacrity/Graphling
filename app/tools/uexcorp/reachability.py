"""Which commodities each terminal offers and accepts, derived from the prefetched
routes-by-origin rows — so the overlay can narrow its filters locally instead of asking the
server about each terminal as the pilot picks it (selecting a region froze the overlay for
seconds doing exactly that).

Built from route rows rather than price rows because a route is the thing a search can
actually return: a terminal "offers" a commodity only if it has stock to sell on some
route, and "accepts" one only if it still has room to buy. The same two rules decide which
search results are hidden (source sold out, destination full), so the filters and the
results can't disagree.

Pure functions with no UEX/DB access, so the server and the client share them.
"""
from datetime import UTC, datetime

from pydantic import BaseModel, Field

# UEX's commodities_status tiers: 1 is "Out of Stock" on the buy side (nothing to take),
# 7 is "Maximum Inventory" on the sell side (nothing more wanted).
STATUS_OUT_OF_STOCK = 1
STATUS_FULL = 7


def source_sold_out(status: int, scu: float) -> bool:
    return status == STATUS_OUT_OF_STOCK or scu <= 0


def destination_full(status: int, scu: float) -> bool:
    return status == STATUS_FULL or scu <= 0


class Reachability(BaseModel):
    # terminal id -> commodity ids. JSON keys arrive as strings; pydantic turns them back.
    offers: dict[int, list[int]]
    accepts: dict[int, list[int]]
    built_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def build_reachability(rows_by_origin: dict[int, list[dict]]) -> Reachability:
    offers: dict[int, set[int]] = {}
    accepts: dict[int, set[int]] = {}

    for origin_id, rows in rows_by_origin.items():
        # An origin with fresh rows but nothing in stock is known-and-empty, which is not
        # the same as unknown: the client only skips narrowing for terminals it has no row for.
        offers.setdefault(origin_id, set())
        for row in rows:
            commodity_id = row["id_commodity"]
            if not source_sold_out(row["status_origin"], row["scu_origin"]):
                offers[origin_id].add(commodity_id)
            if not destination_full(row["status_destination"], row["scu_destination"]):
                accepts.setdefault(row["id_terminal_destination"], set()).add(commodity_id)

    return Reachability(
        offers={terminal_id: sorted(ids) for terminal_id, ids in offers.items()},
        accepts={terminal_id: sorted(ids) for terminal_id, ids in accepts.items()},
    )
