import os
from typing import Annotated

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage
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
    decline_line_str: str | None = None


class TopicClassification(BaseModel):
    on_topic: bool
    decline_line_str: str | None = None
    reason: str


class DeclineLine(BaseModel):
    id: int
    tag: str
    text: str


load_dotenv()

PERSONA_TEMPLATE = load_prompt("alice-persona")
CLASSIFY_TEMPLATE = load_prompt("topic-classification")

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
# TradeAdvisorTool ("is my committed run still the best call") is parked, not deleted —
# best_route below covers the question pilots actually ask ("what's the best route from
# here"); the committed-vs-alternatives framing wasn't judged worth keeping in the tool
# list. Code stays intact in case that changes.

trade_run_tools = [mark_arrived_tool, mark_cargo_acquired_tool, mark_cargo_sold_tool,
                   confirm_cargo_loaded_tool, confirm_cargo_unloaded_tool, trade_run_status_tool,
                   cargo_packing_tool]

# General Tools
timer_tool = StartTimerTool()
check_timer_tool = CheckTimerTool()
travel_time_tool = TravelTimeTool(uex_client=uex_client, scw_client=scw_client)
best_route_tool = BestRouteTool(uex_client=uex_client, scw_client=scw_client)

general_tools = [timer_tool, check_timer_tool, travel_time_tool, best_route_tool]

tools = uex_backed_tools + trade_run_tools + general_tools

llm = get_chat_llm().bind_tools(tools)
classifier_llm = get_chat_llm().with_structured_output(TopicClassification)


async def respond(state: State) -> dict:
    messages = PERSONA_TEMPLATE.invoke({}).to_messages()
    response = await llm.ainvoke(messages + state.messages)
    return {"messages": [response]}


async def classify_topic(state: State) -> dict:
    messages = CLASSIFY_TEMPLATE.invoke({}).to_messages()
    response = await classifier_llm.ainvoke(messages + state.messages)
    return {
        "on_topic": response.on_topic,
        "decline_line_str": response.decline_line_str
    }


async def decline_topic(state: State) -> dict:
    return {"messages": [AIMessage(state.decline_line_str)]}


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
