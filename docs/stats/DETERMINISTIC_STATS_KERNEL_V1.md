# Deterministic statistics kernel v1

Status: bounded engineering kernel; not production inference evidence.

## Frozen random stream

The scalar seed is an unsigned 64-bit integer. SplitMix64 starts with that value,
advances by `0x9e3779b97f4a7c15`, and applies the frozen two-multiply mixer. Its first
two outputs are, in order, PCG32 `initstate` and `initseq`. PCG32 is then initialized
exactly as `pcg32_srandom_r` and generates PCG-XSH-RR 64/32 values.

For seed `20260812`, the mapping is:

- `initstate = 0x379caa668a6a8b02`
- `initseq = 0xcec0b7ef1f25958d`

The fixture freezes the first 16 results from that project stream. It separately
reproduces the first six values published by the PCG project for
`initstate=42, initseq=54`; this prevents a self-generated project vector from being
the only oracle. Sources: [PCG C reference and known-answer output](https://www.pcg-random.org/using-pcg-c.html),
[PCG minimal implementation](https://www.pcg-random.org/download.html), and
[Steele, Lea, and Flood's SplitMix paper](https://gee.cs.oswego.edu/dl/papers/oopsla14.pdf).

The acceptance vectors also force the rejection branch for bound `2**31 + 1`,
the full-`uint32` bound path, and explicit small bootstrap replicates across a
truncated last block and the next replicate. The bounded-status manifest has a
strict schema, so removing either production blocker is a test failure.

Every arithmetic state transition is explicitly masked to 32 or 64 bits. The kernel
does not use `random.Random`, NumPy, ambient entropy, time, process identity, or
platform state. It is not suitable for cryptographic use.

## Draw transforms

- Uniform draws are `uint32 / 2**32`, a binary-exact grid in `[0, 1)`.
- Bounded indices use the PCG rejection rule, not `% bound` alone, so non-power-of-two
  bounds do not introduce modulo bias.
- Geometric block lengths require a positive integer mean `L`. Repeated unbiased
  draws on `{0,...,L-1}` terminate on zero, giving support `{1,2,...}`, success
  probability `1/L`, and exact mean `L`.

The geometric rule deliberately avoids logarithmic inverse-CDF evaluation. That keeps
replay independent of platform math libraries. A non-integer mean is rejected; it is
never rounded or approximated silently. Direct draws reject means above `100,000` and
abort if a draw would consume `10,000,000` trials. This makes pathological calls
fail closed instead of consuming an effectively unbounded amount of CPU.

## Stationary bootstrap

Each replicate repeatedly draws an independent uniform start index and an integer-
geometric block length. Consecutive indices wrap modulo the series length. The last
block is truncated so each replicate contains exactly `series_length` indices. One
generator stream is consumed serially in replicate order.

The implementation generates indices only; consumers remain responsible for binding
those indices to one immutable, commonly aligned observation matrix. Inputs must be
positive integers, the mean block length cannot exceed the series length, and the
output is capped at 10,000,000 cells to prevent accidental unbounded allocation.
The same `100,000` mean-block limit applies to bootstrap requests.

## Explicit blocker

This slice does **not** implement or claim the Politis–White automatic block-length
selector or the Patton–Politis–White correction. The planning document does not freeze
enough of that method—pilot spectral window, autocovariance conventions, stopping
rule, constants, degeneracy behavior, and finite-sample rounding—to support one exact
cross-platform implementation. Until those choices are registered from the primary
method source and independently verified, callers must supply a pre-registered integer
mean block length and production inference remains blocked.
