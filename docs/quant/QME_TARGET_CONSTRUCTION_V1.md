# QME composition ticket A — deterministic target-construction kernel (V1)

Kernel: `qme/quant/targets_v1.py`
Tests: `tests/quant/test_target_construction.py`
Regression fixture: `tests/quant/fixtures/target-construction-v1.json`
Change tier: **T1_ACCEPTED_KERNEL** (kernel, tests, fixture) / **T3_DOCUMENTATION** (this file)

| Identifier | Value |
|---|---|
| `KERNEL_ID` | `QME-COMPOSITION-TARGET-CONSTRUCTION-KERNEL-V1` |
| `SCHEMA_VERSION` | `qme.target_construction.v1` |
| `TICKET_ID` | `PENDING_OWNER_ASSIGNMENT` |

**Ticket status.** This is composition ticket A under gate **NEE-108**, lead plan
2026-08-25. The owner has not assigned a Linear identifier, so none is invented:
the emitted artifact, the fixture, and this document all record
`ticket_id: PENDING_OWNER_ASSIGNMENT`. When the owner assigns an identifier it is
recorded by a successor change, never back-filled here.

**Non-claims.** This kernel claims no production deployment, no prospective
consumption, no empirical performance, no alpha, no capacity value, no
production readiness, and no live-order authority. `qme.quant.execution_v1.NON_CLAIMS`
is copied verbatim into every emitted artifact and into the regression fixture.
Passing the KATs and the two-sided oracle is neither blocker clearance nor
acceptance evidence; the fixture carries
`status: REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE` and
`review_status: PENDING_INDEPENDENT_REVIEW`.

---

## 1. What the kernel does

The kernel converts a rank-ordered **selected set**, **prior raw holdings**, typed
**raw fill prices**, and opening **cash / receivables** into signed INTEGER-quantum
share deltas that implement the FROZEN v0.1 weighting rules of
`configs/quant/qme-v0.1-contract-v2.json`, such that the constructed program
satisfies the NEE-129 execution engine's self-financing walls **by construction**.

The selected set is **consumed, never recomputed**. The kernel takes the ordered
tuple of security identifiers plus the caller-declared `K_t` and refuses
`K_t != len(selected)` (typed `INVALID_SELECTION_COUNT_MISMATCH`); a zero
selection is refused (`INVALID_ZERO_SELECTION_SIZE`), mirroring the contract
state. Selection, ranking, and breadth logic belong to the signal lane and are
neither imported nor reimplemented here — the kernel imports no `signal` module,
no `qme.quant.contract_v2`, and contains neither the `20 * N_t` selection
formula nor the `minimum_rank_eligible_breadth` threshold.

## 2. The frozen `weighting` block (verbatim)

Reproduced verbatim from the `weighting` object of
`configs/quant/qme-v0.1-contract-v2.json` (contract `qme-long-only-momentum-v0.1`,
NEE-119). The kernel implements exactly these rules and invents nothing:

```json
{
  "control_target": "EQUAL_WEIGHT",
  "ideal_weight_authority": "EXACT_RATIONAL",
  "target_weight_rational": {"numerator": 1, "denominator": "K_t"},
  "decimal_weight_is_display_only": true,
  "decimal_display_formula": "round_half_even(Decimal(1) / Decimal(K_t), 18)",
  "trade_universe": "UNION_CURRENT_HOLDINGS_AND_SELECTED_SECURITIES",
  "pre_trade_nav_identity": "cash_pre + sum(raw_positions_i * common_raw_execution_mark_i) + receivables_pre",
  "pre_trade_nav_identity_tolerance": "0.000001",
  "raw_position_storage_quantum": "0.00000001",
  "fractional_raw_positions_allowed": true,
  "order_quantum": "1",
  "orders_must_be_integer_quantum_multiples": true,
  "fractional_position_residual": "CARRIED_UNTIL_BOUND_CASH_IN_LIEU_OR_FRACTIONAL_DISPOSITION_HANDLER",
  "unselected_current_holdings_target": "SELL_INTEGER_ORDERABLE_COMPONENT_CARRY_FRACTIONAL_RESIDUAL",
  "selected_target_formula": "fractional_residual_i + floor(((pre_trade_nav / K_t) - fractional_residual_i * raw_execution_price_i) / raw_execution_price_i / order_quantum) * order_quantum",
  "cost_rate_encoding": "INTEGER_BASIS_POINTS_FROM_REGISTERED_COST_POLICY",
  "cost_rate_minimum_bps": 0,
  "cost_rate_maximum_exclusive_bps": 10000,
  "cash_formula": "cash_pre + sum((raw_positions_i - target_raw_positions_i) * common_raw_execution_mark_i) - TC - TAX - supported_withholding - fees",
  "cash_components": [
    "TC_FROM_BOUND_NEE_118_COST_FUNCTION",
    "TAX_FROM_BOUND_NEE_118_TAX_FUNCTION",
    "SUPPORTED_WITHHOLDING_FROM_BOUND_NEE_118_EVENT_FUNCTION",
    "FEES_FROM_BOUND_NEE_118_FEE_FUNCTION"
  ],
  "component_rounding": "ROUND_HALF_EVEN_TO_BOUND_NEE_118_INTERNAL_CURRENCY_QUANTUM",
  "negative_cash_repair": {
    "enabled": true,
    "step": "decrement_one_selected_target_order_quantum",
    "choice_order": ["current_target_notional_descending", "security_id_utf8_bytes_descending"],
    "recompute_tc_tax_withholding_fees_and_rounding_after_each_step": true,
    "terminal_invariant": "cash_post >= 0"
  },
  "residual_cash": "EXPLICIT_NOT_REDISTRIBUTED",
  "leverage_allowed": false
}
```

The governing `numeric_policy` is likewise frozen: `basis_points_denominator`
10000, `binary_float_forbidden` true, `decimal_precision_digits` 50,
`input_encoding` `canonical_decimal_string`, `rounding_mode` `ROUND_HALF_EVEN`,
`weight_artifact_scale` 18.

## 3. The construction, clause by clause

### 3.1 Trade universe

`UNION_CURRENT_HOLDINGS_AND_SELECTED_SECURITIES`, ordered by UTF-8 bytes
ascending. Every trade-universe security must carry a typed raw execution price;
a missing price is a typed `INVALID_WEIGHTING_INPUT`.

### 3.2 Exact-rational weight, display-only decimal

The ideal weight is the exact rational `1 / K_t` (`EXACT_RATIONAL`). The decimal
weight is display-only and is computed as `round_half_even(Decimal(1) / Decimal(K_t), 18)`
at the frozen 50-digit precision; it never feeds the target arithmetic. Emitted
selected rows carry both `target_weight_rational` (`{"numerator": "1", "denominator": K_t}`)
and `target_weight_decimal_display`; unselected holdings carry neither.

### 3.3 Selected target formula

The selected target is the frozen string, evaluated in exact `Fraction`
arithmetic:

```
fractional_residual_i + floor(((pre_trade_nav / K_t) - fractional_residual_i
    * raw_execution_price_i) / raw_execution_price_i / order_quantum) * order_quantum
```

where `fractional_residual_i` is the carried sub-quantum part of the prior raw
position, `pre_trade_nav` is the identity value (§3.5), and `order_quantum` is the
frozen one-share quantum `"1"`. The floor term is cross-checked against the frozen
NEE-118 rounder `qme.quant.equations.round_long_target_shares`; a disagreement is
refused rather than papered over. A formula result below zero is refused
(`INVALID_NEGATIVE_LONG_ONLY_TARGET`) because the engine would reject the implied
short.

### 3.4 Unselected current holdings

`SELL_INTEGER_ORDERABLE_COMPONENT_CARRY_FRACTIONAL_RESIDUAL`: an unselected
holding sells its integer orderable component and carries its exact sub-quantum
`fractional_position_residual` unchanged. The residual is
`CARRIED_UNTIL_BOUND_CASH_IN_LIEU_OR_FRACTIONAL_DISPOSITION_HANDLER`; the kernel
asserts `fractional_residual_out == fractional_residual_in` on every row.

### 3.5 Pre-trade NAV identity

`cash_pre + sum(raw_positions_i * common_raw_execution_mark_i) + receivables_pre`,
computed by the frozen `qme.quant.equations.PortfolioState.nav`. The
caller-declared pre-trade NAV is compared against that identity value and refused
(`INVALID_PRE_TRADE_NAV_IDENTITY`) when the absolute gap exceeds the registered
tolerance `0.000001`. The identity value — never the declaration — drives the
target formula. The registered coordinate has execution price equal to the common
raw execution mark, so `raw_execution_price_i` and `common_raw_execution_mark_i`
are one number per security.

### 3.6 Cash formula and its components

`cash_post = cash_pre + sum((raw_positions_i - target_raw_positions_i) * common_raw_execution_mark_i) - TC - TAX - supported_withholding - fees`.

Every component comes **from the bound NEE-118 functions**, never from a
reimplemented formula:

* **TC** and **TAX** and the per-fill `ROUND_HALF_EVEN` quantization to the
  NEE-118 internal currency quantum (`0.00000001`) are produced by
  `qme.quant.equations.rebalance`, inside which `TransactionTaxPolicy.assess`
  runs. The cost rate is `INTEGER_BASIS_POINTS_FROM_REGISTERED_COST_POLICY` with
  `0 <= bps < 10000`, resolved through the registered cost-rate policy.
* **supported_withholding** is projected as **zero by structural absence**: the
  bound NEE-118 event function books withholding only on a dividend-entitlement
  event, and a target-construction projection contains no such event. This is
  structural absence, not an assumed rate.
* **fees** are projected as **zero** only under the engine's
  `EXCLUDED_SYNTHETIC_NON_REGULATORY_SOURCE` fee mode, in which the bound engine
  itself books zero regulatory fees. The posted-historical fee mode is refused
  (`BLOCKED_UNSUPPORTED_REGULATORY_FEE_MODE`) rather than approximated, and — as
  the engine wall does — a `regulatory_authority` cost policy may not select the
  excluded mode.

`component_rounding` is `ROUND_HALF_EVEN_TO_BOUND_NEE_118_INTERNAL_CURRENCY_QUANTUM`
throughout. `residual_cash` is `EXPLICIT_NOT_REDISTRIBUTED`: leftover cash is
reported, never swept back into targets. `leverage_allowed` is false.

### 3.7 Negative-cash repair

When the projected `cash_post` is negative the registered repair runs:

* `step`: `decrement_one_selected_target_order_quantum`.
* `choice_order`: `["current_target_notional_descending", "security_id_utf8_bytes_descending"]`
  — the selected name with the greatest current target notional is decremented;
  ties break on `security_id` UTF-8 bytes descending.
* `recompute_tc_tax_withholding_fees_and_rounding_after_each_step`: true — every
  step re-runs the bound NEE-118 projection.
* `terminal_invariant`: `cash_post >= 0`.

**Termination is proven, not assumed.** Each step strictly reduces total target
buy notional by one order quantum, and the decrementable quanta are finite:
the kernel counts them up front as `repair_iteration_ceiling` (the sum over
selected names of `(target_i - residual_i) / order_quantum`). The candidate walk
refuses (`INVALID_NEGATIVE_POST_TRADE_CASH`) when no selected target can be
decremented further. The explicit ceiling refusal
(`BLOCKED_REPAIR_ITERATION_CEILING`) is present as a typed guard and is
unreachable in every fixture case. The full ordered repair trace — each
decrement with the recomputed cost, tax, withholding, fees, buy notional, and
`cash_post` after it — is emitted.

## 4. Bound dependency surfaces (reuse, not reimplementation)

The kernel binds four artifacts, observing each one's grouped digest at run time
(a missing file fails closed as `BLOCKED_MISSING_BOUND_ARTIFACT`). No digest is
written into this document or into source, so nothing self-pins outside T0.

| Role | Path | Bound identity |
|---|---|---|
| `NEE_118_ACCOUNTING_CONFIG` | `configs/quant/accounting-equations-v1.json` | `NEE-118-QME-ACCOUNTING-V1` |
| `NEE_118_EQUATIONS_KERNEL` | `qme/quant/equations.py` | `NEE-118-QME-ACCOUNTING-V1` |
| `NEE_119_QUANTITATIVE_CONTRACT_V2` | `configs/quant/qme-v0.1-contract-v2.json` | `qme-long-only-momentum-v0.1` |
| `NEE_129_EXECUTION_ENGINE` | `qme/quant/execution_v1.py` | `QME-NEE129-RAW-PRICE-EXECUTION-SELF-FINANCING-ENGINE-V1` |

Every owned surface the kernel calls, with the exact call site, so the reuse
claim is auditable:

| Bound surface | Called from |
|---|---|
| `qme.quant.equations.PortfolioState` | `construct_targets` (pre-trade NAV identity via the frozen `.nav`); `_project` (cushion-shifted observation state) |
| `qme.quant.equations.Trade` | `_project` (typed fill construction) |
| `qme.quant.equations.TransactionTaxPolicy.assess` | inside `qme.quant.equations.rebalance`; never called around it |
| `qme.quant.equations.rebalance` | `_project` (TC, TAX, per-fill rounding, and cash — the true call and the cushion-shifted observation call) |
| `qme.quant.equations.round_long_target_shares` | `_selected_target` (frozen NEE-118 floor cross-check) |
| `qme.quant.execution_v1.SignedTargetDelta` | `_deltas` (typed delta wall) |
| `qme.quant.execution_v1.order_fills` | `_project` (registered `ALL_SELLS_THEN_ALL_BUYS` fill order) |
| `qme.quant.execution_v1.require_ledger_price_quantum` | `TargetConstructionRequest.__post_init__` |
| `qme.quant.execution_v1.require_share_quantum` | `TargetConstructionRequest.__post_init__` |
| `qme.quant.execution_v1.resolve_cost_rate_policy` | `construct_targets` |
| `qme.quant.execution_v1.resolve_ledger_coordinate_source` | `construct_targets` |

Cost, tax, withholding, and fee formulas are never reimplemented: the kernel
calls the NEE-118 surfaces above and books withholding and fees exactly as the
bound engine does under the excluded-fee mode.

## 5. The two-sided oracle

Correctness rests on one equivalence, verified against the engine source rather
than assumed: under the engine's `EXCLUDED_SYNTHETIC_NON_REGULATORY_SOURCE` fee
mode, `qme.quant.execution_v1._execute_fills` computes a rebalance stage by
calling exactly `qme.quant.equations.rebalance(before, trades, transaction_cost_rate_bps=cost_policy.rate_bps, transaction_tax_policy=..., raw_marks_after=...)`
and books zero regulatory fees — the same call the kernel's projection makes.
A `DeclaredSignedDeltas` rebalance stage consumes the caller's deltas verbatim
(no re-derivation and no engine-side repair), so the kernel's accept decision is
the engine's accept decision, delta for delta and cent for cent.

For every fixture case the acceptance tests therefore build a
`DeclaredSignedDeltas` / `ExecutionProgram` from the kernel's deltas and run
`qme.quant.execution_v1.run_execution_program` with the SAME registered records.
Each run must reach `EXECUTION_OK`; the ledger's signed deltas must equal the
kernel's; closing cash must be non-negative; and the ledger's `nav_minus`,
`transaction_cost`, `transaction_tax`, and `cash_plus` must equal the kernel's
projected totals. The kernel never reports success on a program the engine would
refuse — the negative-cash case proves the converse too: the unrepaired initial
targets are refused by the engine with `BLOCKED_NEGATIVE_POST_TRADE_CASH`.

Inside the kernel's own repair projection, when `rebalance` refuses a
cash-negative order set the exact would-be closing cash is *observed* through the
same frozen kernel — the identical fill sequence is re-run with opening cash
shifted by a cushion of `3 * gross_trade_notional + 1` and the cushion subtracted
back out. Every per-fill amount in `rebalance` is independent of the running cash
level, so the difference is exact, and the accept/reject verdict always comes from
the unshifted call.

## 6. Owner-gated registries ship EMPTY, fail closed

The cost-rate, participation-limit, ledger-coordinate-source, withholding,
deferral, spread, residual-cash, and unsupported-event registries are the NEE-129
engine's own (`qme.quant.execution_v1.RegistryOverrides`), shipped EMPTY there;
tests inject `TEST_CONSTRUCTED` records only, which can never ship.

This kernel adds exactly **one** registry of its own, because the contract names
a handler that does not exist yet:

* `REGISTERED_FRACTIONAL_DISPOSITION_HANDLERS` ships `()` (EMPTY BY DESIGN). The
  contract carries `fractional_position_residual: CARRIED_UNTIL_BOUND_CASH_IN_LIEU_OR_FRACTIONAL_DISPOSITION_HANDLER`,
  so carrying is the only registered behavior. A request that names a handler
  fails closed as `BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER`,
  following the empty-registry pattern of `qme.data.alpha_vantage.plan_v1` and
  `qme.data.stores.riskfree_v1`. No cash-in-lieu or disposition rule is invented.

No threshold, coefficient, or schedule value that the frozen contract does not
carry is introduced anywhere in the module.

## 7. Output and lineage

`construct_targets` returns a frozen `TargetConstructionResult`:

* **Per-security rows** — `security_id`, `membership`, `prior_raw_shares`,
  `target_weight_rational`, `target_weight_decimal_display`,
  `fractional_residual_in`, `target_raw_shares`, `signed_delta_raw_shares`,
  `fractional_residual_out`, `repair_decrements`, `raw_execution_price`, lineage.
* **Totals** — `selection_count_k_t`, `pre_trade_nav`, `declared_pre_trade_nav`,
  `pre_trade_nav_identity_tolerance`, projected TC / TAX / withholding / fees,
  `projected_cash_post`, `initial_projected_cash_post`, projected gross buy and
  sell notional, `repair_steps_total`.
* **Repair trace** — the ordered decrements, each with the recomputed cash after
  it.
* **Lineage** — input, config, code, and schema grouped digests, including the
  execution-engine and equations identities.

The artifact serializes as canonical JSON with a grouped SHA-256 self-hash (eight
8-hex groups). Two calls are byte-identical; input permutations (selected order,
prior-position order, price-map order) produce byte-identical output because the
registered `EQUAL_WEIGHT` control carries no rank information — invariance is
proven by a shuffle test that asserts the shuffle actually reordered.

## 8. Typed fail-closed states

`construct_targets` refuses only through `TargetConstructionError`, whose state is
asserted to be one of the declared, sorted, unique
`TARGET_CONSTRUCTION_FAIL_CLOSED_STATES`; the constructor rejects any undeclared
state, and a completeness assertion runs at import. The states are
`BLOCKED_BOUND_KERNEL_REFUSAL`, `BLOCKED_MISSING_BOUND_ARTIFACT`,
`BLOCKED_NO_REGISTERED_COST_RATE_POLICY`,
`BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER`,
`BLOCKED_REPAIR_ITERATION_CEILING`,
`BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE`,
`BLOCKED_UNSUPPORTED_REGULATORY_FEE_MODE`, `INVALID_DUPLICATE_SECURITY_ID`,
`INVALID_NEGATIVE_LONG_ONLY_TARGET`, `INVALID_NEGATIVE_POST_TRADE_CASH`,
`INVALID_PRE_TRADE_NAV_IDENTITY`, `INVALID_SELECTION_COUNT_MISMATCH`,
`INVALID_WEIGHTING_INPUT`, and `INVALID_ZERO_SELECTION_SIZE`. The three
engine-named `BLOCKED_*` states keep their registered NEE-129 names when they
surface here, so a caller sees one vocabulary either way. `INVALID_WEIGHTING_INPUT`,
`INVALID_ZERO_SELECTION_SIZE`, `INVALID_DUPLICATE_SECURITY_ID`, and
`INVALID_NEGATIVE_POST_TRADE_CASH` mirror the contract-v2 `fail_closed_states`
vocabulary verbatim.
