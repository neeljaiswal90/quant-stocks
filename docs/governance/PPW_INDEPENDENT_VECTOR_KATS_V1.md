# PPW independent vector known-answer classes v1

This packet registers **independent numeric known-answer vectors** for the
NEE-204 corrected Patton-Politis-White selector. It does not accept selection
009, does not flip predecessor `TYPED_UNRESOLVED` labels, and does not remove
any of the 12 active Freeze V5 blockers. Freeze V4 remains immutable historical
lineage.

## Why this packet exists

Ticket requirement 8 asks for independent known-answer vectors for short,
constant, IID, negatively correlated, zero/negative-intermediate, floor/cap,
integer-boundary, and 96-column aggregation cases.

PR 27 already published the seeded 60-by-96 candidate interval KAT. That
artifact is not a substitute for these independent vector classes. The
predecessor source-equation fixture still labels IID, negative-correlation,
short, and 96-column aggregation as `TYPED_UNRESOLVED`. Those labels stay
`TYPED_UNRESOLVED`. The numbers in this packet are independent numeric
evidence only.

## Vector classes

| Case | Construction | Known answer |
| --- | --- | --- |
| SHORT | 59-by-96 constant `1` | `PPW_SERIES_TOO_SHORT` |
| CONSTANT | 60-by-96 constant `1` | `PPW_CONSTANT_COLUMN` |
| IID | LCG(60, a=17, c=5, m=103, x0=1) repeated across 96 columns | aggregate raw `1.359388032397440740176305835019253713`, common block length 3 |
| NEGATIVELY_CORRELATED | alternating `+1/-1` | `PPW_NO_INSIGNIFICANT_RUN` |
| ZERO_NEGATIVE_INTERMEDIATE | integer AR `x_t = floor(x_{t-1}/4) + e_t` | `G_hat < 0`, aggregate raw `8.892855503530100958894110173893287925`, block length 9 |
| FLOOR | LCG(60, a=21, c=1, m=97, x0=3) | raw `< 3` so overlay floor yields 3 |
| CAP | LCG(60, a=13, c=7, m=101, x0=2) | raw `> floor(60/4)` so overlay cap yields 15 |
| INTEGER_BOUNDARY | LCG(60, a=30, c=1, m=97, x0=9) | raw `3.001401156735570409247720827280170906`, one ceiling yields 4 |
| NINETY_SIX_COLUMN_AGGREGATION | per-column LCG(60, a=23, c=1, m=109, x0=column+1) | median of order statistics 48 and 49, then one ceiling, yields 3 |

LCG recurrence: `x <- (a * x + c) mod m`, emitted value `x - floor(m/2)`,
canonical signed decimal string.

## Registered correction authority

The packet binds the exact NEE-204 coordinator correction comment
`40b5e5c2-0908-4be8-b23a-2edd4ed9be6e` (446 raw UTF-8 bytes, SHA-256
`6329e0be18eb222d27be20c974205e3ef2b9dd008964441ab1b961c1b56d77fb`).
That correction withdraws successor-freeze authorization, requires these
versioned independent KAT classes plus an extra-terminal disposition, and keeps
selection 009 false. It does not authorize this artifact to accept selection
009 or remove a blocker.

## Extra selector terminals

Selection 004 still freezes exactly five degeneracy terminals:

- `PPW_NONFINITE_INPUT`
- `PPW_SERIES_TOO_SHORT`
- `PPW_CONSTANT_COLUMN`
- `PPW_DEGENERATE_DENOMINATOR`
- `PPW_NONPOSITIVE_BLOCK_LENGTH`

The executable selector also uses three engineering terminals. This packet
registers them so they are no longer unregistered selector terminals:

- `PPW_INVALID_MATRIX_SHAPE`
- `PPW_INVALID_CANONICAL_DECIMAL`
- `PPW_DECIMAL_ARITHMETIC_FAILURE`

`PPW_NO_INSIGNIFICANT_RUN` remains the selection 003 lag-selection terminal.

## Binding nonclaims

`selection_009_accepted` remains false. `freeze_blocker_changed` remains false.
Freeze V4 policy and manifest bytes remain unchanged, while this packet also
binds the exact Freeze V5 12-blocker successor state. This
packet does not claim DSR, Holm, empirical or production effective-trials,
alpha, M0 completion, production readiness, or live-order authority.
