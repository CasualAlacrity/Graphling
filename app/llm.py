import os

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

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


def get_embeddings():
    provider = os.getenv("LLM_PROVIDER", "ollama")

    if provider == "ollama":
        return OllamaEmbeddings(model=os.getenv("OLLAMA_EMBED_MODEL"))
    elif provider == "openai":
        return OpenAIEmbeddings(model=os.getenv("OPENAI_EMBED_MODEL"))
    else:
        raise ValueError(f"No embedding support for LLM_PROVIDER: {provider}")