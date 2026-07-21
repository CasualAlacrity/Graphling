from tools.cargo_packing import (
    best_container_mix,
    format_container_sizes,
    max_packable_scu,
    parse_container_sizes,
    usable_container_sizes,
)


def test_usable_container_sizes_is_the_intersection():
    assert usable_container_sizes([1, 2, 4, 8, 16, 24, 32], [1, 2, 4, 8, 16]) == [1, 2, 4, 8, 16]


def test_usable_container_sizes_handles_disjoint_sets():
    assert usable_container_sizes([1, 2], [8, 16]) == []


def test_max_packable_scu_asgard_example():
    # The concrete example from the decided design: an Asgard (180 SCU) with only
    # 32/16 SCU containers available caps out at 176, not 180 — naive full-fill
    # silently drops the 4 SCU gap.
    assert max_packable_scu(180, [16, 32]) == 176


def test_max_packable_scu_exact_fit():
    assert max_packable_scu(64, [1, 2, 4, 8, 16, 24, 32]) == 64


def test_max_packable_scu_no_sizes_returns_zero():
    assert max_packable_scu(180, []) == 0


def test_max_packable_scu_zero_capacity_returns_zero():
    assert max_packable_scu(0, [1, 2, 4, 8, 16, 24, 32]) == 0


def test_max_packable_scu_never_exceeds_capacity():
    for capacity in range(0, 50):
        assert max_packable_scu(capacity, [16, 32]) <= capacity


def test_best_container_mix_reconstructs_the_asgard_example():
    mix = best_container_mix(180, [16, 32])
    assert sum(size * count for size, count in mix.items()) == 176
    assert set(mix) <= {16, 32}


def test_best_container_mix_matches_max_packable_scu_across_range():
    sizes = [1, 2, 4, 8, 16, 24, 32]
    for capacity in range(0, 100, 7):
        mix = best_container_mix(capacity, sizes)
        assert sum(size * count for size, count in mix.items()) == max_packable_scu(capacity, sizes)


def test_best_container_mix_empty_when_no_sizes():
    assert best_container_mix(180, []) == {}


def test_parse_container_sizes_from_csv():
    assert parse_container_sizes("1,2,4,8,16,24,32") == [1, 2, 4, 8, 16, 24, 32]


def test_parse_container_sizes_handles_empty_string():
    assert parse_container_sizes("") == []
    assert parse_container_sizes(None) == []


def test_format_container_sizes_sorts_and_joins():
    assert format_container_sizes({32, 1, 8}) == "1,8,32"


def test_format_and_parse_round_trip():
    sizes = [1, 2, 4, 8, 16, 24, 32]
    assert parse_container_sizes(format_container_sizes(sizes)) == sizes
