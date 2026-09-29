import asyncio
import os
from typing import Annotated

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel

from llm import get_chat_llm
from prompt_loader import load_prompt
from tools.best_route_tool import BestRouteTool
from tools.starcitizenwiki.client import StarCitizenWikiClient
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


class TopicClassification(BaseModel):
    on_topic: bool
    reason: str


class RejectLine(BaseModel):
    line: str


load_dotenv()

PERSONA_TEMPLATE = load_prompt("alice-persona")
CLASSIFY_TEMPLATE = load_prompt("topic-classification")
REJECT_TEMPLATE = load_prompt("topic-reject-voice")

uex_client = UEXCorpClient(
    api_key=os.getenv("UEXCORP_API_KEY"),
    bearer_token=os.getenv("UEXCORP_BEARER_TOKEN"),
)
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

# reasoning=True only here, not on classifier_llm/reject_llm -- respond is where the
# model actually decides whether/which tool to call, the one place the missed-tool-call
# failures showed up. classify_topic and the reject line stay reasoning-off on purpose:
# a fast binary decision and a short in-character line don't need it, and reasoning
# costs real latency (see docs/todo.md's agent_eval entries for what's being tested
# against). Experimental -- the harness is what decides whether this actually helps
# versus just costing tokens/latency.
llm = get_chat_llm(reasoning=True).bind_tools(tools)
classifier_llm = get_chat_llm().with_structured_output(TopicClassification)
reject_llm = get_chat_llm().with_structured_output(RejectLine)

# name -> (llm instance, its static system-prompt template). Only a content-invariant
# system prompt is worth prewarming at all: Ollama's cache is keyed on exact prompt
# text, so a template that interpolates per-turn variables into the system message
# (unlike these three) would never match a real call's prompt anyway — see
# decline_topic's own note on why REJECT_TEMPLATE stays variable-free for this reason.
_PREWARM_TARGETS = [
    ("persona", llm, PERSONA_TEMPLATE),
    ("classifier", classifier_llm, CLASSIFY_TEMPLATE),
    ("reject", reject_llm, REJECT_TEMPLATE),
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

    to_warm = [(name, model, tmpl) for name, model, tmpl in _PREWARM_TARGETS if name not in _warmed]
    if not to_warm:
        return

    warmup_turn = [HumanMessage(content="(warmup)")]

    async def _warm_one(name: str, model, template) -> None:
        try:
            await model.ainvoke(
                template.invoke({}).to_messages() + warmup_turn,
                config={"run_name": f"prewarm-{name}", "tags": ["warmup"]},
            )
            _warmed.add(name)
        except Exception as exc:
            print(f"[ALICE] Prewarm failed for {name!r} ({exc}) — its first real call will be slow instead.")

    await asyncio.gather(*[_warm_one(name, model, tmpl) for name, model, tmpl in to_warm])


async def respond(state: State) -> dict:
    messages = PERSONA_TEMPLATE.invoke({}).to_messages()
    response = await llm.ainvoke(messages + state.messages)
    return {"messages": [response]}


async def classify_topic(state: State) -> dict:
    messages = CLASSIFY_TEMPLATE.invoke({}).to_messages()
    response = await classifier_llm.ainvoke(messages + state.messages)
    return {"on_topic": response.on_topic, "reason": response.reason}


async def decline_topic(state: State) -> dict:
    # REJECT_TEMPLATE.invoke({}) takes no variables on purpose — utterance/reason are
    # appended as a separate HumanMessage instead of interpolated into the system
    # prompt, so the system prompt stays content-invariant and prewarm() can actually
    # warm it (a template with per-turn variables baked into its system message would
    # never match a real call's prompt text, so warming it with dummy content
    # wouldn't help — see _PREWARM_TARGETS' note).
    context = HumanMessage(
        content=f"Pilot said: {state.messages[-1].content}\nWhy it's off-topic: {state.reason}"
    )
    messages = REJECT_TEMPLATE.invoke({}).to_messages() + [context]
    response = await reject_llm.ainvoke(messages)
    return {"messages": [AIMessage(response.line)]}


def route_topic(state: State) -> str:
    return "respond" if state.on_topic else "decline"


graph_builder = StateGraph(State)
graph_builder.add_node("classify_topic", classify_topic)
graph_builder.add_node("respond", respond)
graph_builder.add_node("decline", decline_topic)

graph_builder.add_edge(START, "classify_topic")
graph_builder.add_conditional_edges("classify_topic", route_topic)

tool_node = ToolNode(tools)
graph_builder.add_node("tools", tool_node)
graph_builder.add_conditional_edges("respond", tools_condition)
graph_builder.add_edge("tools", "respond")

graph = graph_builder.compile(checkpointer=MemorySaver())
