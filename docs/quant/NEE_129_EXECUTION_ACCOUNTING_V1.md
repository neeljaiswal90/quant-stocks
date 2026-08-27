# NEE-129 — Raw-price research execution and self-financing portfolio accounting (V1)

Engine: `qme/quant/execution_v1.py`
Tests: `tests/quant/test_execution_accounting.py`
Regression fixture: `tests/quant/fixtures/execution-accounting-v1.json`
Change tier: **T1_ACCEPTED_KERNEL** (engine, tests, fixture) / **T3_DOCUMENTATION** (this file)

| Identifier | Value |
|---|---|
| `ENGINE_ID` | `QME-NEE129-RAW-PRICE-EXECUTION-SELF-FINANCING-ENGINE-V1` |
| `METHOD_ID` | `QME-NEE129-RAW-EXECUTION-SELF-FINANCING-ACCOUNTING-V1` |
| `SCHEMA_VERSION` | `qme.execution_accounting.v1` |
| `ACCOUNTING_COORDINATE` | `RAW_CASH_RAW_SHARES_RAW_EXECUTION_PRICES_RAW_MARKS` |

**Non-claims.** This engine claims no production deployment, no prospective
consumption, no empirical performance, no alpha, no capacity value, no
production readiness, and no live-order authority. `NON_CLAIMS` is written into
every manifest and every artifact it emits. Passing the golden fixture is not
blocker clearance and is not acceptance evidence.

---

## 1. The frozen event sequence

`REGISTERED_EVENT_SEQUENCE` is the executable form of the ticket's ordered list,
and every emitted record names the step that produced it.

| # | Step | Where it runs |
|---|---|---|
| 1 | `APPLY_EFFECTIVE_SHARE_AND_ACTION_STATE_AND_RECOGNIZE_DIVIDEND_ENTITLEMENT` | `apply_corporate_action_stage` |
| 2 | `MARK_PRE_TRADE_POSITIONS_AT_DECLARED_RAW_EXECUTION_MARKS` | `_execute_rebalance_stage` → `_portfolio` |
| 3 | `COMPUTE_NAV_MINUS` | `_execute_rebalance_stage` → `PortfolioState.nav` |
| 4 | `GENERATE_SIGNED_TARGET_DELTAS` | `_equal_weight_targets`, `_deltas_from_targets`, `_solve_with_negative_cash_repair` |
| 5 | `EXECUTE_SELLS_THEN_COST_AWARE_BUYS_WITHOUT_CHANGING_ECONOMIC_TARGET_PRIORITY` | `order_fills` |
| 6 | `APPLY_COSTS_FEES_TAXES_AND_CASH_UPDATES` | `_execute_fills` |
| 7 | `VERIFY_POST_TRADE_INVARIANTS_AND_PUBLISH_FILLS_AND_LOTS` | `_replay_fills`, `_self_financing`, `publish_tax_lots` |
| 8 | `PRODUCE_DAILY_RAW_CLOSE_MARKS_AND_RECEIVABLE_AND_CASH_TRANSITIONS` | `_apply_session_close`, and the payment leg of `apply_corporate_action_stage` |

Ordering never changes economic target priority: step 5 permutes the *sequence*
of the same signed deltas (all sells, then all buys, UTF-8 ascending within each
stage), and the solved target vector is identical for every input permutation.

## 2. Registered equations (verbatim)

```
GTN = sum(|dq_i| * P_i)
C_plus = C_minus - sum(dq_i * P_i) - TC - TAX
at common marks NAV_plus = NAV_minus - TC - TAX
Target shares must be solved so C_plus >= 0 after costs AND rounding.
```

The last line is enforced by the NEE-119 negative-cash repair loop, run to
convergence *before* the kernel is asked to post — `qme.quant.equations.rebalance`
refuses a cash-negative order set rather than repairing it, and the V3 fee
adapter refuses to rescale trades. The loop uses the registered step
(`decrement_one_selected_target_order_quantum`) and the registered choice order
(`current_target_notional_descending`, then `security_id_utf8_bytes_descending`),
recomputes costs, taxes, fees, and rounding after each step, and terminates
because each step strictly reduces the total target share count — no invented
step limit.

## 3. Zero same-bar fills, structurally

`FillSession` admits only an `EligibleFillSession`, which is built solely by
`derive_eligible_fill_session` and requires `eligible.ordinal ==
signal.ordinal + 1` and `eligible.session_date > signal.session_date` on one
content-bound calendar. A fill session must then satisfy `fill.ordinal >=
eligible.ordinal`, so a fill on the signal bar is unrepresentable. Handing the
constructor the signal session refuses with `BLOCKED_SAME_SESSION_FILL`; handing
`FillSession` a bare `SessionRef` fails `mypy --strict` (proved by an in-test
probe). Every constructed fill session also calls the frozen
`qme.quant.equations.validate_fill_timing`, closing the timing gap the
golden-fixture harness leaves open.

### Fill-price hierarchy (frozen, reason-coded)

`REGISTERED_FILL_REASON_PRECEDENCE`, evaluated in this order:

1. `OFFICIAL_NEXT_SESSION_RAW_OPEN`
2. `DECLARED_FIRST_REGULAR_SESSION_PRINT`
3. `BOUNDED_NEXT_SESSION_DEFERRAL` — requires a **registered** maximum deferral
4. `SOURCED_DELISTING_OR_UNSUPPORTED_EVENT_HANDLING`

A security delisted between signal and fill short-circuits to rung 4 because
rung 4 is the only rung that can admit a fill for such a name; without a sourced
outcome the run is refused (`BLOCKED_DELISTING_BETWEEN_SIGNAL_AND_FILL`) rather
than valued. The maximum deferral is **registered, not assumed**: the registry
ships empty, so any deferral without a registered bound is
`BLOCKED_NO_REGISTERED_MAXIMUM_FILL_DEFERRAL`, and a deferral past a registered
bound is `BLOCKED_UNAVAILABLE_FILL_AFTER_REGISTERED_BOUND`.

## 4. Adjusted prices are confined to signal / diagnostic fields

Three independent mechanisms, all tested:

1. **Static.** The ledger observation types are `RawExecutionPrice` and `RawMark`.
   `AdjustedSignalObservation` is a *sibling*, not a subtype, and the only field
   that admits it is `SignalDiagnostics.observations`. Placing one in a
   `LedgerMarkSet` or a `SignedTargetDelta` fails `mypy --strict` (in-test probe
   asserts one `dict-item` and one `arg-type` error).
2. **Runtime.** `LedgerMarkSet.__post_init__` and `SignedTargetDelta.__post_init__`
   use `type(x) is not RawMark` / `is not RawExecutionPrice`, so a subclass cannot
   slip past either.
3. **Evidence scan.** `FORBIDDEN_LEDGER_COORDINATE_TOKENS` is checked against every
   ledger observation's `source_id`, `snapshot_id`, and `security_id`, so a
   total-return or split-adjusted series cannot be smuggled in behind a raw type.

`AdjustedSignalObservation` values reach the run's `input_sha256_grouped` and
nothing else: the published artifact contains no adjusted coordinate at all,
which the test asserts by scanning the canonical bytes.

## 5. Fail-closed states

`EXECUTION_FAIL_CLOSED_STATES` holds 32 sorted states. A completeness test
asserts that the union of states observed across the whole test module equals the
registry exactly — no unraisable state may be registered, and no unregistered
state may be raised. The ticket's required refusals map as:

| Ticket condition | State |
|---|---|
| missing required held marks | `BLOCKED_MISSING_HELD_RAW_MARK` |
| unsupported events | `BLOCKED_UNSUPPORTED_HELD_CORPORATE_ACTION`, `BLOCKED_NO_REGISTERED_UNSUPPORTED_EVENT_OUTCOME` |
| negative cash | `BLOCKED_NEGATIVE_POST_TRADE_CASH` |
| unavailable fill after the registered bound | `BLOCKED_UNAVAILABLE_FILL_AFTER_REGISTERED_BOUND` |
| inconsistent lots | `BLOCKED_INCONSISTENT_TAX_LOTS` |
| adjusted-price ledger inputs | `BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT` |

## 6. Owner-gated registries — all EMPTY

Each ships `()`, carries the provenance quintet on its record type, has a
`validate_*`/`resolve_*` pair, and refuses with a typed state. `TEST_CONSTRUCTED`
records may be injected through `RegistryOverrides` and may never ship (identity
check against the module constant).

| Registry | Typed blocked state | Why it is empty |
|---|---|---|
| `REGISTERED_COST_RATE_POLICIES` | `BLOCKED_NO_REGISTERED_COST_RATE_POLICY` | NEE-119 takes integer bps from a registered cost policy; none exists |
| `REGISTERED_MAXIMUM_FILL_DEFERRALS` | `BLOCKED_NO_REGISTERED_MAXIMUM_FILL_DEFERRAL` | the ticket requires registration, not assumption |
| `REGISTERED_PARTICIPATION_LIMITS` | `BLOCKED_NO_REGISTERED_PARTICIPATION_LIMIT` | NEE-118 makes maximum participation a required run parameter and registers no value |
| `REGISTERED_SPREAD_IMPACT_MODELS` | `BLOCKED_NO_REGISTERED_SPREAD_IMPACT_MODEL` | spread/slippage/impact is excluded unresolved scope |
| `REGISTERED_RESIDUAL_CASH_DISPOSITIONS` | `BLOCKED_NO_REGISTERED_RESIDUAL_CASH_DISPOSITION` | NEE-119 carries residual cash as `EXPLICIT_NOT_REDISTRIBUTED` with no handler |
| `REGISTERED_UNSUPPORTED_EVENT_OUTCOMES` | `BLOCKED_NO_REGISTERED_UNSUPPORTED_EVENT_OUTCOME` | NEE-125 records `sourced_unsupported_outcome_policy_registered: false` |
| `REGISTERED_WITHHOLDING_POLICIES` | `BLOCKED_NO_REGISTERED_WITHHOLDING_POLICY` | NEE-119 names a withholding function NEE-118 does not implement |

Two further vocabularies are registered rather than empty, because the owner has
already frozen them: `REGISTERED_SHARE_MODES` and `REGISTERED_REGULATORY_FEE_MODES`.

## 7. Reused kernels — every call site

Nothing below is reimplemented.

| Bound role | Path | Kernel identity |
|---|---|---|
| `NEE_116_ASYMMETRIC_COST_LEDGER_ADAPTER_V3` | `qme/quant/asymmetric_costs_v3.py` | `QME-NEE116-HISTORICAL-ASYMMETRIC-COST-LEDGER-ADAPTER-V3` |
| `NEE_116_TAX_LOT_KERNEL` | `qme/quant/tax_lots.py` | `HIFO_IF_ACCOUNT_ELECTION_VERIFIED_ELSE_FIFO` |
| `NEE_118_ACCOUNTING_CONFIG` | `configs/quant/accounting-equations-v1.json` | `NEE-118-QME-ACCOUNTING-V1` |
| `NEE_118_EQUATIONS_KERNEL` | `qme/quant/equations.py` | `NEE-118-QME-ACCOUNTING-V1` |
| `NEE_119_QUANTITATIVE_CONTRACT_V2` | `configs/quant/qme-v0.1-contract-v2.json` | `qme-long-only-momentum-v0.1` |
| `NEE_125_CORPORATE_ACTION_FACTOR_KERNEL` | `qme/data/corporate_actions/factors_v1.py` | `QME-NEE125-CORPORATE-ACTION-FACTOR-KERNEL-V1` |
| `NEE_126_PRICE_STORE_RAW_COORDINATE` | `qme/data/stores/prices_v1.py` | `QME-NEE126-PRICE-STORE-V1` |
| `NEE_205_HISTORICAL_REGULATORY_FEE_KERNEL_V2` | `qme/quant/regulatory_fees_v2.py` | `QME-NEE205-HISTORICAL-REGULATORY-FEE-KERNEL-V2` |
| `NEE_205_REGULATORY_FEE_HISTORICAL_SCHEDULE` | `configs/governance/regulatory-fee-historical-schedule-v1.json` | `NEE-205-REGULATORY-FEE-HISTORICAL-SCHEDULE-V1` |

Digests are **observed** from `repository_root` at run time (`bind_registered_kernels`)
and written into the manifest, so a change to any bound artifact changes the run's
`config_sha256_grouped` and `code_sha256_grouped`. No digest of another file is
pinned as a literal, and this module never hashes its own bytes.

Call sites (`KERNEL_CALL_SITES`, asserted against this document by test):

- `qme.quant.asymmetric_costs_v3.asymmetric_self_financing_error_v3` — `_self_financing` (regulatory fee mode `POSTED_HISTORICAL_REGULATORY_FEES_V3`)
- `qme.quant.asymmetric_costs_v3.rebalance_with_historical_regulatory_fees_v3` — `_execute_fills`
- `qme.quant.equations._decimal` — `to_ledger_decimal`
- `qme.quant.equations.apply_split` — `apply_corporate_action_stage`
- `qme.quant.equations.dividend_receivable` — `apply_corporate_action_stage`
- `qme.quant.equations.rebalance` — `_execute_fills` (batch) and `_replay_fills` (staged replay)
- `qme.quant.equations.round_long_target_shares` — `_equal_weight_targets`
- `qme.quant.equations.self_financing_error` — `_self_financing`
- `qme.quant.equations.validate_fill_timing` — `FillSession.__post_init__`
- `qme.quant.regulatory_fees_v2.assess_regulatory_fees_historical` — delegated by the V3 adapter; never called directly
- `qme.quant.tax_lots.build_tax_lot_ledger` — `publish_tax_lots`

## 8. Golden two-rebalance reconciliation

All three frozen paths (`WHOLE_SHARE_ORDERS_WITH_FRACTIONAL_CUSTODY`,
`FRACTIONAL_CUSTODY_INTEGER_ORDERS`, `SYNTHETIC_BENCHMARK_SAME_LEDGER`) reconcile
**byte-exactly** against the independent oracle
`qme.fixtures.golden_two_rebalance.evaluate_fixture`, which never imports
`qme.quant`.

**What reconciles**

- positions (`positions_plus`, `positions_after_fill`, whole-dict)
- cash (`cash_plus`, `cash_after_fill`, `cash_after_payment`)
- receivables (`receivables_plus`, `dividend_receivable`, `receivables_after_payment`)
- costs (`transaction_cost`, per fill and aggregated)
- taxes (`transaction_tax`, per fill and aggregated, SELL-side only)
- NAV (`initial_nav`, `nav_minus`, `nav_plus`, `nav_after_split`,
  `nav_after_entitlement`, `nav_after_payment`, `final_nav`)
- the self-financing residual `0.00000000` on all six rebalances
- **lot share counts** — the engine seeds one opening lot per opening position at
  the opening raw mark, and the published open-lot shares equal the ledger
  positions per security exactly

**What cannot reconcile, and why** (pinned in the fixture's
`golden_reconciliation.cannot_reconcile`):

| Quantity | Reason |
|---|---|
| lot basis / realized gain / holding period / wash-sale fields | `TAX_LOT_REALIZATION_CONVENTION` is in the fixture's explicitly excluded unresolved scope; no basis or gain value is pinned anywhere in it |
| real regulatory fees | the golden policy `source_id` is `SYNTHETIC_FIXTURE_NOT_REGULATORY_AUTHORITY` and its 20 bps SELL charge is invented; the NEE-205 kernel runs in this engine's separate historical-fee scenario instead |
| asymmetric buy/sell costs | `ASYMMETRIC_BUY_SELL_COSTS` is excluded scope; the fixture cost is symmetric 10 bps |
| spread / slippage / impact | both the oracle and the frozen identity require execution price equal to the common mark, and no non-zero residual vector exists to calibrate against |
| capacity / target-trim solver | `CAPACITY_OR_TARGET_TRIM_SOLVER` is excluded scope; the vectors supply signed deltas as given |
| `gtn_ratio`, `one_way_turnover` | the golden expected ledger pins neither |
| half-even tie-break at 1e-8 | every product in the golden path is exactly representable at 1e-8, so no pinned value discriminates the half rule |
| zero-position pruning | no symbol reaches zero anywhere in the fixture |

## 9. Regression fixture

`tests/quant/fixtures/execution-accounting-v1.json` is
`REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE`, `reviewer_identity: null`,
`review_status: PENDING_INDEPENDENT_REVIEW`, `data_class:
SYNTHETIC_NON_EMPIRICAL_TEST_ONLY`. It was generated from this engine and is a
regression pin, **not** an independent oracle — the independent oracle is the
NEE-116A golden fixture in §8. Its regulatory-fee numbers are additionally
checked against hand arithmetic in the tests
(`500 × 20.60 / 1e6 = 0.0103` SEC, `10 × 0.000195 = 0.00195` FINRA TAF,
total `0.01225`, posted `0.01225000`).

Scenarios: `historical-regulatory-fee-posting`, `whole-share-integral-custody`,
`integer-orders-fractional-custody`, `equal-weight-residual-cash-repair`.
Blocked cases: `missing-open`, `halt`, `action-on-fill-date`,
`delisting-between-signal-and-fill`, `unavailable-after-registered-bound`,
`unsupported-held-corporate-action`, `negative-cash`.

## 10. Deviations and deliberate additions

1. **The NEE-125 corporate-action factor kernel is bound by identity and observed
   digest, not imported.** `qme/data/corporate_actions/__init__.py` imports
   `qme.data.corporate_actions.registered_events`, which imports
   `qme.data.alpha_vantage.store`; importing `factors_v1` therefore loads
   `qme.data.alpha_vantage.acquisition` and `qme.data.alpha_vantage.client` into
   the process, and `tests/architecture/test_import_boundaries.py` declares those
   modules off-limits to the research packages (`qme/quant`, `qme/stats`,
   `qme/experiments`, `qme/promotion`). No existing module under `qme/quant/**`
   or `qme/stats/**` imports `qme.data.**`. The engine therefore uses the frozen
   NEE-118 ledger executables `apply_split` and `dividend_receivable` for the
   share and entitlement transitions, checks split conservation exactly against
   the declared post-split mark, and the **test module** — which is not a research
   package — imports `factors_v1.verify_split_conservation` and cross-checks the
   engine's split transition against the accepted kernel.
2. **A local `group_sha256`.** The public grouped-hash helpers live in
   `qme.promotion` and `qme.governance` (T0 packages a T1 kernel must not import)
   and in `qme.data.stores.calendar_v1` (same package-init edge as deviation 1).
   `qme.foundation.lineage` supplies the canonical-JSON helper the engine does
   import and carries no grouped form. This follows the precedent in
   `qme/data/classification/rules_v1.py` and `qme/data/universe/av_proxy_review_v2.py`.
3. **`ungroup_sha256` at the evidence boundary.** The frozen NEE-118 evidence
   types require a contiguous 64-hex digest, and this repository forbids a
   contiguous 40/64-hex literal in source. Grouped values are stored and
   un-grouped at run time; no contiguous digest is ever written as a literal.
4. **`code_sha256_grouped` does not hash this module's own source.** T1 forbids
   self-pinning outside the grandfathered paths, and `qme/quant/execution_v1.py`
   is not one of them. Following
   `qme.data.stores.calendar_v1.store_binding_digest`, the code identity binds
   the engine's declared identifiers plus the **observed** source digest of every
   kernel it calls, so a change to any called kernel changes the value.
5. **`schema_sha256_grouped` is a descriptor digest, not a schema-file digest.**
   This lane may not add a file under `schemas/**` (T0 frozen contract), so the
   schema identity is the grouped digest over `OUTPUT_SCHEMA_DESCRIPTOR`
   (schema version plus the declared field order of every emitted row type).
6. **The two frozen tax-metric labels disagree, and the engine records both.**
   `configs/quant/accounting-equations-v1.json` freezes
   `PRE_CAPITAL_GAINS_TAX_AFTER_TRANSACTION_COSTS_AND_SUPPORTED_TRANSACTION_TAX`;
   `docs/quant/QME_ACCOUNTING_EXECUTION_METRICS_SPEC.md` §7 freezes
   `PRE_CAPITAL_GAINS_TAX_AFTER_TRANSACTION_COSTS_AND_SUPPORTED_WITHHOLDING`.
   Both byte sets are pinned in the NEE-118 manifest. The engine records the
   **config** label as canonical (`TAX_METRIC_LABEL_AUTHORITY =
   NEE_118_CONFIG_TAX_SCOPE_CANONICAL_METRIC_LABEL`), records the markdown variant
   verbatim beside it as `UNRESOLVED_ALTERNATE_TAX_METRIC_LABEL`, and normalizes
   neither. **This remains an open owner escalation.**
7. **`FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY`.** The golden fixture's cost and
   tax policy is explicitly non-regulatory, so reconciling against it requires a
   path that does not post SEC/FINRA fees. The mode is registered, reason-coded,
   and gated: a cost policy whose `regulatory_authority` flag is true cannot
   select it, so a real run cannot silently drop a fee.
8. **Opening lots are seeded from the opening positions** at the opening raw mark
   on the opening session, so published lots are comparable to the ledger. Without
   this the lot ledger would be missing every pre-existing position and the
   consistency invariant could not be checked at all.
9. **The negative-cash repair evaluates a full trial execution per step** rather
   than a cost model, so the loop is fee-aware exactly as the posted path is.
10. **`_decimal` is imported from `qme.quant.equations`.** It is a private symbol,
    but it is the single frozen base-10 parser in this repository and
    `qme/quant/capacity_solver_v2.py` already establishes this precedent; a second
    implementation of the same grammar would be the larger error.

## 11. What this lane did NOT do

- It did not modify any existing file. The only files added are the four named at
  the top.
- It did not add or change any governance, schema, or config artifact.
- It did not register a single owner-gated value.
- It did not clear a freeze blocker, record an independent review, or produce a
  governance receipt.
- It did not evaluate portfolio capacity. NEE-118 freezes
  `portfolio_capacity_output: null` and the V3 capacity solver's freeze candidate
  still carries an active blocker; emitting a certificate here would overwrite a
  frozen `null`.
- It did not implement supported withholding, spread/impact, participation
  limits, or residual-cash disposition. Each is an empty registry that refuses.
