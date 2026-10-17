"""What the response matrix asks ALICE (response_matrix.py runs it). Two kinds of case,
both meant to be written and edited by hand:

- SingleTurn: one pilot line, answered once. For "does she understand this phrasing".
- Conversation: a workflow, a sequence of pilot lines in one conversation. Every turn is
  its own row in the CSV, so each of ALICE's replies gets marked up, not just the last.
  For "does she keep the thread": follow-ups, refinements, a whole trade run by voice.

Both can start from a scenario (matrix_scenarios.py): an active trade run, two runs, a
running timer. Without one, the pilot has no runs and no timers.

`tool` is the tool ALICE should use: a tool name, "(none)" when no tool is right,
"(gap)" when nothing handles it yet, "(clarify) X" when asking first is as right as
calling X (something she needs wasn't said), "(context) X" when answering from what she
already knows is as right as calling X (the active run is in her context).

Started 2026-10-16 from Jeff's markup of the first matrix run: rows he flagged as unclear
were rewritten or dropped (noted in place), and the rows that "need context pieces" now
run under a scenario.
"""
from dataclasses import dataclass

from matrix_scenarios import BUYING, NO_RUN, SELLING, TIMER, TWO_RUNS


@dataclass(frozen=True)
class SingleTurn:
    area: str
    says: str
    tool: str
    scenario: str = NO_RUN


@dataclass(frozen=True)
class Turn:
    says: str
    tool: str


@dataclass(frozen=True)
class Conversation:
    name: str
    area: str
    turns: tuple[Turn, ...]
    scenario: str = NO_RUN
    # What the workflow is checking, for whoever marks it up.
    purpose: str = ""


SINGLE_TURNS: list[SingleTurn] = [
    # --- prices and lookups -------------------------------------------------------------
    SingleTurn("Commodity prices", "Who's paying the most for quant right now?", "price_lookup"),
    # Was "RMC at Grim Hex" (row 38): no context, unclear what it tested.
    SingleTurn("Commodity prices", "What's RMC going for at Grim Hex?", "price_lookup"),
    SingleTurn("Items", "Where do I get a Pyro RYT multitool?", "price_lookup"),
    SingleTurn("Items", "Cheapest size 3 quantum drive", "price_lookup"),
    SingleTurn("Items", "Can I sell my old FS-9 anywhere?", "price_lookup"),
    SingleTurn("Vehicle purchase", "How much is a Hull A in-game?", "price_lookup"),
    SingleTurn("Vehicle rental", "Can I rent a Cat for a day near Lorville?", "price_lookup"),
    SingleTurn("Mining", "Where's Quantainium on Daymar?", "mining_location_lookup"),
    SingleTurn("Mining", "Hadanite spots in Stanton", "mining_location_lookup"),
    # No location given: asking for one is right (baseline, 2026-10-17).
    SingleTurn("Refining", "Best refinery for Gold near me", "(clarify) refinery_yield_lookup"),
    # --- routes ---------------------------------------------------------------------------
    SingleTurn("Route finding", "Find me the best route from Orison in my Railen", "best_route"),
    SingleTurn("Route finding", "Find me a route from Oreson in my railing", "best_route"),
    SingleTurn("Route finding", "I've got 96 SCU and 400k, what should I haul?", "(clarify) best_route"),
    SingleTurn("Route finding", "Something short, I've got 20 minutes", "(gap) best_route time budget"),
    SingleTurn("Route finding", "Morning ALICE, what's good to haul?", "(clarify) best_route"),
    SingleTurn("Route finding", "I'm in the C2 today.", "(gap) set the ship"),
    # --- an active run: the rows that "need context pieces" --------------------------------
    SingleTurn("Arrival", "Landed.", "mark_arrived", BUYING),
    SingleTurn("Arrival", "Landed.", "(clarify) mark_arrived", TWO_RUNS),
    SingleTurn("Arrival", "I'm at the buyer.", "mark_arrived", SELLING),
    # Prices as pilots say them, with the unit (Jeff, 2026-10-17): "14.2 thousand" for
    # 14,200, "11 hundred" for 1,100. Live UEX prices are whole aUEC, mostly thousands; the
    # bare "14.2" these rows used to say isn't how anyone says a price today.
    SingleTurn("Buy", "Bought 640 at 3.2 thousand", "mark_cargo_acquired", BUYING),
    SingleTurn("Buy", "Bought 640 at 32 hundred", "mark_cargo_acquired", BUYING),
    SingleTurn("Buy", "Bought 640 at 3.2 thousand", "(clarify) mark_cargo_acquired", TWO_RUNS),
    SingleTurn("Buy", "Got the copper", "mark_cargo_acquired", BUYING),
    SingleTurn("Buy", "Got the copper", "mark_cargo_acquired", TWO_RUNS),
    SingleTurn("Unload", "Cargo's off", "confirm_cargo_unloaded", SELLING),
    SingleTurn("Sell", "Sold it all, 4,567 a unit", "mark_cargo_sold", SELLING),
    SingleTurn("Sell", "Sold it all at 45 hundred", "mark_cargo_sold", SELLING),
    SingleTurn("Sell", "Sold 300, keeping the rest for Area18.", "mark_cargo_sold", SELLING),
    SingleTurn("Run status", "Where am I going again?", "(context) trade_run_status", SELLING),
    SingleTurn("Run status", "What's after this?", "(context) trade_run_status", BUYING),
    SingleTurn("Run status", "How much am I carrying?", "(context) trade_run_status", SELLING),
    SingleTurn("Run status", "Where am I going again?", "trade_run_status"),
    SingleTurn("Reassess a run", "Is it still worth going to Pyro?", "trade_advisor", SELLING),
    # Was "Did I pick badly?" (row 66): unclear on its own; with a run it means "reassess".
    SingleTurn("Reassess a run", "Did I pick a bad run?", "trade_advisor", SELLING),
    SingleTurn("Container mix", "What crates do I buy?", "cargo_packing_suggestion", BUYING),
    SingleTurn("Container mix", "Can I fit 32s in the Railen?", "cargo_packing_suggestion", BUYING),
    SingleTurn("Corrections", "Actually I sold at Area18.", "(gap) correct_trade_value", SELLING),
    SingleTurn("Abandon / loss", "Scrap this run.", "(gap) abandon run", BUYING),
    SingleTurn("Abandon / loss", "I died, cargo's gone.", "(gap) abandon run", SELLING),
    # --- travel and timers ------------------------------------------------------------------
    SingleTurn("Travel time", "How far to Baijini?", "estimate_travel_time"),
    SingleTurn("Travel time", "How far to Baijini?", "estimate_travel_time", BUYING),
    # Was "Can I make it to Pyro?" (row 70): fuel or time was unclear. Now plainly time.
    SingleTurn("Travel time", "How long to get from Seraphim to Pyro?", "estimate_travel_time"),
    SingleTurn("Timers", "Loading takes 15, ping me", "start_timer"),
    SingleTurn("Timers", "Refinery job's 4 hours, remind me", "start_timer"),
    SingleTurn("Timers", "How long left?", "check_timer", TIMER),
    SingleTurn("Timers", "How long left?", "check_timer"),
    # --- behaviour ----------------------------------------------------------------------------
    SingleTurn("Behaviour: unknowable", "Is Pyro safe right now?", "(none)"),
    SingleTurn("Meta", "What can you do?", "(none)"),
    # --- gaps ---------------------------------------------------------------------------------
    SingleTurn("Gap: ledger history", "How much did I make today?", "(gap) ledger history"),
    SingleTurn("Gap: ledger history", "What was my last run?", "(gap) ledger history"),
    SingleTurn("Gap: ship facts", "How much SCU does a Hull C hold?", "(gap) ship facts"),
    SingleTurn("Gap: ship facts", "Can my Freelancer land at Orison?", "(gap) landing pad size"),
    SingleTurn("Gap: mining economics", "Is this rock worth it? 12% Quant, 8,000 mass.", "(gap) mining module"),
    # Dropped (Jeff, 2026-10-16): "Quantuming out." (bad test), "Loaded, what's the ETA?"
    # (out of context). "Shorter." and "Say that again." moved into a conversation below.
]

def on_script(intended: str, called: str) -> bool | None:
    """Did this turn go the way the conversation's script assumes? None when the intended
    tool can't be checked ("(gap)": nothing handles it yet, so any answer is a guess).

    - "(none)" (also "(none) social"): no tool called.
    - "(clarify) X" / "(context) X": no tool (asking first, or answering from what she
      already knows) or calling X both count.
    - otherwise: at least one intended tool name was called ("a + b", "x (compare)")."""
    called_tools = set()
    if called and called != "(none)":
        called_tools = set(called.split(" + "))
    if intended.startswith("(gap)"):
        return None
    if intended.startswith("(none)"):
        return not called_tools
    either = intended.startswith(("(clarify)", "(context)"))
    wanted = set()
    for part in intended.removeprefix("(clarify)").removeprefix("(context)").split(" + "):
        name = part.strip().split(" ")[0]
        if name:
            wanted.add(name)
    if either and not called_tools:
        return True
    return bool(wanted & called_tools)


ROUTE_FROM_ORISON = "Find me the best route from Orison in my Railen"

CONVERSATIONS: list[Conversation] = [
    Conversation(
        name="Full trade run by voice",
        area="Workflow",
        purpose="A whole run without touching the overlay: find, commit, buy, haul, sell, check.",
        turns=(
            Turn(ROUTE_FROM_ORISON, "best_route"),
            Turn("Yeah, do it", "start_trade_run"),
            Turn("Landed.", "mark_arrived"),
            Turn("Bought 640 at 14.2 thousand", "mark_cargo_acquired"),
            Turn("Loaded.", "confirm_cargo_loaded"),
            Turn("Where am I going again?", "(context) trade_run_status"),
            Turn("I'm at the buyer.", "mark_arrived"),
            Turn("Cargo's off", "confirm_cargo_unloaded"),
            Turn("Sold it all, 4,567 a unit", "mark_cargo_sold"),
            Turn("How did that run go?", "(context) trade_run_status"),
        ),
    ),
    Conversation(
        name="Pick a different route",
        area="Workflow",
        purpose="Committing to the runner-up, and a smaller load than offered.",
        turns=(
            Turn(ROUTE_FROM_ORISON, "best_route"),
            Turn("Let's do the second one, but only 500 SCU", "start_trade_run"),
            Turn("How much am I carrying?", "(context) trade_run_status"),
        ),
    ),
    Conversation(
        name="Search, then exclude a system",
        area="Follow-up: search frame",
        purpose="'Nothing in Pyro' is an exclusion: the next answer must not be in Pyro (row 49).",
        turns=(
            Turn(ROUTE_FROM_ORISON, "best_route"),
            Turn("Nothing in Pyro", "best_route"),
        ),
    ),
    Conversation(
        name="What about Pyro?",
        area="Follow-up: search frame",
        purpose="Jeff's reference: ask which part 'Pyro' changes, then answer as a comparison.",
        turns=(
            Turn("Give me a good route from Orison.", "best_route"),
            Turn("What about Pyro?", "(clarify) best_route"),
            Turn("No, just routes to Pyro.", "best_route (compare)"),
        ),
    ),
    Conversation(
        name="Price, then narrow it",
        area="Follow-up",
        purpose="A follow-up narrows the last question instead of starting over.",
        turns=(
            Turn("Who's paying the most for quant right now?", "price_lookup"),
            Turn("What about in Pyro?", "price_lookup"),
        ),
    ),
    Conversation(
        name="Is it worth it, from where I am",
        area="Follow-up",
        purpose="Was a single row (36) that needed the origin; now the pilot gives it first.",
        turns=(
            Turn("I'm at Seraphim in my Railen.", "(gap) set location and ship"),
            Turn("Is Laranite worth taking to Hurston?", "price_lookup"),
        ),
    ),
    Conversation(
        name="Correct a misheard number",
        area="Corrections",
        purpose="Whisper hears 640 for 620: the pilot corrects it right after the readback.",
        scenario=BUYING,
        turns=(
            Turn("Bought 640 at 14.2 thousand", "mark_cargo_acquired"),
            Turn("No, it was 620, not 640.", "(gap) correct_trade_value"),
        ),
    ),
    Conversation(
        name="Decide for me",
        area="Behaviour: decision-seeking",
        purpose="Information, not decisions: she lays out the options and the pilot picks.",
        turns=(
            Turn(ROUTE_FROM_ORISON, "best_route"),
            Turn("Just pick one for me.", "(none)"),
            Turn("Which should I do?", "(none)"),
        ),
    ),
    Conversation(
        name="Repeat and shorten",
        area="Meta",
        purpose="'Shorter' means a shorter answer here, not a shorter run (row 91 was unclear).",
        turns=(
            Turn(ROUTE_FROM_ORISON, "best_route"),
            Turn("Say that again.", "(none)"),
            Turn("Give me that in fewer words.", "(none)"),
        ),
    ),
    Conversation(
        name="Timer while loading",
        area="Timers",
        purpose="Set a timer mid-run, then ask about it without naming it.",
        scenario=BUYING,
        turns=(
            Turn("Bought 640 at 14.2 thousand", "mark_cargo_acquired"),
            Turn("Loading takes 15, ping me", "start_timer"),
            Turn("How long left?", "check_timer"),
        ),
    ),
    Conversation(
        name="Greet, work, sign off",
        area="Social",
        purpose="Social turns get short dry lines; the work in between isn't affected.",
        turns=(
            Turn("Good morning.", "(none) social"),
            Turn(ROUTE_FROM_ORISON, "best_route"),
            Turn("Thanks ALICE.", "(none) social"),
            Turn("Signing off, fly safe.", "(none) social"),
        ),
    ),
]
