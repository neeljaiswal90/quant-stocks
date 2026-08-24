# NEE-125 — Corporate-action factor / TRI kernel V1 (M1 Lane B prebuild)

Status: T2 engineering prebuild. It clears no blocker, registers no evidence, and
records no independent review.

- Kernel: `QME-NEE125-CORPORATE-ACTION-FACTOR-KERNEL-V1`
- Runtime: `qme/data/corporate_actions/factors_v1.py`
- Tests: `tests/data/test_corporate_action_factors.py`
- Known-answer vectors: `tests/fixtures/data/corporate-action-factors-v1.json`

This is the parallel-safe subset of NEE-125: the pure deterministic math kernel
and synthetic fixtures. Alpha Vantage raw-cache integration, security-identity
joins, vendor-adjusted comparison runs, and sourced outcome policies for
unsupported held events are **out of scope** and remain serialized behind
NEE-123 / NEE-127 or an owner registration.

## Mathematical contract

With `s_t` = new shares per old share on session `t` and `d_t` = cash per
**pre-action** share on session `t`:

| # | Contract line | Where it lives |
|---|---|---|
| C1 | `gross_return_t = (s_t * P_t + d_t) / P_(t-1)` | `gross_return()` |
| C2 | `TRI_t = TRI_(t-1) * gross_return_t` | `build_factor_series()` |
| C3 | `q_after = s_t * q_before` | `walk_ledger()` |
| C4 | `q_after * (P_before / s_t) = q_before * P_before` | `verify_split_conservation()` |
| C5 | `receivable_t = q_eligible * d_t` | `walk_ledger()` |
| C6 | NAV includes the receivable between entitlement and payment | `LedgerState.nav()` |
| C7 | `A_(u\|a) = prod s_v for u < v <= a` | `split_adjustment_factor()` + `build_factor_series()` |
| C8 | `P_split_adjusted_(u\|a) = P_raw_u / A_(u\|a)` | `SessionFactors.split_adjusted_close` |
| C9 | `V_split_adjusted_(u\|a) = V_raw_u * A_(u\|a)` | `SessionFactors.split_adjusted_volume` |
| C10 | raw dollar volume stays `P_raw_u * V_raw_u` | `SessionFactors.raw_dollar_volume` |
| C11 | dividends never adjust volume | `A` multiplies split factors only |
| C12 | actions after `a` are prohibited (typed error) | `BLOCKED_POST_CUTOFF_EVENT` |
| C13 | raw OHLCV never mutated; derived series separately named | `DERIVED_SERIES_NAMES` |
| C14 | unsupported action on a held position | `RUN_INVALID_UNSUPPORTED_HELD_ACTION` |
| C15 | unsupported action on an unheld security | `EXCLUDED_UNSUPPORTED_UNHELD_ACTION` |
| C16 | same-day composite ordering per the registered rule | bound, see below |
| C17 | exact base-10 arithmetic, no binary float | `Fraction` everywhere |
| C18 | input-permutation determinism | deterministic sort in `normalize_actions()` |

## Bound registered rules

Nothing about the same-day composite order is invented here. It is bound from
the reviewed bytes of `configs/quant/qme-v0.1-total-return-methodology.json`,
whose sha256 in the repository's grouped form is

```
95381821:c1c8ff00:e0e626b3:d7ee3646:6d12c3be:9e6b8cb7:5ee166f0:043454ac
```

That digest is asserted against the config file's own bytes by
`test_kernel_binds_the_registered_total_return_methodology_bytes`, and each rule
below is asserted field-by-field by
`test_same_day_ordering_is_bound_from_the_config_not_invented`.

| Registered field | Value | Kernel binding |
|---|---|---|
| `split_policy.event_order` | `SPLIT_BEFORE_DIVIDEND_UNIT_CONVERSION` | `REGISTERED_SAME_DAY_EVENT_ORDER` |
| `split_policy.split_applied_before_dividend_coordinate_conversion` | `true` | split collapses before the dividend coordinate conversion |
| `split_policy.ambiguous_pre_or_post_split_dividend` | `BLOCKED_AMBIGUOUS_EVENT_COORDINATE` | same-day dividend with no declared basis |
| `dividend_reinvestment.coordinate` | `POST_SPLIT_CASH_PER_SHARE` | `REGISTERED_DIVIDEND_COORDINATE` |
| `dividend_reinvestment.negative_distribution_allowed` | `false` | `BLOCKED_NEGATIVE_DISTRIBUTION` |
| `gross_factor_formula` | `split_factor_t * (raw_close_t + dividend_post_split_per_share_t) / raw_close_t_minus_1` | cross-checked inside `gross_return()` |
| `revision_policy.post_cutoff_event_in_current_run` | `BLOCKED_POST_CUTOFF_EVENT` | cutoff refusal state |
| `revision_policy.duplicate_source_event_id` | `BLOCKED_DUPLICATE_EVENT` | duplicate-event refusal state |
| `numeric_policy.rounding_mode` | `ROUND_HALF_EVEN` | `ROUNDING_MODE` |
| `numeric_policy.artifact_scale` | `18` | `ARTIFACT_SCALE` |
| `numeric_policy.binary_float_forbidden` | `true` | AST test: no float constant, no `float` name |

Ledger quanta come from the NEE-118 accounting units already carried by
`configs/quant/golden-two-rebalance-v1.json` (currency and share quantum
`0.00000001`, `ROUND_HALF_EVEN`).

### The two gross-factor forms are the same number

The ticket writes `(s_t * P_t + d_t) / P_(t-1)` with `d_t` in the **pre-action**
share coordinate; the methodology writes
`s_t * (P_t + d_post_split_t) / P_(t-1)` with the distribution in the
**post-split** coordinate. Because a holder of one pre-action share receives
`s_t` post-split shares, `d_t = s_t * d_post_split_t`, and the two expressions
are algebraically identical. `gross_return()` evaluates the ticket form and
cross-checks the registered form on every call; an inconsistent coordinate pair
raises `BLOCKED_AMBIGUOUS_EVENT_COORDINATE` rather than silently preferring one.

### If the ordering had not been registered

It is registered, so it is bound. The counterfactual is still reachable and
tested: `build_factor_series()` and `walk_ledger()` take
`same_day_event_order`, and a session carrying **both** a split and a dividend
under an unregistered value (including `None`) raises
`BLOCKED_UNREGISTERED_SAME_DAY_EVENT_ORDER`. Sessions without a composite are
unaffected, because there is nothing to order.

## Numerics

Every input is a canonical base-10 decimal string lifted to an exact
`fractions.Fraction`. There is no `float` in the module and no intermediate
rounding; values are rounded exactly once at the artifact boundary.

- Exactly-representable fields (`raw_close`, `raw_volume`, `raw_dollar_volume`,
  `split_adjustment_factor`, `split_adjusted_volume`,
  `split_adjusted_dollar_volume`, `applied_split_factor`,
  `applied_dividend_pre_action_per_share`) serialize as exact canonical decimals,
  and `render_exact()` raises if a value is not terminating.
- Ratio fields (`split_adjusted_close`, `gross_return`, `total_return_index`,
  `applied_dividend_post_split_per_share`) serialize at the registered
  `artifact_scale = 18` with `ROUND_HALF_EVEN`.
- Ledger currency and share values quantize at the NEE-118 `1e-8` quantum with
  `ROUND_HALF_EVEN`. An opening state or a split that would put shares outside
  that quantum raises `BLOCKED_NONREPRESENTABLE_SHARE_QUANTUM`, and opening cash
  or receivables outside it raise `BLOCKED_NONREPRESENTABLE_LEDGER_QUANTUM` —
  mirroring the golden oracle, which rejects non-representable positions rather
  than rounding them in.
- Every dataclass also exposes the underlying `Fraction`, so a downstream
  consumer never re-derives from a rounded artifact.

## Naming discipline

Raw OHLCV is immutable and echoed verbatim; every derived coordinate has its own
name, so a raw value cannot be silently read as an adjusted one:

```
raw_close  raw_volume  raw_dollar_volume
split_adjustment_factor  split_adjusted_close  split_adjusted_volume
split_adjusted_dollar_volume
gross_return  total_return_index
```

## Fixture inventory

`tests/fixtures/data/corporate-action-factors-v1.json` —
`SYNTHETIC_NON_EMPIRICAL_TEST_ONLY`, 10 series cases and 17 blocked cases.
Expected values were produced by an independent flat-formula oracle, not by the
kernel under test.

### Series cases

| Case | Contract lines | What it pins |
|---|---|---|
| `forward-split-4-for-1` | C1 C2 C3 C7 C8 C9 C10 | 4:1 split; adjusted close runs 100..104, TRI ends at exactly `1.04` |
| `reverse-split-1-for-10` | C1 C2 C7 C8 C9 C10 | `s = 0.1`; TRI ends at exactly `1.2` |
| `cash-dividend-only` | C1 C2 C10 C11 | `A` stays 1, volume untouched; TRI = `10000/9909` at scale 18 |
| `same-day-split-dividend-post-split-basis` | C1 C2 C16 | registered order, `POST_SPLIT` coordinate |
| `same-day-split-dividend-pre-action-basis` | C1 C2 C16 C17 | same economics declared as `d_t`; byte-identical output |
| `multi-action-chain` | C1 C2 C7 C9 C10 | splits 2, 3, 0.5 plus a dividend; `A` products 3, 1.5, 1.5, 0.5, 1, 1, 1 |
| `multi-action-chain-earlier-cutoff` | C7 | same chain, `a = 2026-06-15`: `A(2026-06-01)` becomes 6, not 3 |
| `special-dividend` | C1 C2 C11 | `SPECIAL` classification, $15 distribution |
| `unheld-unsupported-action-excluded` | C15 | series truncated at the spinoff; typed exclusion record |
| `golden-two-rebalance-aaa-sleeve` | C1 C2 C16 | the registered NEE-116A action session in factor space |

### Blocked cases

| Case | State |
|---|---|
| `post-cutoff-action` | `BLOCKED_POST_CUTOFF_EVENT` |
| `post-cutoff-payment-leg` | `BLOCKED_POST_CUTOFF_EVENT` |
| `post-cutoff-session` | `BLOCKED_POST_CUTOFF_SESSION` |
| `ambiguous-same-day-dividend-coordinate` | `BLOCKED_AMBIGUOUS_EVENT_COORDINATE` |
| `duplicate-event-id` | `BLOCKED_DUPLICATE_EVENT` |
| `duplicate-bar-session` | `BLOCKED_DUPLICATE_SESSION` |
| `foreign-security-action` | `BLOCKED_FOREIGN_SECURITY_ACTION` |
| `held-unsupported-action` | `RUN_INVALID_UNSUPPORTED_HELD_ACTION` |
| `negative-distribution` | `BLOCKED_NEGATIVE_DISTRIBUTION` |
| `nonpositive-split-factor` | `BLOCKED_NONPOSITIVE_SPLIT_FACTOR` |
| `payment-before-entitlement` | `BLOCKED_PAYMENT_BEFORE_ENTITLEMENT` |
| `nonpositive-raw-close` | `BLOCKED_NONPOSITIVE_RAW_CLOSE` |
| `negative-raw-volume` | `BLOCKED_NEGATIVE_RAW_VOLUME` |
| `noncanonical-decimal-input` | `BLOCKED_NONFINITE_INPUT` |
| `action-session-without-raw-bar` | `BLOCKED_MISSING_RAW_CLOSE` |
| `supported-action-after-exclusion` | `BLOCKED_SUPPORTED_ACTION_AFTER_EXCLUSION` |
| `unregistered-action-type` | `BLOCKED_UNKNOWN_ACTION_TYPE` |

The remaining seven states — `BLOCKED_NEGATIVE_HELD_SHARES`,
`BLOCKED_NEGATIVE_LEDGER_VALUE`, `BLOCKED_NONREPRESENTABLE_LEDGER_QUANTUM`,
`BLOCKED_NONREPRESENTABLE_SHARE_QUANTUM`, `BLOCKED_SPLIT_CONSERVATION_VIOLATED`,
`BLOCKED_SPLIT_WITHOUT_PRIOR_RAW_CLOSE`,
`BLOCKED_UNREGISTERED_SAME_DAY_EVENT_ORDER` — plus
`BLOCKED_UNREGISTERED_UNSUPPORTED_ACTION_POLICY` have dedicated tests.
`test_every_fail_closed_state_is_exercised` asserts the union is exactly
`FAIL_CLOSED_STATES`, so a new state cannot land without a test.

## Golden-two-rebalance cross-check

`tests/fixtures/quant/golden-two-rebalance-v1.vectors.json`
(`1e4b2d56:9f88e3b9:96f3c208:f0f3ba08:756bb9e2:c2b5134e:9a515c3d:72d3f2bb`) and
`...expected.json`
(`08ec14be:67de2cd9:120fa4fc:01736982:3bb6f05b:79dd483f:c06fa5ed:19dc0b47`) are
read, never modified, and their bytes are asserted before the comparison.

The golden fixture is a two-security ledger fixture; this kernel is
single-security. The cross-check runs the AAA sleeve and adds the constant BBB
sleeve value back to compare NAV. Both the strategy variant and the benchmark
reproduce exactly:

| Registered field | Strategy | Benchmark |
|---|---|---|
| `post_split_raw_shares` | 12.5 | 15 |
| `split_reference_value_before` / `_after` | 500 / 500 | 600 / 600 |
| `dividend_eligible_raw_shares` | 12.5 | 15 |
| `dividend_receivable` | 25 | 30 |
| `nav_after_split` | 1018 | 1018.4 |
| `nav_after_entitlement` | 1018 | 1018.4 |
| `cash_after_payment` | 43 | 48.4 |
| `receivables_after_payment` | 0 | 0 |
| `nav_after_payment` | 1018 | 1018.4 |

Two further overlaps fall out of the same run:

- the fixture's `raw_marks_after_split` value for AAA (40) equals this kernel's
  `split_reference_price` **and** its `split_adjusted_close` for the pre-split
  session, so the fixture's split-reference mark and the kernel's price factor
  are the same number reached two ways;
- the per-share total-return factor across the action session is exactly `1`,
  which is the factor-space statement of the fixture's NAV invariance
  (1018 before, 1018 after).

The registered blocked case `UNSUPPORTED_HELD_CORPORATE_ACTION`
(`held_raw_shares = 10`, `input = MERGER`) maps onto this kernel's
`RUN_INVALID_UNSUPPORTED_HELD_ACTION`; the state names differ because the
fixture names a ledger block and this kernel names a run state.

**Could not be cross-checked, and why:**

| Field / behaviour | Reason |
|---|---|
| `split_adjusted_volume`, `raw_dollar_volume`, C11 | the golden fixture carries no volume at all |
| `A_(u\|a)` products, C7 | the fixture has one action session, so every product is trivial |
| cutoff exclusion, C12 | the fixture has a point-in-time `analysis_as_of` but no split-adjustment cutoff |
| `split_adjusted_close` as a series | the fixture is a ledger fixture in raw coordinates only |
| `transaction_cost`, `transaction_tax`, `self_financing_residual`, `order_quantum`, `fill_states` | rebalance accounting, out of this kernel's scope |
| `BLOCKED_MISSING_OFFICIAL_RAW_OPEN` | an open-price sourcing policy, not a corporate-action rule |
| `applied_event_registry_after` | the fixture's replay-protection registry; this kernel is stateless per call |

## Deviations and deliberate additions

Everything below is beyond the ticket-verbatim lines and is flagged so a
reviewer can accept or reject it explicitly.

1. **`BLOCKED_POST_CUTOFF_SESSION`.** The ticket prohibits *actions* after `a`.
   A raw *bar* after `a` is also refused: `A_(u|a)` for `u > a` would be an
   empty product of 1, which is well-formed but meaningless in a point-in-time
   run. Refusing is fail-closed; the ticket does not require it.
2. **`BLOCKED_SUPPORTED_ACTION_AFTER_EXCLUSION`.** Once an unheld security is
   excluded, a split or dividend at or after the exclusion session is refused
   rather than silently dropped from `A`, which would otherwise change adjusted
   prices before the exclusion.
3. **Every action session must carry a raw bar** (`BLOCKED_MISSING_RAW_CLOSE`).
4. **A split on the first session of a ledger walk is refused**
   (`BLOCKED_SPLIT_WITHOUT_PRIOR_RAW_CLOSE`): C4 needs a `P_before`.
5. **`same_day_event_order` parameter.** Added so the "fail closed if the
   methodology registers no ordering" branch is reachable and testable even
   though the config does register one. Its default is the registered value.
6. **Multiple actions of one class on one session** collapse by product
   (splits) and sum (dividends). Both are commutative, so C18 holds by
   construction; a mixed-coordinate dividend pair is still converted before
   summing.
7. **Payment settlement is last within a session.** Settlement moves receivable
   to cash and never touches shares, so its position cannot change any value;
   fixing it makes the walk deterministic.
8. **Two registered scales are in play.** Factor/TRI artifacts use NEE-119
   `artifact_scale = 18`; the ledger uses the NEE-118 `1e-8` quantum. Both are
   bound and both are tested.
9. **No import of `qme.data.alpha_vantage` or
   `qme.data.corporate_actions.registered_events`.** The canonical-decimal,
   exact-render, and half-even helpers are re-implemented locally so this kernel
   stays a leaf with no vendor-cache coupling while a sibling builder owns
   `qme/data/alpha_vantage/**`.
10. **`qme/data/corporate_actions/__init__.py` is not modified.** The kernel is
    imported by its full module path.

## Integration seams left open for NEE-123 / NEE-127

The later integration calls exactly these signatures; nothing else in this
module is intended as a public entry point.

```python
build_factor_series(
    bars: Sequence[RawSessionBar],
    actions: Sequence[CorporateAction],
    *,
    security_id: str,
    adjustment_cutoff_session: str,
    held_raw_shares: str = "0",
    base_index: str = "1",
    same_day_event_order: str | None = REGISTERED_SAME_DAY_EVENT_ORDER,
    unsupported_action_policy: object = None,
) -> FactorSeries

walk_ledger(
    opening: LedgerState,
    bars: Sequence[RawSessionBar],
    actions: Sequence[CorporateAction],
    *,
    security_id: str,
    adjustment_cutoff_session: str,
    same_day_event_order: str | None = REGISTERED_SAME_DAY_EVENT_ORDER,
    unsupported_action_policy: object = None,
) -> LedgerWalk

normalize_actions(...) -> NormalizedActions
opening_ledger_state(*, raw_shares="0", cash="0", receivables="0") -> LedgerState
gross_return(...) -> Fraction
verify_split_conservation(...) -> Fraction
split_adjustment_factor(split_factors_after_session) -> Fraction
```

**NEE-123 (raw-cache integration)** must construct `RawSessionBar`,
`SplitAction`, `CashDividendAction`, and `UnsupportedAction` from the stored,
hash-verified pulls; carry the pull id and sha256 alongside, since this kernel
records no provenance; and supply `adjustment_cutoff_session` from the run's
point-in-time cutoff. It must also decide `share_basis` from sourced terms — the
kernel refuses to guess.

**NEE-127 (identity join)** must supply a `security_id` that is one continuous
security across the whole bar and action stream. The kernel refuses actions
whose `security_id` differs from the series (`BLOCKED_FOREIGN_SECURITY_ACTION`)
but cannot detect ticker reuse or a missed identity change; that guarantee is
the join's.

**Owner registration (later)** attaches sourced outcome policies for unsupported
held events by widening `unsupported_action_policy` past `None`. Until then any
non-`None` value raises `BLOCKED_UNREGISTERED_UNSUPPORTED_ACTION_POLICY`.

**Still owner-gated and not addressed here:** vendor-adjusted comparison runs and
their tolerances, delisting terminal values, and any promotion of these outputs
to evidence.

## Non-claims

- Synthetic only. No empirical corporate-action value is produced or validated.
- No vendor comparison, no tolerance, no identity join, no security master.
- No independent review is recorded; no freeze blocker changes.
- Serialized artifacts carry these non-claims in a `claims` block.
