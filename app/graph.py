import asyncio
import logging
import os
from typing import Annotated
from uuid import UUID

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel

import ledger_client
import reply_recovery
import topic_client
import topic_decision
from db.models import LegType
from db.trade_run_store import trade_run_info
from llm import (
    anthropic_cache_control,
    anthropic_cache_mode,
    get_chat_llm,
    get_chat_llm_small,
    get_classification_llm,
)
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
from tools.trade_run.update_leg_details_tool import UpdateLegDetailsTool
from tools.travel_time_tool import TravelTimeTool
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.mining_location_tool import MiningLocationTool
from tools.uexcorp.price_lookup_tool import PriceLookupTool
from tools.uexcorp.refinery_yield_tool import RefineryYieldTool
from voice.timer_tool import CancelTimerTool, CheckTimerTool, StartTimerTool


class State(BaseModel):
    messages: Annotated[list[BaseMessage], add_messages]
    on_topic: bool = True
    reason: str | None = None
    # classify_topic's TopicClassification.social for this turn: pure social talk gets
    # social_reply instead of respond or the off-topic decline.
    social: bool = False
    # The pilot's in-progress trade runs as a note for respond (_active_runs_note), and the
    # pilot turn it was read for (_pilot_turn), so the tool loop reuses it within a turn and
    # the next turn reads it fresh.
    active_runs: str = ""
    active_runs_turn: int = -1
    # The tool-routing experiment (ALICE_TOOL_ROUTING): the tool groups respond binds this
    # pilot turn, or None for every tool. Same per-turn marker as active_runs.
    tool_groups: list[str] | None = None
    tool_groups_turn: int = -1
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
    # A UUID, like every leg id. It was typed int, so the first successful voice purchase
    # crashed the turn when the graph stored it (response matrix, 2026-10-17).
    last_touched_leg_id: UUID | None = None


class TopicClassification(BaseModel):
    """The chat-model classifier's answer, used only when the topic decision model
    (topic_client.py) can't be reached. It decides on- or off-topic and never "social":
    asked to, it marked requests wrapped in a greeting ("Hey ALICE, landed.") as social and
    so dropped them (2026-10-16)."""
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
SOCIAL_MESSAGES = prompt_messages("social-reply")

uex_client = UEXCorpClient(via_server=True)  # no UEX key on the client; lookups go through the server cache
scw_client = StarCitizenWikiClient()

# UEX Backed Tools
# One tool for every buy / sell / rent price question (replaced four per-catalogue
# lookups 2026-10-17: tools/uexcorp/price_lookup_tool.py).
price_lookup_tool = PriceLookupTool(client=uex_client)
refinery_yield_tool = RefineryYieldTool(client=uex_client)
mining_location_tool = MiningLocationTool(client=uex_client)

uex_backed_tools = [price_lookup_tool, refinery_yield_tool, mining_location_tool]

# Trade Run Voice Tools
mark_arrived_tool = MarkArrivedTool()
mark_cargo_acquired_tool = MarkCargoAcquiredTool()
update_leg_details_tool = UpdateLegDetailsTool()
mark_cargo_sold_tool = MarkCargoSoldTool()
confirm_cargo_loaded_tool = ConfirmCargoLoadedTool()
confirm_cargo_unloaded_tool = ConfirmCargoUnloadedTool()
trade_run_status_tool = TradeRunStatusTool()
cargo_packing_tool = CargoPackingTool(client=uex_client)
start_trade_run_tool = StartTradeRunTool()
trade_advisor_tool = TradeAdvisorTool(uex_client=uex_client, scw_client=scw_client)

trade_run_tools = [mark_arrived_tool, update_leg_details_tool, mark_cargo_acquired_tool, mark_cargo_sold_tool,
                   confirm_cargo_loaded_tool, confirm_cargo_unloaded_tool, trade_run_status_tool,
                   cargo_packing_tool, start_trade_run_tool, trade_advisor_tool]

# General Tools
timer_tool = StartTimerTool()
check_timer_tool = CheckTimerTool()
cancel_timer_tool = CancelTimerTool()
travel_time_tool = TravelTimeTool(uex_client=uex_client, scw_client=scw_client)
best_route_tool = BestRouteTool(uex_client=uex_client, scw_client=scw_client)

general_tools = [timer_tool, check_timer_tool, cancel_timer_tool,travel_time_tool, best_route_tool]

tools = uex_backed_tools + trade_run_tools + general_tools

# get_chat_llm() (respond), get_classification_llm() (classify_topic) and
# get_chat_llm_small() (the decline and social lines) are separately configured in .env
# (OLLAMA_CHAT_MODEL/OLLAMA_REASONING, OLLAMA_CLASSIFICATION_MODEL,
# OLLAMA_SMALL_CHAT_MODEL) -- see llm.py. respond is where the missed-tool-call
# failures showed up and where reasoning has measurably helped (evals/agent_eval/
# results/); classify_topic is a fast decision and the decline/social lines are short
# in-character lines, none needing reasoning or necessarily respond's model.
# Anthropic prompt caching (llm.anthropic_cache_mode) -- "off" for every other provider.
# "full"'s rolling breakpoint rides on langchain-anthropic's cache_control kwarg, which
# marks the last eligible block of each request; the fixed one is _persona_messages().
CACHE_MODE = anthropic_cache_mode()

chat_model = get_chat_llm()
llm = chat_model.bind_tools(tools)
if CACHE_MODE == "full":
    llm = llm.bind(cache_control=anthropic_cache_control())

# Tool-routing experiment: respond bound to only some tool groups, built once per set of
# groups (TOOL_GROUPS order, so a set is always the same prompt and caches).
_routed_llms: dict[tuple[str, ...], object] = {}


def _llm_for(groups: list[str] | None):
    if groups is None:
        return llm
    key = tuple(groups)
    if key not in _routed_llms:
        names = set()
        for group in groups:
            names.update(topic_decision.TOOL_GROUPS[group])
        subset = []
        for tool in tools:
            if tool.name in names:
                subset.append(tool)
        routed = chat_model.bind_tools(subset)
        if CACHE_MODE == "full":
            routed = routed.bind(cache_control=anthropic_cache_control())
        _routed_llms[key] = routed
    return _routed_llms[key]
classifier_llm = get_classification_llm().with_structured_output(TopicClassification)
# The decline and social reply are ALICE talking, not classifying: a small chat model,
# so get_classification_llm() can become a pure classifier without breaking them.
reject_llm = get_chat_llm_small().with_structured_output(RejectLine)
social_llm = get_chat_llm_small()

# name -> (llm instance, its static system-prompt messages). Only a content-invariant
# system prompt is worth prewarming at all: Ollama's cache is keyed on exact prompt
# text, so a template that interpolates per-turn variables into the system message
# (unlike these three) would never match a real call's prompt anyway — see
# decline_topic's own note on why the reject prompt stays variable-free for this reason.
_PREWARM_TARGETS = [
    ("persona", llm, PERSONA_MESSAGES),
    ("classifier", classifier_llm, CLASSIFY_MESSAGES),
    ("reject", reject_llm, REJECT_MESSAGES),
    ("social", social_llm, SOCIAL_MESSAGES),
]

_warmed: set[str] = set()

# Providers that end up on an Ollama prefix cache: a local Ollama, or ALICE's gateway,
# which relays to Ollama on heket. Hosted providers have no such cold start.
_PREWARM_PROVIDERS = ("ollama", "alice")


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
    arbitrary. Runs for LLM_PROVIDER=alice too: the gateway relays to Ollama on
    heket. A pilot's first turn spent ~20s in classify on 2026-10-15, most
    likely this cold prompt.
    The box's cache is shared, so one pilot's warm-up also warms it for the others.
    Hosted providers don't have this cold-start cost, so this is a no-op for them
    rather than spending real money on a call that buys nothing. (With alice, a warm-up
    can still land on the hosted fallback while heket is down; that's three
    small metered calls per launch, accepted for now.)

    Idempotent via _warmed — safe to call more than once; only targets not already
    warmed this process do real work. Adding a future model (a tool-family router,
    a second reasoning-tier model, whatever's next) is a one-line addition to
    _PREWARM_TARGETS, not an edit to this function.

    Best-effort per target: one target failing to warm shouldn't block the others or
    startup — it only means that specific call pays the cold-start cost for real."""
    if os.getenv("LLM_PROVIDER", "ollama") not in _PREWARM_PROVIDERS:
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


async def _resolve_last_touched_leg_id(messages: list[BaseMessage]) -> UUID | None:
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
# off the previous turn's results.
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


log = logging.getLogger(__name__)

ACTIVE_RUNS_HEADER = (
    "The pilot's active trade runs, as of their last message. When they report progress "
    "without naming the commodity or terminal ('Landed.', 'Bought 640 at 14.2', 'Cargo's off'), "
    "it's about the run whose current step fits: call the tool for it rather than asking. If "
    "more than one run fits, ask which. Never say anything was recorded unless a tool just did it."
)


async def _active_runs_note() -> str:
    """The in-progress runs for respond's context, or "" when there are none or they can't
    be read (respond then works as it did before this existed). Jeff's matrix markup,
    2026-10-16: without it ALICE never called a trade tool for "Landed." or "Bought 640 at
    14.2", asking which commodity and terminal instead, although the tools resolve the
    active run by themselves."""
    try:
        runs = await ledger_client.get_in_progress_runs()
    except Exception:
        log.warning("couldn't read the active trade runs; answering without them", exc_info=True)
        return ""
    if not runs:
        return ""
    descriptions = []
    for run in runs:
        descriptions.append(trade_run_info(run))
    return ACTIVE_RUNS_HEADER + "\n\n" + "\n\n".join(descriptions)


_TOOL_NAMES = {tool.name for tool in tools}


async def _reply(model, prompt: list[BaseMessage], conversation: list[BaseMessage]) -> AIMessage:
    """The model's reply, repaired (reply_recovery.py): a tool call written out as text
    becomes a real call, and an empty reply gets one retry, then the turn's short tool
    result or a request to repeat, never silence."""
    response = reply_recovery.recover_tool_call(await model.ainvoke(prompt), _TOOL_NAMES)
    if not reply_recovery.is_empty(response):
        return response
    log.warning("empty reply from the model; retrying once")
    retry = reply_recovery.recover_tool_call(await model.ainvoke(prompt), _TOOL_NAMES)
    if not reply_recovery.is_empty(retry):
        return retry
    log.warning("empty reply twice; falling back")
    return AIMessage(content=reply_recovery.fallback_line(conversation),
                     response_metadata=retry.response_metadata)


def _pilot_turn(messages: list[BaseMessage]) -> int:
    count = 0
    for message in messages:
        if isinstance(message, HumanMessage):
            count += 1
    return count


async def respond(state: State) -> dict:
    # Read alongside the topic decision (classify_topic) on most turns. When the topic
    # check was skipped (the pilot answering ALICE's own question), read it here, once per
    # turn: the tool loop reuses it.
    active_runs = state.active_runs
    pilot_turn = _pilot_turn(state.messages)
    if state.active_runs_turn != pilot_turn:
        active_runs = await _active_runs_note()
    context = []
    if active_runs:
        context.append(SystemMessage(content=active_runs))
    # After the persona, so its prompt-cache prefix stays the same from turn to turn.
    messages = _persona_messages() + context + _windowed_history(
        state.messages, HISTORY_TURNS, HISTORY_FULL_TOOL_RESULT_TURNS,
    )
    pending_confirmation = _just_ran_confirmation_prone_tool(state.messages)

    # Run the leg re-resolution alongside the LLM call rather than before it. It's a real
    # network round trip (resolve_leg -> ledger_client.get_in_progress_runs()), and this
    # is already the turn the pilot is waiting on a spoken reply for -- the whole reason
    # pending_confirmation exists at all is to save time on this exact turn. An LLM
    # completion is reliably slower than one HTTP call to our own ledger server, so
    # running them concurrently adds ~0 wall-clock time instead of adding it serially.
    # Routed tool groups only for the turn they were decided for; a turn that skipped the
    # topic check gets every tool.
    tool_groups = state.tool_groups if state.tool_groups_turn == pilot_turn else None
    model = _llm_for(tool_groups)
    if pending_confirmation:
        response, resolved_leg_id = await asyncio.gather(
            _reply(model, messages, state.messages),
            _resolve_last_touched_leg_id(state.messages),
        )
    else:
        response = await _reply(model, messages, state.messages)
        resolved_leg_id = None

    last_touched_leg_id = resolved_leg_id if resolved_leg_id is not None else state.last_touched_leg_id
    return {
        "messages": [response],
        "pending_confirmation": pending_confirmation,
        "last_touched_leg_id": last_touched_leg_id,
        "active_runs": active_runs,
        "active_runs_turn": pilot_turn,
    }


def _with_last_turns_groups(groups: list[str] | None, messages: list[BaseMessage]) -> list[str] | None:
    """Routed groups plus the groups of every tool ALICE used in the previous turn, so a
    follow-up keeps its tools: "Yeah, do it" after a route search says nothing about routes
    or trade runs on its own, and Tev1 sees only that line."""
    if groups is None:
        return None
    used = []
    pilot_messages_seen = 0
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            pilot_messages_seen += 1
            if pilot_messages_seen == 2:
                break
            continue
        if pilot_messages_seen == 1 and isinstance(message, AIMessage):
            for call in message.tool_calls:
                used.append(call["name"])
    wanted = set(groups) | topic_decision.groups_of_tools(used)
    ordered = []
    for group in topic_decision.TOOL_GROUPS:
        if group in wanted:
            ordered.append(group)
    return ordered


# What decline_topic is told about why, when the decision model said off-topic (it gives
# a choice, not a reason).
OFF_TOPIC_REASON = "A real-world topic with no connection to Star Citizen."


async def classify_topic(state: State) -> dict:
    """Request, social talk or off-topic. Asks the topic decision model first (Tev1 via
    topic_client.py: ~30 ms on heket, and it tells a greeting from a request wrapped in
    one); falls back to the chat-model classifier, which only decides on- or off-topic."""
    # The active runs are read alongside the decision, so respond gets them without
    # waiting; social and off-topic turns just don't use them.
    decision, active_runs = await asyncio.gather(
        topic_client.decide(_text_of(state.messages[-1])), _active_runs_note(),
    )
    pilot_turn = _pilot_turn(state.messages)
    runs_context = {"active_runs": active_runs, "active_runs_turn": pilot_turn}
    if decision is not None:
        kind = decision.kind
        runs_context["tool_groups"] = _with_last_turns_groups(decision.tool_groups, state.messages)
        runs_context["tool_groups_turn"] = pilot_turn
        # Social talk is never off-topic: _alice_just_asked reads on_topic as "the last
        # turn wasn't a decline".
        on_topic = kind != topic_decision.OFF_TOPIC
        reason = OFF_TOPIC_REASON if not on_topic else kind
        return {"on_topic": on_topic, "reason": reason, "social": kind == topic_decision.SOCIAL, **runs_context}

    messages = list(CLASSIFY_MESSAGES)
    # full_result_turns=1: classify runs before this turn's tools, so every tool result
    # it sees is a previous turn's -- all stubbed, none needed to judge topic.
    history = _windowed_history(state.messages, CLASSIFY_HISTORY_TURNS, 1)
    response = await classifier_llm.ainvoke(messages + history)
    return {"on_topic": response.on_topic, "reason": response.reason, "social": False, **runs_context}


# A greeting that takes longer than this gets SOCIAL_FALLBACK_LINE instead: it's meant to
# feel instant, and a slow heket shouldn't leave "Good morning." hanging.
SOCIAL_REPLY_TIMEOUT_S = 4.0
SOCIAL_FALLBACK_LINE = "Copy."


async def social_reply(state: State) -> dict:
    """A greeting, "how are you", thanks or farewell: one short line written by the model
    from the social-reply prompt, whose multishot examples are Jeff's own lines. Like
    decline_topic, the pilot's words go in a separate message so the prompt stays static
    for prewarming."""
    context = HumanMessage(content=f"Pilot said: {_text_of(state.messages[-1])}")
    try:
        response = await asyncio.wait_for(
            social_llm.ainvoke(SOCIAL_MESSAGES + [context]), timeout=SOCIAL_REPLY_TIMEOUT_S,
        )
        line = _text_of(response).strip().strip('"')
    except Exception:
        line = ""
    if not line:
        line = SOCIAL_FALLBACK_LINE
    return {"messages": [AIMessage(line)]}


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
    if state.social:
        return "social"
    return "respond" if state.on_topic else "decline"


def _text_of(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    parts = []
    for block in message.content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return " ".join(parts)


def _alice_just_asked(messages: list[BaseMessage], last_turn_on_topic: bool) -> bool:
    """True if ALICE's previous turn asked the pilot something, so the utterance now at
    the end of `messages` is most likely the answer.

    Short answers have nothing on-topic to recognise: on 2026-10-15 ALICE asked which
    ship, the pilot said "Uh, I'll take my railing" (Railen, misheard) and then "U-Pick",
    and classify_topic declined both. The question can be ALICE's own words or a tool's
    ("Which ship are you flying?", a hedge listing candidates) that she relayed in other
    words ("I need to know which ship..."), so any "?" in the previous turn's replies or
    tool results counts.

    Never after a decline (`last_turn_on_topic` False): a decline often ends in a
    question too ("What are we actually moving?"), and skipping the check there would
    let off-topic chat through on the second try."""
    if not last_turn_on_topic:
        return False
    if len(messages) < 2 or not isinstance(messages[-1], HumanMessage):
        return False

    for message in reversed(messages[:-1]):
        if isinstance(message, HumanMessage):
            return False
        if isinstance(message, (AIMessage, ToolMessage)) and "?" in _text_of(message):
            return True
    return False


def route_start(state: State) -> str:
    # Skips classify_topic entirely (not just overriding its verdict) when the pilot's
    # utterance is a reply to ALICE's own immediately preceding readback or question --
    # a real classifier call saved, not just a wasted one ignored.
    if state.pending_confirmation or _alice_just_asked(state.messages, state.on_topic):
        return "respond"
    return "classify_topic"


graph_builder = StateGraph(State)
graph_builder.add_node("classify_topic", classify_topic)
graph_builder.add_node("respond", respond)
graph_builder.add_node("decline", decline_topic)
graph_builder.add_node("social", social_reply)

graph_builder.add_conditional_edges(START, route_start)
graph_builder.add_conditional_edges("classify_topic", route_topic)

tool_node = ToolNode(tools)
graph_builder.add_node("tools", tool_node)
graph_builder.add_conditional_edges("respond", tools_condition)
graph_builder.add_edge("tools", "respond")

graph = graph_builder.compile(checkpointer=MemorySaver())
