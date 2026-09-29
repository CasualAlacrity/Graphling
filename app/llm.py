import os

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

load_dotenv()

def get_chat_llm(reasoning: bool | None = None):
    """reasoning only affects the Ollama branch today -- OpenAI/Anthropic don't take
    an equivalent override here yet. None (the default) keeps every existing caller's
    behavior unchanged (reasoning off); callers that want extended thinking on a
    specific node (e.g. graph.py's tool-calling `llm`, not classify_topic/reject)
    pass reasoning=True explicitly."""
    provider = os.getenv("LLM_PROVIDER", "ollama")

    if provider == "ollama":
        return ChatOllama(model=os.getenv("OLLAMA_CHAT_MODEL"), reasoning=False if reasoning is None else reasoning)
    elif provider == "openai":
        return ChatOpenAI(model=os.getenv("OPENAI_CHAT_MODEL"))
    elif provider == "anthropic":
        return ChatAnthropic(model=os.getenv("ANTHROPIC_CHAT_MODEL"))
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")