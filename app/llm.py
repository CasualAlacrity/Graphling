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


ANTHROPIC_CACHE_MODES = ("off", "system", "full")


def anthropic_cache_mode() -> str:
    """Prompt caching for respond's calls -- only meaningful when LLM_PROVIDER=anthropic
    (OpenAI caches long prefixes automatically, Ollama has its own prefix cache, see
    graph.prewarm), so every other provider reports "off" regardless of .env.

    "system": one breakpoint after tools+persona, the ~11.5k-token prefix that's
    identical on every call. "full": that, plus a rolling breakpoint at the end of the
    conversation, so a turn's second respond call (after its tools ran) also reuses the
    history the first call just wrote. Never only the rolling one: _windowed_history
    slides the history's start every turn, so without the fixed breakpoint nothing
    written would ever be read back -- every call would pay the 1.25x write price."""
    if os.getenv("LLM_PROVIDER", "ollama") != "anthropic":
        return "off"
    mode = os.getenv("ANTHROPIC_CACHE_MODE", "system").strip().lower()
    if mode not in ANTHROPIC_CACHE_MODES:
        raise ValueError(f"Unknown ANTHROPIC_CACHE_MODE: {mode} (expected one of {ANTHROPIC_CACHE_MODES})")
    return mode


def anthropic_cache_control() -> dict:
    """The cache_control block every breakpoint uses. ANTHROPIC_CACHE_TTL is "5m"
    (writes cost 1.25x input) or "1h" (writes cost 2x) -- 1h only pays off when the
    pilot regularly goes 5+ minutes between turns, e.g. a long quantum jump."""
    ttl = os.getenv("ANTHROPIC_CACHE_TTL", "5m").strip().lower()
    if ttl not in ("5m", "1h"):
        raise ValueError(f"Unknown ANTHROPIC_CACHE_TTL: {ttl} (expected 5m or 1h)")
    return {"type": "ephemeral", "ttl": ttl}
