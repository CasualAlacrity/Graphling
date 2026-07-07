import logging

import chainlit as cl
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from graph import State, graph

load_dotenv()
logger = logging.getLogger(__name__)


@cl.on_chat_start
async def start_chat():
    cl.user_session.set("thread_id", cl.context.session.id)


@cl.on_message
async def handle_message(message: cl.Message):
    thread_id = cl.user_session.get("thread_id")
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [cl.LangchainCallbackHandler()],
    }

    user_message = HumanMessage(content=str(message.content))
    state_input = State(messages=[user_message])

    msg = cl.Message(content="")
    try:
        async for event in graph.astream_events(state_input, config=config, version="v2"):
            if event["event"] == "on_chat_model_stream" and event["metadata"].get("langgraph_node") == "respond":
                chunk = event["data"]["chunk"]
                if chunk.content:
                    await msg.stream_token(chunk.content)
        await msg.send()
    except Exception:
        logger.exception("Error while generating a response")
        await cl.Message(content="Something went wrong on my end — mind trying that again?").send()
