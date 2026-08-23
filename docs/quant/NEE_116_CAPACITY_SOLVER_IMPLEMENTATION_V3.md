# NEE-116 Capacity Solver — Implementation V3

Status: fail-closed correction candidate. It does not clear a blocker.

- Economic method: `QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1`
- Implementation: `QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-IMPLEMENTATION-V3`
- Runtime: `qme/quant/capacity_solver_v3.py`
- Tests: `tests/quant/test_capacity_solver_v3.py`

## Reason for V3

The exact-rational V2 implementation correctly repaired V1's one-quantum
boundary error, but its top-level greatest-capital entry point computed the
dominating bound before validating four economic parameter domains. Certain
invalid values therefore produced negative or zero scan counts and an
`UNAVAILABLE_NO_FEASIBLE_CAPITAL` certificate instead of failing closed.

V2 is preserved byte-for-byte because the protected owner implementation
correction binds it as immutable evidence. V3 supersedes it operationally.

## Correction

Before any bound or scan, V3 parses through the existing canonical decimal
grammar and enforces:

- `0 <= transaction_cost_rate_bps < 10000`;
- `0 <= cash_buffer_weight < 1`;
- `0 < maximum_participation <= 1`;
- `order_quantum > 0`;
- `capacity_quantum > 0` for greatest-capital scans; and
- `capital > 0` for single-point portfolio evaluation.

Violations raise the existing `CapacitySolverV2Error`. They cannot reach V2's
bound computation, derive a malformed scan count, hash an empty bitmap, or
return a certificate. The public point solver and public bound use the same
domain validator.

For valid inputs, V3 delegates to the immutable exact-rational V2 calculation
and changes only the certificate implementation identity from V2 to V3. The
economic method, exact feasibility arithmetic, exhaustive grid, bitmap,
portfolio, bound, and result values are unchanged.

## Regression evidence

The V3 tests cover the independent-review reproductions exactly:

- `maximum_participation=-1`;
- `cash_buffer_weight=2`;
- `order_quantum=-2`; and
- `transaction_cost_rate_bps=-9999`.

They also cover zero and upper-bound violations, both other public entry
points, invalid grids, binary floats, proof that invalid inputs never call the
V2 scan, and exact valid-result parity with V2 apart from `implementation_id`.

## Nonclaims

- V2 is not rewritten; V3 is a versioned correction.
- No empirical capacity value is produced.
- No Freeze V6 blocker changes in this implementation patch.
- Fresh exact-byte independent review and owner signoff remain required.
- M0, production readiness, and live-order authority remain false.
