# NDX GIW Membership Runbook V1

Blocker: `NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP` (engineering leg)
Owner decision (2026-08-12): *Manual GIW downloads — controlled workflow: dated
Nasdaq GIW component files + official change announcements for reconciliation,
stored as immutable hashed snapshots.*
Code: `qme/data/ndx/giw_snapshot.py`, `qme/cli/ndx_membership.py`
Tests: `tests/data/test_ndx_membership.py`
Change tier: **T2 ENGINEERING** (`docs/governance/CHANGE_TIER_POLICY_V1.md`) — PR + CI only.

This runbook is the owner's procedure. Nothing in the codebase downloads
anything: there is no network call in `qme/data/ndx/**`, and there is no
scraper. The owner performs the download by hand and hands the file to the CLI.

---

## 1. What this workflow does and does not claim

**Claims** — stamped on every artifact as `claims`:

| claim | value |
|---|---|
| `authoritative_nasdaq_100_membership_available` | `false` |
| `historical_membership_before_first_snapshot_claimed` | `false` |
| `freeze_blocker_changed` | `false` |
| `source_class` | `MANUAL_GIW_DOWNLOAD` |

`authoritative_nasdaq_100_membership_available` stays `false` in code. Flipping
it is a T0 registration decision about the *evidence*, not a code path — an
owner-approved snapshot makes `resolve_membership` return a basket, and that is
all this slice claims.

**Non-claims**, all enforced by code, not convention:

1. **No constituent count is assumed.** The Nasdaq-100 tracks companies, several
   issuers list more than one eligible share class, and Fast Entry can lift the
   count above 100. Nothing validates a count; the loader consumes a dated list
   of dynamic length.
2. **Pre-first-download history is not claimed.** `resolve_membership` in
   `point_in_time_membership` mode raises `MembershipUnavailable` for any date
   before the earliest accepted snapshot. A backtest fails closed rather than
   silently using today's basket. Historical NDX membership needs either
   accumulated snapshots going forward or the licensed GIFFD feed.
3. **Weights are never guessed.** `index_weight` is `null` when the export has no
   weight column or the cell is blank. Present weights are stored as canonical
   decimal strings exactly as published; the unit is **not** normalized
   (`index_weight_unit = "AS_PUBLISHED_UNNORMALIZED"`). Compare weights only
   within one snapshot, or normalize downstream and say so.
4. **Issuer identity is not claimed.** `cik` is always `null` — even when the
   export carries one — until the identity layer exists. `security_id` is the
   internal, stable `NDX:<SYMBOL>` id.
5. **Change reasons are not inferred.** `reason` is `null` on every row: a
   component file states membership, not why it changed. Scheduled review versus
   Fast Entry versus corporate action is only visible in the announcement, and
   the reconciliation output records that separately.
6. **A matching announcement is evidence, not acceptance.** Acceptance always
   requires `approve` (see §5).
7. **QQQ is not an authority.** Nothing here reads ETF holdings; per the plan
   §2.2 that is a discrepancy check only, and it is out of scope for this slice.

---

## 2. Download procedure (owner, monthly and on every announcement)

Cadence: **monthly**, and additionally **on the day of any official Nasdaq change
announcement** and on the announcement's effective date.

1. Sign in to **Nasdaq Global Index Watch** (`indexes.nasdaqomx.com`) with the
   entitled account. Do not use the logged-out public page: it exposes only
   partial data.
2. Open the **NDX** index and its **component / weighting** view for the date you
   are capturing.
3. Export as **CSV, UTF-8**. If the export tool offers only a workbook format,
   convert to CSV and keep the workbook alongside it — the loader parses CSV text
   only and refuses a workbook with an actionable error.
4. Delete any title/preamble rows and any totals/footer row. The **first
   non-empty line must be the header row**, and every following non-empty line
   must be a constituent. The loader does not skip a preamble, because guessing
   which line is the header is exactly the kind of silent inference this workflow
   exists to avoid.
5. Record, at the moment of download:
   - **`source_url`** — the exact URL the file came from (`https://…`);
   - **`source_acquired_at`** — the download time as ISO-8601 **with a UTC
     offset**, e.g. `2026-06-22T21:05:00+00:00`;
   - **`effective_at`** — the date this membership becomes/became active
     (`YYYY-MM-DD`);
   - **`announced_at`** — the official announcement date or timestamp, when the
     capture follows an announcement (optional).
6. When the capture follows an announcement, also save the **official change
   announcement** as a JSON record for §4 and note its URL.

Do not rename the file to encode metadata. Provenance lives in the recorded
fields, not in filenames; the stored copy is content-addressed by SHA-256.

---

## 3. Ingest

```bash
export QME_DATA_ROOT=<external data root>          # or pass --data-root
python -m qme.cli.ndx_membership ingest \
    --source-file  ./NDX-components-2026-06-22.csv \
    --source-url   https://indexes.nasdaqomx.com/Index/Weighting/NDX \
    --acquired-at  2026-06-22T21:05:00+00:00 \
    --effective-at 2026-06-22 \
    --announced-at 2026-06-12
```

What happens, in this order:

1. The file is read and **parsed first**. If it cannot be parsed, nothing is
   written — the raw store never accumulates orphan downloads.
2. The bytes are copied **byte-for-byte** to
   `raw/nasdaq_giw/NDX/<effective_at>/<sha256[:12]>.csv`, created with `O_EXCL`
   and never overwritten, beside
   `raw/nasdaq_giw/NDX/<effective_at>/<sha256[:12]>.meta.json`
   (`source_url`, `source_acquired_at`, `sha256`, `byte_length`, `effective_at`,
   `announced_at`, `claims`).
3. One line is appended to `raw/nasdaq_giw/_audit.jsonl`.
4. The parsed rows are published to
   `derived/ndx-membership/NDX/<effective_at>/<snapshot_id>.json` as canonical
   JSON, immutable, with `supersedes_snapshot_id`, the `diff`, and an
   `acceptance_status`.

`snapshot_id` is `NDX-<effective_at>-<sha256(canonical rows)[:12]>`. The digest
covers the parsed rows *including* their provenance fields, so the same basket
captured from a different URL or at a different acquisition time is a distinct
snapshot — the id binds the basket **and** how it was obtained.

**Re-running the same ingest is safe.** Identical bytes under identical
provenance reuse the stored copy, append no second audit line, and republish
nothing. Identical bytes recorded under *different* provenance, or a stored copy
whose bytes no longer match, are refused: immutable downloads are never
re-provenanced or silently repaired.

Artifacts contain no absolute paths. Every stored reference is a root-relative
logical id (`raw/nasdaq_giw/...`, `derived/ndx-membership/...`).

### 3.1 Accepted CSV header aliases

Headers are folded before matching: BOM stripped, lowercased, every
non-alphanumeric character replaced by a space, whitespace runs collapsed. So
`Security_Symbol`, `SECURITY SYMBOL`, and `Security  Symbol` are one alias, and
`Index Weight (%)` folds to `index weight`.

| field | required | accepted (folded) aliases |
|---|---|---|
| `security_symbol` | **yes** | `symbol`, `security symbol`, `ticker`, `ticker symbol`, `trading symbol`, `stock symbol`, `constituent symbol` |
| `company_name` | **yes** | `company name`, `company`, `name`, `security name`, `issuer name`, `constituent name` |
| `index_weight` | no | `index weight`, `weight`, `weighting`, `weight percent`, `percent weight`, `percent of index` |
| `share_class` | no | `share class`, `security class`, `class` |
| `cik` | no | `cik`, `cik number`, `central index key` — recognised so the column is not reported as unknown; the value is **not** stored (see §1 non-claim 4) |

Columns matching no alias (`Index Shares`, `Index Market Value`, …) are
tolerated and listed in the snapshot's `ignored_columns` — visible, not silently
dropped.

Ingest fails with a typed `GiwHeaderError` that **lists every header it saw**
when:

- a required field has no accepted alias (`no column for security_symbol`);
- two different columns claim the same field (`'Symbol' and 'Ticker' both map to
  'security_symbol'`) — ambiguity is never resolved by picking one;
- a header is repeated.

Row-level failures raise `GiwSnapshotError` naming the row number: unusable
symbol, duplicate symbol, blank company name, non-decimal or negative weight,
too few columns, or a header with no constituent rows.

### 3.2 How share classes and Fast Entry appear

- **Share classes**: each eligible class is an ordinary separate row with its own
  `security_symbol` and `security_id` (`NDX:GOOG`, `NDX:GOOGL`). They may share a
  `company_name`, and `share_class` is populated only if the export has a class
  column — it is never derived from the ticker. No issuer-level grouping is
  claimed; that needs the identity layer.
- **Fast Entry**: an ordinary row. A component file does not label the entry
  route, so `reason` stays `null` and the route is visible only through the
  announcement in §4.

---

## 4. Diff and reconcile

```bash
python -m qme.cli.ndx_membership diff
python -m qme.cli.ndx_membership reconcile --announcement-file ./ndx-2026-06-22.json
```

`diff` prints the stored delta against the superseded snapshot: `added`,
`removed`, `retained`, `count_before`, `count_after`, `supersedes_snapshot_id`.
For the **first** snapshot of an index there is no prior basket, so `is_initial`
is `true` and `added`/`removed`/`retained` are empty with `count_before = 0` —
the workflow refuses to describe an unobserved history as a set of additions.
Its rows carry `change_type = "INITIAL"` rather than `ADD` or `RETAIN`, for the
same reason.

The announcement record is a JSON object:

```json
{
  "source_url": "https://www.nasdaq.com/<official announcement>",
  "announced_at": "2026-06-12",
  "effective_at": "2026-06-22",
  "add": ["ALAB", "CRWV", "NBIS", "RKLB", "TER"],
  "remove": ["CHTR", "CTSH", "INSM", "VRSK", "ZS"]
}
```

Classifications:

| classification | meaning | exit code |
|---|---|---|
| `MATCHES_ANNOUNCEMENT` | the announcement explains every add and every remove, exactly | 0 |
| `PARTIAL_MATCH` | lists `unexplained_adds` / `unexplained_removes` (observed, not announced) and `missing_adds` / `missing_removes` (announced, not observed) | 1 |
| `NO_ANNOUNCEMENT` | none supplied, the snapshot is initial, or the announcement covers a different effective date | 1 |

The June 2026 change set is available as a reconciliation fixture at
`tests/data/fixtures/ndx-june-2026-change-set.json`, loaded by
`june_2026_change_set()`. **The plan registered the change set contents and
effective date but not the announcement URL**, so the fixture carries
`"source_url": "PENDING_OWNER_RECORDED_ANNOUNCEMENT_URL"` and
`"source_url_recorded": false`. Replace it with the retrieved announcement URL
before citing a reconciliation against it in an approval.

---

## 5. Acceptance

**A non-empty, unreconciled diff blocks acceptance.** `acceptance_status` is
computed at publish time and is never auto-set to accepted for a change:

| situation | `acceptance_status` | `acceptance_reason` |
|---|---|---|
| first snapshot for the index | `PENDING_MANUAL_APPROVAL` | `INITIAL_SNAPSHOT_REQUIRES_OWNER_APPROVAL` |
| basket changed | `PENDING_MANUAL_APPROVAL` | `UNRECONCILED_DIFF_REQUIRES_ANNOUNCEMENT_OR_APPROVAL` |
| basket unchanged, predecessor accepted | `ACCEPTED_UNCHANGED` | `BASKET_UNCHANGED_FROM_ACCEPTED_PREDECESSOR` |
| basket unchanged, predecessor not accepted | `PENDING_MANUAL_APPROVAL` | `PREDECESSOR_NOT_ACCEPTED` |

`ACCEPTED_UNCHANGED` is the only automatic acceptance, and it claims nothing the
already-accepted predecessor did not.

A `MATCHES_ANNOUNCEMENT` reconciliation does **not** accept the snapshot. The
announcement record is itself a manually supplied local file — there is no
network and therefore no authenticated retrieval — so treating it as automatic
authority would only be trusting an unverified input. It is the evidence the
owner cites:

```bash
python -m qme.cli.ndx_membership approve \
    --snapshot-id NDX-2026-06-22-<digest> \
    --approver   "<owner identity>" \
    --note       "MATCHES_ANNOUNCEMENT vs https://www.nasdaq.com/<announcement>"
```

`approve` **appends** to `derived/ndx-membership/_approvals.jsonl` and never
touches the snapshot file: the reviewed bytes stay byte-identical forever, and
acceptance is an append-only log beside them. Approving twice appends twice; the
history of who accepted what is not editable.

---

## 6. Resolve

```bash
python -m qme.cli.ndx_membership resolve --as-of 2026-07-01 --mode point_in_time_membership
python -m qme.cli.ndx_membership resolve --as-of 2026-08-16 --mode current_membership
```

- `current_membership` — the latest **accepted** snapshot. `--as-of` is recorded
  for provenance and does **not** bound the result. Live research only; never a
  backtest.
- `point_in_time_membership` — the accepted snapshot with the greatest
  `effective_at <= as_of`, and only when `as_of` is on or after
  `coverage_start` (the earliest accepted snapshot). Anything earlier raises
  `MembershipUnavailable` and the CLI exits 2. Mandatory for backtests and
  historical agent evaluation.

Both modes raise `MembershipUnavailable` when no accepted snapshot exists.
Nothing degrades to "use today's basket".

---

## 7. Exit codes

| code | meaning |
|---|---|
| 0 | success (`reconcile`: `MATCHES_ANNOUNCEMENT`) |
| 1 | `reconcile` produced `PARTIAL_MATCH` or `NO_ANNOUNCEMENT` — acceptance is blocked |
| 2 | typed contract error, including `MembershipUnavailable`, `GiwHeaderError`, `GiwAnnouncementError`, and data-root errors |

---

## 8. Scope note

This slice clears the **authority registration** leg of NEE-119: the acquisition
path, storage contract, diff, reconciliation, approval, and fail-closed
resolution all exist and are tested. It does not produce historical membership.
M6 historical work needs either snapshots accumulated from here forward or the
licensed GIFFD delivery.
