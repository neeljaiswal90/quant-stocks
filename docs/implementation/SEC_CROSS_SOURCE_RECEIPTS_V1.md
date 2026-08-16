# SEC Cross-Source Receipts V1 — acquisition record (2026-08-16)

Status: `ENGINEERING_OUTPUT_T2` — this is the cross-source leg of blocker
`NEE-116-CORPORATE-ACTION-EDGE-CASES`, claim `cross_source_receipts_attached`.
It acquires, hashes, and immutably stores the SEC primary-source documents that
corroborate each registered corporate-action fixture event. It is **not** a T0
registration, it resolves no blocker, and it decides no pack correction; a later
registration may cite the accession numbers and sha256s below.

| field | value |
|---|---|
| code | `qme/data/sec/edgar_receipts.py`, `qme/data/sec/__init__.py`, `qme/cli/sec_receipts.py` |
| tests | `tests/data/test_edgar_receipts.py` (59 tests, hermetic; every test injects a fake transport — no network) |
| registered set | `docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md` §5.1; taxonomy §5.3 |
| companion single-source record | `docs/implementation/AV_M0_FIXTURE_PULLS_2026-08-16.md`; the extraction slice on `codex/nee-116-corporate-action-events` |
| data root | owner machine, `D:\qme-data-local`; writes only under `raw/sec_edgar/` and `derived/corporate-actions/receipts/`; logical ids below are root-relative |
| tool | `python -m qme.cli.sec_receipts fetch-registered --repository-root . --data-root D:\qme-data-local --min-interval 1.2` |
| acquisition run | `20260816T194840Z-sec-receipts` — 2026-08-16T19:48:40Z → 19:49:07Z, **24 requests** (8 submissions indexes, 3 filing-header indexes, **13 documents downloaded**); 0 throttles, 0 retries |
| index run (definitive) | `20260816T200430Z-sec-receipts` — 2026-08-16T20:04:30Z → 20:04:42Z, **11 requests** (8 submissions indexes, 3 filing-header indexes, **0 documents downloaded** — all 13 reused after sha256 re-verification, identical digests) |
| index | `derived/corporate-actions/receipts/20260816T200430Z-sec-receipts/receipts-index.json` |
| index sha256 | `f55c4a5016a08fe8a2488e8d1a872ff7d3025e32f43b9ef34408a925b0d30bc8` |
| outcome | counts `{'CORROBORATED': 7}`, 13 documents stored, all 7 registered events located; CLI exit code 0 |
| claims | cross_source_receipts_stored_immutably=true; cross_source_receipts_reviewed=**false**; oracle_fixture_built=**false**; freeze_blocker_changed=**false** |

## Fair access

Every request declared `User-Agent: qme-research/0.1 (neeljaiswal90@gmail.com)`
— `EdgarClient` refuses to construct without a contact address, so an anonymous
pull cannot happen by accident — and requests were paced at one per 1.2 s, an
order of magnitude under the published 10/s ceiling. `403`/`429` back off for at
least 10 s and retry at most twice before the run records `PULL_UNAVAILABLE`;
neither path was exercised in this run. Only documented endpoints were used:
`https://data.sec.gov/submissions/CIK##########.json` for the filing index and
`https://www.sec.gov/Archives/edgar/data/<cik>/<accession>/<document>` for
documents, plus the Archives `-index-headers.html` SGML header of three
individual filings to resolve exhibit types. No HTML search page was scraped.

Two runs were executed. The first downloaded all 13 documents; the second — after
the quote filters were tightened — re-derived the index from the same criteria
and re-used every stored document with **zero** document requests, each body
re-hashed against its sidecar and matching byte for byte. The second run is the
definitive index; the first is retained because the store is append-only.

## What was acquired

Filings are located by **criteria**, never by a hard-coded accession number: a
target declares (form types, inclusive filing-date window, optional any-of 8-K
item filter), and the run resolves it against the submissions index. A target
that does not resolve is recorded `RECEIPT_NOT_LOCATED` with the candidate rows
that were seen. Nothing in this slice can invent an accession number.

| event_id | status | form | accession | filed | document | sha256[:12] | bytes |
|---|---|---|---|---|---|---|---|
| `AAPL-SPLIT-DIVIDEND-2020` | CORROBORATED | 8-K | `0000320193-20-000060` | 2020-07-30 | `aapl-20200730.htm` | `a449baf26955` | 77,993 |
| `AAPL-SPLIT-DIVIDEND-2020` | CORROBORATED | 8-K EX-99.1 | `0000320193-20-000060` | 2020-07-30 | `a8-kexhibit991q3202062.htm` | `6830ad7c65b2` | 278,170 |
| `NVDA-SPLIT-2024` | CORROBORATED | 8-K | `0001045810-24-000113` | 2024-05-22 | `nvda-20240522.htm` | `f170f8e4996b` | 28,273 |
| `NVDA-SPLIT-2024` | CORROBORATED | 8-K EX-99.1 | `0001045810-24-000113` | 2024-05-22 | `q1fy25pr.htm` | `1e1f8a53abed` | 284,910 |
| `MSFT-DIVIDEND-2026Q3` | CORROBORATED | 10-Q | `0001193125-26-027207` | 2026-01-28 | `msft-20251231.htm` | `a60bb6a07479` | 7,483,278 |
| `COST-SPECIAL-DIVIDEND-2024` | CORROBORATED | 8-K | `0000909832-23-000062` | 2023-12-14 | `cost-20231213.htm` | `da79a3ac0bbf` | 24,127 |
| `COST-SPECIAL-DIVIDEND-2024` | CORROBORATED | 8-K EX-99.1 | `0000909832-23-000062` | 2023-12-14 | `costex9918-k121423.htm` | `c8d223a0b981` | 118,495 |
| `ATVI-CASH-MERGER-DELISTING-2023` | CORROBORATED | 8-K | `0001104659-23-108985` | 2023-10-13 | `tm2328253d1_8k.htm` | `4953b64acd5e` | 49,451 |
| `ATVI-CASH-MERGER-DELISTING-2023` | CORROBORATED | 25-NSE | `0001354457-23-000768` | 2023-10-13 | `xslF25X02/primary_doc.xml` | `43da04a1a76f` | 10,133 |
| `BBBY-ADVERSE-DELISTING-2023` | CORROBORATED | 8-K (item 3.01) | `0001193125-23-115523` | 2023-04-25 | `d89202d8k.htm` | `7c920e8e63a7` | 26,408 |
| `BBBY-ADVERSE-DELISTING-2023` | CORROBORATED | 25-NSE | `0001354457-23-000478` | 2023-07-10 | `xslF25X02/primary_doc.xml` | `e059e22f137a` | 10,126 |
| `BBBY-ADVERSE-DELISTING-2023` | CORROBORATED | 8-K (item 3.03) | `0001193125-23-247428` | 2023-09-29 | `d579010d8k.htm` | `9db142548b93` | 37,708 |
| `FB-META-IDENTITY-2022` | CORROBORATED | 8-K (item 8.01) | `0001326801-22-000070` | 2022-05-31 | `fb-20220531.htm` | `144ee0ca8d9b` | 36,251 |

`acceptedDateTime` is recorded per receipt in the index (EDGAR serves it as
`acceptanceDateTime`); e.g. the COST 8-K was accepted `2023-12-14T21:39:35.000Z`
and the ATVI merger 8-K `2023-10-13T08:34:52.000Z`. Bodies are stored at
`raw/sec_edgar/<10-digit CIK>/<accession>/<document>` with a `.meta.json`
sidecar and an append-only `raw/sec_edgar/_audit.jsonl` line each.

## What the documents say (verbatim, from the stored bytes)

Each quotation below is extracted mechanically from the hashed document named in
the table above. HTML/text primary documents and exhibits only; no PDF was read.

### AAPL — split and dividend, both in the Q3 FY2020 earnings release

From EX-99.1 `a8-kexhibit991q3202062.htm` (`6830ad7c65b2`):

> The Board of Directors has also approved a four-for-one stock split to make the stock more accessible to a broader base of investors.

> Each Apple shareholder of record at the close of business on August 24, 2020 will receive three additional shares for every share held on the record date, and trading will begin on a split-adjusted basis on August 31, 2020.

> The dividend is payable on August 13, 2020 to shareholders of record as of the close of business on August 10, 2020.

The declared amount in the same release is `$0.82 per share` — the pre-split,
as-declared amount, matching the Alpha Vantage `DIVIDENDS` row exactly. The pack's
split date **2020-08-31** is stated verbatim. The pack's dividend ex-date
**2020-08-07** is *not* in the filing (an ex-date is exchange-derived, not
issuer-declared); the filing gives record 2020-08-10, which under the T+2
regular-way convention then in force is one business day after Friday
2020-08-07. That consistency is **reported, not asserted as a registration**.

### NVDA — ten-for-one forward split

From the 8-K primary document `nvda-20240522.htm` (`f170f8e4996b`):

> On May 22, 2024, the Company announced a ten-for-one forward stock split, or the Stock Split, of the Company's issued common stock …

> As a result of the Stock Split, each record holder of common stock as of the close of market on Thursday, June 6, 2024 will receive nine additional shares of common stock, to be distributed after the close of market on Friday, June 7, 2024.

> Trading is expected to commence on a split-adjusted basis at market open on Monday, June 10, 2024.

The pack's split date **2024-06-10** is stated verbatim.

### MSFT — the registered dividend, from the 10-Q equity note

Microsoft announces quarterly dividends by press release, not by 8-K; the only
8-K in the announcement window (`0001193125-25-311196`, filed 2025-12-08, items
5.02/5.07) is the annual-meeting report and does **not** carry the declaration.
The SEC-filed primary source is the 10-Q for the quarter ended 2025-12-31
(`msft-20251231.htm`, `a60bb6a07479`), whose stockholders' equity note reads:

> Dividends Our Board of Directors declared the following dividends: Declaration Date Record Date Payment Date Dividend Per Share Amount Fiscal Year 2026 (In millions) September 15, 2025 November 20, 2025 December 11, 2025 $ 0.91 $ 6,762 December 2, 2025 February 19, 2026 March 12, 2026 0.91 6,760 …

Declaration 2025-12-02, **record 2026-02-19**, **payment 2026-03-12**, **$0.91** —
the pack's amount and payable date confirmed verbatim. Note the pack calls
2026-02-19 the *ex-date* while the filing calls it the *record date*; under T+1
(effective 2024-05-28) those coincide for regular-way trades, which is also why
the Alpha Vantage row carries `record_date == ex_dividend_date == 2026-02-19`.
Recorded, not normalized.

### COST — the receipt the ex-date correction turns on

From the 8-K primary document `cost-20231213.htm` (`da79a3ac0bbf`):

> On December 13, 2023, the Board of Directors declared a special cash dividend on the Company's common stock of $15 per share, payable January 12, 2024, to shareholders of record at the close of business on December 28, 2023.

The EX-99.1 press release `costex9918-k121423.htm` (`c8d223a0b981`) repeats the
same three values. Verbatim: amount **$15 per share**, record date
**December 28, 2023**, payable **January 12, 2024**.

The pack registers ex-date **2024-01-11**. The filing shows 2024-01-11 is neither
the record date nor the payment date; it is the business day before the payment
date. The Alpha Vantage pull's ex 2023-12-27 is the business day before the
record date 2023-12-28, i.e. what the then-current T+2 regular-way convention
produces. This slice **reports** that alignment and does **not** correct the
registration: changing §5.1 is a T0 decision for the owner.

### ATVI — cash consideration and the delisting

From the merger-completion 8-K `tm2328253d1_8k.htm` (`4953b64acd5e`), items
2.01/3.01/3.03/5.01/5.02/5.03/9.01:

> … was cancelled and automatically converted into the right to receive $95.00 in cash (the "Merger Consideration"), without interest.

> … a Notification of Removal from Listing and/or Registration under Section 12(b) of the Securities Exchange Act of 1934, as amended … on Form 25 to report that the Shares are no longer listed on Nasdaq …

The Form 25 itself (`25-NSE`, `0001354457-23-000768`, filed and accepted
2023-10-13T09:01:06Z, `43da04a1a76f`) names `Issuer: Activision Blizzard, Inc.`
and `Exchange: Nasdaq Stock Market LLC`.

The `$95.00 in cash` sentence is the **sourced deal consideration** §5.3 requires
for the `CASH_MERGER` valuation rule — the input a price pull could not supply, and
the reason that class was `BLOCKED` before this slice. The registered $95 and the
delisting date 2023-10-13 are both corroborated; the last raw close of 94.42 is a
market print and is not reconciled against the consideration here.

### BBBY — two dated coordinates, reported, not reconciled

The registered fixture date is 2023-05-03; the last `BBBYQ` print in the Alpha
Vantage pull is 2023-09-29. Both coordinates now have a receipt.

From the 8-K item 3.01 `d89202d8k.htm` (`7c920e8e63a7`), filed 2023-04-25:

> On April 24, the Company received written notice (the "Delisting Notice") from the Listing Qualifications Department of the Nasdaq Stock Market LLC ("Nasdaq") notifying the Company that, as a result of the Chapter 11 Cases and in accordance with Nasdaq Listing Rules 5101, 5110(b) and IM-5101-1, Nasdaq had determined that the Company's common stock will be delisted from Nasdaq.

> Trading of the Company's common stock will be suspended at the opening of business on May 3, 2023.

The Form 25 (`25-NSE`, `0001354457-23-000478`, filed 2023-07-10, `e059e22f137a`,
`Issuer: BED BATH & BEYOND INC`, `Exchange: Nasdaq Stock Market LLC`) is the
formal removal from listing — **68 days after the trading suspension**.

From the 8-K item 3.03 `d579010d8k.htm` (`9db142548b93`), filed 2023-09-29:

> The Confirmed Plan became effective (the "Effective Date") on Friday, September 29, 2023.

> … all of the Company's equity interests, consisting of outstanding shares of common stock and Series A Convertible Preferred Stock of the Company and related rights to receive or purchase shares of common stock, were cancelled on the Effective Date …

So the registered 2023-05-03 is the **Nasdaq trading-suspension** date, 2023-07-10
is the **Form 25 removal-from-listing** date, and 2023-09-29 is the **plan
effectiveness / equity cancellation** date and the last OTC print. Three distinct
dated facts. Which one the fixture should use is a registration question and is
**not** decided here.

### FB → META — identity change

From the 8-K item 8.01 `fb-20220531.htm` (`144ee0ca8d9b`):

> … issued a press release announcing that its Class A common stock will begin trading on The Nasdaq Global Select Market under the new ticker symbol 'META' prior to market open on June 9, 2022.

The pack's 2022-06-09 is stated verbatim. The filing's own cover page still lists
`Trading Symbol(s): FB`, which is exactly the point-in-time identity split the
§1.3 identity layer will have to represent.

## Judgment calls made in this slice

1. **Selection is by criteria, not by accession.** Every target is (form types,
   date window, optional 8-K item). Each of the seven resolved to exactly one
   filing per target, unambiguously. Nothing is hard-coded that a re-run could
   not re-derive from the submissions index.
2. **AAPL's split announcement is in an item 2.02 filing, not item 8.01.** The
   §5.1 working note expected item 8.01; Apple filed it inside the Q3 FY2020
   earnings 8-K. The item filter follows the filing.
3. **MSFT is corroborated by a 10-Q, not an 8-K**, because no 8-K carries the
   declaration (see above). The 10-Q is a stronger primary source anyway: it
   tabulates declaration, record, payment and amount together.
4. **Exhibits are resolved from the Archives `-index-headers.html` SGML header.**
   `index.json` in the same directory lists file names but its `type` field is an
   icon name, so it cannot identify `EX-99.1`. The header page is an Archives
   artifact, not a search page. Three such fetches were made (AAPL, NVDA, COST).
5. **The Form 25 documents are stored as EDGAR indexes them** — the submissions
   index names `xslF25X02/primary_doc.xml` (the XSL-rendered view) as the primary
   document, so that is what was fetched and hashed, rather than a hand-picked
   sibling path.
6. **`Pacer` is reused from `qme/data/alpha_vantage/client.py`** rather than
   duplicated. It carries no Alpha Vantage semantics; duplicating twelve lines of
   pacing logic was judged worse than the import.
7. **Re-runs are idempotent and cheap.** A document already stored under its
   logical id is re-hashed against its sidecar and reused with **no** request;
   only the submissions and filing indexes are re-read. A stored body whose bytes
   no longer match its sidecar is a fail-closed error, never a silent refetch.
8. **Quoted sentences are stored in the index** next to the sha256 they came
   from. They are reading aids extracted by a deliberately dumb tag-stripper —
   not a parsed, typed reading of the filing. No value in this slice is derived
   from them.

## What remains (explicit non-claims)

- **Owner review.** `cross_source_receipts_reviewed=false`. The documents are
  fetched and hashed; nobody has signed off that they say what this record says
  they say. §5.2's `OWNER_REVIEWED_NOT_INDEPENDENT` state is not entered here.
- **The COST ex-date correction.** The receipt settles the *facts* (record
  2023-12-28, payable 2024-01-12) and shows the registered ex-date 2024-01-11
  matches neither. Amending §5.1 — or registering the discrepancy — is a T0
  decision that this slice does not make.
- **The BBBY coordinate.** Three dated facts are now on the record
  (2023-05-03 suspension, 2023-07-10 Form 25, 2023-09-29 plan effective /
  cancellation). Choosing the fixture's delisting date, and the §5.3
  `ADVERSE_UNKNOWN` scenario applied to it, is a registration decision.
- **Oracle fixture construction.** `oracle_fixture_built=false`. No golden ledger
  fixture is built from these receipts; `qme/fixtures/golden_two_rebalance.py` is
  untouched.
- **Freeze blocker.** `NEE-116-CORPORATE-ACTION-EDGE-CASES` is unchanged
  (`freeze_blocker_changed=false`). Resolving it still needs the owner review,
  the two registration decisions above, and the oracle extension.
- **Issuer documents.** No issuer-hosted press release was fetched; every receipt
  is an SEC filing, so `raw/issuer_documents/` was not created. If a future event
  has no SEC filing, that path is where its receipt belongs.
- **Identity layer.** The `BBBY→BBBYQ` and `FB→META` mappings remain per-fixture
  notes; the §1.3 point-in-time identity layer does not exist yet.
