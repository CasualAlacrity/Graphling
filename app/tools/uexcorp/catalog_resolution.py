"""Which thing does the pilot mean? Resolves a spoken name against commodities, items and
ships at once, for a price question with an action (buy, sell, rent), and either settles
on one or says which few it could be. Used by price_lookup (price_lookup_tool.py).

Why one search across catalogues: with a tool per catalogue, the model had to decide what
kind of thing a "Hull A" is before anything resolved the name, and it picked item lookup
every time (5 of 5 baseline runs), because five liveries are named "Hull A ... Livery".
None of those liveries is sold anywhere; the ship is. "FS-9" listed a magazine and four
skins of the LMG when the pilot meant the LMG.

How a candidate is ranked:
1. Name match: an exact name (100), a name containing every word said (90), else the
   fuzzy score. Pilot slang goes through vocabulary/aliases.py first, with ships under
   their own vocabulary ("cat" is a Caterpillar).
2. Variants rank below the thing they vary: liveries, paints and named editions
   ('FS-9 "Stoneface" LMG') lose VARIANT_PENALTY, accessories like magazines
   ACCESSORY_PENALTY. Not excluded: some liveries are sold in game, and a pilot can name one.
3. Only what the action allows: ships can't be sold in game, only vehicles can be rented,
   and a candidate no terminal deals in for this action drops out (the Hull A liveries
   have no sellers). If the best name match has no terminal at all, that is the answer
   ("nothing sells it"), rather than quietly switching to something else.
The winner must lead the next candidate by CONFIDENT_MARGIN; otherwise ALICE asks, naming
at most MAX_OPTIONS candidates.
"""
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from tools.uexcorp.matching import LOW_CONFIDENCE_MAX, _contained_matches, _exact_name_matches, _top_matches
from vocabulary.aliases import canonicalize

COMMODITY = "commodity"
ITEM = "item"
VEHICLE = "ship"

BUY = "buy"
SELL = "sell"
RENT = "rent"

VARIANT_PENALTY = 20
ACCESSORY_PENALTY = 15
CONFIDENT_MARGIN = 10
MAX_OPTIONS = 3
# How many top candidates get their price rows fetched to check availability.
MAX_CHECKED = 4
MIN_SCORE = 60
# The score a name containing every word the pilot said gets; at or above it, the name
# match is sure (an exact name scores 100).
CONTAINED_SCORE = 90

# Not \b: UEX also names paints like "paint_cutlass_black_procyon", where "_" is a word
# character and \b never matches.
_VARIANT_WORDS = re.compile(r"(?<![a-z])(livery|paint|skin)(?![a-z])", re.IGNORECASE)
_NAMED_EDITION = re.compile(r'"[^"]+"')
_ACCESSORY_WORDS = re.compile(r"\b(magazine|battery|ammo)\b", re.IGNORECASE)

# What can be done with each kind of thing in game. Ships have no resale market.
_KINDS_FOR_ACTION = {
    BUY: (COMMODITY, ITEM, VEHICLE),
    SELL: (COMMODITY, ITEM),
    RENT: (VEHICLE,),
}


# How a candidate's name matched what was said. Only the first two are sure enough to
# answer "nothing sells it" about: a fuzzy score can reach 90 by partial overlap alone
# ("quantum drive" against Quantainium).
EXACT = "exact"
CONTAINED = "contained"
FUZZY = "fuzzy"


@dataclass
class Candidate:
    kind: str
    entity: object
    score: float
    matched_by: str = FUZZY
    rows: list[dict] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.entity.name


@dataclass
class Resolution:
    """Either `chosen` (with its price rows), or `message` for ALICE to say instead."""
    chosen: Candidate | None = None
    message: str | None = None
    # Said alongside the answer when the choice isn't obvious from the words alone, so a
    # wrong guess is easy to catch: "the Hull A ship, not one of its liveries".
    understood_as: str | None = None
    # Set only when picking took judgement (judgement_call): why this one, for ALICE to say
    # in one dry aside. None for an obvious match, which she should just answer.
    judgement_call: str | None = None


def _penalty(candidate: Candidate) -> float:
    if candidate.kind != ITEM:
        return 0
    if _VARIANT_WORDS.search(candidate.name) or _NAMED_EDITION.search(candidate.name):
        return VARIANT_PENALTY
    if _ACCESSORY_WORDS.search(candidate.name):
        return ACCESSORY_PENALTY
    return 0


def _matches(query: str, pool: list, label: str, scorer) -> list[tuple[object, float, str]]:
    query = canonicalize(query, label)
    exact = _exact_name_matches(query, pool)
    if exact:
        return [(exact[0], 100.0, EXACT)]
    found: dict[int, tuple[object, float, str]] = {}
    for entity in _contained_matches(query, pool):
        found[id(entity)] = (entity, float(CONTAINED_SCORE), CONTAINED)
    winners, score = _top_matches(query, pool, MIN_SCORE, scorer)
    for entity in winners:
        if id(entity) not in found:
            found[id(entity)] = (entity, score, FUZZY)
    return list(found.values())


def _candidates(query: str, action: str, cache) -> list[Candidate]:
    pools = {
        COMMODITY: (cache.commodities, "commodity", fuzz.WRatio),
        ITEM: (cache.items, "item", fuzz.WRatio),
        VEHICLE: (cache.vehicles, "ship", fuzz.token_sort_ratio),
    }
    candidates = []
    for kind in _KINDS_FOR_ACTION[action]:
        pool, label, scorer = pools[kind]
        for entity, score, matched_by in _matches(query, pool, label, scorer):
            candidate = Candidate(kind, entity, score, matched_by)
            candidate.score -= _penalty(candidate)
            candidates.append(candidate)
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates


def _offers(row: dict, action: str) -> bool:
    if action == RENT:
        return bool(row.get("price_rent"))
    if action == SELL:
        return bool(row.get("price_sell"))
    return bool(row.get("price_buy"))


def _spoken_kind(candidate: Candidate) -> str:
    if candidate.kind == VEHICLE:
        return f"the {candidate.name} ship"
    return candidate.name


async def resolve(query: str, action: str, cache,
                  fetch_rows: Callable[[Candidate], Awaitable[list[dict]]]) -> Resolution:
    """The thing the pilot means, with its price rows for `action`. fetch_rows(candidate)
    returns a candidate's raw UEX price rows (the tool knows which endpoint per kind)."""
    candidates = _candidates(query, action, cache)
    if not candidates:
        return Resolution(message=f"Couldn't find anything called '{query}' to {action}.")

    checked = candidates[:MAX_CHECKED]
    available = []
    for candidate in checked:
        rows = await fetch_rows(candidate)
        candidate.rows = rows
        if any(_offers(row, action) for row in rows):
            available.append(candidate)

    best_named = checked[0]
    doing = {BUY: "sells", SELL: "buys", RENT: "rents out"}[action]
    # Only a match on the name itself (exact, or every word said) is sure enough to answer
    # "nothing sells it" about. "Cheapest size 3 quantum drive" fuzzy-matched Quantainium;
    # telling the pilot nobody sells Quantainium answered a question they never asked.
    named_for_sure = best_named.matched_by in (EXACT, CONTAINED)
    if not available:
        if not named_for_sure:
            return Resolution(message=f"Couldn't find anything matching '{query}' to {action}.")
        return Resolution(message=f"No terminal currently {doing} {_spoken_kind(best_named)}.")

    # The clearly-meant thing has no terminal for this action, only lesser matches do
    # ("Can I sell my old FS-9?": the LMG has no buyer, its magazine might): say so,
    # rather than quietly answering about the magazine.
    if best_named not in available and named_for_sure and best_named.score - available[0].score >= CONFIDENT_MARGIN:
        return Resolution(message=f"No terminal currently {doing} {_spoken_kind(best_named)}.")

    top = available[0]
    runner_up = available[1] if len(available) > 1 else None
    weak_single = runner_up is None and top.score < LOW_CONFIDENCE_MAX
    if weak_single:
        return Resolution(message=f"Didn't catch which thing you meant by '{query}' clearly enough "
                                  f"to be sure. Can you say it again?")
    if runner_up is not None and top.score - runner_up.score < CONFIDENT_MARGIN:
        options = []
        for candidate in available[:MAX_OPTIONS]:
            options.append(_spoken_kind(candidate))
        listed = ", ".join(options[:-1]) + f" or {options[-1]}"
        return Resolution(message=f"'{query}' could be {listed}. Which one?")

    # Something else matched the words too: say what was understood.
    understood_as = _spoken_kind(top) if len(checked) > 1 else None
    return Resolution(chosen=top, understood_as=understood_as,
                      judgement_call=judgement_call(top, runner_up, checked, available, action))


# Below this lead over the runner-up (but above CONFIDENT_MARGIN, where ALICE would ask),
# the pick was close enough to be worth saying why.
CLOSE_MARGIN = 25


def _listed(candidates: list[Candidate]) -> str:
    names = []
    for candidate in candidates:
        names.append(candidate.name)
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{names[0]} and {len(names) - 1} others"


def judgement_call(top: Candidate, runner_up: Candidate | None, checked: list[Candidate],
                   available: list[Candidate], action: str) -> str | None:
    """A short aside, in ALICE's voice, saying what was picked and why, but only when
    picking took judgement; None otherwise, so she doesn't narrate easy calls.

    Never for an exact name: "Hull A" and "Cutlass Black" are ships' own names, and nobody
    asking about them means the paint job or the "Cutlass Black Plushie" that also match.
    Only genuine rivals count, things matching every word said; loose fuzzy matches
    ("Starlite" for Laranite) don't. Tried live 2026-10-17: flagging every pick with any
    rival fired on nearly every answer, and ALICE mostly ignored it; written as a line she
    can say as-is, it reads naturally. The genuine case is FS-9: the LMG, its skins and its
    magazine all match the words equally well."""
    if top.matched_by == EXACT:
        return None
    rivals = []
    for candidate in checked:
        if candidate is not top and candidate.matched_by in (EXACT, CONTAINED):
            rivals.append(candidate)
    close = runner_up is not None and top.score - runner_up.score < CLOSE_MARGIN
    if not rivals and not close:
        return None

    picked = _spoken_kind(top)
    unsold = []
    for candidate in rivals:
        if candidate not in available:
            unsold.append(candidate)
    if unsold and len(unsold) == len(rivals):
        nowhere = {BUY: "isn't sold anywhere", SELL: "has no buyers", RENT: "isn't rented out"}[action]
        return f"Taking that as {picked}; the rest {nowhere}."
    if rivals and not _penalty(top):
        variants = []
        for candidate in rivals:
            if _penalty(candidate):
                variants.append(candidate)
        if variants:
            return f"Taking that as {picked}, not one of its skins or accessories."
    if close:
        return f"Close call between {top.name} and {runner_up.name}; went with {top.name}."
    return None
