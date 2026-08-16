# Alpha Vantage M0 Fixture Pulls — Evidence Record (2026-08-16)

Status: `ENGINEERING_OUTPUT_T2` — raw pulls executed and stored immutably under the
owner's data root. This record is **not** a T0 registration; the pull ids and sha256s
below are what a later T0 registration for `NEE-116-PRODUCTION-PIT-DATA`,
`NEE-116-CORPORATE-ACTION-EDGE-CASES`, and `NEE-119-AV-PROXY-EVIDENCE` may cite.

| field | value |
|---|---|
| run_id | `20260816T033624Z-av-m0-fixture-pulls` |
| started / finished (UTC) | 2026-08-16T03:36:24+00:00 / 2026-08-16T03:36:52+00:00 |
| listing_date (signal session) | **2026-07-31** — assumption: most recent month-end session before the run; T0 registration must confirm or re-pull |
| data root | owner machine, `QME_DATA_ROOT=D:\qme-data-local` (deliberately **not** the documented example path, which must never exist as a runtime default — see `tests/foundation/test_config.py`); logical ids below are root-relative |
| tool | `python -m qme.cli.av_ingest m0-fixtures` (paced 1.2 s/request; premium key; no soft errors) |
| outcome | counts `{'OK': 23}`, all_ok `True` |
| claims | raw_pulls_stored_immutably=true; production_pit_evidence_registered=**false**; proxy_snapshot_reviewed=**false**; freeze_blocker_changed=**false** |

## Pull records (23 registered)

| function | symbol | params | rows | range | sha256 | pull_id |
|---|---|---|---|---|---|---|
| LISTING_STATUS | — | `{'date': '2026-07-31', 'state': 'active'}` | 13611 | — | `5f36e616dd82e506659e310db50ac70e94da0b153b7fdf127c6d0970f9c81030` | `20260816T033626707774Z-5f36e616dd82` |
| LISTING_STATUS | — | `{'date': '2026-07-31', 'state': 'delisted'}` | 10078 | — | `c88ac8e0948ac059c4a421052bb470be31fc10ad448ef6c95210adf49378e684` | `20260816T033627569033Z-c88ac8e0948a` |
| TIME_SERIES_DAILY | AAPL | `{'outputsize': 'full'}` | 6737 | 1999-11-01..2026-08-14 | `b1cd3580367ce83dee43addec15ff85613fcfbbb35c4538d82ddbb5670035616` | `20260816T033628897425Z-b1cd3580367c` |
| DIVIDENDS | AAPL | `{}` | 58 | 2012-08-09..2026-08-10 | `3760057d1606f272661446c5004f58b81301385e9cf9d2ba90ec6b2fc1416a0b` | `20260816T033629270542Z-3760057d1606` |
| SPLITS | AAPL | `{}` | 4 | 2000-06-21..2020-08-31 | `22a12013328173ff73f8d4885ec57827d39c3906ba8bbd3013d7c79ebd2292d7` | `20260816T033630450654Z-22a120133281` |
| TIME_SERIES_DAILY | NVDA | `{'outputsize': 'full'}` | 6737 | 1999-11-01..2026-08-14 | `7810fd11de2760e70f11f78e60be1b965dfb921abf61c9db87f3a393f2ffde2b` | `20260816T033632153341Z-7810fd11de27` |
| DIVIDENDS | NVDA | `{}` | 56 | 2012-11-20..2026-06-04 | `c5145d3e021889a8d386f176b11ba4124cc4d193deca0e9e474498557519ab09` | `20260816T033632890563Z-c5145d3e0218` |
| SPLITS | NVDA | `{}` | 6 | 2000-06-27..2024-06-10 | `a300dffc4d426d69c04fd8db62f93bca4de91f5ec5b0195b261944094fe7905f` | `20260816T033634077188Z-a300dffc4d42` |
| TIME_SERIES_DAILY | MSFT | `{'outputsize': 'full'}` | 6737 | 1999-11-01..2026-08-14 | `f744e62116ceb893b2c3c5c82a8ded5511d273774519f4a6c4f3f48bc49709ad` | `20260816T033635745248Z-f744e62116ce` |
| DIVIDENDS | MSFT | `{}` | 91 | 2003-02-19..2026-08-20 | `098339f935e9c0233ae5577b2a81cf64f682f3ecec93d38eeec9d65c6a8f646e` | `20260816T033636537665Z-098339f935e9` |
| SPLITS | MSFT | `{}` | 2 | 1999-03-29..2003-02-18 | `97cb08f881c0b64921a61ad8f538884a19234ec7d08dbfcd249085c7607b82e1` | `20260816T033637657182Z-97cb08f881c0` |
| TIME_SERIES_DAILY | COST | `{'outputsize': 'full'}` | 6737 | 1999-11-01..2026-08-14 | `8c80266aa764d0f41e84af8df728445873a6b1f9d86240609edd8f6c641c4a44` | `20260816T033639324101Z-8c80266aa764` |
| DIVIDENDS | COST | `{}` | 96 | 1999-05-24..2026-07-24 | `7355c1324529bff2014754e4b61d9b907eaddd2942c788718418589292999971` | `20260816T033640095967Z-7355c1324529` |
| SPLITS | COST | `{}` | 1 | 2000-01-14..2000-01-14 | `f397be293f283ef4c7793613e95271a67ff5ccf433f59f735a03b98419f31aca` | `20260816T033641238056Z-f397be293f28` |
| TIME_SERIES_DAILY | META | `{'outputsize': 'full'}` | 3580 | 2012-05-18..2026-08-14 | `78da904cf51fe16caf33e9081900b88560f5247c89e8c00e3b9ca6db893505f8` | `20260816T033642845690Z-78da904cf51f` |
| DIVIDENDS | META | `{}` | 10 | 2024-02-21..2026-06-15 | `fcd17a4be5bcccc578e969133d0e99533632b44ae9d48576519d8b6cb94b7a1b` | `20260816T033643664096Z-fcd17a4be5bc` |
| SPLITS | META | `{}` | 0 | — | `db75876d1a54cd4b3afd42dbb2f182b74f67e3cca35ba39b171a9a45846e1af8` | `20260816T033644872720Z-db75876d1a54` |
| TIME_SERIES_DAILY | ATVI | `{'outputsize': 'full'}` | 6027 | 1999-11-01..2023-10-13 | `6c045f290591bbe779b33e2a4783fa68b162afefe59cc5934e8c7ca2e6255190` | `20260816T033646517830Z-6c045f290591` |
| DIVIDENDS | ATVI | `{}` | 14 | 2010-02-18..2023-08-01 | `e9949aabf4a4b8873a190f1ad1f08d640abed38cd4e4dc23c3b670a76846835a` | `20260816T033647246267Z-e9949aabf4a4` |
| SPLITS | ATVI | `{}` | 6 | 2001-11-21..2008-09-08 | `2a21ca40c2200f4f3203a4e640e826339fde861718ad4f3b230ca3565f3e45e2` | `20260816T033648490783Z-2a21ca40c220` |
| TIME_SERIES_DAILY | BBBY | `{'outputsize': 'full'}` | 6092 | 2002-05-30..2026-08-14 | `0feffbca8c1ab603bff1001a077ee7f584d42160b87a08e334c875315dbe9715` | `20260816T033650146830Z-0feffbca8c1a` |
| DIVIDENDS | BBBY | `{}` | 0 | — | `c8ac086ba1026a7e6e72c9e1cb409d1377f565ac08aeee730a45bab3dbec4c4b` | `20260816T033650873094Z-c8ac086ba102` |
| SPLITS | BBBY | `{}` | 0 | — | `c8ac086ba1026a7e6e72c9e1cb409d1377f565ac08aeee730a45bab3dbec4c4b` | `20260816T033652084426Z-c8ac086ba102` |

## Identity findings surfaced by the production data

1. **`BBBY` on Alpha Vantage is now Beyond Inc** (NYSE, IPO 2002-05-30 — the Overstock→Beyond lineage that
   adopted the Bed Bath & Beyond name and ticker). The 2026-07-31 active snapshot lists `BBBY,Beyond Inc,NYSE`.
   The **original** Bed Bath & Beyond (NASDAQ, delisted 2023-05-03) is served under its final symbol **`BBBYQ`**
   (6,017 daily rows, 1999-11-01 → 2023-09-29, i.e. through the OTC period to cancellation) and has **no row**
   in the delisted listing snapshot. → The registered ADVERSE_DELISTING fixture must be sourced as `av_symbol=BBBYQ`.
   This is a symbol-mapping correction, not a class substitution.
2. **`FB` is now a ProShares ETF** (BATS, listed 2025-06-26). `META` serves the continuous history from 2012-05-18,
   so the IDENTITY_TICKER_CHANGE fixture is sourced as `av_symbol=META`; `FB` must not be used.
3. **AV keys delisted securities by their final symbol** (`JOANQ` valid with full pre-/post-Chapter-11 history;
   `JOAN` → `Error Message`). Any point-in-time identity layer must map historical tickers to AV's final symbol.
4. **LISTING_STATUS delisted coverage is incomplete for bankruptcies**: BBBY(Q), SIVB, SBNY, RAD, PRTY, BIG are absent
   under original and `…Q` symbols; mergers (ABMD, AAWW, ATVI) are present. 2023 delisted rows: 1,384. This is
   evidence for the `AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY` claim scope, not against it — but the proxy
   snapshot review must state it.
5. `ATVI` is clean: delisted list `1993-10-25 → 2023-10-13`; daily history ends 2023-10-13. `MSFT` splits: AV lists 2
   (1999-03-29, 2003-02-18); coverage before 1999 is not claimed.

## Candidate probes (recorded, not part of the registered set)

| symbol | class | rows | range | sha256 | pull_id |
|---|---|---|---|---|---|
| JOANQ | OK | — | — | `220ea00f9ce5dffb08299b95e846cd229839cca5fcdd9af32a8e4e8c95ccb4b8` | `20260816T033906811232Z-220ea00f9ce5` |
| JOAN | SOFT_ERROR_ERROR_MESSAGE | — | — | `bb343fd5157935fb1336978a44dd1d6c4d0e1f370907d3c9f3ebbcbfeea635ed` | `20260816T033907546788Z-bb343fd51579` |
| BBBYQ | OK | — | — | `87f7a22f98d54c0c9ca33362f06bf7191d357df167d90174c53e91d0a9b5abca` | `20260816T033909079427Z-87f7a22f98d5` |
| RADCQ | OK | — | — | `3eba8643eaf7b2f486673a7187a44531f5212a24067132965b6e7f6e69cce135` | `20260816T033913040035Z-3eba8643eaf7` |

(BBBYQ: 6,017 rows 1999-11-01..2023-09-29; JOANQ: 789 rows 2021-03-12..2024-04-30; JOAN: Error Message; RADCQ: 6,376 rows 1999-11-01..2026-08-14.)

## What remains for the T0 registrations

- Confirm the listing signal-session date (2026-07-31 assumed) or re-pull at the registered date.
- Register `av_symbol` per fixture: AAPL, NVDA, MSFT, COST, META, ATVI, **BBBYQ**.
- Cross-source receipts (SEC filings / issuer releases) per §5.1 remain to be fetched and hashed.
- The reviewed proxy snapshot (exclusion classes applied, review log) has not been produced.
- Key hygiene: the API key was pasted in chat on 2026-08-16; rotate after the M0 pulls are registered.
