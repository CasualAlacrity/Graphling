import os
from typing import Annotated

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel

from llm import get_chat_llm
from prompt_loader import load_prompt
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.commodity_tool import CommodityPriceTool
from tools.uexcorp.item_tool import ItemPriceTool
from tools.uexcorp.mining_location_tool import MiningLocationTool
from tools.uexcorp.refinery_yield_tool import RefineryYieldTool
from tools.uexcorp.vehicle_purchase_tool import VehiclePurchaseTool
from tools.uexcorp.vehicle_rental_tool import VehicleRentalTool


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

commodity_price_tool = CommodityPriceTool(client=uex_client)
item_price_tool = ItemPriceTool(client=uex_client)
vehicle_purchase_tool = VehiclePurchaseTool(client=uex_client)
vehicle_rental_tool = VehicleRentalTool(client=uex_client)
refinery_yield_tool = RefineryYieldTool(client=uex_client)
mining_location_tool = MiningLocationTool(client=uex_client)

tools = [commodity_price_tool, item_price_tool, vehicle_purchase_tool, vehicle_rental_tool, refinery_yield_tool,
         mining_location_tool]

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
