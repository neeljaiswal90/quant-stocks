# NEE-116 Asymmetric Costs V1 — sell-side regulatory fees in the ledger

Module: `qme/quant/asymmetric_costs.py` (T1; no self-pinning) · Tests: `tests/quant/test_asymmetric_costs.py` (12)
Registration implemented: M0 pack §5.4 (`NEE-116-ASYMMETRIC-COST-METHOD`)
Method id: `QME-NEE116-ASYMMETRIC-COST-BPS-PLUS-SELL-SIDE-REGULATORY-FEES-V1`
Status: `ENGINEERING_CANDIDATE_BLOCKER_EVIDENCE_PENDING_T0_REGISTRATION`

## Method

| side | cost |
|---|---|
| BUY | registered bps × gross notional (canonical `rebalance`) |
| SELL | registered bps × gross notional **+ SEC §31 + FINRA TAF** from the **dated** historical schedule at the fill's trade date |

`sec31_raw = notional × rate_per_million / 1e6`; `finra_taf_raw = 0` if `price < low_price_threshold` (equality charged), else `min(shares × rate, cap)`. Rates, caps, thresholds, and interval ids come from `lookup_regulatory_fee_schedule` — nothing is hardcoded without an effective date. Only `COVERED_EQUITY_ELIGIBLE_NO_EXEMPTION` is supported; exemptions fail closed.

**Rounding is unregistered** (the 2026 kernel records `rounding_method_registered=false`): fees are carried raw-exact and quantized only when posted at the ledger's internal quantum, labelled `FEE_ROUNDING_UNREGISTERED_RAW_EXACT_QUANTIZED_AT_LEDGER_QUANTUM_ONLY`. Broker pass-through semantics are not applied.

## Ledger integration

`equations.py` is hash-pinned by the golden manifest, so the integration **composes**: `rebalance_with_regulatory_fees` runs the canonical `rebalance` (bps cost + transaction tax), then charges the fee lines as a **separately reported** cash reduction with the extended identity `NAV⁺ = NAV⁻ − TC − TAX − REG_FEES` (`assert_asymmetric_self_financing` delegates the common-mark preconditions to the canonical `self_financing_error`). Trades are never rescaled to absorb fees; negative cash is rejected.

## Evidence in tests

- **Hand-checked golden extension** (pack §5.4): 100 sh × $1,200 sold 2024-06-10 → SEC `3.336` (27.80/$M) + TAF `0.0166` (.000166/sh, cap 8.30) = `3.3526`, recomputed independently with `Fraction`.
- **Cross-check against the sealed 2026 kernel** on its own valid request: `2.06` / `0.0195` reproduced exactly.
- Cap application, low-price exclusion (strict `<`, equality charged), and dated-rate differences (2011: 16.90/$M, .000075/sh) covered.

## Non-claims

No change to the frozen equations, golden fixture, or manifests; no freeze-blocker change. Clearing `NEE-116-ASYMMETRIC-COST-METHOD` requires a T0 registration citing this method id and an independently reviewed ledger fixture built from production receipts.
