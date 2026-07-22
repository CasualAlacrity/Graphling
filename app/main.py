import logging

import chainlit as cl
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from graph import State, graph, tools

load_dotenv()
logger = logging.getLogger(__name__)
TOOL_LABELS = {tool.name: tool.progress_label for tool in tools}


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
    tool_steps: dict[str, cl.Step] = {}
    try:
        async for event in graph.astream_events(state_input, config=config, version="v2"):
            if event["event"] == "on_chat_model_stream" and event["metadata"].get("langgraph_node") == "respond":
                chunk = event["data"]["chunk"]
                if chunk.content:
                    await msg.stream_token(chunk.content)
            if event["event"] == "on_chain_end" and event["metadata"].get("langgraph_node") == "decline":
                msg.content = event["data"]["output"]["messages"][0].content
            if event["event"] == "on_tool_start":
                step = cl.Step(
                    name=TOOL_LABELS.get(event["name"], event["name"]),
                    parent_id=event["parent_ids"][0] if event["parent_ids"] else None,
                    type='tool',
                )
                step.input = event["data"]["input"]
                tool_steps[event["run_id"]] = step
                await step.send()
            if event["event"] == "on_tool_end":
                try:
                    step = tool_steps.pop(event["run_id"])
                    step.output = event["data"]["output"]
                    await step.update()
                except KeyError:
                    logger.error(f"Tool step with id {event['run_id']} was not found.")

        await msg.send()
    except Exception:
        logger.exception("Error while generating a response")
        await cl.Message(content="Something went wrong on my end — mind trying that again?").send()
