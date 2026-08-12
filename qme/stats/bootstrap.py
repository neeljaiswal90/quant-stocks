"""Deterministic stationary-bootstrap index generation."""

from __future__ import annotations

from qme.stats.rng import MAX_GEOMETRIC_MEAN, UINT32_MODULUS, Pcg32, StatsInputError

MAX_BOOTSTRAP_CELLS = 10_000_000


def _positive_count(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StatsInputError(f"{name} must be an integer")
    if value <= 0 or value > UINT32_MODULUS:
        raise StatsInputError(f"{name} must be in [1, {UINT32_MODULUS}]")
    return value


def stationary_bootstrap_indices(
    *,
    series_length: object,
    replicate_count: object,
    mean_block_length: object,
    seed: object,
) -> tuple[tuple[int, ...], ...]:
    """Return Politis--Romano stationary-bootstrap index replicates.

    Each block starts at an independently uniform series index, has an exact
    integer-geometric length, and wraps around the end of the series.  A final
    block is truncated to make every replicate exactly ``series_length`` long.
    The RNG stream is shared across replicates in output order.

    This bounded kernel requires a pre-registered integer mean block length.
    It does not select that value and makes no Politis--White claim.
    """

    length = _positive_count(series_length, name="series_length")
    replicates = _positive_count(replicate_count, name="replicate_count")
    # Validate the mean before constructing or advancing the generator.
    if isinstance(mean_block_length, bool) or not isinstance(mean_block_length, int):
        raise StatsInputError("mean_block_length must be an integer")
    if mean_block_length <= 0 or mean_block_length > UINT32_MODULUS:
        raise StatsInputError(f"mean_block_length must be in [1, {UINT32_MODULUS}]")
    if mean_block_length > length:
        raise StatsInputError("mean_block_length cannot exceed series_length")
    if mean_block_length > MAX_GEOMETRIC_MEAN:
        raise StatsInputError(
            f"mean_block_length exceeds the {MAX_GEOMETRIC_MEAN} safety limit"
        )
    if length * replicates > MAX_BOOTSTRAP_CELLS:
        raise StatsInputError(
            f"bootstrap output exceeds the {MAX_BOOTSTRAP_CELLS}-cell safety limit"
        )

    generator = Pcg32.from_seed(seed)
    output: list[tuple[int, ...]] = []
    for _ in range(replicates):
        replicate: list[int] = []
        while len(replicate) < length:
            start = generator.bounded_index(length)
            block_length = generator.geometric(mean_block_length)
            remaining = length - len(replicate)
            replicate.extend((start + offset) % length for offset in range(min(block_length, remaining)))
        output.append(tuple(replicate))
    return tuple(output)
