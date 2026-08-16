# NEE-116 Tax-Lot and Wash-Sale Estimator V1

Module: `qme/quant/tax_lots.py` (T1 accepted-kernel tier; no self-pinning) · Tests: `tests/quant/test_tax_lots.py` (23, hand-worked `Fraction` oracles)
Registration implemented: `M0_REGISTRATION_PROPOSALS_2026-08-12.md` §5.5; owner mandate `review_and_accounting`; `economic-promotion-decision-v2.json` → `after_tax_co_condition`
Blocker addressed (engineering leg): `NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE`
Status: `ESTIMATOR_CANDIDATE_NOT_A_TAX_RETURN`

## Registered rules → implementation

| registered | implemented |
|---|---|
| `HIFO_IF_ACCOUNT_ELECTION_VERIFIED_ELSE_FIFO` | FIFO default; HIFO only with `election_verified=True` (a dated account-method artifact is still required to set that flag); every ledger carries `LOT_METHOD_*` and `ELECTION_*` labels |
| ST/LT boundary **> 365 days** | `(sale_date − holding_start).days > 365` ⇒ `LONG_TERM`; leap-year boundary tested (365 → ST, 366 → LT) |
| wash sales: 30 calendar days **before and after** a loss sale, strategy account only | replacement lots acquired in `[sale−30, sale+30]` absorb the loss pro rata by shares; sold shares are never their own replacement; each replacement share absorbs one wash; disallowed loss is **added to the replacement lot's basis**; the replacement lot's holding start **tacks** to the sold lot's; chains propagate (a washed replacement sold at a loss and replaced again carries the accumulated disallowance forward) |
| cross-account wash sales | out of scope; every result carries `…CROSS_ACCOUNT_UNDERSTATEMENT` |
| rate scenarios `{22%, 24%, 32%}` ST + `15%` LT | `estimate_scenario_tax` accepts only registered ST scenarios; labelled `REGISTERED_RATE_SCENARIO_NOT_OWNER_BRACKET` |
| splits | shares × ratio, total basis conserved (NEE-116A convention); same-day split applies before fills |

Fees: added to basis on BUY, subtracted from proceeds on SELL. Arithmetic is exact `Fraction`; outputs are canonical decimals at the accounting quantum (1e-8) — a 100/3 basis-per-share lot consumed in three sales conserves total basis to within one quantum.

## Netting (estimator choices — labelled, not registered)

Per year: ST and LT netted separately (with optional carry-in), then a net loss in one character offsets a net gain in the other; residual net loss carries forward **by character**. The ordinary-income deduction, state tax, NIIT, dividend taxation, and basis adjustments for return of capital are **not** modelled (`ORDINARY_INCOME_LOSS_DEDUCTION_STATE_NIIT_DIVIDENDS_OUT_OF_SCOPE`).

## Fail-closed

Short sales, oversells, non-positive quantities, negative prices/fees, unparsable numbers, unknown methods, HIFO without verified election, unregistered rate scenarios → `TaxLotError`. Nothing defaults to zero tax.

## Non-claims

No after-tax co-primary evaluation, no ledger integration (the strategy ledger does not yet emit fills to this module), no promotion or freeze-blocker change. Registering HIFO, the owner's actual bracket, and the wash-sale evidence in a golden fixture remain T0 work.
