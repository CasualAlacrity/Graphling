import os

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from auth.session_auth import PilotSessionAuth

load_dotenv()

# LLM_PROVIDER=alice: model calls go through ALICE's own server (server/routes/llm.py,
# docs/llm-gateway.md), which picks the real model and holds every provider key. The
# client asks for a ROLE, never a model -- the server can swap models, thinking, and
# fallback without a client release.
ALICE_CHAT_ROLE = "alice-chat"
ALICE_CLASSIFY_ROLE = "alice-classify"
ALICE_SMALL_CHAT_ROLE = "alice-chat-small"


def _alice_llm(role: str, reasoning: bool) -> ChatOllama:
    """ChatOllama pointed at the gateway, which speaks Ollama's native /api/chat -- the
    same protocol the evals ran against, so the heket path is unchanged.

    The gateway decides whether the model actually thinks (per role). `reasoning` here
    only mirrors that so ChatOllama keeps the thinking in each AIMessage and sends it
    back in later history -- the same message shape the evals ran with."""
    api_url = os.getenv("ALICE_API_URL", "http://localhost:8000").rstrip("/")
    return ChatOllama(
        model=role, reasoning=reasoning, base_url=f"{api_url}/llm", client_kwargs={"auth": PilotSessionAuth()},
    )


def get_chat_llm():
    """The general-purpose model -- graph.py's respond (tool-calling), where the
    harness has actually shown reasoning helps (evals/agent_eval/results/). Ollama's
    reasoning mode defaults on here; set OLLAMA_REASONING=false in .env to turn it
    back off."""
    provider = os.getenv("LLM_PROVIDER", "ollama")

    if provider == "alice":
        return _alice_llm(ALICE_CHAT_ROLE, reasoning=True)
    elif provider == "ollama":
        reasoning = os.getenv("OLLAMA_REASONING", "true").strip().lower() != "false"
        return ChatOllama(model=os.getenv("OLLAMA_CHAT_MODEL"), reasoning=reasoning)
    elif provider == "openai":
        return ChatOpenAI(model=os.getenv("OPENAI_CHAT_MODEL"))
    elif provider == "anthropic":
        return ChatAnthropic(model=os.getenv("ANTHROPIC_CHAT_MODEL"))
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")


def get_classification_llm():
    """For classify_topic -- a fast decision, needing neither get_chat_llm's reasoning
    nor necessarily its model size. Only classifies: ALICE's spoken lines go through
    get_chat_llm_small(), so this can become a pure decision model. Falls back to *_CHAT_MODEL when a dedicated
    *_CLASSIFICATION_MODEL isn't set, so an unset .env behaves like one model
    everywhere, same as before this split existed. Reasoning defaults off (the
    fast/simple case here); set OLLAMA_CLASSIFICATION_REASONING=true in .env if a
    future classification model actually needs it turned on."""
    provider = os.getenv("LLM_PROVIDER", "ollama")

    if provider == "alice":
        return _alice_llm(ALICE_CLASSIFY_ROLE, reasoning=False)
    elif provider == "ollama":
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


def get_chat_llm_small():
    """A chat model, as small as will still sound like ALICE -- for the short spoken
    lines that need her voice but no tools or reasoning: the off-topic decline and the
    social reply (graph.py). Kept apart from get_classification_llm() so that one can
    become a pure classifier (a decision model that can't write a sentence) without
    taking these lines with it. Falls back to *_CHAT_MODEL when *_SMALL_CHAT_MODEL isn't
    set. Reasoning defaults off: a one-line "Good morning to you." shouldn't wait on
    thinking. OLLAMA_SMALL_CHAT_REASONING=true turns it on."""
    provider = os.getenv("LLM_PROVIDER", "ollama")

    if provider == "alice":
        return _alice_llm(ALICE_SMALL_CHAT_ROLE, reasoning=False)
    elif provider == "ollama":
        model = os.getenv("OLLAMA_SMALL_CHAT_MODEL") or os.getenv("OLLAMA_CHAT_MODEL")
        reasoning = os.getenv("OLLAMA_SMALL_CHAT_REASONING", "false").strip().lower() == "true"
        return ChatOllama(model=model, reasoning=reasoning)
    elif provider == "openai":
        model = os.getenv("OPENAI_SMALL_CHAT_MODEL") or os.getenv("OPENAI_CHAT_MODEL")
        return ChatOpenAI(model=model)
    elif provider == "anthropic":
        model = os.getenv("ANTHROPIC_SMALL_CHAT_MODEL") or os.getenv("ANTHROPIC_CHAT_MODEL")
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
