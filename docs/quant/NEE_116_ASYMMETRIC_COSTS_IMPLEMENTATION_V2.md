# NEE-116 Asymmetric Costs — Implementation V2 (kernel-adapter)

**Implementation id:** `QME-NEE116-ASYMMETRIC-COST-LEDGER-ADAPTER-IMPLEMENTATION-V2`
**Economic method id (unchanged):** `QME-NEE116-ASYMMETRIC-COST-BPS-PLUS-SELL-SIDE-REGULATORY-FEES-V1`
**Module:** `qme/quant/asymmetric_costs_v2.py` · **Tests:** `tests/quant/test_asymmetric_costs_v2.py`
**Delegated kernel:** `qme.quant.regulatory_fees.assess_regulatory_fees` (registered NEE-205 bounded kernel,
`configs/governance/regulatory-fee-kernel-v1.json`)
**Status:** T1 engineering candidate — `SAME_CLAUDE_LINEAGE_INTERNAL_QA`; `formal_independent_review_satisfied = false`;
external independent acceptance required before any T0 binding. **No Freeze V4 blocker is cleared**
(`NEE-116-ASYMMETRIC-COST-METHOD` stays ACTIVE; 13 active / 0 resolved; `milestone_m0_complete = false`).

## Why V2 exists

A read-only internal rehearsal at protected-main `4848a7f` (report
`internal-rehearsal-asymcost-2026-08-18/ASYMCOST_REHEARSAL_INTERNAL_CLAUDE_QA.md`) confirmed four P1 contract
mismatches in V1 (`qme/quant/asymmetric_costs.py`, hash-bound by the owner-decision record and therefore
**not modified**), each lead-reproduced with exact numbers:

| id | V1 defect | numbers | V2 correction |
|---|---|---|---|
| F1 | SEC charge date **inferred from `trade_date`**; no `charge_date` input; cannot fail closed | trade 2026-04-03 / charge 2026-04-04, 100 sh @ 1000: V1 SEC **0** (total 0.0195) vs registered **2.06** (total 2.0795) | `charge_date` is an explicit **required** input, never derived; missing/ambiguous → typed error |
| F2 | FINRA cap applied **per sell `Trade`**, not per regulatory trade; no `regulatory_trade_id`/aggregation status/fill identity | 30,000+30,000 sh @ 20 (one regulatory trade, 2026): V1 **11.70** vs registered **9.79** | sell fills grouped by `regulatory_trade_id`; **one kernel call per regulatory trade** (one cap); duplicate fill identity → typed error |
| F3 | registered NEE-205 kernel **never called** — V1 re-implements the fee arithmetic (second calculator) | source has no `regulatory_fees` reference | **no fee arithmetic in V2**; every rate/cap/threshold/window/blocker decision is the kernel's |
| F4 | coverage/exemption + transaction status **defaulted, not declared** | — | `coverage_classification` and `transaction_status` declared per sell fill and passed through verbatim |

V1's arithmetic at coincident dates and its ledger identity were found exact; the *contract* was wrong.

## Public API

```python
rebalance_with_regulatory_fees_v2(
    before, trades, *,
    trade_date, charge_date,                       # charge_date REQUIRED, never derived
    regulatory_trade_metadata,                     # {fill_key -> RegulatoryTradeMetadata(regulatory_trade_id, coverage_classification, transaction_status)}
    aggregation_status,                            # must be "PRE_AGGREGATED_SINGLE_REGULATORY_TRADE"
    transaction_cost_rate_bps, transaction_tax_policy, repository_root,
    pass_through_semantics="NOT_APPLIED",          # anything else -> typed error (no broker claim)
    rounding_semantics="RAW_EXACT_DECIMAL_NO_ROUNDING",   # anything else -> typed error
    raw_marks_after=None, receivables_after=None,
) -> AsymmetricRebalanceResultV2
assess_sell_regulatory_trade_v2(...)   # one pre-aggregated SELL regulatory trade -> one kernel call
asymmetric_self_financing_error_v2 / assert_asymmetric_self_financing_v2
```

Fill key convention: **zero-based positional index into the caller-supplied `trades` sequence** (`FILL_KEY_CONVENTION`).
Every SELL fill must have metadata; a fill belongs to exactly one regulatory trade.

## Behaviour (what the adapter owns vs what the kernel owns)

1. Runs the pinned canonical `equations.rebalance` exactly as V1 (composition, not modification).
2. **BUY fills:** the kernel is not invoked; an explicit zero line is posted (`BUY_NOT_ASSESSED_REGISTERED_ZERO`)
   so zeros are recorded rather than omitted. (Owner decision 2026-08-18.)
3. **SELL fills:** grouped by `regulatory_trade_id`; **exact-only pre-aggregation** — same symbol AND same execution
   price required (otherwise typed error: ambiguous aggregation); eligible shares summed exactly; the kernel's
   identity `covered_sale_notional == eligible_sold_shares × execution_price_per_share` holds exactly; **one kernel
   call per regulatory trade**.
4. **Canonicalization to the kernel grammar** from exact `Decimal` values (trailing zeros stripped; never float/bool);
   non-canonical caller strings (`" 1.5"`, `"1E-3"`, `"+0.001"`, `"1_000"`) are **rejected**, not normalized.
5. **Kernel window:** the kernel's reviewed range is 2026-01-01..2026-08-14 (`outside: BLOCKED_NO_EXTRAPOLATION`).
   Any charge date the kernel reports outside that range becomes a typed error. **No fallback to the historical
   schedule** — V2 never reads it. (Owner decision 2026-08-18: pre-2026 fees require a NEE-205 successor kernel.)
6. **Any kernel status other than assessed** (`BLOCKED`, unsupported coverage/exemption, cancelled/corrected
   transaction, unknown aggregation/pass-through/rounding semantics) → `AsymmetricCostV2Error` carrying the kernel
   `reason_code`. A blocked assessment is never a zero fee and never a silent skip.
7. **Posting:** `RAW_EXACT_QUANTIZED_AT_LEDGER_QUANTUM_ONLY` — the kernel's raw `sec31_raw`/`finra_taf_raw`/`total_raw`
   are carried verbatim; the only quantization is `ledger_amount = q8(total_raw)` at `INTERNAL_CURRENCY_QUANTUM`,
   `ROUND_HALF_EVEN`. **Posting unit = the regulatory trade**: one line per regulatory trade (plus explicit BUY zeros);
   `regulatory_fees_total` = quantized sum of per-line `ledger_amount`. SEC and FINRA are reported separately from
   the bps transaction cost and the transaction tax.
8. Negative cash after fees → typed error (trades are never rescaled).
9. Extended identity: `NAV_after = NAV_before − transaction_cost − transaction_taxes − regulatory_fees`.

## Tests (55; independent oracle)

`tests/quant/test_asymmetric_costs_v2.py` contains an **independent `Fraction` oracle** (imports nothing from
`asymmetric_costs_v2` for its own arithmetic) and compares V2 against the oracle, the registered kernel, and V1
(regression pins). Coverage: charge date governs SEC (V2 2.0795 vs **V1 0.0195** pinned); missing charge date;
window fail-closed (2025-12-31, 2026-09-01, 2024-06-10); SEC transition day-before/day-of; low-price threshold
below/equal/above; TAF cap 50205/50206-share boundary; **aggregation** — two fills one regulatory trade → one cap 9.79
(**V1 11.70** pinned), two regulatory trades → 19.58, same-symbol/different-ids, different symbols, ambiguous
aggregation and missing metadata and wrong `aggregation_status` → typed errors; BUY explicit zero with the kernel
proven not invoked (monkeypatch spy); the fail-closed input matrix (float, bool, NaN, Infinity, exponent, leading
plus, whitespace, negative/zero shares, negative price, unknown coverage/exemption/pass-through/rounding, cancelled
transaction, missing regulatory trade id); ledger integration with oracle recompute (buy-only, sell-only,
sell-then-buy with SELL tax, multiple sell fills, negative-cash → typed error, input-order permutation invariance,
no double counting) and the extended identity; identity/label surface; and a source check that V2 contains **no
second calculator** (no schedule lookup; exactly one kernel call site).

## Non-claims

No Freeze V4 blocker cleared; V1 not modified in place; V2 is a candidate pending external independent acceptance
and a further versioned T0 implementation-correction record (the existing record is not modified); no broker
pass-through or Webull debit is claimed; no empirical or production-readiness claim; the economic method is unchanged —
only the implementation is versioned. Same-Claude-lineage internal QA does not satisfy formal independent review.
