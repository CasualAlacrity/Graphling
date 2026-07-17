import os
from typing import Annotated

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel

from llm import get_chat_llm
from persona import load_persona
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.commodity_tool import CommodityPriceTool
from tools.uexcorp.item_tool import ItemPriceTool
from tools.uexcorp.mining_location_tool import MiningLocationTool
from tools.uexcorp.refinery_yield_tool import RefineryYieldTool
from tools.uexcorp.vehicle_purchase_tool import VehiclePurchaseTool
from tools.uexcorp.vehicle_rental_tool import VehicleRentalTool

load_dotenv()

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


class State(BaseModel):
    messages: Annotated[list[BaseMessage], add_messages]


async def respond(state: State) -> dict:
    system_prompt = SystemMessage(load_persona())
    response = await llm.ainvoke([system_prompt] + state.messages)
    return {"messages": [response]}


graph_builder = StateGraph(State)
graph_builder.add_node("respond", respond)
graph_builder.add_edge(START, "respond")

tool_node = ToolNode(tools)
graph_builder.add_node("tools", tool_node)
graph_builder.add_conditional_edges("respond", tools_condition)
graph_builder.add_edge("tools", "respond")

graph = graph_builder.compile(checkpointer=MemorySaver())
