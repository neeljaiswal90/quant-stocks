# AV Survivorship-Reduced Common-Stock Proxy Snapshot v1 (2026-08-16)

Status: `ENGINEERING_OUTPUT_T2`. This document describes `qme/data/universe/av_proxy_snapshot.py`
and `qme/cli/av_universe.py`. It is **not** a T0 registration and it does not clear
`NEE-119-AV-PROXY-EVIDENCE`. A produced snapshot becomes evidence only when a reviewer has
worked the review log and a T0 registration cites the snapshot id and sha256.

## What it does

Given two immutably stored Alpha Vantage `LISTING_STATUS` raw pulls — `state=active` and
`state=delisted`, both requested for the **exact** signal-session date — the builder:

1. verifies each stored body against the sha256 recorded in `raw/alpha_vantage/_audit.jsonl`
   (`RawPullStore.read_body`) and refuses anything that is not an `OK` response, is not
   `LISTING_STATUS`, or does not carry the exact signal date;
2. parses both bodies strictly (header must equal the documented seven columns; every row must
   have seven fields and the status the state implies);
3. classifies every row through the ordered rule table below;
4. detects symbol collisions across and within the two lists;
5. emits the included set, the exclusion-reason table, the manual-review log, and provenance.

Membership at the signal session is the **active** list. The delisted list supplies the
survivorship context and the identity-conflict evidence; its rows are classified and counted
but are never included. Every parsed row ends in exactly one of three places — included,
excluded with a registered class, or additionally written to the review log. Nothing is dropped.

## Rule table

`rule_table_version = qme.av_proxy_classifier_rules.v1`,
`rule_table_sha256 = 9f2e3ec95d2ce7dc58079d66003a9c14af9ab411fe58b497e8f156fae7629fc7`
(sha256 of the table's own canonical JSON, recorded in every snapshot's provenance; changing
any pattern, order, or rationale changes the sha).

Evaluation is **first match wins**, in order. Rules read only `assetType`, `symbol`, and
`name` (uppercased) from the row — never the other list, never row order, never anything
outside the row — so the verdict for a row is a pure function of the row.

| # | rule_id | field | test | pattern | class |
|---|---|---|---|---|---|
| 1 | `ASSET_TYPE_ETF` | `assetType` | equals | `ETF` | `ETF` |
| 2 | `SYMBOL_GRAMMAR_NONCONFORMING` | `symbol` | fullmatch **absent** | `[A-Z][A-Z0-9]{0,7}(-[A-Z0-9]{1,4}){0,3}` | `AMBIGUOUS_IDENTITY` |
| 3 | `NAME_ACQUISITION_VEHICLE` | `name` | search | `\bACQUISITIONS?\b` or `\bMERGER\s+CORP` | `SPAC_ARTIFACT` |
| 4 | `SYMBOL_SUFFIX_PREFERRED` | `symbol` | search | `^[A-Z0-9]{1,5}-P($ or -)` or `^[A-Z]{3,5}PR[A-Z]$` | `PREFERRED` |
| 5 | `SYMBOL_SUFFIX_WARRANT` | `symbol` | search | `-WS($ or -)` or `^[A-Z]{3,6}WS$` | `WARRANT` |
| 6 | `SYMBOL_SUFFIX_UNIT` | `symbol` | search | `-UN?$` | `UNIT` |
| 7 | `SYMBOL_SUFFIX_RIGHT` | `symbol` | search | `^[A-Z0-9]{1,5}-R($ or -)` | `RIGHT` |
| 8 | `SYMBOL_SUFFIX_WHEN_ISSUED` | `symbol` | search | `-(WI or W or WD)$` | `WHEN_ISSUED` |
| 9 | `SYMBOL_SUFFIX_UNRECOGNIZED` | `symbol` | fullmatch **absent** | `[A-Z0-9]{1,5}(-[A-Z]){0,3}` | `AMBIGUOUS_IDENTITY` |
| 10 | `NASDAQ_FIFTH_CHARACTER_UNIT` | `symbol` | search | `^[A-Z]{4}U$` | `UNIT` |
| 11 | `NASDAQ_FIFTH_CHARACTER_WARRANT` | `symbol` | search | `^[A-Z]{4}W$` | `WARRANT` |
| 12 | `NASDAQ_FIFTH_CHARACTER_RIGHT` | `symbol` | search | `^[A-Z]{4}R$` | `RIGHT` |
| 13 | `NASDAQ_FIFTH_CHARACTER_PREFERRED` | `symbol` | search | `^[A-Z]{4}[MNOP]$` | `PREFERRED` |
| 14 | `NASDAQ_FIFTH_CHARACTER_WHEN_ISSUED` | `symbol` | search | `^[A-Z]{4}V$` | `WHEN_ISSUED` |
| 15 | `NASDAQ_FIFTH_CHARACTER_ADR` | `symbol` | search | `^[A-Z]{4}Y$` | `ADR` |
| 16 | `NAME_UNIT` | `name` | search | `\bUNITS\b` or `(?<=\S\s)\bUNIT\b` | `UNIT` |
| 17 | `NAME_WARRANT` | `name` | search | `\bWARRANTS?\b`, `\bWARR\b`, `\bWTS?\b`, `\bWRTS?\b` | `WARRANT` |
| 18 | `NAME_RIGHT` | `name` | search | `\bRIGHTS?\b`, `\bRTS?\b` | `RIGHT` |
| 19 | `NAME_WHEN_ISSUED` | `name` | search | `WHEN\s?ISSUED`, `WHEN\s?DISTRIBUTED`, `\bEXDISTRIBUTION\b` | `WHEN_ISSUED` |
| 20 | `NAME_ADR` | `name` | search | `AMERICAN\s+DEPOSIT(ARY or ORY)`, `\bADRS?\b` | `ADR` |
| 21 | `NAME_PREFERRED` | `name` | search | guarded (see below) | `PREFERRED` |
| 22 | `NAME_REIT` | `name` | search | `\bREITS?\b`, `REAL\s+ESTATE\s+INVESTMENT\s+TRUST` | `REIT` |
| 23 | `NAME_ABSENT_OR_ECHOES_SYMBOL` | `name` | structural | name empty, or alphanumerics(name) == alphanumerics(symbol) | `AMBIGUOUS_IDENTITY` |
| 24 | `DEFAULT_COMMON_STOCK_PROXY` | — | default | — | `COMMON_STOCK_PROXY` |

Rule 21's guarded pattern is
`\bPREFERRED\s+(STOCK|SHARES?|SECURITIES|SERIES|UNITS?|LP)\b`
or `\b(CUMULATIVE|PERPETUAL|REDEEMABLE|FIXEDRATE|FIXED RATE|NONCUMULATIVE|NON CUMULATIVE|CONVERTIBLE|VARIABLE RATE|SUBORDINATED)\b.{0,60}?\bPREFERRED\b`
or `\bPFD\b|\bPRF\b|\bTRUPS\b|\bQUIPS\b`
or `\bDEPOSITARY SHARES?\b|\bDEPOSITORY SHARES?\b|\bDEP SHS?\b`.

### Why each rule is shaped the way it is

- **1 — `ASSET_TYPE_ETF`.** `assetType` is the only instrument-class field the vendor supplies
  and it carries exactly two values (`Stock`, `ETF`). Vendor declaration beats inference, so it
  runs first.
- **2 — `SYMBOL_GRAMMAR_NONCONFORMING`.** The feed contains symbol strings that are not symbols:
  `SO 6.75 08-01-22`, `CFX 5.75`, `BC/PA`, `DTV_1`, `ETP-`, `SCE--P-D`, `-P-HIZ`,
  `CAPTW(EXP20260807)`. Nothing reproducible can be inferred from these, so they go to review
  rather than being guessed at.
- **3 — `NAME_ACQUISITION_VEHICLE`.** Every line of a blank-check vehicle — unit, warrant,
  right, and the Class A share whose economics are a trust account rather than an operating
  business — is a SPAC artifact. Running it before the generic form rules makes the exclusion
  table report the more specific reason. In the 2026-07-31 pulls, **no** row matching
  `\bACQUISITIONS?\b` has an IPO date before 2005, i.e. the rule finds no pre-SPAC-era
  operating company.
- **4–8 — dashed vendor suffixes.** `-P[-<series>]` preferred/depositary; `-WS[-<tranche>]`
  warrant; `-U`/`-UN` unit; `-R[-W]` right; `-WI`/`-W`/`-WD` when-issued/when-distributed.
  **`-W` is when-issued in this feed, not warrant**: every `-W` row's name reads
  `When Issued` / `WhenIssued` / `ExDistribution When Issued` (`AA-W` Alcoa, `APD-W` Air
  Products, `SEM-W` Select Medical). Warrants use `-WS`. The series letter is consumed by the
  preferred rule, so `NEE-P-U` (a preferred unit) reports `PREFERRED` and `HYT-R-W` (a right
  trading when-issued) reports `RIGHT`.
- **9 — `SYMBOL_SUFFIX_UNRECOGNIZED`.** What survives rules 4–8 must be a base symbol of at
  most five characters with only single-letter share-class suffixes (`BRK-A`, `BF-B`, `MKC-V`).
  `-CL` consolidated-listing tails (`AED-CL`), numeric tails (`ARGD-1`), and long undashed
  tails (`ALLPDCL`, `EAGLW1`) are forms this table does not model → review.
- **10–15 — NASDAQ fifth-character convention.** `U` unit, `W` warrant, `R` right, `P/O/N/M`
  first/second/third/fourth preferred, `V` when-issued, `Y` depositary receipt. These rows
  usually carry the **bare issuer name** (`AGNCP` is named `AGNC Investment Corp`), so without
  the positional rule they would be indistinguishable from the issuer's common line. Lower
  confidence than the dashed suffixes: every row excluded by one of these rules whose name does
  not also match the corresponding name rule is written to the review log
  (`NASDAQ_FIFTH_CHARACTER_UNCORROBORATED`).
- **16 — `NAME_UNIT`.** Plural `Units` anywhere, or singular `Unit` anywhere other than the
  first word. The leading-word carve-out keeps the operating company `Unit Corp` (NYSE,
  delisted 2020-12-23, present in the delisted pull) out of the rule while still catching
  `Arowana Inc Unit` and `<X> - Unit (1 Ord Class A & 1/2 War)`. Evaluated before the warrant
  rule because unit names enumerate their warrant component.
- **19 — `NAME_WHEN_ISSUED`.** The bare token `WI` is deliberately **not** matched; it occurs
  inside ordinary issuer names.
- **20 — `NAME_ADR`.** Runs before the preferred rule so `American Depositary Shares` reports
  `ADR` while a bare `Depositary Shares` — the US preferred convention — reports `PREFERRED`.
  The bare token `ADS` is deliberately **not** matched: `Ads-Tec Energy Plc` is an operating
  company.
- **21 — `NAME_PREFERRED`.** A bare `Preferred` is not enough. `Preferred Bank` (NASDAQ, PFBC)
  and `Preferred Apartment Communities Inc` are common stock, so the word must appear in
  preferred context.
- **23 — `NAME_ABSENT_OR_ECHOES_SYMBOL`.** An empty name or a name that is only the symbol
  re-spelled (`ATEST-B` named `ATEST.B`; `CERCU` named `CERCU`) carries no identity. Exchange
  test issues land here.
- **24 — default.** The proxy claim, not a verified assertion that the row is common stock.

### Deliberately *not* a rule

NASDAQ also reserves `G`/`H`/`I` (convertible bonds) and `L`/`Z` (miscellaneous) as fifth
characters, and rows shaped that way in this feed are mostly baby bonds and notes. The
convention is nonetheless **unusable for classification**: `GOOGL` is Alphabet's Class A common
stock. Those rows are therefore kept in the proxy universe and flagged
`NASDAQ_FIFTH_CHARACTER_MISCELLANEOUS_KEPT` in the review log so a human, not a heuristic,
decides. This is the one place where the table deliberately prefers a review flag to an
exclusion; excluding Alphabet to catch a handful of baby bonds would be the larger error.

## Identity conflicts

Rows are grouped by symbol across both lists. For a symbol with more than one row:

| review reason | condition | effect on classification |
|---|---|---|
| `SYMBOL_REUSE_ACROSS_ACTIVE_AND_DELISTED` | present in both lists with ≥2 distinct `(name, ipoDate)` identities | rows otherwise `COMMON_STOCK_PROXY` → `AMBIGUOUS_IDENTITY` (rule id `SYMBOL_IDENTITY_CONFLICT`) |
| `SYMBOL_DUPLICATE_WITHIN_LIST` | ≥2 rows in one list with ≥2 distinct identities | same |
| `VENDOR_STATUS_CONFLICT_SAME_IDENTITY` | present in both lists with one identity | same (the vendor calls it active and delisted at once) |

Rows already excluded by an instrument-form rule keep that reason: the conflict cannot change
their membership outcome and the form reason is more informative. The conflict is still logged
in full — the review entry carries **every** row for that symbol, with each row's class and
rule id — so nothing is hidden. `identity_conflicts.symbols` in the snapshot counts every
conflicting symbol, including those.

This is the mechanism the M0 evidence record calls for: `BBBY` is `Beyond Inc` on the active
list while the original Bed Bath & Beyond lives under its final symbol `BBBYQ`. Any pull where
both `BBBY` rows appear resolves to `AMBIGUOUS_IDENTITY` on both sides, never to a guess.

## Review-log reasons

| reason | meaning |
|---|---|
| `AMBIGUOUS_IDENTITY_CLASSIFICATION` | a rule classified the row `AMBIGUOUS_IDENTITY` |
| `SYMBOL_REUSE_ACROSS_ACTIVE_AND_DELISTED` | ticker reuse across the two lists |
| `SYMBOL_DUPLICATE_WITHIN_LIST` | two identities under one symbol inside one list |
| `VENDOR_STATUS_CONFLICT_SAME_IDENTITY` | same identity reported active and delisted |
| `NASDAQ_FIFTH_CHARACTER_UNCORROBORATED` | excluded on the positional convention alone |
| `NASDAQ_FIFTH_CHARACTER_MISCELLANEOUS_KEPT` | `G/H/I/L/Z` fifth character, kept, needs a human |

## Output

```
derived/universe/av-proxy-snapshot/<signal_date>/<snapshot_id>.json
derived/universe/av-proxy-snapshot/<signal_date>/<snapshot_id>.review-log.jsonl
```

`snapshot_id = <signal_date>-<sha256(canonical body)[:12]>`. The id is derived from the file's
own bytes, so it is deliberately **not** embedded in the body it names: the file's sha256 is
exactly the hash the id quotes. Both files are written with `O_EXCL`-equivalent semantics — a
second write of the same snapshot is refused, never merged. The review log is written under the
same id (rather than a shared `review-log.jsonl`) so two snapshots for one signal date cannot
clobber each other's log. Logical ids returned to the caller are root-relative POSIX paths;
no absolute path ever enters an artifact.

`security_id = "AV:<symbol>"` is assigned to **included rows only**, NFC-normalized, unique
within the snapshot, and the included list is sorted by `security_id` UTF-8 bytes ascending —
the contract's `stable_key_normalization` / `stable_key_order`.

Every snapshot carries fail-closed `claims`:
`proxy_snapshot_reviewed=false`, `production_pit_evidence_registered=false`,
`freeze_blocker_changed=false`, `universe_claim="AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY"`.

## Usage

```
python -m qme.cli.av_universe build-proxy --repository-root . --data-root <root> \
    --signal-date 2026-07-31 --active-pull-id <id> --delisted-pull-id <id>

python -m qme.cli.av_universe build-proxy --repository-root . --data-root <root> \
    --signal-date 2026-07-31 --latest
```

`--latest` picks the most recently stored `OK` `LISTING_STATUS` pull per state whose
`params_public.date` equals the signal date, read from `raw/alpha_vantage/_audit.jsonl`. Pulls
for a different date and pulls that were not `OK` are never selected. No credential is read;
the command only reads stored raw pulls and writes under `derived`. Exit code is 0 only when a
snapshot file was written.

## Known limitations

1. **`assetType` distinguishes only `Stock` and `ETF`.** Closed-end funds, BDCs, royalty
   trusts, MLP common units, SPACs, preferred lines, baby bonds, and ADRs all arrive as
   `Stock`. Everything beyond ETF is inferred from symbol shape and name text.
2. **ADR detection is name-based plus the `Y` fifth character, and is materially incomplete.**
   NYSE-listed ADRs (`BABA`, `TSM`, `NVO`, …) carry neither marker and are classified
   `COMMON_STOCK_PROXY`. On the 2026-07-31 active pull only 12 rows were classified `ADR`; the
   true count is in the hundreds. A reviewer must not read the `ADR` count as coverage.
3. **REIT detection is name-based and catches a small minority.** Most REITs do not put `REIT`
   or `Real Estate Investment Trust` in their legal name (`Preferred Apartment Communities Inc`
   is a REIT and does not). 19 active rows classified `REIT` is a floor, not the population.
4. **The NASDAQ fifth-character rules are a convention, not a data field.** They are the only
   way to separate `AGNCP` (preferred) from `AGNC` (common) when both carry the issuer's name.
   A five-character base symbol that genuinely ends in `U/W/R/M/N/O/P/V/Y` would be wrongly
   excluded. Rows resting on the convention alone are enumerated in the review log.
5. **`G/H/I/L/Z` fifth characters are not excluded** (see above) — the review log names them.
6. **The delisted list is incomplete for bankruptcies.** Per the M0 evidence record, BBBY(Q),
   SIVB, SBNY, RAD, PRTY, and BIG are absent under both original and `…Q` symbols while merger
   delistings (ABMD, AAWW, ATVI) are present. The universe claim is
   `AV_SURVIVORSHIP_REDUCED_…`, not survivorship-free, precisely because of this.
7. **Identity is not cross-checked against SEC CIK data.** Registration §1.3 makes SEC
   `company_tickers.json` + submissions the identity cross-check layer; this module does not
   consult it. `identity_snapshot_id` / `identity_snapshot_hash` remain unbound.
8. **The signal-session date is whatever the pull was requested for.** The builder enforces that
   both pulls carry the exact date; it does not verify that the date is an exchange session.
9. **The rule table's `pattern`/`rationale` text is documentation of the code, not a second
   implementation of it.** The sha256 pins the table; it does not prove the code and the table
   text agree. A reviewer changing a rule must change both.

## Non-claims

- The snapshot is **not reviewed**. `proxy_snapshot_reviewed` is hard-coded `false` and there is
  no code path that sets it true.
- Producing a snapshot **registers nothing**: no membership authority, no identity authority, no
  production point-in-time evidence, no freeze-blocker change.
- The exclusion classification is **not claimed complete or correct**; see the limitations above.
- The included set is a **proxy**, not a verified common-stock universe. `COMMON_STOCK_PROXY`
  means "nothing in this row marks it as an excluded form", not "this is common stock".
- No claim is made that 2026-07-31 is the correct registered signal-session date; that remains
  open in the M0 evidence record.

## Execution against the owner data root (2026-08-16)

`python -m qme.cli.av_universe build-proxy --data-root D:\qme-data-local --signal-date 2026-07-31 --latest`
(read-only on `raw/`; writes only under `derived/`). Pulls come from run
`20260816T033624Z-av-m0-fixture-pulls`.

| field | value |
|---|---|
| active pull | `20260816T033626707774Z-5f36e616dd82`, sha256 `5f36e616dd82e506659e310db50ac70e94da0b153b7fdf127c6d0970f9c81030`, 13,611 rows |
| delisted pull | `20260816T033627569033Z-c88ac8e0948a`, sha256 `c88ac8e0948ac059c4a421052bb470be31fc10ad448ef6c95210adf49378e684`, 10,078 rows |
| rule table sha256 | `9f2e3ec95d2ce7dc58079d66003a9c14af9ab411fe58b497e8f156fae7629fc7` (24 rules) |
| snapshot id | `2026-07-31-151f89d9f1b5` |
| snapshot logical id | `derived/universe/av-proxy-snapshot/2026-07-31/2026-07-31-151f89d9f1b5.json` |
| snapshot sha256 | `151f89d9f1b533f5235f12ae0665a7afb417a5ae5412d70b43ba86bb87d0ea77` |
| review log | `derived/universe/av-proxy-snapshot/2026-07-31/2026-07-31-151f89d9f1b5.review-log.jsonl` (1,724 entries) |
| **included** | **5,655** |

Active-list classification (13,611 rows):

| class | rows |
|---|---|
| `COMMON_STOCK_PROXY` (included) | 5,655 |
| `ETF` | 5,629 |
| `SPAC_ARTIFACT` | 850 |
| `PREFERRED` | 507 |
| `AMBIGUOUS_IDENTITY` | 444 |
| `WARRANT` | 356 |
| `UNIT` | 81 |
| `RIGHT` | 31 |
| `WHEN_ISSUED` | 27 |
| `REIT` | 19 |
| `ADR` | 12 |

Delisted-list classification (10,078 rows, context only — never included): `COMMON_STOCK_PROXY`
3,513, `ETF` 2,055, `SPAC_ARTIFACT` 1,362, `AMBIGUOUS_IDENTITY` 812, `PREFERRED` 682, `WARRANT`
636, `UNIT` 552, `WHEN_ISSUED` 250, `RIGHT` 153, `ADR` 50, `REIT` 13.

Review log (1,724 entries):

| reason | entries |
|---|---|
| `SYMBOL_REUSE_ACROSS_ACTIVE_AND_DELISTED` | 749 |
| `NASDAQ_FIFTH_CHARACTER_UNCORROBORATED` | 396 |
| `AMBIGUOUS_IDENTITY_CLASSIFICATION` | 224 |
| `SYMBOL_DUPLICATE_WITHIN_LIST` | 221 |
| `NASDAQ_FIFTH_CHARACTER_MISCELLANEOUS_KEPT` | 129 |
| `VENDOR_STATUS_CONFLICT_SAME_IDENTITY` | 5 |

975 symbols carry conflicting identities; 1,020 rows lost an otherwise-included classification
because of them (382 on the active list). **Ticker reuse is pervasive, not exceptional** —
`ACB` (Aurora Cannabis now, ACap Energy until 2021), `ACI` (Albertsons now, Arch Coal until
2020), `ACCL` (Acco Group now, Accelrys until 2014). Any point-in-time identity layer built on
AV symbols must treat the symbol as a non-key.

## What a reviewer must do before this becomes evidence

1. Confirm 2026-07-31 is the registered signal-session date (or re-pull at the registered date).
2. Work the 1,724 review entries, at minimum the 749 cross-list reuse entries and the 224
   ambiguous classifications.
3. Decide whether the incomplete ADR and REIT exclusions are acceptable for v0.1 under the
   `AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY` claim, or whether a second identity source is
   required first.
4. Bind the identity snapshot (SEC CIK cross-check) that §1.3 requires; membership alone does
   not clear `NEE-119-AV-PROXY-EVIDENCE`.
