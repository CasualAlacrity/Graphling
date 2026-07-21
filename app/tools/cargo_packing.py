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
