import os

from rapidfuzz import fuzz

from db import trade_run_store
from db.models import LegType, TradeLeg, TradeRun
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import LOW_CONFIDENCE_MAX, match_by_name_or_code_with_score

uex_client = UEXCorpClient(
    api_key=os.getenv("UEXCORP_API_KEY"),
    bearer_token=os.getenv("UEXCORP_BEARER_TOKEN"),
)


def _describe_run(run: TradeRun) -> str:
    # Set, not list — an ordinary run's acquisition and sale legs share one commodity
    # name, and repeating it ("Copper, Copper") would read like a typo, not emphasis.
    commodities = sorted({leg.commodity_name for leg in run.legs})
    commodity_part = " and ".join(commodities) if commodities else "no commodity yet"
    ship_part = f"the {run.ship}" if run.ship else "an unassigned ship"
    return f"{ship_part} ({commodity_part})"


def _describe_leg(run: TradeRun, leg: TradeLeg) -> str:
    ship_part = f"in the {run.ship}" if run.ship else "on an unassigned ship"
    return f"{leg.commodity_name} at {leg.terminal_name} {ship_part}"


class AmbiguousLegError(Exception):
    """candidates pairs each matching leg with its own run — not just the leg — because
    a bare leg (commodity + terminal) isn't always enough to tell two runs apart on its
    own (two concurrent copper runs from the same terminal, different ships), and the
    message this builds is what every catching tool relays to the pilot verbatim."""

    def __init__(self, candidates: list[tuple[TradeRun, TradeLeg]]):
        self.candidates = candidates
        descriptions = "; ".join(_describe_leg(run, leg) for run, leg in candidates)
        super().__init__(
            f"You've got {len(candidates)} legs matching that, and I'm not sure which one "
            f"you mean — {descriptions}. Which one?"
        )


class AmbiguousRunError(Exception):
    def __init__(self, candidates: list[TradeRun]):
        self.candidates = candidates
        descriptions = "; ".join(_describe_run(run) for run in candidates)
        super().__init__(
            f"You've got {len(candidates)} active runs, and I'm not sure which one you "
            f"mean — {descriptions}. Which one?"
        )


def _confident_match(query, items):
    # A low-confidence canonicalization here is treated the same as no match at all,
    # rather than trusted — the caller falls back to matching on leg_type alone, which
    # is still fully safe (more than one candidate raises AmbiguousLegError rather than
    # silently picking one). Better than confidently filtering against a wrong
    # commodity/terminal a garbled hint happened to score just above the cutoff for.
    if query is None:
        return None
    matched = match_by_name_or_code_with_score(query, items)
    if matched is None:
        return None
    item, score = matched
    return item if score >= LOW_CONFIDENCE_MAX else None


async def resolve_leg(
        leg_type: LegType | None = None, commodity: str | None = None, terminal: str | None = None
) -> TradeLeg:
    cache = await uex_client.get_uex_cache()
    current_runs = await trade_run_store.get_in_progress_runs()

    matched_commodity = _confident_match(commodity, cache.commodities)
    matched_terminal = _confident_match(terminal, cache.terminals)

    # With no commodity/terminal hint given at all, a leg's leg_type match is treated
    # as sufficient on its own — the pilot referring to "the cargo" with no name usually
    # means there's only one sensible answer, not that they want to be asked to repeat
    # the commodity every time. Still fully safe: with more than one candidate leg, this
    # falls through to the len(matches) > 1 branch and raises AmbiguousLegError.
    no_hint_given = matched_commodity is None and matched_terminal is None

    matches = []
    for run in current_runs:
        leg = trade_run_store.current_leg(run)
        if leg and (leg_type is None or leg.leg_type == leg_type):
            if (no_hint_given
                    or (matched_commodity and matched_commodity.name == leg.commodity_name)
                    or (matched_terminal and matched_terminal.name == leg.terminal_name)):
                matches.append((run, leg))

    if len(matches) > 1:
        raise AmbiguousLegError(matches)
    elif len(matches) < 1:
        raise ValueError(f"No run with leg for {leg_type} with {commodity} or {terminal}")

    return matches[0][1] if matches else None


async def resolve_run(ship: str | None = None) -> TradeRun:
    """Resolves to a single active TradeRun — no UEX lookup involved, since ship here is
    just matched against each run's own stored ship string, not a reference catalog. With
    no hint, or only one active run regardless of hint, that run is the answer; with more
    than one candidate this raises AmbiguousRunError instead of guessing."""
    current_runs = await trade_run_store.get_in_progress_runs()

    if ship:
        matches = [run for run in current_runs if run.ship and fuzz.partial_ratio(ship.lower(), run.ship.lower()) >= 60]
    else:
        matches = current_runs

    if len(matches) > 1:
        raise AmbiguousRunError(matches)
    elif len(matches) < 1:
        raise ValueError(f"No active trade run found for ship '{ship}'" if ship else "No active trade runs")

    return matches[0]
