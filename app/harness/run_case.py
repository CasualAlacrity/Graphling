"""Runnable example of the deterministic harness's fake-client layer (docs/todo.md
Phase 3, item 1) — exercises one real tool against harness/world.py's fixed data,
with zero network access and no API keys needed.

Deliberately doesn't invoke the LLM or the graph — this layer only has to prove the
fakes work and a tool resolves correctly against them. Whether the *model* picks
this tool, with these args, for a given spoken utterance is Phase 3's next, separate
piece (the utterance -> expected-tool-call dataset), built on top of this once it
exists.

Run from the repo root:
    cd app && ../.venv/bin/python -m harness.run_case
"""

import asyncio

from harness import world
from harness.fakes import install_fakes
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.commodity_tool import CommodityPriceTool


async def main() -> int:
    # Fake credentials — install_fakes() means these never reach a real request.
    client = UEXCorpClient(api_key="fake", bearer_token="fake")
    tool = CommodityPriceTool(client=client)

    with install_fakes():
        result = await tool._arun(commodity="Laranite")

    cheapest = result["cheapest_to_buy"]
    best_sell = result["best_to_sell"]

    print("commodity_price_lookup(commodity='Laranite') ->")
    print(f"  cheapest_to_buy: {cheapest}")
    print(f"  best_to_sell:    {best_sell}")

    expected_terminal = world.BAIJINI_POINT.name  # the cheaper of the two fake rows
    actual_terminal = cheapest["terminal_name"] if cheapest else None

    print()
    if actual_terminal != expected_terminal:
        print(f"FAIL: expected cheapest_to_buy at {expected_terminal!r}, got {actual_terminal!r}")
        return 1

    print(f"PASS: cheapest_to_buy correctly resolved to {expected_terminal!r} — no network access used.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
