"""Container-packing math for cargo hold fill. A ship's true reachable SCU depends on
which container sizes are actually loadable at both ends of a route, not just its raw
cargo capacity — a hold rarely divides evenly into the sizes available, so naive
"ship max SCU" overstates what's actually achievable. Pure functions, no Qt/DB
dependencies, so both app/db and app/overlay can use this without a layering inversion.
"""


def usable_container_sizes(origin_sizes, destination_sizes) -> list[int]:
    # A size is only usable for a route if it's loadable at the origin AND unloadable at
    # the destination — the intersection of what both terminals support.
    return sorted(set(origin_sizes) & set(destination_sizes))


def max_packable_scu(capacity, container_sizes) -> int:
    """Unbounded-knapsack DP: the largest total <= capacity summable from container_sizes
    with repetition allowed (any number of each size). E.g. 180 SCU capacity with only
    32/16 SCU containers available caps out at 176, not 180 — the 4 SCU gap a naive
    full-fill calculation silently ignores."""
    capacity = int(capacity)
    sizes = sorted({size for size in container_sizes if size > 0})
    if not sizes or capacity <= 0:
        return 0

    best = [0] * (capacity + 1)
    for total in range(1, capacity + 1):
        best[total] = best[total - 1]
        for size in sizes:
            if size <= total:
                best[total] = max(best[total], best[total - size] + size)
    return best[capacity]


def best_container_mix(capacity, container_sizes) -> dict[int, int]:
    """The {size: count} combination achieving max_packable_scu — what to actually buy to
    hit the best fill rate. Reconstructed via the same DP, tracking which size (if any)
    was added at each step."""
    capacity = int(capacity)
    sizes = sorted({size for size in container_sizes if size > 0})
    if not sizes or capacity <= 0:
        return {}

    best = [0] * (capacity + 1)
    used_size = [None] * (capacity + 1)
    for total in range(1, capacity + 1):
        best[total] = best[total - 1]
        for size in sizes:
            # >= (not >) so that among equally-good fills, the largest size tried last
            # wins the tie — fewer, bigger boxes to actually carry rather than an
            # arbitrary equal-SCU pile of small ones.
            if size <= total and best[total - size] + size >= best[total]:
                best[total] = best[total - size] + size
                used_size[total] = size

    mix: dict[int, int] = {}
    remaining = capacity
    while remaining > 0:
        size = used_size[remaining]
        if size is None:
            remaining -= 1
        else:
            mix[size] = mix.get(size, 0) + 1
            remaining -= size
    return mix


def parse_container_sizes(csv_string) -> list[int]:
    if not csv_string:
        return []
    return [int(part) for part in csv_string.split(",") if part]


def format_container_sizes(sizes) -> str:
    return ",".join(str(size) for size in sorted(sizes))


def reachable_scu(route, cargo_scu: int | None = None) -> int:
    """Extracted from the overlay's ResultsPanel.reachable_scu_for — same true-capacity
    logic (DP-based, not naive full-fill), just without the UI's self.cargo_scu binding.
    cargo_scu caps to a specific ship's hold when known; leave unset to use only the
    route's own origin/destination stock ceiling."""
    capacity = min(route.scu_origin, route.scu_destination)
    if cargo_scu is not None and cargo_scu > 0:
        capacity = min(capacity, cargo_scu)

    sizes = usable_container_sizes(route.container_sizes_origin, route.container_sizes_destination)
    if not sizes:
        return capacity
    return max_packable_scu(capacity, sizes)


def estimated_profit(route, scu: int) -> float:
    """Extracted from the overlay's ResultsPanel.estimated_profit_for — same formula,
    just taking an already-computed reachable_scu instead of calling back into a method."""
    return (route.price_destination - route.price_origin) * scu


# Placeholder values pending real footage-derived benchmarks (per-crate handling time,
# and how much slower a big crate is to move than a small one) — not yet supplied. Rough,
# deliberately-labeled guesses so the Trade Advisor's formula is wired and testable now;
# swap these for real numbers later without touching any caller.
DEFAULT_SECONDS_PER_CRATE = 4.0
DEFAULT_SIZE_MODIFIERS = {1: 0.6, 2: 0.75, 4: 0.9, 8: 1.1, 16: 1.4, 24: 1.7, 32: 2.0}


def estimate_transfer_time(
        mix: dict[int, int],
        seconds_per_crate: float = DEFAULT_SECONDS_PER_CRATE,
        size_modifiers: dict[int, float] | None = None,
) -> float:
    """Estimated seconds to move a given {size: count} crate mix — one direction (loading
    or unloading), not round-trip. Bigger crates take proportionally longer to handle than
    small ones, not just more total SCU — see the module docstring's discussion; a 32 SCU
    crate isn't "32x a 1 SCU crate," it's a handful of crates with a heavier per-crate cost."""
    modifiers = size_modifiers if size_modifiers is not None else DEFAULT_SIZE_MODIFIERS
    return sum(count * seconds_per_crate * modifiers.get(size, 1.0) for size, count in mix.items())
