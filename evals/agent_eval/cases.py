"""The agent-behavior eval dataset — docs/todo.md Phase 3's "utterances -> expected
tool call(s) + args" idea, generalized to also cover classify_topic correctness and
toolless reasoning responses (see the plan this was built from for why).

Small and hand-picked on purpose, matching docs/todo.md's own framing: a starting
sample to grow over time, not a generated/exhaustive one. Every tool-call case here
is backed by harness/world.py's fixed data (installed via harness/fakes.py's
install_fakes()) so results are reproducible regardless of what UEX/the wiki
actually say today.
"""

from pydantic import BaseModel


class HarnessCase(BaseModel):
    id: str
    phrasings: list[str]
    expected_on_topic: bool = True
    expected_tool: str | None = None   # None = no tool call is correct here
    expected_args: dict | None = None  # only checked when expected_tool is set
    expected_outcome: str               # what the judge checks the final response against


CASES: list[HarnessCase] = [
    HarnessCase(
        id="commodity_price_cheapest",
        phrasings=[
            "What's the cheapest place to buy Laranite?",
            "Where can I get Laranite for less?",
            "I need to buy some Laranite, where's it cheap?",
        ],
        expected_tool="commodity_price_lookup",
        expected_args={"commodity": "Laranite"},
        expected_outcome=(
            "Should report Baijini Point as the cheapest terminal to buy Laranite, "
            "around 3.15 aUEC (per unit or per SCU -- either phrasing is fine)."
        ),
    ),
    HarnessCase(
        id="vehicle_purchase_cheapest",
        phrasings=[
            "Where's the cheapest place to buy a Cutlass Black?",
            "I want to buy a Cutlass Black, what's the best price?",
            "Find me a good deal on a Cutlass Black.",
        ],
        expected_tool="vehicle_purchase_lookup",
        expected_args={"vehicle": "Cutlass Black"},
        expected_outcome=(
            "Should report Baijini Point as the cheapest terminal to buy a Cutlass Black, "
            "around 1,190,000 aUEC."
        ),
    ),
    HarnessCase(
        id="off_topic_greeting",
        phrasings=[
            "Good morning.",
            "Hey, how are you?",
            "Morning, hope you're doing well.",
        ],
        expected_on_topic=False,
        expected_outcome="Should decline small talk without answering it, staying in character.",
    ),
]

# No general-knowledge/no-tool case for now -- those grade the model's own training
# data on Star Citizen lore, not ALICE's actual tool/response accuracy. Revisit once
# SC context is supplied via RAG rather than relying on what a model happened to
# learn.
