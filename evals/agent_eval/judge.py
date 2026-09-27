"""The eval harness's judge — a separate, fixed model that grades whether a turn's
final spoken response actually conveys the right information, independent of
whichever model is under test.

Deliberately not `get_chat_llm()` (app/llm.py), which is wired to whatever's being
benchmarked via LLM_PROVIDER/*_CHAT_MODEL — a judge built the same way could end up
grading a model with itself, and verdicts wouldn't be comparable across benchmark
runs. Own env vars instead: HARNESS_JUDGE_PROVIDER/HARNESS_JUDGE_MODEL, defaulting
to a cheap, fast, independent model.

Only ever asked to judge the *substance* of a response (does it convey the right
information), never tool selection or args -- those are checked deterministically
in run.py, no LLM call needed for something code can already verify exactly.
"""

import os

from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from pydantic import BaseModel


class JudgeVerdict(BaseModel):
    response_correct: bool
    reasoning: str


def _build_judge_llm():
    # OpenAI by default -- not a preference, just the provider with a real key
    # configured in this project's .env today (Anthropic's is still the
    # your-key-here placeholder). Override via HARNESS_JUDGE_PROVIDER/_MODEL
    # once a real Anthropic (or other) key is in place, if that's preferred.
    provider = os.getenv("HARNESS_JUDGE_PROVIDER", "openai")
    model = os.getenv("HARNESS_JUDGE_MODEL", "gpt-4o-mini")
    if provider == "ollama":
        return ChatOllama(model=model, reasoning=False)
    elif provider == "openai":
        return ChatOpenAI(model=model)
    elif provider == "anthropic":
        return ChatAnthropic(model=model)
    else:
        raise ValueError(f"Unknown HARNESS_JUDGE_PROVIDER: {provider}")


_judge_llm = _build_judge_llm().with_structured_output(JudgeVerdict)

JUDGE_PROMPT = """You are grading whether an AI copilot's spoken response to a Star Citizen \
pilot correctly conveys the right information. Grade substance only -- not style, tone, or \
exact phrasing.

Pilot said: {utterance}

What a correct response should convey: {expected_outcome}

Tool result the copilot had available (may be empty if no tool was expected): {tool_result}

The copilot's actual final spoken response: {final_response}

Does the actual response correctly and adequately convey the expected outcome? Minor \
phrasing differences are fine. Missing the key fact, stating something contradictory to it, \
or inventing information not supported by the tool result are not."""


async def judge_response(
    utterance: str, expected_outcome: str, tool_result: str | None, final_response: str,
) -> JudgeVerdict:
    prompt = JUDGE_PROMPT.format(
        utterance=utterance,
        expected_outcome=expected_outcome,
        tool_result=tool_result or "(none)",
        final_response=final_response,
    )
    return await _judge_llm.ainvoke(prompt)
