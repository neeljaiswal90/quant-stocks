# Historical asymmetric-cost ledger V3

Status: `IMPLEMENTATION_CANDIDATE_ONLY_BLOCKER_CLEARANCE_NOT_REQUESTED`

This additive NEE-205 slice closes the mechanical gap between the protected
official SEC Section 31 / FINRA TAF history and the canonical accounting
ledger. It does not modify `qme/quant/asymmetric_costs.py`,
`qme/quant/asymmetric_costs_v2.py`, `qme/quant/equations.py`, or any protected
manifest. It does not resolve `NEE-116-ASYMMETRIC-COST-METHOD` by itself.

## Protected inputs

The slice was started from protected-main commit
`2c314ffb80d5a43a9e1396248daaa494394848dc` and tree
`f85a35cdf86a4f5316957744adcacfab7a98b630`.

`qme/quant/regulatory_fees_v2.py` executes a single verified capture of the
protected schedule runtime whose SHA-256 is
`bdec3584e297e9cd03be61858e4e3588e5ed17ce7fea17ccf60b8c9fa78889ec`.
The capture is compiled directly under a private stdlib-only import guard; it
does not import or trust an ambient `qme.quant.regulatory_fee_schedule` module.
The protected lookup then reopens and hash-verifies its config, schema, official
source receipts, KAT, predecessor authority, and Freeze V4 bindings for every
assessment.

Coverage is the schedule's exact calendar-date domain:

- SEC: caller-supplied Rule 31 `charge_date`, 2010-01-04 through 2026-08-14;
- FINRA: caller-supplied `trade_date`, the same bounded domain;
- outside either boundary: typed failure, never zero, latest, or extrapolated;
- SEC zero-rate intervals remain exact registered zero rates;
- FINRA low-price exclusion is strict `<`; equality remains charged;
- the 2023-11-06 PTF applicability regime is preserved in every posted line.

## Ledger method

The V3 adapter calls the historical kernel once per explicit
`regulatory_trade_id`. Every SELL fill must supply exact metadata identifying
the registered covered-equity eligibility and final, uncorrected transaction
state. Fills grouped under one ID must share symbol, execution price, coverage,
and transaction state; ambiguity fails closed.

For one regulatory trade:

1. aggregate eligible sold shares exactly;
2. compute exact notional identity `shares * execution_price`;
3. obtain raw SEC and FINRA components from the protected schedule-backed
   kernel;
4. quantize the raw total once to the protected `1e-8` ledger quantum with
   `ROUND_HALF_EVEN`;
5. post one regulatory-fee line.

BUY fills receive an explicit zero line and never invoke the kernel. The sum of
posted regulatory lines is deducted after canonical `equations.rebalance`, so
the existing bps transaction cost and transaction tax stay separate. The
adapter verifies the extended common-mark identity:

```text
NAV_plus = NAV_minus - transaction_cost - transaction_taxes - regulatory_fees
```

Fee-induced negative cash fails; trades are never repaired or rescaled.

## Independent transition fixture

`historical-asymmetric-costs-v3.cases.json` is generated without importing the
candidate runtime. Its oracle contains literal 20-row SEC and 9-row FINRA
tables, exact `Fraction` arithmetic, and an independent integer implementation
of q8 half-even rounding. The 28 unique transition dates cover the start of
every interval and assert interval IDs, raw components, posted fee, ending
cash, and zero self-financing residual. A test executes the generator from a
temporary root and requires byte-for-byte fixture equality.

## Fail-closed and nonclaim boundary

This slice remains a modeled regulatory assessment, not a Webull, broker, SRO,
or customer debit. It does not infer charge date, trade date, eligibility,
exemption, correction status, grouping, pass-through, or post-cutoff rates. It
does not implement CAT fees, options, futures, debt, broker rounding, customer
pass-through, or empirical cost calibration.

No production observation, point-in-time data, M0 completion, alpha result,
promotion, final-freeze, or live-order authority is claimed. A later evidence
packet must bind the then-current protected Freeze lineage, exact source and
fixture bytes, independent review, protected exact-SHA CI, and a causally later
receipt before any blocker transition can be proposed.
