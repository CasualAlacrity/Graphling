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
    # Grade only which tool was called and with what args -- skip the judge. For tools
    # (best_route) whose reply depends on fixtures world.py doesn't have yet, where the
    # thing under test is whether the MODEL sets the right arg from a spoken phrasing.
    args_only: bool = False
    # Every arg the model sent must be in expected_args -- anything else it set
    # (a location it invented, a flag nobody asked for) fails. An arg left at its
    # schema default counts as not sent. Without this, expected_args is only a
    # "contains" check and extra args pass silently.
    exact_args: bool = False
    # Per-phrasing override of expected_args, for when phrasings differ in a spoken value
    # (a different origin station). Keyed by the exact phrasing string.
    args_by_phrasing: dict[str, dict] = {}
    # What the reply must say, scored as separate dimensions (run.py): the terminal it
    # recommends, the price it states for that terminal (within 1%), and the unit
    # ("SCU" -- UEX prices are per SCU and pilots expect to hear that). Any set makes the
    # case "structured": scored on these plus unsupported claims instead of the single
    # free-text judge verdict.
    expected_location: str | None = None
    expected_price: float | None = None
    expected_unit: str | None = None

    @property
    def structured_reply(self) -> bool:
        return any(v is not None for v in (self.expected_location, self.expected_price, self.expected_unit))

    def expected_args_for(self, phrasing: str) -> dict | None:
        return self.args_by_phrasing.get(phrasing, self.expected_args)


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
        exact_args=True,  # commodity_price_lookup has no buy/sell or sort arg; no location was spoken
        expected_location="Baijini Point", expected_price=3.15, expected_unit="SCU",
        expected_outcome=(
            "Should report Baijini Point as the cheapest terminal to buy Laranite, around 3.15 aUEC per SCU."
        ),
    ),
    HarnessCase(
        id="commodity_price_cheapest_skips_out_of_stock",
        phrasings=[
            "What's the cheapest place to buy Agricium?",
            "Where can I get Agricium for less?",
            "I need to buy some Agricium, where's it cheap?",
        ],
        expected_tool="commodity_price_lookup",
        expected_args={"commodity": "Agricium"},
        exact_args=True,
        # The reply may mention Baijini (cheaper but empty); what matters is which it recommends.
        expected_location="Port Tressler", expected_price=26.10, expected_unit="SCU",
        expected_outcome=(
            "Baijini Point has the lowest Agricium price (25.40) but is out of stock "
            "(status_buy 1), so it must NOT be recommended as the place to buy. Should "
            "report Port Tressler, the cheapest terminal that actually has stock, around "
            "26.10 aUEC per SCU."
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
        exact_args=True,
        expected_location="Baijini Point", expected_price=1_190_000,  # ships have no per-SCU price
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

    # --- best_route's sell_at_nqa_only flag (2026-10-06) ---------------------------------
    # args_only: the model's reading of the phrasing is the thing under test, not the
    # route reply (world.py has no best_route fixtures yet). exact_args: every arg is
    # asserted -- origin, and that no other flag got set -- not just the NQA one. Origins
    # are kept to exact stations (Seraphim, Grim Hex) so origin_mode stays "exact" and
    # isn't a second thing under test. See app/tools/best_route_tool.py's Field
    # description for the vocabulary the model is told to listen for.
    HarnessCase(
        id="best_route_nqa_explicit",
        phrasings=[
            "Best route from Seraphim, sell at an NQA terminal",
            "Find me a route that ends at a no-questions-asked station out of Seraphim",
            "Best route out of Grim Hex, but only NQA",
        ],
        expected_tool="best_route",
        expected_args={"origin": "Seraphim", "sell_at_nqa_only": True},
        args_by_phrasing={
            "Best route out of Grim Hex, but only NQA": {"origin": "Grim Hex", "sell_at_nqa_only": True},
        },
        exact_args=True,
        expected_outcome="Not graded (args_only).",
        args_only=True,
    ),
    HarnessCase(
        id="best_route_nqa_slang",
        phrasings=[
            "I need to dump some hot cargo, route from Grim Hex",
            "Where can I fence stuff? Route out of Seraphim",
            "Best haul out of Seraphim that ends somewhere shady",
            "Boosted cargo, route out of Grim Hex",
            "Best black market run from Seraphim",
            "Something off the books, best route from Seraphim",
        ],
        expected_tool="best_route",
        expected_args={"origin": "Seraphim", "sell_at_nqa_only": True},
        args_by_phrasing={
            "I need to dump some hot cargo, route from Grim Hex": {"origin": "Grim Hex", "sell_at_nqa_only": True},
            "Boosted cargo, route out of Grim Hex": {"origin": "Grim Hex", "sell_at_nqa_only": True},
        },
        exact_args=True,
        expected_outcome="Not graded (args_only).",
        args_only=True,
    ),
    HarnessCase(
        id="best_route_nqa_oblique",
        phrasings=[
            "I've got goods of questionable provenance, find me a route from Seraphim",
            "Route from Seraphim to somewhere that won't ask where I got the cargo",
        ],
        expected_tool="best_route",
        expected_args={"origin": "Seraphim", "sell_at_nqa_only": True},
        exact_args=True,
        expected_outcome="Not graded (args_only).",
        args_only=True,
    ),
    # Each phrasing here asserts what SHOULD be set instead (or nothing), so a flag the
    # model wrongly turns on -- or a real one it drops -- both fail.
    HarnessCase(
        id="best_route_nqa_must_not_set",
        phrasings=[
            "Best route from Seraphim",
            "Best route from Seraphim, autoload only",
            "Best route out of Seraphim, space stations only",
            "Best route from Seraphim, avoid any no-questions-asked terminals",
            "I got pirated at Seraphim, what's the best route out of here?",
            "Best legit route from Seraphim, somewhere reputable",
        ],
        expected_tool="best_route",
        expected_args={"origin": "Seraphim"},
        args_by_phrasing={
            "Best route from Seraphim, autoload only": {"origin": "Seraphim", "require_autoload": True},
            "Best route out of Seraphim, space stations only": {
                "origin": "Seraphim", "exclude_ground_stations": True,
            },
        },
        exact_args=True,
        expected_outcome="Not graded (args_only).",
        args_only=True,
    ),
]

# No general-knowledge/no-tool case for now -- those grade the model's own training
# data on Star Citizen lore, not ALICE's actual tool/response accuracy. Revisit once
# SC context is supplied via RAG rather than relying on what a model happened to
# learn.
