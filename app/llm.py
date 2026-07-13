import os

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

load_dotenv()

def get_chat_llm():
    provider = os.getenv("LLM_PROVIDER", "ollama")

    if provider == "ollama":
        return ChatOllama(model=os.getenv("OLLAMA_CHAT_MODEL"), reasoning=False)
    elif provider == "openai":
        return ChatOpenAI(model=os.getenv("OPENAI_CHAT_MODEL"))
    elif provider == "anthropic":
        return ChatAnthropic(model=os.getenv("ANTHROPIC_CHAT_MODEL"))
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")