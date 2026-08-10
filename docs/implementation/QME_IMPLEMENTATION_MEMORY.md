# QME Living Implementation Memory

Document status: `ACTIVE`

Last reconciled: 2026-08-10

Canonical repository: `D:\Quant-Stocks`

Linear project: [Quant Momentum Equities v0.1 Validation and Nasdaq-100 Agent Review](https://linear.app/neel-jaiswal/project/quant-momentum-equities-v01-validation-and-nasdaq-100-agent-review-3a9beb237520)

Quantitative methodology: [Quant Momentum Equities System — Quantitative Features & Methodology](https://docs.google.com/document/d/1EKKI_x60ovTGvrqT4-Fz8kWWFwecfpeOJDBH57Fbsho/edit?usp=drivesdk)

Detailed UI contract: [QME Ticker Scores UI Specification](../ui/QME_TICKER_SCORES_UI_SPEC.md)

UI architecture: [ADR-001 — Local QME UI Architecture](../ui/ADR-001_LOCAL_UI_ARCHITECTURE.md)

Independent UI review: [QME UI Architecture — Independent Review Record](../ui/UI_ARCHITECTURE_INDEPENDENT_REVIEW.md)

## 1. Purpose

This is the durable, human-readable memory for the Quant Momentum Equities (QME)
implementation. It records what is verified, what is planned, what is blocked, and
which evidence must exist before work may be described as complete.

This document is a reconciliation view. It does not replace Git, CI, immutable run
artifacts, runtime evidence, or Linear. A statement here is authoritative only to the
extent that its cited evidence is still valid.

Every implementation turn that changes source code, schemas, tests, Linear tickets,
CI, or runtime behavior must update the relevant sections of this document and append
one observation to `docs/implementation/evidence-ledger.jsonl` before handoff.

## 2. Status vocabulary

| Status | Meaning |
|---|---|
| `VERIFIED` | Directly observed in the repository, a content-addressed artifact, CI, or runtime evidence. |
| `IN_PROGRESS` | Work has started, but its required acceptance evidence is incomplete. |
| `PLANNED` | Approved scope or a recorded ticket exists; implementation evidence does not. |
| `BLOCKED` | A named prerequisite or safety gate prevents valid continuation or acceptance. |
| `DEFERRED` | Intentionally outside the current delivery sequence. |
| `SUPERSEDED` | Replaced by a later, explicitly linked decision or artifact. |

Linear workflow state and implementation evidence state are intentionally separate:

1. `PLANNING_ONLY`
2. `LOCAL_UNCOMMITTED`
3. `COMMITTED_UNVERIFIED`
4. `CI_VERIFIED`
5. `MERGED`
6. `RUNTIME_EVIDENCED`
7. `ACCEPTED`

A Linear ticket in `Done` is not implementation proof. Untracked or dirty-worktree
code is never more mature than `LOCAL_UNCOMMITTED`.

## 3. Source-of-truth precedence

When sources disagree, reconcile them in this order:

1. Immutable runtime/run artifacts whose hashes, schema, code revision, configuration,
   data snapshot, and policy lineage validate.
2. Git commits and required CI results tied to the exact tested commit SHA.
3. Current repository files and repeatable local tests, explicitly labeled if dirty or
   uncommitted.
4. Live Linear state for planning, ownership, and workflow only.
5. This implementation memory as a synthesized status view.
6. Historical plans, reports, ticket descriptions, and prior observations.

Never silently resolve a conflict. Record it under **Discrepancies and blockers** and
name the evidence required to clear it.

## 4. Reconciliation procedure

Before changing a status or claiming progress:

1. Record the observation date and repository path.
2. Run `git status --short --branch` and capture the current branch, commit, and dirty
   state.
3. Run `python -m pytest -q`.
4. Run `python -m ruff check qme tests`.
5. Run broader checks only when their scope is understood; do not convert legacy or
   vendored failures into a false core failure or false pass.
6. Read the affected source, tests, schemas, and generated artifacts.
7. Read the affected Linear issues and their dependency relationships from the live
   system; do not rely on cached descriptions.
8. Attach or record exact evidence: commit SHA, CI URL and conclusion, artifact URI and
   SHA-256, schema version, run ID, data/config/policy hashes, reviewer, and review time.
9. Update this document and append a new ledger event. Corrections supersede prior
   events; they never erase history.

Required drift handling:

- Linear `Done` below evidence state `ACCEPTED` -> `BLOCKING_STATUS_DRIFT`.
- Evidence state `ACCEPTED` while Linear is not `Done` -> `WORKFLOW_DRIFT_REVIEW`.
- CI without the exact tested SHA -> `COMMITTED_UNVERIFIED` at most.
- Runtime evidence with `dirty=true`, missing code SHA, or missing data/config/schema/
  policy hashes -> not `RUNTIME_EVIDENCED`.
- A generated branch name, project percentage, ticket description, or planning approval
  never establishes implementation.

## 5. Verified snapshot — 2026-08-10

### Repository and validation

| Item | Evidence state | Observation |
|---|---|---|
| Git repository | `COMMITTED_UNVERIFIED` | Clean branch `codex/linear-foundation` at `5f776f54fd2588d49f1a3082ac4d44c43f154de4`; twelve reviewed local commits exist before this memory update. |
| Python package | `VERIFIED` locally | `qme` version `0.1.0`; CPython contract is `>=3.12,<3.13` and validation used CPython 3.12.10. |
| Runtime/development locks | `VERIFIED` locally | Base runtime pins `tzdata==2026.3` so IANA exchange-timezone behavior is reproducible on Windows. Fully hashed build, development, and agent locks verify; clean agent resolution was dry-run only with runtime disabled. |
| Agent dependency | `COMMITTED/BLOCKED` | TradingAgents is pinned to archive SHA-256 and upstream commit `a33fd4c0f134485a43553a2c23a63cb14adbd88f`; the packet-native service, parent-attested supervisor, strict schemas, freshness authority, and evaluation gate remain missing. |
| Core tests | `VERIFIED` locally | `python -m pytest -q -p no:cacheprovider` -> `184 passed`. |
| Lint and typing | `VERIFIED` locally | Ruff over `qme tests scripts` passes; strict mypy over `qme` and the verification scripts passes. |
| Build/install | `VERIFIED` locally | Wheel build, clean no-dependency install, `pip check`, and both CLI help smokes pass. |
| Fixture determinism | `VERIFIED` locally | Two independent canonical foundation manifests are byte-identical at SHA-256 `7468180f9ce2cce7bf6decdc6c54910966fd2a713e9da5b047689d4e78a28957`. |
| Scoped quality policy | `VERIFIED` locally | CI deliberately checks `qme`, `tests`, and `scripts`; user-owned `/tools/` checkouts and local environments are ignored and were not committed. |
| CI provenance | `BLOCKED` | The repository has no configured remote and no exact-SHA GitHub Actions run URL; local evidence is not `CI_VERIFIED`. |

### Implemented local surface

The currently implemented QME package is limited to:

- immutable agent-review evidence packet contracts;
- a packet tool gateway;
- a disabled-by-default TradingAgents adapter boundary;
- a single-evidence-packet `qme-agent-review` CLI;
- safe data-root and canonical lineage/manifest foundations;
- the frozen NEE-118 accounting/execution/cost/turnover/metric equation contract and
  executable arithmetic reference, with authoritative portfolio capacity explicitly
  unavailable until its discrete solver exists;
- the frozen NEE-119 v0.1 signal/rank/selection/rational-weight/filter-child contract,
  with production use blocked on named point-in-time evidence registrations;
- the NEE-120 economic-promotion and abort decision contract, with strict direction,
  non-inferiority boundary, unit, immutable `NO_GO`, and sticky-abort behavior while all
  unavailable mandate values remain explicit blockers;
- the NEE-121 development/confirmation/retrospective/prospective split, independent
  horizon purging, historical availability, append-only access lineage, and restart
  contract, with pristine-holdout and prospective sufficiency claims blocked;
- strict local tests for these boundaries.

Agent output remains report-only. It has `trade_eligible=false` and cannot change
deterministic ranks, portfolio weights, or orders. The unmodified upstream
TradingAgents graph is not an approved runtime path.

### Not yet implemented

The repository does not yet contain accepted implementations for:

- production Alpha Vantage ingestion and immutable normalized stores;
- point-in-time identity, Nasdaq-100 membership, or full-universe coverage;
- the production 12-1 signal, ranking, eligibility, selection, target solver, and
  full-universe portfolio engine;
- the complete event ledger, authoritative capacity solver, walk-forward backtest, or
  empirical quantitative validation;
- an immutable full-universe score artifact or cross-ticker agent result batch;
- a Webull production execution or reconciliation path;
- a web API, frontend package, dashboard, or browser tests.

Synthetic NVDA values in tests are fixtures, not production research results.

### Affected live Linear snapshot

After evidence reconciliation, NEE-117 through NEE-121 are `In Progress`; NEE-168
through NEE-171 remain `Backlog`. NEE-118/119 moved from `Todo` only after their local
commits, independent corrections, and validation evidence existed. NEE-120/121 follow
the same evidence rule. All remain below
`ACCEPTED` until their outstanding CI, runtime, or producer gates exist.

## 6. System mission and non-negotiable boundary

QME is the deterministic system of record for:

- membership and security identity;
- point-in-time market and fundamental evidence;
- features, scores, ranks, eligibility, and review-set selection;
- portfolio targets, constraints, capacity, and accounting;
- order preparation, preview lineage, and reconciliation;
- performance, diagnostics, validation, and promotion gates.

Agents may synthesize and challenge immutable evidence, but they may not fetch current
market data during a historical run, mutate deterministic outputs, place orders, or
turn a missing/degraded result into a neutral or `Hold` result.

The new UI inherits the same boundary: it is a read-only observer, not a strategy
engine, agent orchestrator, broker controller, or alternative source of truth.

## 7. Delivery map

| Workstream | Status | Exit evidence required |
|---|---|---|
| M0 foundation | `COMMITTED_UNVERIFIED` | Local commit `9fee0ab`; clean install, locks, lineage and tests pass. Exact-SHA remote CI is still required. |
| M1 market-data spine | `PLANNED` | Immutable raw receipts, schema validation, rate-limit/retry tests, replayable normalized outputs. |
| M2 identity/corporate actions/coverage | `PLANNED` | Point-in-time identity and membership, explicit exclusions, adjustment fixtures, coverage gate. |
| M3 deterministic signal/backtest | `IN_PROGRESS` | NEE-118/119 contracts are locally committed and tested; production engine, greatest-capital capacity solver, golden two-rebalance fixtures, and backtest remain. |
| M4 reporting/validation | `IN_PROGRESS` | NEE-120/121 governance is locally committed and tested; mandate thresholds, inference registrations, prospective evidence sufficiency, multiplicity execution, immutable empirical reports, and exact-SHA CI remain. |
| M5 broker/paper operations | `PLANNED` | Preview-only default, account/environment allowlist, confirmation authority outside UI, reconciliation and abort evidence. |
| M6 Nasdaq-100 agent review | `IN_PROGRESS/BLOCKED` | Full deterministic universe artifact, immutable evidence packets, process-isolated attested runtime, strict typed outputs, cross-ticker normalization. |
| M7 deferred research extensions | `DEFERRED` | Promotion gates for any extension; no holdout tuning. |
| M8 read-only UI | `PLANNING_ONLY` — architecture committed | Local-only ADR commit `37f52cf`; deterministic snapshots, viewer code, browser/accessibility/performance evidence remain unimplemented. |

## 8. M8 UI implementation decision

### Product outcome

Provide one local interface that lets an operator select an immutable run and inspect:

- all official Nasdaq-100 members, including invalid or degraded rows;
- deterministic features, score/rank, eligibility, selection, and target weights;
- holdings, pending changes, constraints, capacity, and reconciliation state;
- bounded agent-review status, typed role outputs, citations, and resource usage;
- source freshness, artifact hashes, schemas, configuration, code revision, and lineage.

### Preferred architecture — approved for local implementation

1. Producer jobs remain the sole quantitative authority and write finalized immutable
   run artifacts with schemas, lineage, and payload SHA-256 checksums.
2. A separate offline `qme.ui_projection` command maps an explicit source-pointer field
   registry, copies or redacts fields, performs presentation-only base-10 Decimal
   formatting, and atomically publishes one content-addressed immutable JSON snapshot.
3. The snapshot manifest is unsigned. Its SHA-256 detects accidental corruption and
   wrong-file mixing but does not claim authenticity or protection from a same-user
   local rewrite.
4. A pure Python catalog discovers locally available snapshots, reads bounded bytes once,
   validates size/checksum/schema/identity, reconciles the exact security-ID set/hash,
   and builds frozen read models before listening.
5. Flask/Jinja renders semantic HTML and optional versioned read-only JSON from those
   same models; Waitress serves on unauthenticated `127.0.0.1` for one trusted user.
6. The runtime has first-party CSS/minimal JavaScript and no Node/npm frontend toolchain,
   React, TypeScript, database, watcher, WebSocket, service worker, CDN, provider/model/
   broker client, agent runtime, write route, or mutable QME authority.
7. Portable Python packaging with a pinned lock is the default. PyInstaller `onedir` is
   optional only after the normal package, accessibility, performance, and clean-machine
   checks pass.

Authentication/sessions, Ed25519 signatures/key custody, signed catalogs/high-water,
restricted Windows identities/ACLs, firewall/OS sandboxing, and a security release gate
are deliberately removed from v0.1 after the owner's 2026-08-10 local-only decision.

Two independent proposals and a separate red-team review are recorded in ADR-001 and
the independent-review document. The server-rendered design is approved as the best fit
for the single-user, offline, <=200-row scope. This is planning evidence only; no UI
implementation exists yet.

### Required screens

1. **Overview** — run health, breadth, selection, portfolio, agent batch, freshness,
   and blocking conditions.
2. **Universe** — complete membership denominator and ticker/feature/score/rank/
   eligibility table with presentation-only sorting and filtering.
3. **Security Detail** — identity, features, eligibility reasons, portfolio state,
   evidence sources, derived lineage, and agent review.
4. **Portfolio & Risk** — canonical current/target/delta weights, constraints, capacity,
   cash, turnover, and reconciliation summaries.
5. **Agent Reviews** — review-set membership, mandatory holdings, per-role typed results,
   citations, receipts, failures, latency, and resource use.
6. **Runs & Provenance** — locally available content-hashed snapshots, manifest/schema
   compatibility, checksum diagnostics, source freshness, code/config/data/policy
   lineage, and snapshot identity.
7. **Preview & Reconciliation** — evidence chain from target through prepared order,
   preview, broker events, fills/fees/cash/lots/positions, with no mutation controls.

### UI invariant

For every present numeric field, the snapshot carries finite base-10 canonical
and display decimals, finite `scale > 0`, precision, rounding mode, preformatted
`display_text`, opaque sort key, missing state, source pointer, and source artifact hash.
Absent numeric fields omit both decimals. The registered policy proves
`abs((display_decimal/scale)-canonical_decimal) <= 0.5*10^-precision/scale` and derives
`display_text` from `display_decimal`. The browser displays that text exactly and must
not parse either decimal. Charts requiring numeric conversion are deferred unless
separately proven presentation-only. Tables/counts reconcile to the exact unique member
set and the domain-separated canonical membership-set hash. Neither builder nor browser
may calculate momentum, ranks, targets, costs, P&L, or agent eligibility.

No composite agent-plus-quant score is permitted. Deterministic rank/score and agent
rating must remain separate fields with separate provenance.

### M8 work packages

| Package | Status | Scope | Principal integration gates |
|---|---|---|---|
| UI parent — [NEE-168](https://linear.app/neel-jaiswal/issue/NEE-168/qme-p8-read-only-evidence-and-operations-console) | `PLANNING_ONLY` | Completion gate for the read-only evidence and operations console. | All three child tickets |
| UI-1 — [NEE-169](https://linear.app/neel-jaiswal/issue/NEE-169/build-deterministic-local-snapshot-catalog-read-only-api-and) | `PLANNING_ONLY` | Freeze schema/field/state/Decimal/membership fixtures; build deterministic snapshots, local catalog/read models, HTML+JSON, and memory view. | NEE-117; NEE-134, 136, 149, 159, 161, 165, 166, 167 |
| UI-2 — [NEE-171](https://linear.app/neel-jaiswal/issue/NEE-171/build-deterministic-research-and-report-only-agent-review-dashboard) | `PLANNING_ONLY` | Overview, full universe, security detail, portfolio/risk, agent review, provenance. | NEE-169; NEE-146, 149, 150, 151, 154, 155, 166, 167 |
| UI-3 — [NEE-170](https://linear.app/neel-jaiswal/issue/NEE-170/build-preview-and-reconciliation-evidence-console) | `PLANNING_ONLY` | Immutable preview/reconciliation evidence, accessibility, browser tests, packaging, performance. | NEE-169; NEE-157, 158, 159, 160, 161, 167 |

The UI may be developed against frozen fixtures before producers are complete. It may
not be accepted against production contracts until each consumed schema is accepted.

## 9. Canonical future run artifact contract

The offline projection builder requires, but does not itself produce, a finalized
full-universe producer artifact. The viewer consumes only the deterministic JSON
snapshot:

```text
producer-results/<run_id>/manifest.json
producer-results/<run_id>/universe_scores.v1.json
producer-results/<run_id>/tickers/<security_id>/evidence_packet.v1.json
producer-results/<run_id>/tickers/<security_id>/agent_review.json

ui-snapshots/<snapshot_manifest_sha256>/snapshot-manifest.json
ui-snapshots/<snapshot_manifest_sha256>/run.json
ui-snapshots/<snapshot_manifest_sha256>/universe.json
ui-snapshots/<snapshot_manifest_sha256>/securities.json
ui-snapshots/<snapshot_manifest_sha256>/portfolio.json       # optional
ui-snapshots/<snapshot_manifest_sha256>/agent-reviews.json   # optional
ui-snapshots/<snapshot_manifest_sha256>/operations.json      # optional
```

The manifest indexes every payload exactly once; the directory basename equals the exact
manifest-byte SHA-256. It is an unsigned checksum identity, not proof of authorship. The
builder writes payloads and the manifest in staging and atomically publishes a never-
overwritten directory. Invalid snapshots cannot contribute payload data to valid views.

Minimum row identity and state:

```text
run_id, analysis_as_of, membership_snapshot_id,
security_id, issuer_id, ticker, company_name, share_class, sector, industry,
membership_state, data_status, feature_status, rank_eligible,
rank, rank_percentile, selected, selection_reason,
momentum_12_1, adv20_usd, explicitly versioned feature fields,
review_reasons[], event_flags[], data_snapshot_ids,
strategy_config_hash, code_revision, row_hash
```

Minimum manifest state:

```text
schema_version, run_id, analysis_as_of, generated_at, run_status,
completeness_status, membership_snapshot_id, membership_hash, membership_count,
member_status_counts{VALID,DEGRADED,STALE,MISSING,BLOCKED,INVALID}, minimum_breadth,
selection_rule, data_revision, strategy_config_hash,
source_policy_hash, code_revision, rows_hash
```

The producer contract must define membership, missingness, ties, normalization, and
selection math. The UI renders those results and definitions; it never invents them.

## 10. Discrepancies and blockers

1. **No remote CI provenance:** twelve reviewed local commits exist before the current
   memory update, but there is no remote,
   pull request, or exact-SHA CI run. Evidence is `COMMITTED_UNVERIFIED`, not accepted.
2. **No production scoring artifact:** UI integration must use fixtures until the full
   Nasdaq-100 producer schema is accepted.
3. **Agent runtime disabled:** current adapter is a safety boundary, not an operational
   review service.
4. **Structured artifact parity incomplete:** durable typed role objects, strict optional
   fields, finite-number checks, citations/receipts, and unknown-role rejection remain
   required.
5. **Freshness and derivation authority incomplete:** packet-declared maximum ages and
   inline source IDs are insufficient without source-class caps and raw-to-visible
   payload lineage.
6. **No safe broker mutation authority:** the UI must not import or expose legacy Webull
   helpers; it may only display accepted immutable preview/reconciliation artifacts.
7. **Production quantitative registrations absent:** minimum breadth, source-class
   freshness, point-in-time identity/membership, and production total-return event
   evidence remain deliberately unregistered and blocking; no result is inferred.
8. **Authoritative portfolio capacity unavailable:** the fixed-trade participation
   diagnostic is not portfolio capacity; the greatest-capital discrete solver remains
   `UNAVAILABLE_DISCRETE_SOLVER_NOT_IMPLEMENTED`.
9. **UI implementation unresolved:** the local-only architecture is approved, but no
   snapshot schemas/fixtures, field registry, projection builder, local catalog/read
   models, Flask/Jinja/Waitress viewer, browser evidence, packaged artifact, or clean-
   machine evidence exists.

## 11. Next required actions

1. Configure a repository remote and run required CI against the exact branch SHA; do
   not mark NEE-117 accepted from local tests alone.
2. Complete NEE-116 golden two-rebalance/corporate-action fixtures against the committed
   NEE-118/119 contracts, then implement the production target/cost solver.
3. Freeze the deterministic data, membership, feature, score/rank, eligibility, and
   full-universe artifact contracts.
4. Complete point-in-time Nasdaq-100 producer work and create the first immutable
   universe fixture plus corrupt/degraded variants.
5. Execute NEE-169 Stage 0: commit producer/snapshot schemas, field/source-pointer
   registry, state/completeness mapping, membership-hash/Decimal vectors, resource bounds,
   benchmarks, and valid/adversarial fixtures.
6. Build and verify the deterministic content-addressed snapshot builder, then implement
   the local in-memory catalog/read models and unauthenticated loopback viewer.
7. Build the deterministic research dashboard before agent and broker views.
8. Add agent-review views only after typed batch outputs and receipts validate.
9. Add preview/reconciliation views only after canonical operations artifacts exist.
10. Attach browser, accessibility, performance, and deterministic snapshot
   evidence to an exact committed SHA.

## 12. Progress log

| Observed | Change | Evidence state | Evidence / next gate |
|---|---|---|---|
| 2026-08-09 | Created living implementation memory and M8 UI specification from repository and live Linear audit. | `LOCAL_UNCOMMITTED` | Files are untracked; first reviewed commit and CI remain required. |
| 2026-08-09 | Recorded local core verification. | `VERIFIED` locally | 41 tests passed; scoped Ruff passed; no committed/CI provenance. |
| 2026-08-09 | Added read-only UI workstream as planned observer scope. | `PLANNING_ONLY` | Freeze producer contracts and UI decision record before framework implementation. |
| 2026-08-09 | Created Linear parent NEE-168 and children NEE-169, NEE-170, and NEE-171 with explicit quantitative, security, provenance, accessibility, and integration acceptance criteria. | `PLANNING_ONLY` | Tickets are Backlog; ticket creation is not implementation evidence. |
| 2026-08-09 | Re-read all four UI tickets and their relations, enumerated all 64 project issues, reran scoped tests/lint, and parsed the evidence ledger. | `VERIFIED` locally | Parent is blocked by all children; 41 tests and scoped Ruff pass; 2 ledger records parsed before this appended observation. |
| 2026-08-09 | Compared independent SPA and server-rendered proposals, red-teamed the preferred design, and recorded ADR-001. | `PLANNING_ONLY` | Flask/Jinja/Waitress plus signed offline projection is preferred and conditionally approved for Stage 0; production is `NO-GO` pending P0-1 through P0-7 and exact-SHA evidence. |
| 2026-08-09 | Final independent audit found and the plan resolved catalog rollback/omission, builder/signer custody, control-file recursion, public-session side effects, quarantine, numeric-display, membership-hash, completeness, and route-identity specification gaps. | `PLANNING_ONLY` | Contracts and NEE-168/169/170/171 were reconciled; no implementation exists and Stage 0A remains unaccepted until committed fixtures prove the mechanisms. |
| 2026-08-10 | Owner changed the UI scope to a single trusted user on one local computer with no authentication or security program. ADR-001 and the independent review now approve an unauthenticated local Flask/Jinja/Waitress viewer over deterministic content-addressed JSON snapshots; NEE-168/169/170/171 descriptions and audit comments were reconciled without changing Backlog state or dependencies. | `PLANNING_ONLY` | Supersedes the 2026-08-09 auth/signing/catalog-high-water/Windows-isolation plan. Preserve schema/checksum/provenance, exact member-set/hash, Decimal fidelity, fail-closed state, read-only/no-order, accessibility, and measured-performance gates. |
| 2026-08-10 | Created reviewed foundation commit `9fee0ab0fa7f55503524d7024ff1998ab6f78f6b` with fully hashed locks, CI workflow, safe data-root/lineage contracts, staged-byte secret scanning, and hard-disabled agent runtime. | `COMMITTED_UNVERIFIED` | Local clean install/build/CLI/lock/secret checks pass; exact-SHA remote CI remains absent. |
| 2026-08-10 | Committed corrected NEE-118 accounting contract as `21c6cde0b14d30d055a5bad9de52a4ff01e5b438`. | `COMMITTED_UNVERIFIED` | 14 focused tests; raw-coordinate evidence, sell-before-buy, per-fill costs/taxes, timing, strict schemas and undefined capacity state pass. |
| 2026-08-10 | Committed corrected NEE-119 v0.1 quantitative contract as `943da8dcecc1148cd158383a7b5682d0fe0a85ba`. | `COMMITTED_UNVERIFIED` | 41 focused tests; exact calendar anchors, rational weights, total-return methodology, near ties and immutable filter children pass. Production evidence registrations remain blocking. |
| 2026-08-10 | Committed the local-only UI architecture contract as `37f52cf918dba5a04d47c95940beeb12d196c73f`. | `PLANNING_ONLY` | ADR/spec/review are committed; no UI code or runtime evidence exists. Full integration validation at this SHA: 113 tests, Ruff, strict mypy, compile, locks, wheel and CLI smokes pass. |
| 2026-08-10 | Reconciled live Linear descriptions and comments with commit/test evidence; moved NEE-118 and NEE-119 from `Todo` to `In Progress`, retained NEE-117 `In Progress`, and retained NEE-168 `Backlog`. | `COMMITTED_UNVERIFIED` / `PLANNING_ONLY` | No ticket was marked Done. Exact-SHA remote CI, production evidence registrations, capacity solver, and UI implementation remain explicit gates. |
| 2026-08-10 | Committed fail-closed NEE-120 promotion and abort governance as `5f9546e6308a4adadeccb6e06b1aeb900ca37285`. | `COMMITTED_UNVERIFIED` | 16 focused tests and exact arithmetic pass. Production remains `BLOCKED_UNRESOLVED_MANDATE`; no threshold, margin, AUM, inference, sample-size, or abort value was invented. |
| 2026-08-10 | Committed NEE-121 sample/holdout governance and the pinned Windows IANA timezone dependency as `cc43567826f6498abf156b76db71f2bea44410ff`. | `COMMITTED_UNVERIFIED` | 54 focused tests; full suite 183 passed; Ruff, strict mypy, three hashed locks, and staged-byte secret scan pass. Confirmation provenance and prospective evidence sufficiency remain explicitly blocked. |
| 2026-08-10 | Committed manifest-aware secret scanning as `cb51c93cbb437653e301d940ea9882ca829afee4` after the final full-tree gate exposed allowlist drift for the new self-verifying governance manifests. | `COMMITTED_UNVERIFIED` | Regression validates all four registered manifests; 184 tests, Ruff, strict mypy, staged and 97-file full secret scans pass. |
| 2026-08-10 | Committed repository-local script package marker as `5f776f54fd2588d49f1a3082ac4d44c43f154de4` after an independent ambient-Python run found a third-party `scripts` package shadowing the verification module. | `COMMITTED_UNVERIFIED` | Both ambient and locked CPython 3.12 environments pass all 184 tests; Ruff and strict mypy pass. |
| 2026-08-10 | Independently audited the next execution wave against live Linear after NEE-120/121. | `BLOCKED` | NEE-116 and NEE-122 are explicitly blocked by NEE-117; NEE-123 through NEE-128 are blocked by the NEE-110 specification gate. No downstream ticket was started around those dependencies. The immediate external gate is a repository remote plus exact-HEAD CI evidence for NEE-117. |

## 13. Per-ticket evidence record template

Use this template whenever a ticket changes evidence state:

```yaml
ticket_id:
linear_url:
linear_state:
linear_updated_at:
observed_at:
objective_revision_hash:
evidence_state: PLANNING_ONLY
branch_or_pr:
commit_sha:
merge_sha:
dirty_worktree:
required_ci_checks:
  - name:
    run_url:
    tested_sha:
    conclusion:
artifacts:
  - type:
    uri:
    sha256:
    schema_version:
    run_id:
    code_sha:
    config_hash:
    data_hash:
    policy_hash:
reviewer_and_reviewed_at:
discrepancies: []
next_required_gate:
```
