import os
from typing import Annotated

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel

from llm import get_chat_llm
from persona import load_persona
from tools.uexcorp import UEXCorpClient, CommodityPriceTool

load_dotenv()

uex_client = UEXCorpClient(
    api_key=os.getenv("UEXCORP_API_KEY"),
    bearer_token=os.getenv("UEXCORP_BEARER_TOKEN"),
)

commodity_price_tool = CommodityPriceTool(client=uex_client)

llm = get_chat_llm().bind_tools([commodity_price_tool])


class State(BaseModel):
    messages: Annotated[list[BaseMessage], add_messages]


async def respond(state: State) -> dict:
    system_prompt = SystemMessage(load_persona())
    response = await llm.ainvoke([system_prompt] + state.messages)
    return {"messages": [response]}


graph_builder = StateGraph(State)
graph_builder.add_node("respond", respond)
graph_builder.add_edge(START, "respond")

tool_node = ToolNode([commodity_price_tool])
graph_builder.add_node("tools", tool_node)
graph_builder.add_conditional_edges("respond", tools_condition)
graph_builder.add_edge("tools", "respond")

graph = graph_builder.compile(checkpointer=MemorySaver())
