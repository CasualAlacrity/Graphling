import pytest

from db import current_user


@pytest.fixture(autouse=True)
def _reset_current_user():
    # The module under test is deliberately one mutable global (see its own docstring —
    # one pilot per process, no per-request scoping) — reset it around every test so
    # tests can't leak a pilot id into each other via import-shared state.
    current_user._current_user_id = None
    yield
    current_user._current_user_id = None


def test_get_before_set_raises():
    with pytest.raises(RuntimeError, match="No pilot identity set"):
        current_user.get_current_user_id()


def test_set_then_get_round_trips():
    current_user.set_current_user_id("123456789012345678")

    assert current_user.get_current_user_id() == "123456789012345678"


def test_set_again_overwrites_the_previous_value():
    current_user.set_current_user_id("first")
    current_user.set_current_user_id("second")

    assert current_user.get_current_user_id() == "second"
