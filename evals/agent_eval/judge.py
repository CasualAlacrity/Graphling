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


class ExtractedClaims(BaseModel):
    """What a reply actually says, pulled out as facts -- run.py compares them to the
    case's expectations in code. Extraction is a much easier job for a small model than
    grading, and keeps the pass/fail decision itself deterministic."""
    recommended_terminal: str | None
    price_for_recommended_terminal: float | None
    unsupported_claims: list[str]


_judge_llm = _build_judge_llm().with_structured_output(JudgeVerdict)
_extractor_llm = _build_judge_llm().with_structured_output(ExtractedClaims)

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


EXTRACT_PROMPT = """You are extracting facts from an AI copilot's spoken reply to a Star Citizen \
pilot. Do not grade it -- just report what it says, and flag claims the data doesn't back up.

Pilot said: {utterance}

Tool result the copilot had available: {tool_result}

The copilot's reply: {final_response}

Report:
- recommended_terminal: the single terminal the reply points the pilot to as the place to buy \
(or sell) -- its main pick, not every terminal it mentions. null if it names none.
- price_for_recommended_terminal: the price the reply states for that terminal, as a plain number \
("1.19 million" -> 1190000). null if it states none.
- unsupported_claims: each specific statement the reply AFFIRMS that the tool result contradicts or \
doesn't contain. Only things the reply says -- leaving something out (no stock level mentioned, no \
terminal named) is not a claim, so never list an omission. Include a reply that reverses buy and sell: a terminal that SELLS to the pilot \
(price_you_pay_to_acquire) must not be described as "buying", and vice versa. Also include a \
terminal recommended despite being out of stock (status_buy 1). Empty list if everything is backed."""


async def extract_claims(utterance: str, tool_result: str | None, final_response: str) -> ExtractedClaims:
    prompt = EXTRACT_PROMPT.format(
        utterance=utterance, tool_result=tool_result or "(none)", final_response=final_response,
    )
    return await _extractor_llm.ainvoke(prompt)
