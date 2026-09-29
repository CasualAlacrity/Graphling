import os

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

load_dotenv()


def get_chat_llm():
    """The general-purpose model -- graph.py's respond (tool-calling), where the
    harness has actually shown reasoning helps (evals/agent_eval/results/). Ollama's
    reasoning mode defaults on here; set OLLAMA_REASONING=false in .env to turn it
    back off."""
    provider = os.getenv("LLM_PROVIDER", "ollama")

    if provider == "ollama":
        reasoning = os.getenv("OLLAMA_REASONING", "true").strip().lower() != "false"
        return ChatOllama(model=os.getenv("OLLAMA_CHAT_MODEL"), reasoning=reasoning)
    elif provider == "openai":
        return ChatOpenAI(model=os.getenv("OPENAI_CHAT_MODEL"))
    elif provider == "anthropic":
        return ChatAnthropic(model=os.getenv("ANTHROPIC_CHAT_MODEL"))
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")


def get_classification_llm():
    """For classify_topic and the reject-voice line -- both a fast binary decision
    and a short canned-shape line, neither needing get_chat_llm's reasoning or
    necessarily its model size. Falls back to *_CHAT_MODEL when a dedicated
    *_CLASSIFICATION_MODEL isn't set, so an unset .env behaves like one model
    everywhere, same as before this split existed. Reasoning defaults off (the
    fast/simple case here); set OLLAMA_CLASSIFICATION_REASONING=true in .env if a
    future classification model actually needs it turned on."""
    provider = os.getenv("LLM_PROVIDER", "ollama")

    if provider == "ollama":
        model = os.getenv("OLLAMA_CLASSIFICATION_MODEL") or os.getenv("OLLAMA_CHAT_MODEL")
        reasoning = os.getenv("OLLAMA_CLASSIFICATION_REASONING", "false").strip().lower() == "true"
        return ChatOllama(model=model, reasoning=reasoning)
    elif provider == "openai":
        model = os.getenv("OPENAI_CLASSIFICATION_MODEL") or os.getenv("OPENAI_CHAT_MODEL")
        return ChatOpenAI(model=model)
    elif provider == "anthropic":
        model = os.getenv("ANTHROPIC_CLASSIFICATION_MODEL") or os.getenv("ANTHROPIC_CHAT_MODEL")
        return ChatAnthropic(model=model)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
