from tools.uexcorp.reachability import build_reachability, destination_full, source_sold_out


def _row(commodity, origin, destination, status_origin=3, scu_origin=100, status_destination=2, scu_destination=100):
    return {
        "id_commodity": commodity, "id_terminal_origin": origin, "id_terminal_destination": destination,
        "status_origin": status_origin, "scu_origin": scu_origin,
        "status_destination": status_destination, "scu_destination": scu_destination,
    }


def test_offers_and_accepts_come_from_route_rows():
    result = build_reachability({1: [_row(10, 1, 2), _row(20, 1, 3)]})
    assert result.offers == {1: [10, 20]}
    assert result.accepts == {2: [10], 3: [20]}


def test_a_sold_out_origin_offers_nothing_but_is_still_known():
    result = build_reachability({1: [_row(10, 1, 2, status_origin=1)]})
    assert result.offers == {1: []}
    assert result.accepts == {2: [10]}


def test_a_full_destination_accepts_nothing():
    result = build_reachability({1: [_row(10, 1, 2, status_destination=7), _row(20, 1, 3, scu_destination=0)]})
    assert result.offers == {1: [10, 20]}
    assert result.accepts == {}


def test_zero_scu_counts_as_sold_out_or_full():
    assert source_sold_out(status=3, scu=0)
    assert destination_full(status=3, scu=0)
    assert not source_sold_out(status=3, scu=1)
    assert not destination_full(status=3, scu=1)


def test_json_round_trip_restores_integer_keys():
    from tools.uexcorp.reachability import Reachability

    original = build_reachability({1: [_row(10, 1, 2)]})
    restored = Reachability.model_validate(original.model_dump(mode="json"))
    assert restored.offers == {1: [10]}
