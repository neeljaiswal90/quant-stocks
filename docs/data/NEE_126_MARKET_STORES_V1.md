# NEE-126 — Price / trading-calendar / vintage risk-free stores V1 (M1 prebuild)

Status: T2 engineering prebuild. It clears no blocker, registers no evidence, and
records no independent review.

- Calendar store: `QME-NEE126-XNAS-TRADING-CALENDAR-STORE-V1` — `qme/data/stores/calendar_v1.py`
- Price store: `QME-NEE126-PRICE-STORE-V1` — `qme/data/stores/prices_v1.py`
- Vintage risk-free store: `QME-NEE126-VINTAGE-RISK-FREE-STORE-V1` — `qme/data/stores/riskfree_v1.py`
- Tests: `tests/data/test_market_stores.py`
- Known-answer vectors: `tests/fixtures/data/market-stores-v1.json`

This is the parallel-safe subset of NEE-126. Two things are deliberately **out of
scope**: the actual risk-free vintage **source** (an owner decision that has not
been made — the source registry ships empty and every real-source resolution
fails closed), and real Alpha Vantage price-ingestion volume (an integration
follow-up). Nothing in this package imports a transport module or opens a socket.

## 1. Calendar-authority determination

The repository carries four XNAS calendar fixtures and two evidence configs, so
"which artifact is authority" had to be settled before anything could read a
session. The determination:

> **The accepted session authority as of Freeze V8 is the V1 candidate byte set,
> accepted by the V2 acceptance record.** The bytes to read are V1's; the
> authority to read them is V2 plus Freeze V8.

### Evidence chain

| # | Link | Artifact | Grouped sha256 |
|---|---|---|---|
| 1 | Freeze V8 closes the blocker | `configs/governance/specification-freeze-policy-v8.json` | `34925587:f2782d25:d72e8983:fd8f45be:cfaaf8a1:24c6114a:ae36537c:2c16c15d` |
| 2 | The M0 evidence leg that cleared it | `configs/governance/m0-substantive-evidence-candidate-v1.json` | `c03f0b46:7e058e10:034ca642:197a88d7:d193d4de:9c5f770a:45615e27:996881e3` |
| 3 | The acceptance record it names | `tests/fixtures/governance/xnas-session-calendar-acceptance-candidate-v2.json` | `f53ff11a:90c3cf28:6dd89787:b37fd918:73cb435f:77cb9569:d963dfc2:61681161` |
| 4 | The pre-transition evidence config | `configs/governance/xnas-session-calendar-evidence-v1.json` | `348e67d9:92183c49:4f625f90:ada2bb58:e90165f5:3fd90419:3e6d8584:7eb0e290` |
| 5 | Its manifest | `configs/governance/xnas-session-calendar-evidence-v1.hashes.json` | `31077a2d:6b7a6eb9:f974b343:b91c99d5:b48c2049:d38758a2:a455c10d:5ca2f453` |
| 6 | **The session bytes the store reads** | `tests/fixtures/governance/xnas-session-calendar-2010-2027-v1.candidate.json` | `a414d89a:2d18a3e2:27c7cfab:05c271c8:209490e3:beb49bf0:bb1a00f1:9ecd2a5e` |
| 7 | The ordered session vector | `tests/fixtures/governance/xnas-ordered-session-vector-2010-2027-v1.candidate.json` | `97f0eebd:efa68f08:46dfc1dc:a6ad4de5:31a8c0db:066b908f:073f45d0:a9bb9b4e` |
| 8 | The bounded official cases | `tests/fixtures/governance/xnas-session-calendar-v1.official-cases.json` | `d9646f29:8439975d:f8a9ab77:45662b8b:b0b74625:591c1144:96570031:b684e2d8` |

The ordered session-id **value** digest that both V1 and V2 pin is
`dfbb9bc1:13e7de06:67c5226a:4451634a:943d3d70:2aa87db4:ffb1a72d:0d3f2bd8`
(sha256 of the compact canonical JSON of the session-id list, matching
`scripts/materialize_xnas_calendar_v1.py`). `load_calendar()` recomputes it from
the calendar rows it actually parsed, so a drifted session list cannot be served
even if a file digest somehow matched.

### Why V1's bytes and not something else

1. **Freeze V8 is the current freeze and it closed the blocker.**
   `specification-freeze-policy-v8.json` has `policy_id =
   NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V8`, `policy_status =
   M0_COMPLETE_0_ACTIVE_FINAL_FREEZE`, `unresolved_blockers: []`, and lists
   `NEE-121-CALENDAR-SESSION-REGISTRATION` under
   `resolved_or_superseded_blocker_codes`. The standalone V1 evidence config
   still reads `DETERMINISTIC_CANDIDATE_PROJECTION_BLOCKER_RETAINED` with
   `production_calendar_available: false` — that is the **pre-transition**
   record, superseded on this point. Reading the V1 config alone would have
   given the wrong answer.
2. **The evidence leg names exactly which artifacts cleared it.** The freeze's
   `accepted_m0_substantive_evidence` cites candidate
   `NEE-110-M0-SUBSTANTIVE-EVIDENCE-CANDIDATE-V1`; that candidate's leg for the
   calendar blocker (`evidence_class =
   PINNED_XNAS_GENERATOR_LOCK_SESSION_VECTOR_AND_LINUX_REPLAY`) lists the V1
   evidence config, the V1 manifest, the **V2 acceptance candidate**, and the A4
   external-review verdict.
3. **V2 is an acceptance record over the same bytes, not a second dataset.** It
   contains no `sessions` array. It pins `session_count = 4526` and a
   `session_ids_sha256` identical to V1's ordered vector, and it is what raises
   `production_calendar_available` from `false` to `true` — on the strength of a
   Linux replay with `replay_result = IDENTICAL` and an external `GO` verdict
   (xAI / Grok Build). So V2 accepts V1; it does not replace it.

`verify_bound_artifacts()` re-checks all eight links before any session is
served, and the tests re-derive premises 1–3 from the governance JSON rather than
trusting this prose.

### Retained non-claims

Acceptance is bounded and the store does not widen it. The accepted record itself
states `complete_official_history_verified: false` and
`future_sessions_are_observed_market_authority: false`:

| Authority phase | Sessions |
|---|---|
| `GENERATED_HISTORICAL_CANDIDATE` (checked against 7 bounded primary-source cases) | 4178 |
| `GENERATED_FUTURE_CANDIDATE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY` | 348 |

Every `SessionRow` carries its phase and `SessionRow.is_projected` exposes it, so
a consumer can refuse projected sessions; the store never silently upgrades one.
`NON_CLAIMS` is written into every manifest this package emits. The accepted
correction policy is
`ANY_CORRECTION_PRODUCES_XNAS_CALENDAR_V2_NEVER_IN_PLACE_OVERWRITE`, which is why
the digests above are literals in the module rather than recomputed trust.

## 2. Ticket contract → where it lives

| # | Contract line | Where it lives |
|---|---|---|
| C1 | raw price table | `RawPriceRow`, `RAW_COORDINATE` |
| C2 | separately named split-adjusted series | `SplitAdjustedPriceRow`, `SPLIT_ADJUSTED_COORDINATE` |
| C3 | separately named total-return series | `TotalReturnRow`, `TOTAL_RETURN_COORDINATE` |
| C4 | raw and derived not joinable without explicit field names | `assert_coordinates_non_joinable()` (import-time), `join_coordinates()` |
| C5 | naming bound to the #62 kernel | `assert_kernel_naming_bound()` against `DERIVED_SERIES_NAMES` / `RAW_SERIES_NAMES` |
| C6 | exchange sessions | `TradingCalendar.session()`, `.is_session()` |
| C7 | half-days | `.is_half_day()`, `.half_day_sessions()`, `close_class` |
| C8 | month-end sessions | `.month_end_session()`, `.is_month_end_session()` |
| C9 | exact session offsets | `.offset()`, `.sessions_between()` |
| C10 | next-eligible-session mapping | `.next_eligible_session()` |
| C11 | risk-free observations with source, vintage interval, quote unit, convention, availability time | `RiskFreeSource` + `RiskFreeObservation` |
| C12 | converted period return | `period_return()`, `period_return_between()` |
| C13 | dataset manifests and grouped hashes | `.manifest()`, `.dataset_digest()`, `canonical_dataset_digest()` |
| C14 | lineage in every manifest row | five lineage fields, §5 |

### Schema-level non-joinability (C4)

Value-field name sets are pairwise disjoint, share nothing with the join keys,
and contain no generic market-data name:

| Coordinate system | Value fields |
|---|---|
| `raw_price` | `raw_close`, `raw_volume`, `raw_dollar_volume` |
| `split_adjusted_price` | `split_adjustment_factor`, `split_adjusted_close`, `split_adjusted_volume`, `split_adjusted_dollar_volume` |
| `total_return` | `gross_return`, `total_return_index` |

The only names shared across coordinates are the declared identity keys
`security_id` and `session_id`. `FORBIDDEN_GENERIC_FIELD_NAMES` bans `close`,
`price`, `volume`, `adj_close`, `value`, `return`, and eleven more from ever
becoming a value field. `assert_coordinates_non_joinable()` runs at **import
time**, so adding a field named `close` to any coordinate makes importing the
module fail rather than making a test fail later. `join_coordinates()` refuses
any join key that is not a declared identity field
(`BLOCKED_IMPLICIT_COORDINATE_JOIN`), so a caller cannot line a raw series up
against a derived one by value. The risk-free coordinate
(`risk_free_period_return`) is disjoint from all three.

## 3. Risk-free conversion and the numeric policy

The adapter implements the **declared** convention and nothing else:

| Declared `compounding` | Formula | Exactness |
|---|---|---|
| `SIMPLE_ANNUAL` | `r_period = y * day_fraction` | always `EXACT_RATIONAL` |
| `EFFECTIVE_ANNUAL`, integral `day_fraction` | `(1 + y) ** n` exactly | `EXACT_RATIONAL` |
| `EFFECTIVE_ANNUAL`, `y = 0` | exactly `0` at any horizon | `EXACT_RATIONAL` |
| `EFFECTIVE_ANNUAL`, otherwise | `r_period = (1 + y)^(day_fraction) - 1` | `ROUNDED_DECIMAL` |

**What is exact.** `y` is exact: a canonical base-10 decimal string lifted to a
`Fraction`, with `PERCENT_PER_ANNUM` divided by an exact integer `100`.
`day_fraction` is exact: an integer day or session count over the declared
integer denominator. Simple-annual returns are exact rationals. Effective-annual
returns with an integral exponent are exact rationals. No binary float appears
anywhere in the module.

**What is correctly-rounded Decimal.** Only the non-integral effective-annual
power. It is computed under an explicit, per-call context —
`DECIMAL_WORKING_PRECISION = 60` significant digits (the ticket floor is 34),
`ROUND_HALF_EVEN`, trapping `InvalidOperation` / `DivisionByZero` / `Overflow`.
The base enters exactly (it is `1` plus an exact decimal quote); the exponent is
a context division; the power is `Context.power`.

**Artifact rendering.** Every artifact string is rendered at the NEE-125 artifact
scale `18` with `ROUND_HALF_EVEN`. Note the distinction: `EXACT_RATIONAL` means
the *computed value* is exact and the 18-digit string is its correct rounding;
`ROUNDED_DECIMAL` means the computed value is itself already a rounding.

### Error bound

With `b = 1 + y`, `f = n/d`, `P = 60`:

| Source | Contribution |
|---|---|
| base `b` enters the context exactly | none |
| exponent formed by context division, correctly rounded | relative `<= 5e-60` |
| `Context.power` — CPython's C implementation is documented as *almost always* correctly rounded | `<= 1` ulp, relative `<= 1e-59` |
| exponent sensitivity `\|ln b\| * \|f\|` over accepted ranges (`0 < b <= 2`, `\|f\| <= 100`) | `< 1e-57` |

Subtracting the exact `1` is exact in the context, so the **absolute** error of
`r_period` before rendering is bounded by `1e-57` — 39 orders of magnitude below
the `1e-18` artifact quantum. The rendered artifact is therefore the correctly
rounded value of the true result **except** where the true result lies within
`1e-57` of an exact `1e-18` tie. That residual is stated rather than denied;
`EFFECTIVE_ANNUAL_ERROR_BOUND` carries it as a citable string and every manifest
repeats it. `MAX_ABSOLUTE_DAY_FRACTION` and `MAX_GROWTH_BASE` fail closed on
inputs outside the ranges the bound covers, so the claim is never made about
inputs it does not cover.

The expected values in `market-stores-v1.json` were produced by an **independent
exact-rational bisection** on `x**d == (1+y)**n` at 45 decimal digits — integers
only, no `Decimal`, no float, no library power — and all fourteen agree with the
module.

### Why a silent divide-by-252 is structurally impossible

`252` appears in exactly one place: `DAY_COUNT_DENOMINATORS`, keyed by the literal
basis `BUS/252`. A test parses the module's AST and asserts there is exactly one
`252` constant in the file. There is no default day-count basis, no default
compounding, and no default quote unit — `RiskFreeSource` requires all three and
validates them at construction, so an unconvertible record cannot exist. Reaching
the `252` denominator additionally requires an accepted trading calendar, because
the numerator is a **session count**; without one, `day_fraction()` raises
`BLOCKED_MISSING_CALENDAR`. A caller who wants `/252` must say so twice.

Supported bases: `ACT/360`, `ACT/365F`, `30/360US`, `BUS/252`.

### The source registry ships empty

`REGISTERED_SOURCES = ()`. The vintage-source decision (ALFRED-style vintage
archive versus alternatives) is the owner's and has not been made, so
`validate_source_registry()`, `resolve_source()`, and `build_risk_free_store()`
all fail closed with `BLOCKED_NO_REGISTERED_RISK_FREE_SOURCE`. This mirrors the
plan-evidence pattern in `qme/data/alpha_vantage/plan_v1.py`: the machinery is
complete and tested, and it refuses to run until a sourced record exists. Tests
pass their own records through the `sources=` parameter under the
`TEST_CONSTRUCTED` kind, which `validate_source_registry()` forbids in the
shipped registry.

## 4. Cutoff rules — typed fail-closed states

| Rule | State | Raised by |
|---|---|---|
| exact session lookup never substitutes a nearby date | `BLOCKED_MISSING_SESSION` | `TradingCalendar.session()` / `.position()` |
| a date outside accepted coverage | `BLOCKED_DATE_OUT_OF_COVERAGE` | same, and `.next_eligible_session()` |
| an offset past the coverage edge is never clamped | `BLOCKED_SESSION_OFFSET_OUT_OF_RANGE` | `.offset()` |
| a malformed date is rejected before any lookup | `BLOCKED_NOT_AN_ISO_DATE` | `iso_date()` |
| missing calendar | `BLOCKED_MISSING_CALENDAR` | `require_calendar()` (price store and `BUS/252`) |
| bound calendar bytes changed | `BLOCKED_CALENDAR_AUTHORITY_BYTES_MISMATCH` | `verify_bound_artifacts()`, `load_calendar()` |
| bound artifact absent | `BLOCKED_CALENDAR_ARTIFACT_MISSING` | `grouped_sha256_file()` |
| a price on a day the market was closed | `BLOCKED_SESSION_NOT_IN_CALENDAR` | `build_price_store()` |
| a raw session after the run's PIT cutoff | `BLOCKED_SESSION_AFTER_PIT_CUTOFF` | `build_price_store()` |
| a future action restating a historical screen | `BLOCKED_ADJUSTMENT_CUTOFF_AFTER_PIT_CUTOFF` | `build_price_store()` |
| an action after the adjustment cutoff | `BLOCKED_POST_CUTOFF_EVENT` | the #62 kernel, re-raised unchanged |
| a bar after the adjustment cutoff | `BLOCKED_POST_CUTOFF_SESSION` | the #62 kernel, re-raised unchanged |
| duplicate / empty raw table | `BLOCKED_DUPLICATE_PRICE_ROW`, `BLOCKED_EMPTY_PRICE_TABLE` | `build_price_store()` |
| a value-field join across coordinates | `BLOCKED_IMPLICIT_COORDINATE_JOIN` | `join_coordinates()` |
| a coordinate schema collision | `BLOCKED_COORDINATE_FIELD_COLLISION` | `assert_coordinates_non_joinable()` |
| no registered risk-free source | `BLOCKED_NO_REGISTERED_RISK_FREE_SOURCE` | `validate_source_registry()` |
| no observation for the exact reference date | `BLOCKED_MISSING_RISK_FREE_OBSERVATION` | `resolve_observation()` |
| everything visible was published after the cutoff | `BLOCKED_NO_VALID_OBSERVATION_AT_CUTOFF` | `resolve_observation()` |
| overlapping vintages | `BLOCKED_AMBIGUOUS_RISK_FREE_VINTAGE` | `resolve_observation()` |
| missing / undeclared quote convention | `BLOCKED_UNREGISTERED_QUOTE_UNIT`, `BLOCKED_UNREGISTERED_COMPOUNDING`, `BLOCKED_UNREGISTERED_DAY_COUNT` | `RiskFreeSource.__post_init__()` |
| a naive availability time or cutoff | `BLOCKED_MISSING_AVAILABILITY_TIME` | `iso_instant()` |
| `(1 + y) <= 0` under an effective-annual power | `BLOCKED_NONPOSITIVE_GROWTH_BASE` | `_effective_annual_return()` |

Two of these deserve emphasis because they are the ones most easily softened:

- **Never substitute.** An exact anchor lookup on a closed day raises. Mapping a
  non-session date to a tradable one requires calling `next_eligible_session()`
  by name — it is the only substitution path in the package.
- **Never carry forward.** When an observation for the exact reference date
  exists but was published after the cutoff, the result is a typed non-valid,
  not the previous date's value. The test asserts that a visible earlier
  reference date sitting right there is *not* used.

One consequence worth stating for callers: the #62 kernel refuses a raw bar after
the **adjustment** cutoff, not merely after the PIT cutoff. A run with
`adjustment_cutoff < pit_cutoff` must stop its bars at the adjustment cutoff or
get `BLOCKED_POST_CUTOFF_SESSION`. That is the correct reading of a
point-in-time split-adjusted series — the adjustment basis and the price history
it adjusts have to end together — and this store does not soften it.

## 5. Manifests, grouped hashes, and lineage

`canonical_dataset_digest()` hashes `qme.foundation.lineage.canonical_json_bytes`
output (sorted keys, no NaN, compact separators, UTF-8, trailing newline) and
renders it in the repository's grouped form (eight 8-hex groups). Rows are
emitted in session order, so identical inputs produce identical dataset hashes
regardless of the order the bars, actions, or reference dates arrived in — a
permutation-shuffle test asserts this across every permutation of the action set
and three bar orderings.

Every manifest row carries all five lineage fields:

| Field | Price store | Risk-free store |
|---|---|---|
| `raw_rows_sha256_grouped` | digest of the raw price table | digest of the resolved observation ids |
| `action_set_sha256_grouped` | order-invariant digest of the action set | `null` (no actions apply) |
| `calendar_id` + `calendar_sha256_grouped` | accepted calendar identity and bytes | same, when a calendar was used |
| `source_vintage` | `null` (prices carry no vintage in this prebuild) | source id, series, kind, availability cutoff, and all three declared conventions |
| `code_config_sha256_grouped` | `store_binding_digest()` | `store_binding_digest()` |

`store_binding_digest()` covers the **declared bindings**: the store identities
and schema versions, the full accepted-calendar authority chain with its grouped
digests, and the caller-supplied kernel identities. It deliberately does **not**
hash this repository's Python source — a source-tree digest is the repository
lock's job, and self-pinning a module's own bytes is reserved for the
grandfathered T1 paths in `configs/governance/change-tier-policy-v1.json`. So the
digest moves when a bound artifact or a declared schema version moves, and does
not move on a non-semantic source edit. That boundary is stated here so the field
is not read as more than it is.

## 6. Acceptance fixtures

`tests/fixtures/data/market-stores-v1.json` carries 30 calendar cases, 8
day-fraction cases, and 14 conversion cases. Coverage of the nine required
acceptance criteria:

| Required case | Vectors | Example |
|---|---|---|
| holiday month-end | 3 | `2013-03` → `2013-03-28`: 03-31 is a Sunday and 03-29 is Good Friday, so month-end is displaced two further days |
| leap year | 4 | `2016-02` → `2016-02-29` (leap day is the month-end session); `2020-02` → `2020-02-28` (leap day is a Saturday); `2015-02` → `2015-02-27` (non-leap control) |
| half-day | 4 | `2012-11-23`, `2013-07-03`, `2018-12-24` are `EARLY_CLOSE`; `2012-11-21` is the `NORMAL` control |
| consecutive closure | 1 | Hurricane Sandy: `2012-10-29` and `2012-10-30` both absent, `2012-10-26` → `2012-10-31` |
| exact 21-session offset | 4 | `2013-06-28` −21 → `2013-05-30`; `2012-11-01` −21 → `2012-10-01`, stepping over the Sandy closure |
| exact 252-session offset | 2 | `2013-06-28` −252 → `2012-06-26`; `2016-03-31` −252 → `2015-03-31` |
| next session | 3 + 4 next-eligible | `2012-10-26` → `2012-10-31`; `2016-02-29` → `2016-03-01` |
| missing session | 5 | `2012-10-29`, `2013-03-29`, `2015-04-03` → `BLOCKED_MISSING_SESSION`; `2009-12-31` → `BLOCKED_DATE_OUT_OF_COVERAGE` |
| positive / zero / negative rates | 14 | `5.25%`, `0`, `-0.5%`, `-0.75%` |
| simple / effective conversion | 6 simple + 8 effective | same quote and horizon under the two conventions give `0.004375000000000000` and `0.004273127766158050` |

Offsets are additionally asserted to be exactly invertible
(`offset(offset(a, n), -n) == a`) and consistent with `sessions_between()`, which
a calendar-day approximation would not be.

## 7. Non-claims

- Synthetic and read-only. This package registers no vendor comparison, no
  tolerance, no identity join, and no security master. It is not evidence and
  clears no freeze blocker.
- The accepted calendar's own bounds are retained verbatim: no complete official
  history, and future sessions are projections rather than observed market
  authority.
- No risk-free vintage source is registered; the store cannot produce a real rate
  until the owner's source decision lands.
- No Alpha Vantage raw-cache integration and no price-ingestion volume.

## 8. Seams

- **AV ingest.** `build_price_store()` takes
  `factors_v1.RawSessionBar` sequences, which is what an ingest adapter over
  `qme.data.alpha_vantage.store` would emit. The store validates sessions against
  the accepted calendar, so the ingest seam needs no calendar logic of its own.
- **Identity (PR #64).** Rows key on an opaque `security_id`; nothing here
  resolves symbols to entities. When `qme/data/identity/**` lands, the seam is
  the `security_id` argument to `build_price_store()`.
- **Coverage audit.** The calendar's bounded-official-case count (7) and its two
  authority phases are exposed on every row and manifest, which is the input a
  coverage-audit ticket needs to quantify how much of a backtest window rests on
  generated rather than observed sessions.
