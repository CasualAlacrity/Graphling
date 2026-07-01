from typing import Annotated

import chainlit as cl
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel

from app.llm import get_chat_llm
from app.persona import load_persona

load_dotenv()


class CompanionState(BaseModel):
    messages: Annotated[list[BaseMessage], add_messages]


def respond(state: CompanionState) -> dict:
    llm = get_chat_llm()
    system_prompt = SystemMessage(load_persona())
    response = llm.invoke([system_prompt] + state.messages)
    return {"messages": [response]}


graph = StateGraph(CompanionState)
graph.add_node("respond", respond)
graph.add_edge(START, "respond")
graph.add_edge("respond", END)
companion_graph = graph.compile(checkpointer=MemorySaver())


@cl.on_chat_start
async def start_chat():
    cl.user_session.set("thread_id", cl.context.session.id)


@cl.on_message
async def handle_message(message: cl.Message):
    thread_id = cl.user_session.get("thread_id")
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    user_message = HumanMessage(content=str(message.content))
    state_input = CompanionState(messages=[user_message])

    result = companion_graph.invoke(state_input, config=config)

    await cl.Message(content=result["messages"][-1].content).send()