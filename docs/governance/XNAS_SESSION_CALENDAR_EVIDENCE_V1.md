# NEE-174 — bounded XNAS session-calendar evidence V1

Status: `DETERMINISTIC_CANDIDATE_PROJECTION_BLOCKER_RETAINED`

This slice materializes a deterministic, hash-bound calendar candidate for the
owner-ratified method `QME-XNAS-SESSION-CALENDAR-MATERIALIZATION-V1` and calendar
identity `XNAS_2010-01-04_2027-12-31_v1`. It does not modify Sample Holdout V2,
the materialization crosswalk, or the specification-freeze policy.

## Generator boundary

The generator is deliberately isolated from the shipping `qme` runtime. Its
direct inputs are declared in `requirements-xnas-calendar-generator.in`, and its
complete CPython 3.12 Windows AMD64 dependency set is pinned by hashes in
`requirements-xnas-calendar-generator.lock`. The standard-library verifier does
not import pandas, `pandas_market_calendars`, or `exchange_calendars`.

The exact calendar resolution is explicit:

- `pandas_market_calendars==5.4.0` receives its supported literal selector
  `NASDAQ` and resolves it to class `NYSEExchangeCalendar`, name `NYSE`.
- The same pinned package is required to reject `XNAS` as unregistered; the
  isolated replay fails if that behavior changes.
- Pinned transitive `exchange_calendars==4.13.2` receives literal alias `XNAS`
  and resolves it to canonical alias `XNYS`, class `XNYSExchangeCalendar`.
- The generator proves that the two schedules have identical session dates,
  opens, and closes over the entire materialized interval. There is no hidden
  selector fallback.
- `zoneinfo` system search paths and caches are cleared before conversion.
  Pinned `tzdata==2026.3` is the sole IANA timezone source.

The NumPy and pandas hashes in this lock identify Windows AMD64 wheels. There is
no Linux hash lock and no Windows-versus-Linux byte-replay evidence. This slice
therefore claims deterministic replay only for the pinned CPython 3.12.10
Windows AMD64 generator. Cross-platform replay remains explicitly blocked.

The resulting artifact uses the registered XNAS identity, while openly
recording both upstream alias resolutions. Independent XNAS acceptance remains
required because the selected PMC object is named NYSE and does not itself
constitute exchange certification.

## Artifact contract

The calendar contains one row per strictly ascending, unique session ID from
2010-01-04 through 2027-12-31. Every row has a canonical local timestamp for the
09:30 open and either a 16:00 normal close or 13:00 early close. The separate
ordered-vector artifact must equal the calendar's session IDs position by
position and by canonical value hash.

Rows through the evidence as-of date 2026-08-13 are marked
`GENERATED_HISTORICAL_CANDIDATE`. Later rows are marked
`GENERATED_FUTURE_CANDIDATE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY`.
That label is material: package-generated future rows are neither evidence that
Nasdaq published the complete schedule nor evidence that a market session
actually occurred.

## Bounded primary-source cases

The official-case fixture checks selected Nasdaq Trader evidence:

- Hurricane Sandy closures on
  [2012-10-29](https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2012-44) and
  [2012-10-30](https://www.nasdaqtrader.com/TraderNews.aspx?id=mfqsnews2012-003).
- The national day-of-mourning closure on
  [2018-12-05](https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2018-98).
- The national day-of-mourning closure on
  [2025-01-09](https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2025-1).
- The 13:00 Thanksgiving early close on
  [2018-11-23](https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2018-92).
- Published 13:00 early closes on 2026-11-27 and 2026-12-24 from the
  [Nasdaq Trader 2026 calendar](https://www.nasdaqtrader.com/trader.aspx?id=Calendar).

These are bounded known-answer checks, not a complete immutable primary-source
history for every closure and half-day from 2010 through 2027.

## Fail-closed verification

`qme.governance.xnas_calendar_evidence_v1` verifies:

- confined regular files, bounded same-handle reads, strict UTF-8 JSON,
  duplicate-key rejection, and exact grouped hashes;
- exact-const schema/config parity and the semantic digest;
- literal equality of the Sample Holdout V2, Crosswalk V3, and owner-supplement
  XNAS registrations, including their still-null production hashes;
- generator, complete transitive lock, wheel, tzdata, and script provenance;
- calendar/vector identity, coverage, order, timestamps, row equality, and
  canonical value hash;
- every bounded primary-source case; and
- the exact outer manifest path order and every leaf digest.

Generator replay in a disposable environment:

```powershell
python --version  # must report Python 3.12.10
python -m venv .tmp-xnas-generator
.\.tmp-xnas-generator\Scripts\python.exe -m pip install --require-hashes -r requirements-xnas-calendar-generator.lock
.\.tmp-xnas-generator\Scripts\python.exe scripts\materialize_xnas_calendar_v1.py --repository-root . --verify
```

## Explicit nonclaims

This slice does not claim a production calendar, complete official history,
observed authority for future sessions, exchange certification, final freeze
evidence, prospective-sample eligibility, M0 completion, data-spine authority,
or production readiness. It also does not claim Linux or cross-platform replay.
`NEE-121-CALENDAR-SESSION-REGISTRATION` remains active.
Clearing it requires separately accepted, complete primary-source coverage and
the versioned governance update authorized for that evidence.
