# M0 Closeout Execution Plan — 14 Remaining Blockers

Date: 2026-08-12
Status: `PLANNING_ONLY` — this plan sequences engineering and evidence work; it is
not itself evidence. Every slice follows the established process: branch →
protected-main exact-SHA CI → manifest rebind → ledger event → Linear reconcile.

Owner decisions collected 2026-08-12 and binding on this plan:

| decision | value |
|---|---|
| Calendar production | Dev-only generator (`pandas_market_calendars` in a separate hashed dev/tools lock); runtime consumes only the frozen hashed artifact |
| Estimator numerics | Pure-Python cyclic Jacobi eigensolver + in-repo Ledoit–Wolf; no numpy in any lock |
| AV data acquisition | Fixture-first on free tier (7 fixture securities + dated `LISTING_STATUS`); full-universe backfill deferred |
| NDX membership | Manual dated GIW downloads + official change announcements as reconciliation; no licensed feed for now |

---

## 1. Lane structure

Three lanes run in parallel. Lane C cannot start until Lanes A and B are fully
accepted (by construction).

```text
Lane A (pure engineering, no external data)
  A1  Shared deterministic stats kernel  (feeds #9, #12, #13)
  A2  #12 Correlated-trial fixtures
  A3  #13 Dependence estimator
  A4  #9  Inference implementation
  A5  #2  Asymmetric sell-side fees
  A6  #6  Tax-lot + wash-sale engine
  A7  #3  Greatest-capital capacity solver          (after A5)
  A8  #14 Access-chain export + inclusion proof

Lane B (data acquisition and frozen data artifacts)
  B1  #10 Calendar + session vector                 (first: A- and B-items bind session identities)
  B2  #8  AV proxy membership snapshot
  B3  #5  Production PIT receipts (fixture securities)
  B4  #4  Corporate-action edge-case fixtures       (after B1+B3, extends NEE-116A oracle)
  B5  #7  GIW manual NDX snapshots

Lane C (closure, strictly last)
  C1  #1  Cross-contract semantic approval
  C2  #11 Final freeze timestamp (registers itself at the acceptance commit)
```

Dependency edges: A1→{A2,A3,A4}; A2→A3 (fixtures validate estimator);
A5→A7; B1→{B2,B3,B4}; B3→B4; everything→C1→C2.

---

## 2. Lane A designs

### A1 — Shared deterministic stats kernel (`qme/stats/`)

One module used by #9, #12, #13 so the bootstrap exists exactly once.

- **PRNG**: in-repo SplitMix64-seeded PCG32 with frozen constants and published
  known-answer vectors (first 16 outputs for seed `20260812`). Do **not** use
  `random.Random`: stdlib distribution methods have changed across CPython
  versions, and bit-exact replay is a registered system property. All uniform,
  geometric, and index draws derive from this generator via inverse-CDF.
- **Stationary bootstrap** (Politis–Romano): geometric block lengths from
  inverse-CDF of the registered mean block length; wrap-around indexing;
  deterministic given (seed, series length, replicate count).
- **Politis–White automatic block length** with the Patton–Politis–White (2009)
  correction; registered floor 3, cap N/4. Fixture: known-answer value on a
  frozen synthetic AR(1) series (generated once from the kernel PRNG, values
  committed as canonical decimal strings).
- Acceptance: known-answer PRNG vectors; bootstrap replicate hash stable across
  two independent runs and both CI platforms; block-length fixture exact.

### A2 — #12 Correlated-trial fixtures

Implement the registered fixture table (pack §4.3) as committed canonical-decimal
data plus an exact-arithmetic expectation check:

- Cases A/B/C assert PR exactly (2, 1, 8) — eigenvalues are analytic; compute
  expected PR with `Fraction`, compare estimator output within registered decimal
  tolerance (`1e-9`).
- Case D (two 4-blocks, ρ=0.81): closed form PR = 64/23.7464 — expected value
  computed by `Fraction` from the block eigenvalue formulas
  `1+3ρ (×1), 1−ρ (×3)` per block; assert tolerance and bootstrap-interval
  coverage.
- Cases E/F assert the typed failures (`N_EFF_NOT_COMPUTABLE`), never a number.
- Independent validation: the expected values live in a fixture module that does
  **not** import the estimator (NEE-116A oracle pattern).

### A3 — #13 Dependence estimator

- **Correlation**: Pearson on monthly net returns, common-month alignment,
  ≥60 common months or fail closed (registered).
- **Ledoit–Wolf shrinkage** to the identity target, explicit 2004 formulas,
  shrinkage intensity clamped [0,1]; property tests: output symmetric, unit
  diagonal, PSD (via the eigensolver itself), intensity monotone in noise.
- **Eigensolver**: cyclic Jacobi for symmetric matrices; fixed sweep order
  (row-major upper triangle), rotation threshold `1e-12`, max 100 sweeps,
  float64; eigenvalues sorted descending before PR. 96×96 is trivial cost.
- `N_eff_used = min(96, ceil(P97.5 of bootstrap distribution))` per the
  registered conservative rule, bootstrap from A1 with seed `20260812`,
  B=2000.
- Acceptance: A2 fixtures pass; 2×2 and 3×3 hand-known eigen fixtures exact to
  1e-12; cross-platform CI hash agreement on the full fixture suite.

### A4 — #9 Inference implementation

- Paired monthly delta series → stationary bootstrap CI (percentile and
  percentile-t reported; registered decision bound = percentile) at the
  registered one-sided 95% orientation; Newey–West HAC t as secondary
  diagnostic (lag = block floor − 1 minimum, registered rule).
- Holm step-down over the registered confirmatory family (m=1 today; the
  machinery must still take a family so a later filter-child claim cannot be
  computed ad hoc).
- Decision math binds the existing `decision_math` block verbatim: oriented
  bound, exact-boundary `NO_GO`, missing → `NO_GO`.
- Acceptance: hand-computed fixture on a 24-month synthetic series (CI bounds
  frozen as expected decimals); a degenerate all-zero-delta series produces
  `NO_GO` at the exact boundary; Holm fixture with 3 synthetic p-values matches
  hand calculation.

### A5 — #2 Asymmetric sell-side fees

- New dated parameter artifact `configs/quant/sell-side-fee-schedule-v1.json`:
  SEC Section 31 rate and FINRA TAF (rate, per-share, cap) as **dated rate
  tables** with source URLs and effective dates — backtests spanning 2011–present
  need the historical table, not one current value. Current and historical rates
  must be transcribed from SEC fee-rate advisories and FINRA notices at
  implementation time (do not trust memory for any rate; each row carries its
  advisory URL).
- Rounding conventions (SEC round-up-to-cent; TAF rounding and cap application
  order) are sourced and registered alongside the rates — acceptance requires a
  citation per rule, and the golden fixture recomputes one SELL fill by hand
  with both fees.
- Ledger: BUY = registered bps; SELL = registered bps + section31 + TAF, each a
  separately visible ledger component (no netting into one "cost" figure).
- Acceptance: extended NEE-116A fixture with hand-checked fee lines; a rate-table
  boundary test (fill dated one day before/after a rate change picks the correct
  row); independent oracle recomputation.

### A6 — #6 Tax-lot + wash-sale engine

- Lot ledger per security: `{open_session, shares, basis}`; consumption per
  registered election `FIFO` (default until Webull HIFO support is confirmed —
  that verification remains an open task on the registration).
- ST/LT boundary: holding > 365 days; explicit fixtures at 365 and 366.
- Wash sale (in-strategy scope per registration): loss sale with replacement buy
  within ±30 calendar days → loss disallowed, added to replacement basis,
  holding period tacks. Partial replacement prorates by shares.
- Interactions: split adjusts lot shares/basis per the frozen NEE-116A split
  rules (total basis conserved); wash-sale day windows use calendar days but
  session identities for trade events.
- Acceptance fixtures (hand-worked, oracle-independent): simple FIFO gain; HIFO
  choosing highest basis; full and partial wash sale; wash sale chained through
  a split; LT boundary pair; a sell consuming three lots across ST/LT.

### A7 — #3 Greatest-capital capacity solver

- Definition: `C* = max { C : solve(C) is feasible }` where `solve(C)` is the
  existing discrete cost-aware target solver at initial capital `C`, and
  feasibility = all registered constraints hold (integer order quantums,
  participation ≤ registered ADV20 policy per name, post-trade cash ≥ registered
  minimum buffer, no negative cash after TC + fees, no constraint left
  unevaluated).
- Integer effects make feasibility non-monotone near the boundary, so pure
  bisection is insufficient. Algorithm: exponential bracket → bisection on the
  $0.01 capital quantum → **verification sweep** of every quantum in a registered
  neighborhood (default ±$100) of the bisection point; `C*` is the largest
  verified-feasible capital in the sweep.
- Output is a certificate: the full solved portfolio at `C*` plus the named
  first-violated constraint at the smallest verified-infeasible capital above it.
  No certificate → no capacity claim (clears the
  `UNAVAILABLE_DISCRETE_SOLVER_NOT_IMPLEMENTED` state honestly).
- Acceptance: synthetic fixture where `C*` is hand-computable (two securities,
  coarse ADV limits); a fixture where naive bisection is wrong by construction
  (feasibility island) and the sweep catches it; determinism across runs.

### A8 — #14 Access-chain export + inclusion proof

- Merkle tree over the event-hash sequence (leaves = event hashes in sequence
  order, domain-separated interior hashing
  `SHA256("QME_ACCESS_CHAIN_NODE_V1\0" || left || right)`, odd-node promotion
  rule fixed and documented).
- Export artifact: `{registry_id, chain_length, merkle_root, head_event_hash}`;
  registry bindings embed `{root, length, event_index, merkle_path}` instead of
  the full chain.
- Verification: recompute path → root; reject wrong index, truncated path,
  cross-registry path reuse (domain separation includes registry id in the leaf
  derivation).
- Acceptance: synthetic chain ≥ 10,000 events binds and verifies within the
  event-size limit; tamper fixtures (flipped byte in leaf, swapped siblings,
  path from another registry) all fail typed.

---

## 3. Lane B designs

### B1 — #10 Calendar + session vector (do first in this lane)

- New hashed **dev/tools lock** (separate from runtime/build/agent locks) pinning
  `pandas_market_calendars` + its closure; generator script under `scripts/`
  emits canonical JSON:
  `{calendar_id: "XNYS_2010-01-04_2027-12-31_v1", generator: {package, version},
  timezone: "America/New_York", sessions: [{date, open_utc, close_utc, half_day}]}`.
- Runtime never imports the library; it consumes the artifact by hash
  (`calendar_sha256`, `ordered_session_vector_sha256`).
- Acceptance fixtures before hash acceptance: 2012-10-29/30 (Sandy) absent;
  2018-12-05 (Bush mourning) absent; 2001-09-11..14 out of range (document why);
  half-day list for 2011–2026 matches exchange publications (each with source
  URL); per-year session counts transcribed from exchange-published calendars —
  sourced at implementation, not from memory; spot-check open/close UTC offsets
  across a DST boundary (tzdata 2026.3 already pinned).

### B2 — #8 AV proxy membership snapshot

- Free-tier pulls: `LISTING_STATUS` state=active and state=delisted for the
  registered signal-session date (2 requests), stored as immutable raw CSV +
  SHA-256 + pull manifest (endpoint, params, timestamp, response hash).
- Derived artifact: the reviewed survivorship-reduced common-stock proxy snapshot
  applying the registered exclusion classes, with an exclusion-reason count table
  and a manual-review log for ambiguous rows.
- Review: owner review recorded per the NEE-116-pattern
  (`OWNER_REVIEWED_NOT_INDEPENDENT`), plus the client-side schema validation the
  probe script already models (all three AV soft-error keys typed).

### B3 — #5 Production PIT receipts (fixture-first scope)

Free-tier budget plan (≤25 requests/day):

| day | pulls | count |
|---|---|---|
| 1 | `LISTING_STATUS` ×2 (B2); `TIME_SERIES_DAILY` full + `DIVIDENDS` + `SPLITS` for AAPL, NVDA, MSFT | 11 |
| 2 | Same triple for COST, META, ATVI, BBBY | 12 |
| 3 | Retries, gap fills, and any fallback substitutions | ≤25 |

- **Verification step before committing to this plan**: probe whether
  `outputsize=full` on `TIME_SERIES_DAILY` and history for **delisted** symbols
  (ATVI, BBBY) are served on the free tier. If a delisted symbol returns no
  usable history, register a substitute fixture event of the same class (another
  sourced cash-merger / adverse delisting) rather than degrading the class
  coverage — the class list is registered, the specific ticker is not sacred.
- Every response cached immutably before parsing (raw JSON/CSV + SHA-256 +
  pull manifest); cross-source receipts (SEC filings, issuer press releases)
  fetched and hashed the same day with their URLs and retrieval times.

### B4 — #4 Corporate-action edge-case fixtures

- For each registered event (pack §5.1): construct the golden ledger fixture from
  the B3 receipts, extend the independent `Fraction` oracle, and hand-check the
  anchor numbers exactly as NEE-116A did for the synthetic cases.
- The merger (ATVI) and adverse-delisting (BBBY) fixtures exercise the registered
  taxonomy: deal consideration from the sourced merger docs; the `{0.0, 0.5}`
  scenario pair for the adverse case with the conservative branch marked
  promotion-relevant.
- Acceptance: oracle and production ledger agree per event; each fixture binds
  its receipt hashes; reviewer identity per the registered self-review
  disclosure.

### B5 — #7 GIW manual NDX snapshots

- Runbook: monthly (and on any announcement) manual download of the GIW NDX
  component/weighting file; store raw file + SHA-256 + `source_url` +
  `source_acquired_at`; snapshot schema per the NDX plan §2.3; diff against the
  prior snapshot; any diff requires a matching official Nasdaq announcement
  reference or manual approval before the snapshot is accepted.
- Reconciliation fixture: the June 2026 change set
  (add ALAB/CRWV/NBIS/RKLB/TER, remove CHTR/CTSH/INSM/VRSK/ZS) verified against
  the first downloaded snapshot that spans it.
- Scope note: clears the *authority registration*; historical (pre-first-download)
  NDX membership remains explicitly unavailable and is not claimed — M6
  historical work will need either accumulated snapshots or the licensed feed.

---

## 4. Lane C — closure

### C1 — #1 Cross-contract semantic approval

Run only when every other blocker is accepted. Checklist (each item cites the
artifact and hash it verified):

1. Units: annualized-log-return fields consistent across NEE-119/120 and the
   inference implementation; positive-magnitude drawdown everywhere.
2. Coordinates: `signal_session` close vs `analysis_as_of` vs T+1-open fill
   chronology consistent across freshness policy, label contract, and ledger.
3. Bindings: every `*_hash` in the run-binding lists resolves to an accepted
   artifact; no dangling or superseded hash.
4. Reason codes: precedence list unchanged or version-incremented with diff.
5. Registry: confirmatory family m=1 wiring matches NEE-120; exploratory family
   96/288 matches NEE-122; N_eff consumers reference the accepted estimator.
6. Owner sign-off event with this checklist's completed form as the artifact.

### C2 — #11 Final freeze timestamp

Self-registering per the accepted derivation rule: the committer UTC timestamp of
the protected-main commit that flips NEE-110 acceptance true, evidenced by that
commit SHA and the freeze export hash. No action beyond making that commit.

---

## 5. Suggested slice order

Interleave lanes so CI slices stay small and each is independently revertible:

```text
S1  B1 calendar artifact + dev lock            (unblocks fixture session identities)
S2  A1 stats kernel (PRNG, bootstrap, block rule)
S3  A2+A3 fixtures + dependence estimator      (clears #12, #13)
S4  A4 inference implementation                 (clears #9)
S5  A5 fee schedule + ledger extension          (clears #2)
S6  A6 tax-lot engine                           (clears #6)
S7  A7 capacity solver                          (clears #3)
S8  A8 chain export                             (clears #14)
S9  B2+B3 AV pulls and receipts                 (clears #8, #5 — real-world latency, start early in parallel)
S10 B4 production fixtures                      (clears #4)
S11 B5 GIW runbook + first snapshot             (clears #7)
S12 C1 semantic approval → C2 freeze            (clears #1, #11 — M0 complete)
```

S9 has wall-clock latency (3+ days of quota) — start its pulls as soon as S1
lands, in parallel with S2–S8.

Every slice: focused tests + full gate (pytest, Ruff, strict mypy, locks, secret
scan), manifest rebind where an artifact set changes, ledger event, Linear
comment, no auto-Done.

---

## 6. Corrections addendum — 2026-08-12 (supersedes conflicting text above)

An independent audit of this plan found six defects. All six are accepted. The
corrections below supersede the corresponding sections; prior text is retained
above for history and must not be executed where it conflicts.

### 6.1 S0A contract materialization is the first slice (accepted; supersedes §5)

The slice order omitted the mandatory step of transcribing the owner-approved
registration-pack values into versioned NEE-119/120/121 contracts with manifest
rebinds before any dependent engineering lands. NEE-172
(`codex/m0-s0a-contract-materialization`) is that slice and runs first. The §5
order is amended to: **S0A (NEE-172) → S1 → …**, and S2 (stats kernel) is
already complete (protected-main `6972e406eae307670dd8db662c09acd03db23ec9`,
exact-SHA run `31628849151`, PCG32 validated against the official PCG reference
vectors — a stronger anchor than the self-published vectors §2/A1 proposed).

### 6.2 Calendar identity is `XNAS`, not `XNYS` (accepted; supersedes §3/B1)

The frozen fixture contracts pin `"calendar_id": {"const": "XNAS"}`
(`schemas/quant/golden-two-rebalance-v1.schema.json:127,148`; accounting vectors
throughout). US regular-session equities trade the same sessions on either
identity, so the correction is nominal: register
`calendar_id = XNAS_2010-01-04_2027-12-31_v1`, generated from the library's XNAS
calendar. The freeze-policy v2 blocker text that says "pinned XNYS library" must
be corrected to XNAS in the next policy increment. All acceptance fixtures in
§3/B1 (Sandy closure, mourning days, half-day list, per-year session counts,
DST spot checks) are unchanged.

### 6.3 Generator environment scope (owner decision 2026-08-12; amends the
numerics decision wording)

Amended wording: **numpy/pandas are prohibited in the runtime, build, and agent
locks. The calendar generator runs in a separate, isolated generation
environment whose fully hashed lock is recorded but never shipped; runtime
consumes only the frozen hashed artifact.** The artifact's provenance block
records generator package, version, and generation-lock hash. This resolves the
contradiction between the dev-lock proposal and the recorded "no numpy in any
lock" wording; the pure-Python Jacobi decision for runtime numerics is
unchanged.

### 6.4 AV acquisition is a one-month premium burst (owner decision 2026-08-12;
supersedes §3/B3)

Full daily history is premium-only, so the free-tier schedule in B3 is
invalid. Corrected plan: purchase one month of AV premium (~$50), scoped
strictly to M0-blocker evidence — the 7 registered fixture securities
(`TIME_SERIES_DAILY` full + `DIVIDENDS` + `SPLITS`), dated `LISTING_STATUS`
snapshots for the proxy artifact, and retries/substitutions — then cancel. The
delisted-symbol probe (ATVI/BBBY) and the same-class substitution rule stand.
Full-universe backfill remains deferred to M1. The premium rate limit removes
the 3-day quota schedule; all pulls should complete in one session with the
immutable raw-cache + manifest discipline unchanged.

### 6.5 Capacity solver must prove its maximum (accepted; supersedes §2/A7)

The bracket + bisection + neighborhood-sweep design proves only a local
certificate; a feasibility island beyond the sweep window defeats the
"greatest capital" claim. Corrected design:

1. Register a **capacity search quantum** coarser than the ledger cent quantum:
   `capacity_quantum = $100` (capacity is a planning bound, not an accounting
   value).
2. Prove an **analytic dominating upper bound** `Ĉ`: under the registered
   solver semantics (full investment less the cash buffer), any `C > Ĉ` forces
   the per-name target notional above the participation cap on the
   smallest-ADV selected name — `Ĉ = K · p_max · min_i(ADV20_i)` with the
   buffer margin folded in. The bound is a lemma with its own test, not an
   assumption.
3. **Exhaustively scan every capacity quantum in `[C_min, Ĉ]`** — for
   `Ĉ ≈ $6M` at $100 quantum that is ~60k solver calls, tractable offline —
   and emit the full feasibility bitmap as a hashed artifact.
4. `C*` = the greatest feasible point in the scan. The claim is now a global
   maximum **at the registered quantum**, proven by enumeration; the certificate
   is the bitmap plus the solved portfolio at `C*` and the first-violated
   constraint at the smallest infeasible capital above it.

Non-monotone islands are captured by construction; no neighborhood heuristic
remains.

### 6.6 M1 sequencing conflict (accepted; clarifies §3/B and §5)

NEE-123–128 remain blocked by NEE-110; nothing in this plan starts them. All
data acquisition in Lane B is scoped to M0-blocker evidence only (NEE-116
fixtures, NEE-119 proxy snapshot, NEE-121 calendar, NEE-122 chain evidence).
Any pull that serves only the M1 backfill is out of scope until the freeze
lands.

### Corrected slice order

```text
S0A NEE-172 contract materialization           (in progress)
S1  B1 calendar artifact (XNAS, isolated gen env)
S2  A1 stats kernel                            (DONE — 6972e406, run 31628849151)
S3  A2+A3 fixtures + dependence estimator
S4  A4 inference implementation
S5  A5 fee schedule + ledger extension
S6  A6 tax-lot engine
S7  A7 capacity solver (corrected §6.5 design)
S8  A8 chain export
S9  B2+B3 AV premium-burst pulls (corrected §6.4 scope)
S10 B4 production fixtures
S11 B5 GIW runbook + first snapshot
S12 C1 semantic approval → C2 freeze
```
