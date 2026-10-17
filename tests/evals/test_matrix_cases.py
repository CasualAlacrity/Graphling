"""Covers evals/agent_eval/matrix_cases.py's on_script: when a scripted conversation turn
went the way the script assumed, so the matrix can flag where a workflow diverged."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals" / "agent_eval"))

from matrix_cases import CONVERSATIONS, on_script  # noqa: E402


@pytest.mark.parametrize(("intended", "called", "expected"), [
    ("best_route", "best_route", True),
    ("best_route", "(none)", False),
    ("best_route", "commodity_price_lookup", False),
    ("confirm_cargo_loaded + estimate_travel_time", "estimate_travel_time", True),
    ("best_route (compare)", "best_route", True),
    ("(none)", "(none)", True),
    ("(none) social", "best_route", False),
    ("(clarify) best_route", "(none)", True),
    ("(clarify) best_route", "best_route", True),
    ("(clarify) best_route", "start_trade_run", False),
    ("(gap) correct_trade_value", "mark_cargo_acquired", None),
])
def test_on_script(intended, called, expected):
    assert on_script(intended, called) is expected


def test_every_conversation_has_turns_and_a_purpose():
    for conversation in CONVERSATIONS:
        assert conversation.turns, conversation.name
        assert conversation.purpose, conversation.name
