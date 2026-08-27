# QME Rebalance-Schedule Kernel V1 (calendar-derived session spine)

Status: composition-lane kernel candidate. It clears no blocker, registers no
schedule, and makes no production, prospective-consumption,
empirical-performance, alpha, capacity-value, production-readiness, or
live-order claim.

- Kernel: `QME-COMPOSITION-REBALANCE-SCHEDULE-KERNEL-V1`
- Schema: `qme.rebalance_schedule.v1`
- Runtime: `qme/quant/schedule_v1.py`
- Tests: `tests/quant/test_rebalance_schedule.py`
- KAT fixture: `tests/quant/fixtures/rebalance-schedule-v1.json`
- ticket_id: PENDING_OWNER_ASSIGNMENT (composition ticket B under gate
  NEE-108, lead plan 2026-08-25; a Linear id is deliberately not invented)

## Objective

Derive, from the accepted exchange calendar **only**, the ordered
walk-forward schedule a composed backtest follows: for each rebalance event
the signal session, the fill session, and warmup availability for a given
`(L, S)` feature variant — point-in-time, hash-bound, refusing anything
outside accepted coverage.

## The schedule frequency is owner-gated; the registry ships empty

`configs/quant/qme-v0.1-contract-v2.json` — the frozen v0.1 strategy
contract — carries **no rebalance-frequency or schedule key of any kind**.
The lead verified this on 2026-08-25, and
`test_frozen_contract_v2_carries_no_rebalance_schedule_or_frequency_key`
re-verifies it against the frozen bytes on every run (no key of the contract
contains `rebalance`, `schedule`, or `frequency`).

A rebalance frequency is therefore an owner decision that has not been made:

- `REGISTERED_SCHEDULE_POLICIES = ()` — empty by design, mirroring the
  fail-closed registry pattern of `qme/data/alpha_vantage/plan_v1.py` and
  `qme/data/stores/riskfree_v1.py`;
- every derivation, resolution, or validation against the shipped registry
  fails closed with `BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY`;
- a `SchedulePolicy` record declares
  `{policy_id, frequency_kind, source_kind, source, source_reference}` and
  validates every field at construction;
- `MONTH_END_SESSIONS` is the one implemented, parameterized frequency
  **mechanism** a registered policy may select. It is exercised in tests via
  `TEST_CONSTRUCTED` records only; `validate_schedule_policy_registry`
  forbids that source kind in the shipped registry
  (`REGISTERED_SOURCE_KINDS = (OWNER_DECISION_RECORD, FROZEN_CONTRACT_KEY)`).
- nothing defaults to monthly, and no threshold, coefficient, or schedule
  rule absent from the frozen contract is invented here.

## Bound calendar authority (consumed, never reimplemented)

The kernel binds `qme/data/stores/calendar_v1.py` (committed M1 authority,
store `QME-NEE126-XNAS-TRADING-CALENDAR-STORE-V1`, accepted calendar
`XNAS_2010-01-04_2027-12-31_v1`, 4526 sessions) and calls only its owned
surfaces:

| Surface | Used for |
| --- | --- |
| `load_calendar` (caller side) | the typed, byte-verified calendar input |
| `month_end_sessions` | `MONTH_END_SESSIONS` frequency selection |
| `next_eligible_session` | the fill mapping (the store's only substitution path) |
| `position` / `offset` | warmup counting and anchor resolution |
| `session` rows | `close_class` / `authority_phase` carried verbatim per event |
| `iso_date`, `require_calendar` | input guards |
| `manifest`, `store_binding_digest`, `canonical_dataset_digest` | lineage and hashing |

This module holds no session table, no holiday knowledge, and no date
arithmetic beyond forming `signal_session + 1 calendar day` as the argument
to the store's named next-eligible-session mapping. Reimplementing session
arithmetic that `calendar_v1` owns is a defect by ticket definition.

## Event derivation

Inputs: the typed calendar, a `schedule_policy_id` resolved against a policy
registry, a closed ISO range `[range_start, range_end]` bounding the signal
sessions, and feature-variant offsets `(L, S)` as plain integers
(`0 <= S < L`; the signal engine is deliberately not imported).

Each event row carries:

- `event_ordinal` (0-based, ascending session order — content-derived);
- `signal_session` — the frequency-selected session (e.g. month-end), with
  its calendar position, `close_class`, and `authority_phase` verbatim;
- `fill_session` — the next eligible session **strictly after** the signal
  session via `next_eligible_session(signal_session + 1 day)`, with the same
  verbatim classifications; a fill may fall after `range_end` (the range
  bounds signal sessions), never outside accepted coverage;
- resolved anchors `recent = -S`, `old = -L` with their session dates, each
  `None` when the exact offset would leave accepted coverage;
- `prior_observed_sessions`, `required_minimum_observed_sessions = L + 1`,
  and the typed `warmup_state`;
- the accepted `calendar_id` and grouped calendar bytes hash — bound into
  **every** row, and again into the manifest.

## Warmup convention (pinned by the ticket's hand count)

Warmup requires the exact minimum of `L + 1` observed sessions of history
ending at the signal session, counted as the accepted sessions **strictly
before** it (the signal session's own bar is not part of the history it
consumes): `WARMUP_SATISFIED` iff `position(signal_session) >= L + 1`.

Worked hand count for `(L, S) = (252, 21)`, indices derived from
`calendar_v1` and asserted on both sides in
`test_first_schedulable_event_off_by_one_hand_count_for_252_21`:

- session index 252 (`2011-01-03`): 252 prior observed sessions — one short
  of the required 253 — `WARMUP_INSUFFICIENT_HISTORY`, even though both
  anchors resolve (the old anchor is the very first accepted session,
  `2010-01-04`, which itself has no observed predecessor);
- session index 253 (`2011-01-04`): 253 prior observed sessions — the exact
  minimum — `WARMUP_SATISFIED`.

The convention is one session stricter than bare anchor resolvability and
refuses exactly the degenerate window whose old anchor is the first covered
session. `test_schedule_level_warmup_boundary_asserts_both_sides_through_events`
proves the same flip through derived events (a real month-end placed exactly
at index `L`, then at index `L + 1` for `L - 1`). An event that fails warmup
is retained with its typed state, never dropped; unresolvable anchors are
carried as `None`, never clamped.

## Point-in-time, fail-closed states (each typed and tested)

| State | Raised when |
| --- | --- |
| `BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY` | the policy registry is empty (the shipped state) |
| `BLOCKED_UNRESOLVED_SCHEDULE_POLICY` | the requested policy id is not registered |
| `BLOCKED_AMBIGUOUS_SCHEDULE_POLICY` | duplicate policy ids in a registry |
| `BLOCKED_MALFORMED_SCHEDULE_POLICY` | non-record entries, bad identifiers, empty provenance |
| `BLOCKED_UNREGISTERED_FREQUENCY_KIND` | a frequency kind outside `FREQUENCY_KINDS` |
| `BLOCKED_UNREGISTERED_SCHEDULE_SOURCE_KIND` | a source kind outside `SOURCE_KINDS`, or `TEST_CONSTRUCTED` shipped |
| `BLOCKED_INVALID_VARIANT_SESSION_OFFSETS` | non-int/bool offsets, `S < 0`, or `L <= S` |
| `BLOCKED_INVERTED_SCHEDULE_RANGE` | `range_start > range_end` |
| `BLOCKED_SCHEDULE_RANGE_OUTSIDE_CALENDAR_COVERAGE` | a range not fully inside accepted coverage — never clamped |
| `BLOCKED_FILL_SESSION_BEYOND_COVERAGE` | a fill session that would fall past accepted coverage (whole derivation refused) |
| `BLOCKED_EMPTY_DERIVED_SCHEDULE` | a range selecting no signal session — a typed state, not an empty tuple |

`SCHEDULE_FAIL_CLOSED_STATES` carries all of them, sorted, with a
completeness assertion in the tests. Calendar-owned refusals (for example
`BLOCKED_NOT_AN_ISO_DATE` on a malformed range endpoint, or
`BLOCKED_MISSING_CALENDAR` when no calendar is supplied) propagate from
`calendar_v1` unchanged. No clock, timezone, or environment value is read
anywhere; the source-hygiene test enforces the import allowlist, the absence
of `now`/`today`/`environ`/`getenv` attribute access, and the absence of any
binary-float literal or true division (every quantity is an exact integer,
satisfying the frozen numeric policy's binary-float ban).

## Output and hashing

`derive_rebalance_schedule` returns a frozen `RebalanceSchedule`: ordered
events, range echo, policy identity, calendar identity (id, grouped bytes
hash, grouped session-ids hash, coverage echo), and full lineage — the
calendar store manifest with its complete accepted-authority chain, the
package `store_binding_digest` extended with this kernel's identity, the
ticket placeholder, and the retained non-claims of the accepted calendar
(all `false`, echoed verbatim so this artifact cannot look more
authoritative than its input).

- `canonical_bytes()` — canonical JSON (sorted keys, compact separators,
  UTF-8, trailing newline) via `qme.foundation.lineage.canonical_json_bytes`;
- `self_sha256_grouped()` — grouped sha256 (eight 8-hex groups) over the
  canonical JSON; no contiguous 40/64-hex literal appears anywhere;
- `manifest()` — the document with `schedule_sha256_grouped` bound in.

## Known-answer fixture

`tests/quant/fixtures/rebalance-schedule-v1.json` pins, byte-for-byte, the
manifest of the schedule for a fixed `TEST_CONSTRUCTED` policy
(`test-month-end-sessions-v1`, `MONTH_END_SESSIONS`) over
`[2010-01-04, 2011-12-31]` with `(L, S) = (252, 21)`: 24 events — the twelve
2010 events retained as `WARMUP_INSUFFICIENT_HISTORY` (the first with both
anchors `None`, the second with only the recent anchor resolved), the twelve
2011 events `WARMUP_SATISFIED`, first satisfied ordinal 12 (`2011-01-31`,
position 271), the 2010-12-31 → 2011-01-03 year-boundary fill, and the
2010-05-28 → 2010-06-01 fill across a weekend plus Memorial Day. The
fixture's recorded `schedule_sha256_grouped` is
`00bcfc09:e36f8757:4fe87a1f:0bdf5dad:f8c0613e:b64a241e:3d002a4c:84abad1b`,
re-derived and independently recomputed on every test run; the fixture is
LF-only with a single trailing newline.

Pinned real calendar cases (each re-derived from the accepted calendar's raw
bytes inside the tests, independently of the kernel): holiday month-ends
`2018-03-29` (Good Friday `2018-03-30`) and `2021-05-28` (Memorial Day
`2021-05-31`), half-day month-end `2019-11-29` (`EARLY_CLOSE`), leap-year
month-end `2024-02-29`, year-boundary fill `2019-12-31 → 2020-01-02`, and
the fill-past-coverage refusal at `2027-12-31` (the final accepted session).
