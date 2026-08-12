"""Bit-exact SplitMix64-seeded PCG32 primitives.

The stream mapping is deliberately explicit because a scalar seed does not by
itself identify one of PCG32's streams.  ``from_seed(seed)`` consumes the first
two SplitMix64 outputs as ``(initstate, initseq)`` and then applies the official
``pcg32_srandom_r`` initialization sequence.

This generator is deterministic research infrastructure, not a cryptographic
random-number generator.
"""

from __future__ import annotations

from typing import Final

UINT32_MODULUS: Final = 1 << 32
UINT32_MASK: Final = UINT32_MODULUS - 1
UINT64_MASK: Final = (1 << 64) - 1

SPLITMIX64_GAMMA: Final = 0x9E3779B97F4A7C15
SPLITMIX64_MULTIPLIER_1: Final = 0xBF58476D1CE4E5B9
SPLITMIX64_MULTIPLIER_2: Final = 0x94D049BB133111EB
PCG32_MULTIPLIER: Final = 6364136223846793005
MAX_GEOMETRIC_MEAN: Final = 100_000
MAX_GEOMETRIC_DRAWS: Final = 10_000_000


class StatsInputError(ValueError):
    """Raised when a deterministic-statistics input is outside its frozen domain."""


def _uint(value: object, *, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StatsInputError(f"{name} must be an integer")
    if value < 0 or value > maximum:
        raise StatsInputError(f"{name} must be in [0, {maximum}]")
    return value


def _positive_uint32(value: object, *, name: str) -> int:
    parsed = _uint(value, name=name, maximum=UINT32_MODULUS)
    if parsed == 0:
        raise StatsInputError(f"{name} must be positive")
    return parsed


def _splitmix64_next(state: int) -> tuple[int, int]:
    state = (state + SPLITMIX64_GAMMA) & UINT64_MASK
    mixed = state
    mixed = ((mixed ^ (mixed >> 30)) * SPLITMIX64_MULTIPLIER_1) & UINT64_MASK
    mixed = ((mixed ^ (mixed >> 27)) * SPLITMIX64_MULTIPLIER_2) & UINT64_MASK
    return state, (mixed ^ (mixed >> 31)) & UINT64_MASK


def splitmix64_seed_material(seed: object) -> tuple[int, int]:
    """Map one uint64 seed to PCG32 ``(initstate, initseq)`` deterministically."""

    state = _uint(seed, name="seed", maximum=UINT64_MASK)
    state, initstate = _splitmix64_next(state)
    _, initseq = _splitmix64_next(state)
    return initstate, initseq


class Pcg32:
    """PCG-XSH-RR 64/32 with an explicit stream and frozen draw transforms."""

    __slots__ = ("_increment", "_state")

    def __init__(self, *, initstate: object, initseq: object) -> None:
        initial_state = _uint(initstate, name="initstate", maximum=UINT64_MASK)
        initial_sequence = _uint(initseq, name="initseq", maximum=UINT64_MASK)
        self._state = 0
        self._increment = ((initial_sequence << 1) | 1) & UINT64_MASK
        self.next_uint32()
        self._state = (self._state + initial_state) & UINT64_MASK
        self.next_uint32()

    @classmethod
    def from_seed(cls, seed: object) -> Pcg32:
        """Construct the one stream registered by the SplitMix64 seed mapping."""

        initstate, initseq = splitmix64_seed_material(seed)
        return cls(initstate=initstate, initseq=initseq)

    def next_uint32(self) -> int:
        """Return the next PCG-XSH-RR output in ``[0, 2**32)``."""

        old_state = self._state
        self._state = (old_state * PCG32_MULTIPLIER + self._increment) & UINT64_MASK
        xorshifted = (((old_state >> 18) ^ old_state) >> 27) & UINT32_MASK
        rotation = old_state >> 59
        return (
            (xorshifted >> rotation) | (xorshifted << ((-rotation) & 31))
        ) & UINT32_MASK

    def uniform_unit_interval(self) -> float:
        """Return a binary-exact grid draw in ``[0.0, 1.0)``.

        Division by ``2**32`` is exact in binary64.  No platform ``libm`` call
        participates in this transform.
        """

        return self.next_uint32() / UINT32_MODULUS

    def bounded_index(self, upper_bound: object) -> int:
        """Return an unbiased index in ``[0, upper_bound)`` using rejection."""

        bound = _positive_uint32(upper_bound, name="upper_bound")
        if bound == UINT32_MODULUS:
            return self.next_uint32()
        threshold = (UINT32_MODULUS - bound) % bound
        while True:
            draw = self.next_uint32()
            if draw >= threshold:
                return draw % bound

    def geometric(self, mean_block_length: object) -> int:
        """Draw an exact integer-geometric block length on ``{1, 2, ...}``.

        For registered integer mean ``L``, repeated unbiased draws on
        ``{0, ..., L-1}`` stop at zero.  Thus ``P(K=k)=(1-1/L)**(k-1)/L`` and
        ``E[K]=L`` without logarithms or a uint32 threshold approximation.
        Non-integer means are rejected rather than rounded implicitly.
        """

        mean = _positive_uint32(mean_block_length, name="mean_block_length")
        if mean > MAX_GEOMETRIC_MEAN:
            raise StatsInputError(
                f"mean_block_length exceeds the {MAX_GEOMETRIC_MEAN} safety limit"
            )
        length = 1
        while length < MAX_GEOMETRIC_DRAWS and self.bounded_index(mean) != 0:
            length += 1
        if length == MAX_GEOMETRIC_DRAWS:
            raise StatsInputError(
                f"geometric draw exceeds the {MAX_GEOMETRIC_DRAWS}-draw safety limit"
            )
        return length
