# Corporate-Action Event Evidence V1 — extraction record (2026-08-16)

Status: `ENGINEERING_OUTPUT_T2` — this is the engineering leg of blocker
`NEE-116-CORPORATE-ACTION-EDGE-CASES`. It extracts, verifies, and packages the
registered corporate-action fixture events from the immutably stored Alpha
Vantage raw pulls. It is **not** a T0 registration and does not resolve the
blocker; a later registration may cite the pull ids and sha256s below.

| field | value |
|---|---|
| code | `qme/data/corporate_actions/registered_events.py`, `qme/cli/corporate_actions.py` |
| tests | `tests/data/test_registered_events.py` (hermetic; synthetic pulls in a tmp data root) |
| registered set | `docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md` §5.1; taxonomy §5.3 |
| raw pulls read | run `20260816T033624Z-av-m0-fixture-pulls` + the `BBBYQ` probe, per `AV_M0_FIXTURE_PULLS_2026-08-16.md` |
| data root | owner machine, `D:\qme-data-local`; `raw/` read-only (tree sha256 unchanged across the run), writes only under `derived/` |
| run_id | `20260816T184844Z-corporate-actions` |
| summary | `derived/corporate-actions/20260816T184844Z-corporate-actions/summary.json` |
| summary sha256 | `a5fc10189e668574d8220a3c06f067176f394a9d025c77bc4bfce75758ef744f` |
| claims | oracle_fixture_built=**false**; independent_review_recorded=**false**; cross_source_receipts_attached=**false**; freeze_blocker_changed=**false** |

## What was extracted

For each registered event the extractor locates the relevant stored pulls
(`TIME_SERIES_DAILY` / `DIVIDENDS` / `SPLITS` for the event's `av_symbol`),
re-verifies each body against its recorded sha256 through
`RawPullStore.read_body`, and records:

- the verbatim matching row(s) — split `effective_date`/`split_factor`; dividend
  `ex_dividend_date`/`amount`/`declaration_date`/`record_date`/`payment_date`;
- a window of **raw, unadjusted** daily bars: five sessions either side of each
  registered anchor date, or the final ten sessions for a delisting (plus a
  ±5-session window around the registered delisting date when that date is not
  the final session);
- the pull ids and sha256s used, the extracted values as canonical decimal
  strings, and any discrepancy text.

Pull selection rule: among `OK` audit records for a `(function, av_symbol)`
pair, the **earliest** `pull_id` (a `pull_id` begins with a UTC timestamp, so
lexicographic order is chronological). Against the owner's data root that rule
selected 12 pulls: 11 from the registered fixture run plus the `BBBYQ` probe
pull — each selected id matches the evidence-record table byte for byte.
A later re-pull appended to `_audit.jsonl` cannot displace registered evidence.

A missing pull is `PULL_UNAVAILABLE`. A body that fails sha256 verification, is
unreadable, or does not have its documented shape raises
`CorporateActionEvidenceError`. No value is ever inferred, adjusted, or filled in.

## Per-event status

| event_id | class | av_symbol | status | extracted |
|---|---|---|---|---|
| `AAPL-SPLIT-DIVIDEND-2020` | ORDINARY_SPLIT_AND_DIVIDEND | AAPL | **CONFIRMED_BY_RAW_PULL** | split 2020-08-31 factor `4.0000` → `4`; dividend ex 2020-08-07 amount `0.82`, declared 2020-07-30, record 2020-08-10, paid 2020-08-13 |
| `NVDA-SPLIT-2024` | LARGE_MODERN_SPLIT | NVDA | **CONFIRMED_BY_RAW_PULL** | split 2024-06-10 factor `10.0000` → `10` |
| `MSFT-DIVIDEND-2026Q3` | ORDINARY_DIVIDEND | MSFT | **CONFIRMED_BY_RAW_PULL** | ex 2026-02-19 amount `0.91`, declared 2025-12-02, record 2026-02-19, paid 2026-03-12 |
| `COST-SPECIAL-DIVIDEND-2024` | SPECIAL_DIVIDEND | COST | **NOT_FOUND_IN_RAW_PULL** | no row with ex_dividend_date 2024-01-11 in 96 rows |
| `ATVI-CASH-MERGER-DELISTING-2023` | CASH_MERGER_DELISTING | ATVI | **CONFIRMED_BY_RAW_PULL** | last session 2023-10-13 = registered delisting date; last close `94.4200`, volume `1` |
| `BBBY-ADVERSE-DELISTING-2023` | ADVERSE_DELISTING | **BBBYQ** | **VALUE_MISMATCH** | bar exists on 2023-05-03 (close `0.0979`); last available session is 2023-09-29 (close `0.0789`) |
| `FB-META-IDENTITY-2022` | IDENTITY_TICKER_CHANGE | **META** | **CONFIRMED_BY_RAW_PULL** | bar on 2022-06-09 (close `184.0000`); 2531 sessions before, 1048 after; no `FB` pull exists or was used |

Five of seven registered events are confirmed by the production raw pulls. Two
are not, and both are recorded rather than reconciled.

### COST — the registered ex-date is absent

The `COST` `DIVIDENDS` pull carries **no** row with `ex_dividend_date`
2024-01-11. It does carry the registered **$15.00** amount, at a different date:

```
{"ex_dividend_date": "2023-12-27", "declaration_date": "2023-12-13",
 "record_date": "2023-12-28", "payment_date": "2024-01-12", "amount": "15.0"}
```

The registered payment-adjacent date 2024-01-12 and the registered amount both
appear on that row; the registered ex-date does not appear anywhere in the pull.
Whether §5.1 recorded a payment date as an ex-date, or Alpha Vantage's ex-date
differs from the registered source, is **not decided here** — it needs the
cross-source receipt (issuer release / SEC filing), and the registration must
then either be corrected or the discrepancy registered.

### BBBY / BBBYQ — two facts, not reconciled

- The registered adverse-delisting date is **2023-05-03**. The `BBBYQ` pull has a
  bar on that session: open `0.0483`, close `0.0979`, volume `82,081,257`.
- The **final** session in the same pull is **2023-09-29** (close `0.0789`,
  volume `8,823,802`). Trading continues for ~5 months after the registered date.

Both facts are recorded verbatim. `TIME_SERIES_DAILY` carries no venue field, so
a NASDAQ→OTC listing change on 2023-05-03 is **not observable** in this pull; the
extractor therefore compares the registered date against the last available
session, they differ, and the status is `VALUE_MISMATCH`. No attempt is made here
to decide which date the fixture should use — that is a registration question
requiring the cross-source receipt.

Symbol note: Alpha Vantage now serves `BBBY` as **Beyond Inc** (NYSE, IPO
2002-05-30). The original Bed Bath & Beyond is keyed by its final symbol
`BBBYQ`. The registry records `symbol="BBBY"`, `av_symbol="BBBYQ"` — a
symbol-mapping correction, not a class substitution. A test asserts that a
`BBBY` pull sitting in the same store is never cited for this fixture.

### ATVI — the $95 consideration is recorded, not asserted

Last close on the final session is `94.4200`; the registered cash consideration
is `95`. The difference `-0.58` is recorded, along with
`last_close_equals_consideration=false` and
`sourced_deal_consideration_in_raw_pull=false`. Per §5.3 the `CASH_MERGER`
valuation rule requires the **sourced deal consideration**, which a price pull
cannot supply; absent that source the class is `BLOCKED`. Nothing here asserts
that the last close equals the deal price.

### §5.3 valuation inputs carried into the fixture inputs

- `CASH_MERGER` (ATVI): `registered_cash_consideration_per_share`, last close,
  the difference, and an explicit `sourced_deal_consideration_in_raw_pull=false`.
- `ADVERSE_UNKNOWN` (BBBYQ): the registered scenario set applied to the last
  trade — `scenario_haircut_000_per_share = 0`,
  `scenario_haircut_050_per_share = 0.03945` (exact half of `0.0789`). Both are
  reported; §5.3 requires promotion to hold under the conservative `0.0`
  scenario.

## Incidental data findings (recorded, not resolved)

1. **AV `DIVIDENDS` amounts are as-declared, not split-adjusted.** The AAPL
   2020-08-07 row reads `0.82` — the pre-split declared amount — while the 4:1
   split effective 2020-08-31 is in the same pull. The post-split-equivalent
   `0.205` does **not** appear. Any oracle that mixes the two endpoints must fix
   the adjustment convention explicitly; this extractor applies none.
2. **`TIME_SERIES_DAILY` is raw and unadjusted**, confirmed across the split
   boundaries: AAPL 2020-08-28 close `499.2300` → 2020-08-31 close `129.0400`;
   NVDA 2024-06-07 close `1208.8800` → 2024-06-10 close `121.7900`.
3. **MSFT `record_date` equals `ex_dividend_date`** (both 2026-02-19) on the
   registered row, unlike the AAPL and COST rows where the record date is one
   session after the ex-date. Recorded verbatim; not normalized.
4. **ATVI's final session has volume `1`** — a one-share print on the delisting
   date. Relevant to any liquidity or fill assumption built on the final bar.
5. The pack registers no dividend **amount** for the AAPL fixture, so the AAPL
   dividend confirms on date alone; the extracted `0.82` is recorded as an
   observation, not checked against a registered value.

## What remains (explicit non-claims)

This slice claims **only** that the listed rows and bars are present in
hash-verified stored pulls. It does not claim any of the following, and none of
them are done:

- **Cross-source receipts.** §5.1 requires one independent cross-source (issuer
  press release or SEC filing) per fixture, hash-bound. None are fetched or
  attached (`cross_source_receipts_attached=false`). Until they exist, every
  confirmation above is **single-source**, and the COST and BBBY discrepancies
  cannot be adjudicated.
- **Oracle fixture construction.** No golden ledger fixture is built from these
  inputs (`oracle_fixture_built=false`). The artifacts are inputs for a later
  extension of `qme/fixtures/golden_two_rebalance.py`, nothing more.
- **Independent review.** Not recorded (`independent_review_recorded=false`);
  §5.2's `OWNER_REVIEWED_NOT_INDEPENDENT` state is not entered here.
- **Freeze blocker.** `NEE-116-CORPORATE-ACTION-EDGE-CASES` is unchanged
  (`freeze_blocker_changed=false`). Resolving it needs the receipts, the
  registration decisions on the COST ex-date and the BBBY delisting date, and
  the oracle extension.
- **Delisting valuation.** No haircut, scenario selection, or merger payout is
  computed or promoted; §5.3's inputs are carried forward, not applied.
- **Identity mapping layer.** The `BBBY→BBBYQ` and `FB→META` corrections are
  recorded on the registry entries for these seven fixtures only. A general
  point-in-time identity layer (§1.3) does not exist yet.
