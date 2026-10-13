import asyncio
import os
from typing import Annotated

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel

from db.models import LegType
from llm import anthropic_cache_control, anthropic_cache_mode, get_chat_llm, get_classification_llm
from prompt_loader import prompt_messages
from tools.best_route_tool import BestRouteTool
from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.trade_run import resolver
from tools.trade_run.cargo_packing_tool import CargoPackingTool
from tools.trade_run.confirm_cargo_loaded import ConfirmCargoLoadedTool
from tools.trade_run.confirm_cargo_unloaded import ConfirmCargoUnloadedTool
from tools.trade_run.mark_arrived_tool import MarkArrivedTool
from tools.trade_run.mark_cargo_acquired_tool import MarkCargoAcquiredTool
from tools.trade_run.mark_cargo_sold_tool import MarkCargoSoldTool
from tools.trade_run.start_trade_run_tool import StartTradeRunTool
from tools.trade_run.trade_advisor_tool import TradeAdvisorTool
from tools.trade_run.trade_run_status_tool import TradeRunStatusTool
from tools.travel_time_tool import TravelTimeTool
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.commodity_tool import CommodityPriceTool
from tools.uexcorp.item_tool import ItemPriceTool
from tools.uexcorp.mining_location_tool import MiningLocationTool
from tools.uexcorp.refinery_yield_tool import RefineryYieldTool
from tools.uexcorp.vehicle_purchase_tool import VehiclePurchaseTool
from tools.uexcorp.vehicle_rental_tool import VehicleRentalTool
from voice.timer_tool import CheckTimerTool, StartTimerTool


class State(BaseModel):
    messages: Annotated[list[BaseMessage], add_messages]
    on_topic: bool = True
    reason: str | None = None
    # Set by respond() whenever a readback-producing tool (mark_cargo_acquired,
    # mark_cargo_sold -- stated numbers are always read back, docs/intent/voice-latency.md)
    # just ran, so the pilot's next utterance (a correction like "no, 614") skips
    # classify_topic entirely instead of asking a small, fast classifier to correctly
    # weigh a short, keyword-free reply against buried context -- the same failure shape
    # that already broke it once ("Let's do it.", docs/todo.md Phase 3 seed #4).
    # Self-clears every time respond() runs, whichever path got it there.
    pending_confirmation: bool = False
    # Set (best-effort) alongside pending_confirmation whenever a confirmation-prone tool
    # just ran -- re-resolves the same leg via resolver.resolve_leg(), using the
    # triggering call's own args, since the tool itself only returns a spoken string, not
    # its leg id. Unlike pending_confirmation, this does NOT self-clear: it's a recency
    # hint for the eventual voice correction tool's leg lookup (resolve_leg() alone is
    # ambiguous exactly when the pilot gives no commodity/terminal hint and has more than
    # one active run -- see docs/intent/run-model.md's leg-resolution note), not a
    # one-turn flag, so it persists until a newer confirmation-prone call overwrites it.
    last_touched_leg_id: int | None = None


class TopicClassification(BaseModel):
    on_topic: bool
    reason: str


class RejectLine(BaseModel):
    line: str


load_dotenv()

# Static (no template variables), so each renders once here. Through ALICE's gateway
# these are placeholders the server expands -- see prompt_loader.prompt_messages.
PERSONA_MESSAGES = prompt_messages("alice-persona")
CLASSIFY_MESSAGES = prompt_messages("topic-classification")
REJECT_MESSAGES = prompt_messages("topic-reject-voice")

uex_client = UEXCorpClient(via_server=True)  # no UEX key on the client; lookups go through the server cache
scw_client = StarCitizenWikiClient()

# UEX Backed Tools
commodity_price_tool = CommodityPriceTool(client=uex_client)
item_price_tool = ItemPriceTool(client=uex_client)
vehicle_purchase_tool = VehiclePurchaseTool(client=uex_client)
vehicle_rental_tool = VehicleRentalTool(client=uex_client)
refinery_yield_tool = RefineryYieldTool(client=uex_client)
mining_location_tool = MiningLocationTool(client=uex_client)

uex_backed_tools = [commodity_price_tool, item_price_tool, vehicle_purchase_tool, vehicle_rental_tool,
                    refinery_yield_tool, mining_location_tool]

# Trade Run Voice Tools
mark_arrived_tool = MarkArrivedTool()
mark_cargo_acquired_tool = MarkCargoAcquiredTool()
mark_cargo_sold_tool = MarkCargoSoldTool()
confirm_cargo_loaded_tool = ConfirmCargoLoadedTool()
confirm_cargo_unloaded_tool = ConfirmCargoUnloadedTool()
trade_run_status_tool = TradeRunStatusTool()
cargo_packing_tool = CargoPackingTool(client=uex_client)
start_trade_run_tool = StartTradeRunTool()
trade_advisor_tool = TradeAdvisorTool(uex_client=uex_client, scw_client=scw_client)

trade_run_tools = [mark_arrived_tool, mark_cargo_acquired_tool, mark_cargo_sold_tool, confirm_cargo_loaded_tool,
                   confirm_cargo_unloaded_tool, trade_run_status_tool, cargo_packing_tool, start_trade_run_tool,
                   trade_advisor_tool]

# General Tools
timer_tool = StartTimerTool()
check_timer_tool = CheckTimerTool()
travel_time_tool = TravelTimeTool(uex_client=uex_client, scw_client=scw_client)
best_route_tool = BestRouteTool(uex_client=uex_client, scw_client=scw_client)

general_tools = [timer_tool, check_timer_tool, travel_time_tool, best_route_tool]

tools = uex_backed_tools + trade_run_tools + general_tools

# get_chat_llm() (respond) and get_classification_llm() (classify_topic + reject) are
# separately configured in .env (OLLAMA_CHAT_MODEL/OLLAMA_REASONING vs
# OLLAMA_CLASSIFICATION_MODEL) -- see llm.py. respond is where the missed-tool-call
# failures showed up and where reasoning has measurably helped (evals/agent_eval/
# results/); classify_topic and the reject line are a fast binary decision and a
# short in-character line, neither needing reasoning or necessarily respond's model.
# Anthropic prompt caching (llm.anthropic_cache_mode) -- "off" for every other provider.
# "full"'s rolling breakpoint rides on langchain-anthropic's cache_control kwarg, which
# marks the last eligible block of each request; the fixed one is _persona_messages().
CACHE_MODE = anthropic_cache_mode()

llm = get_chat_llm().bind_tools(tools)
if CACHE_MODE == "full":
    llm = llm.bind(cache_control=anthropic_cache_control())
classifier_llm = get_classification_llm().with_structured_output(TopicClassification)
reject_llm = get_classification_llm().with_structured_output(RejectLine)

# name -> (llm instance, its static system-prompt messages). Only a content-invariant
# system prompt is worth prewarming at all: Ollama's cache is keyed on exact prompt
# text, so a template that interpolates per-turn variables into the system message
# (unlike these three) would never match a real call's prompt anyway — see
# decline_topic's own note on why the reject prompt stays variable-free for this reason.
_PREWARM_TARGETS = [
    ("persona", llm, PERSONA_MESSAGES),
    ("classifier", classifier_llm, CLASSIFY_MESSAGES),
    ("reject", reject_llm, REJECT_MESSAGES),
]

_warmed: set[str] = set()


async def prewarm() -> None:
    """Fires one throwaway call through each not-yet-warmed LLM at the exact prompt
    shape it'll see for real, so Ollama's prompt-prefix cache is warm before the
    pilot's first turn lands on it instead of during it. Only meaningful for
    LLM_PROVIDER=ollama — measured 2026-09-22: the persona's tool-bound prompt
    (persona + every tool schema, ~9,700 tokens) cost ~30s of pure prompt-eval on its
    first call, dropping to well under a second on every call afterward that shares
    the same prefix. A generic "ping the model" warmup wouldn't fix this —
    load_duration was already ~2ms even on that cold call, so it was never a
    model-loading cost, and Ollama's cache is keyed on the actual prompt content —
    which is why this replays the real prompts rather than sending something
    arbitrary. Hosted providers don't have this cold-start cost, so this is a no-op
    for them rather than spending real money on a call that buys nothing.

    Idempotent via _warmed — safe to call more than once; only targets not already
    warmed this process do real work. Adding a future model (a tool-family router,
    a second reasoning-tier model, whatever's next) is a one-line addition to
    _PREWARM_TARGETS, not an edit to this function.

    Best-effort per target: one target failing to warm shouldn't block the others or
    startup — it only means that specific call pays the cold-start cost for real."""
    if os.getenv("LLM_PROVIDER", "ollama") != "ollama":
        return
    if os.getenv("PREWARM_OLLAMA", "true").strip().lower() == "false":
        return

    to_warm = [(name, model, prompt) for name, model, prompt in _PREWARM_TARGETS if name not in _warmed]
    if not to_warm:
        return

    warmup_turn = [HumanMessage(content="(warmup)")]

    async def _warm_one(name: str, model, prompt: list[BaseMessage]) -> None:
        try:
            await model.ainvoke(
                prompt + warmup_turn,
                config={"run_name": f"prewarm-{name}", "tags": ["warmup"]},
            )
            _warmed.add(name)
        except Exception as exc:
            print(f"[ALICE] Prewarm failed for {name!r} ({exc}) — its first real call will be slow instead.")

    await asyncio.gather(*[_warm_one(name, model, prompt) for name, model, prompt in to_warm])


# Tools whose result is a spoken readback of a stated number (docs/intent/voice-latency.md)
# -- a correction following one of these ("no, 614") is what pending_confirmation exists
# to protect, by skipping classify_topic on the very next turn.
_CONFIRMATION_PRONE_TOOLS = {"mark_cargo_acquired", "mark_cargo_sold"}


def _just_ran_confirmation_prone_tool(messages: list[BaseMessage]) -> bool:
    """True if the ToolMessage(s) now at the end of `messages` came from this turn's own
    tool round trip, not an old one -- state.messages spans the whole thread (MemorySaver),
    so this only looks at the current trailing run: messages[-1] is a ToolMessage only while
    respond() is being called back after tools (a completed turn always ends on a plain
    AIMessage), so scanning back from there for the triggering AIMessage.tool_calls is
    turn-scoped by construction, not a fixed-size lookback guess."""
    if not messages or not isinstance(messages[-1], ToolMessage):
        return False
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            names = {call["name"] for call in (message.tool_calls or [])}
            return bool(names & _CONFIRMATION_PRONE_TOOLS)
    return False


def _confirmation_tool_call(messages: list[BaseMessage]) -> dict | None:
    """Same turn-scoping as _just_ran_confirmation_prone_tool (reversed scan for the
    triggering AIMessage, stopping cold if messages[-1] isn't a ToolMessage) but returns
    the actual tool_call dict instead of a bool -- last_touched_leg_id needs the args the
    pilot's confirmation-prone call used, not just whether one ran. Kept separate from
    _just_ran_confirmation_prone_tool rather than having one call the other, so neither
    changes the other's already-tested behavior."""
    if not messages or not isinstance(messages[-1], ToolMessage):
        return None
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            for call in (message.tool_calls or []):
                if call["name"] in _CONFIRMATION_PRONE_TOOLS:
                    return call
            return None
    return None


_LEG_TYPE_BY_CONFIRMATION_TOOL = {
    "mark_cargo_acquired": LegType.ACQUISITION,
    "mark_cargo_sold": LegType.SALE,
}


async def _resolve_last_touched_leg_id(messages: list[BaseMessage]) -> int | None:
    """Best-effort re-resolution of the exact leg the triggering confirmation-prone tool
    call just touched, using that call's own commodity/terminal args -- the tool itself
    already resolved this same leg a moment ago (resolver.resolve_leg(), same args) but
    only returns a spoken string (UplinkTool's plain-string return, not a Command), never
    its id. Re-resolving is the cheapest way to recover it without touching a tool file.

    Returns None on any resolution failure, including a genuinely ambiguous match -- which
    means the tool's own call didn't record anything either, so there's nothing new to
    remember. respond() keeps the previous last_touched_leg_id rather than clobbering it
    with a failed lookup."""
    call = _confirmation_tool_call(messages)
    if call is None:
        return None
    args = call.get("args") or {}
    try:
        leg = await resolver.resolve_leg(
            leg_type=_LEG_TYPE_BY_CONFIRMATION_TOOL[call["name"]],
            commodity=args.get("commodity"),
            terminal=args.get("terminal"),
        )
    except (ValueError, resolver.AmbiguousLegError):
        return None
    return leg.id


# History windowing -- what each LLM call is SENT, not what's stored. state.messages keeps
# the whole thread (MemorySaver, one thread per app launch), so without this every turn
# resends the entire play session, every old all_results list included, on top of the
# ~11.5k-token tools+persona prefix. Trade-run state lives in the ledger, not the chat, so
# forgetting old turns loses nothing ALICE can't re-fetch (trade_run_status).
#
# HISTORY_TURNS counts pilot turns (HumanMessages); 0 disables windowing entirely.
# HISTORY_FULL_TOOL_RESULT_TURNS is how many of those keep tool results verbatim -- older
# ones get STALE_TOOL_RESULT instead. Never below 1: the current turn's own results must
# survive its respond<->tools loop, and 2 keeps "what about in Pyro?" follow-ups working
# off the previous turn's all_results (commodity_price_lookup's description relies on it).
# CLASSIFY_HISTORY_TURNS is classify_topic's own, smaller window -- it judges the latest
# utterance, needing only enough before it for "Let's do it." to read as on-topic.
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "6"))
HISTORY_FULL_TOOL_RESULT_TURNS = max(1, int(os.getenv("HISTORY_FULL_TOOL_RESULT_TURNS", "2")))
CLASSIFY_HISTORY_TURNS = int(os.getenv("CLASSIFY_HISTORY_TURNS", "2"))
STALE_TOOL_RESULT = "[older result omitted; call the tool again if you need it]"


def _windowed_history(messages: list[BaseMessage], turns: int, full_result_turns: int) -> list[BaseMessage]:
    """The last `turns` pilot turns of `messages`, with tool results older than the last
    `full_result_turns` turns replaced by STALE_TOOL_RESULT. Always cuts at a HumanMessage,
    never mid-turn, so a ToolMessage is never separated from the AIMessage whose tool_call
    it answers -- both hosted APIs reject that with a 400, and Anthropic also requires the
    first message to come from the user. Stubbed ToolMessages keep their tool_call_id for
    the same reason: the pairing has to stay intact even when the content doesn't."""
    if turns <= 0:
        return messages

    human_positions = []
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            human_positions.append(index)

    start = human_positions[-turns] if len(human_positions) > turns else 0
    keep_results_from = human_positions[-full_result_turns] if len(human_positions) >= full_result_turns else 0

    window = []
    for index in range(start, len(messages)):
        message = messages[index]
        if isinstance(message, ToolMessage) and index < keep_results_from:
            message = message.model_copy(update={"content": STALE_TOOL_RESULT})
        window.append(message)
    return window


def _persona_messages() -> list[BaseMessage]:
    """The persona's system message(s), with a cache breakpoint on the last one when
    Anthropic caching is on. Tools render before system in Anthropic's request, so this
    one breakpoint caches every tool schema plus the persona -- the stable prefix,
    unlike the windowed history after it."""
    messages = list(PERSONA_MESSAGES)
    if CACHE_MODE == "off" or not isinstance(messages[-1], SystemMessage):
        return messages
    block = {"type": "text", "text": messages[-1].content, "cache_control": anthropic_cache_control()}
    return messages[:-1] + [SystemMessage(content=[block])]


async def respond(state: State) -> dict:
    messages = _persona_messages() + _windowed_history(
        state.messages, HISTORY_TURNS, HISTORY_FULL_TOOL_RESULT_TURNS,
    )
    pending_confirmation = _just_ran_confirmation_prone_tool(state.messages)

    # Run the leg re-resolution alongside the LLM call rather than before it. It's a real
    # network round trip (resolve_leg -> ledger_client.get_in_progress_runs()), and this
    # is already the turn the pilot is waiting on a spoken reply for -- the whole reason
    # pending_confirmation exists at all is to save time on this exact turn. An LLM
    # completion is reliably slower than one HTTP call to our own ledger server, so
    # running them concurrently adds ~0 wall-clock time instead of adding it serially.
    if pending_confirmation:
        response, resolved_leg_id = await asyncio.gather(
            llm.ainvoke(messages),
            _resolve_last_touched_leg_id(state.messages),
        )
    else:
        response = await llm.ainvoke(messages)
        resolved_leg_id = None

    last_touched_leg_id = resolved_leg_id if resolved_leg_id is not None else state.last_touched_leg_id
    return {
        "messages": [response],
        "pending_confirmation": pending_confirmation,
        "last_touched_leg_id": last_touched_leg_id,
    }


async def classify_topic(state: State) -> dict:
    messages = list(CLASSIFY_MESSAGES)
    # full_result_turns=1: classify runs before this turn's tools, so every tool result
    # it sees is a previous turn's -- all stubbed, none needed to judge topic.
    history = _windowed_history(state.messages, CLASSIFY_HISTORY_TURNS, 1)
    response = await classifier_llm.ainvoke(messages + history)
    return {"on_topic": response.on_topic, "reason": response.reason}


async def decline_topic(state: State) -> dict:
    # The reject prompt takes no variables on purpose — utterance/reason are
    # appended as a separate HumanMessage instead of interpolated into the system
    # prompt, so the system prompt stays content-invariant and prewarm() can actually
    # warm it (a template with per-turn variables baked into its system message would
    # never match a real call's prompt text, so warming it with dummy content
    # wouldn't help — see _PREWARM_TARGETS' note).
    context = HumanMessage(
        content=f"Pilot said: {state.messages[-1].content}\nWhy it's off-topic: {state.reason}"
    )
    messages = REJECT_MESSAGES + [context]
    response = await reject_llm.ainvoke(messages)
    return {"messages": [AIMessage(response.line)]}


def route_topic(state: State) -> str:
    return "respond" if state.on_topic else "decline"


def route_start(state: State) -> str:
    # Skips classify_topic entirely (not just overriding its verdict) when the pilot's
    # utterance is a reply to ALICE's own immediately preceding readback -- a real
    # classifier call saved, not just a wasted one ignored.
    return "respond" if state.pending_confirmation else "classify_topic"


graph_builder = StateGraph(State)
graph_builder.add_node("classify_topic", classify_topic)
graph_builder.add_node("respond", respond)
graph_builder.add_node("decline", decline_topic)

graph_builder.add_conditional_edges(START, route_start)
graph_builder.add_conditional_edges("classify_topic", route_topic)

tool_node = ToolNode(tools)
graph_builder.add_node("tools", tool_node)
graph_builder.add_conditional_edges("respond", tools_condition)
graph_builder.add_edge("tools", "respond")

graph = graph_builder.compile(checkpointer=MemorySaver())
