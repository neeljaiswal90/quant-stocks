# QME Ticker Scores UI Specification

Status: `PLANNED — IMPLEMENTATION NOT STARTED`

Revision: `0.2-local`

Last reviewed: 2026-08-10

Living status source: [QME Implementation Memory](../implementation/QME_IMPLEMENTATION_MEMORY.md)

Architecture decision: [ADR-001 — Local QME UI Architecture](ADR-001_LOCAL_UI_ARCHITECTURE.md)

Independent review: [QME UI Architecture — Independent Review Record](UI_ARCHITECTURE_INDEPENDENT_REVIEW.md)

## 1. Objective

Build a single-user, local, read-only evidence and operations console for QME. It must
show the complete Nasdaq-100 membership denominator, deterministic scores and states,
portfolio information, bounded agent reviews, and provenance without becoming a second
calculation engine or trading control plane.

The owner must be able to answer:

- Which exact immutable run and UI snapshot am I viewing?
- Which membership snapshot and analysis cutoff apply?
- Are all official members present, including degraded, stale, missing, blocked, and
  invalid rows?
- Which canonical features, rank, eligibility, selection, and target state did the
  deterministic engine produce?
- Why was a ticker selected, excluded, reviewed, or blocked?
- Which values are deterministic and which are report-only agent opinions?
- Which source, schema, code, configuration, data, policy, and artifact hashes support
  each value?
- Is the run complete, degraded, stale, conflicting, corrupt, or invalid?
- For operational evidence, where is the immutable chain from target to preview, broker
  event, fill, and reconciliation?

## 2. Hard boundary

The viewer may:

- discover configured local UI snapshot roots;
- verify schemas, content hashes, exact membership, and lineage;
- build frozen typed read models;
- serve versioned `GET`/`HEAD` HTML, JSON, asset, and health routes on `127.0.0.1`;
- render, sort, filter, search, and deep-link within one explicitly identified snapshot.

It may not:

- fetch market, fundamental, social, news, or membership data;
- calculate or change features, scores, ranks, eligibility, weights, costs, P&L, tax,
  capacity, reconciliation, or agent review-set membership;
- start, retry, cancel, or abort a deterministic or agent run;
- call an LLM or model server;
- access provider, broker, GitHub, Linear, or cloud credentials;
- request, generate, refresh, submit, place, replace, cancel, or confirm an order or
  broker preview;
- change canonical artifacts, configuration, tickets, or runtime state;
- treat missing, corrupt, unknown, stale, degraded, or invalid data as zero, success, or
  `Hold`.

All application routes are `GET` or `HEAD`. Route-inventory tests must prove every other
method is absent or returns 405 and that no route can mutate QME, agent, broker,
filesystem, or configuration state.

## 3. Current-state constraint

As of 2026-08-10, the repository has no UI dependency, API server, frontend package,
production Nasdaq-100 score artifact, accepted deterministic scoring engine, or
cross-ticker agent batch. The first increment uses frozen synthetic fixtures covering
valid, degraded, stale, missing, blocked, invalid, corrupt, conflicting, partial, and
unsupported-schema states.

Synthetic data must be visibly labeled and must not be placed under a production result
or UI snapshot root.

## 4. Preferred architecture

```text
Immutable QME producer artifacts
          |
          v
Offline deterministic qme.ui_snapshot builder
  - validates producer schema/hash/lineage
  - applies explicit source-pointer mappings
  - formats exact Decimal display strings
  - writes a content-addressed JSON UI snapshot
          |
          v
Framework-independent Python catalog/read models
  - bounded same-byte read/hash/parse
  - exact Nasdaq membership reconciliation
  - immutable in-memory snapshots
          |
          +-------------------+
          |                   |
          v                   v
Jinja semantic HTML     Versioned GET JSON
          |                   |
          +---------+---------+
                    v
Flask + Waitress on 127.0.0.1
  - no login, session, or mutation route
  - no quant, data, model, agent-run, or broker runtime
```

The runtime uses first-party CSS and minimal first-party JavaScript. It has no Node/npm
frontend build, React, TypeScript, database, WebSocket, service worker, CDN, remote font,
filesystem watcher, or development server. New snapshots become visible after a viewer
restart. An optional PyInstaller `onedir` build follows a portable-wheel and clean-
Windows spike.

Release gates:

| Gate | Required result |
|---|---|
| G1 — Snapshot integrity | Versioned manifest and payload schemas, SHA-256 inventory, same-byte validation, deterministic output |
| G2 — Display fidelity | Source-pointer allowlist, Decimal representation, exact `display_text`, no browser quant math |
| G3 — Universe completeness | Exact security-ID set/hash equality and six-bucket count reconciliation |
| G4 — Read-only authority | `GET`/`HEAD` only, no order/model/provider/broker capability, source artifacts unchanged |
| G5 — Local delivery quality | Loopback only, safe rendering, accessibility, performance, packaging and clean-machine evidence |

## 5. Artifact and snapshot model

### 5.1 Logical layout

```text
producer-results/<run_id>/
  manifest.json
  universe_scores.v1.json
  portfolio.json
  risk.json
  agent_batch.json
  operations/
    prepared_orders.json
    previews.json
    broker_events.json
    reconciliation.json
  tickers/<security_id>/
    evidence_packet.v1.json
    agent_review.json

ui-snapshots/<ui_snapshot_hash>/
  snapshot-manifest.json
  run.json
  universe.json
  securities.json
  portfolio.json
  risk.json
  agent-reviews.json
  operations.json
```

Actual producer paths remain producer-owned. A separate offline command validates the
producer manifest and payload hashes, maps registered source pointers, and atomically
publishes the UI snapshot. The viewer reads only UI snapshots and never selects an
implicit JSON/Parquet fallback.

### 5.2 Snapshot manifest

Required fields:

```text
schema_version
run_id
analysis_as_of
generated_at
run_status
completeness_status
producer_manifest_hash
snapshot_builder_revision
adapter_registry_hash
field_mapping_hash
redaction_policy_hash
formatting_policy_hash
state_policy_hash
membership_snapshot_id
membership_hash
membership_count
member_status_counts{VALID,DEGRADED,STALE,MISSING,BLOCKED,INVALID}
minimum_breadth
selection_rule
data_revision
strategy_config_hash
source_policy_hash
code_revision
rows_hash
artifact_index[]
```

`artifact_index` lists each payload exactly once with logical ID, relative path, byte
size, SHA-256, and schema. `snapshot-manifest.json` is the sole non-indexed control file.
Its canonical byte hash is `ui_snapshot_hash`, and the directory basename must equal
that hash.

The snapshot must reconcile identities:

```text
membership_count = sum(member_status_counts[b] for every registered bucket b)
unique(projected_security_ids) = manifest_membership_security_id_set
count(unique(projected_security_ids)) = membership_count
```

Membership hash construction:

```text
SHA-256(
  UTF8("QME_MEMBERSHIP_SET_V1\0") ||
  canonical_utf8_json_array(sort_utf8(normalize_NFC(projected_security_ids)))
)
```

The canonical array has no BOM or whitespace, uses registered JSON escaping, preserves
case, and contains each schema-valid ID once. Duplicate/normalization/case collisions
are `CONFLICTING`. Known-answer vectors are shared by producer, builder, and viewer.

### 5.3 Universe row

Required identity and state:

```text
run_id
analysis_as_of
membership_snapshot_id
security_id
issuer_id
ticker
company_name
share_class
sector
industry
membership_state
data_status
feature_status
rank_eligible
rank
rank_percentile
selected
selection_reason
momentum_12_1
adv20_usd
features{}
review_reasons[]
event_flags[]
data_snapshot_ids[]
strategy_config_hash
code_revision
row_hash
```

Optional composite scores require `score_definition_id`, factor/sleeve versions,
missingness policy, tie policy, and configuration hash. The UI never normalizes an
unversioned score.

### 5.4 Numeric value

Every present projected numeric value is an object containing:

```text
canonical_decimal
display_decimal
unit
scale
display_precision
rounding_mode
display_text
sort_key
missing_state
source_pointer
source_artifact_hash
```

Both decimals are finite base-10 strings and `scale` is finite and strictly positive.
If `missing_state` is not `PRESENT`, both decimals are absent rather than null or
fabricated.

### 5.5 Agent review record

Minimum fields:

```text
run_id
security_id
evidence_packet_hash
review_set_reason[]
mandatory_holding
status
report_valid
trade_eligible       # always false
influence_mode       # always report_only
typed_role_outputs[]
citations[]
tool_receipts[]
structured_output_hashes[]
model_provider
model_id
upstream_revision
prompt_bundle_hash
schema_version
latency_ms
resource_usage{}
error{}
artifact_hash
```

`FAILED`, `BLOCKED`, `DEGRADED`, `MISSING`, and unknown-schema states are terminal
non-success states. None may be inferred as `Hold`.

### 5.6 Operations evidence

The viewer may display this immutable lineage:

```text
target -> prepared order -> API preview -> broker event -> fill/fee/cash/lot/position reconciliation
```

Each link identifies its parent hash, masked account/environment identity, timestamp,
cutoff, schema, source, and terminal state. A broken link is `NON_ROUTABLE`. The viewer
cannot create or refresh any element in the chain.

## 6. Read-only API

Endpoint-specific schemas use this common run context:

```json
{
  "schema_version": "qme.ui.response.v1",
  "run_id": "...",
  "ui_snapshot_hash": "...",
  "analysis_as_of": "...",
  "generated_at": "... snapshot-derived, never request time ...",
  "run_status": "CORRUPT|CONFLICTING|UNSUPPORTED_SCHEMA|INVALID|MISSING|BLOCKED|STALE|DEGRADED|VALID",
  "completeness_status": "CONFLICTING|INCOMPLETE|COMPLETE",
  "content_hash": "...",
  "membership_snapshot_id": "...",
  "membership_hash": "...",
  "data_revision": "...",
  "strategy_config_hash": "...",
  "code_revision": "...",
  "source_policy_hash": "...",
  "data": {},
  "errors": []
}
```

Required endpoints:

| Endpoint | Purpose |
|---|---|
| `GET|HEAD /health/build` | Process and build identity; never claims run validity |
| `GET|HEAD /api/v1/runs` | Startup-generated local catalog of valid and quarantined snapshots |
| `GET|HEAD /api/v1/runs/{run_id}/{ui_snapshot_hash}/summary` | Counts, blockers, freshness and payload index |
| `GET|HEAD /api/v1/runs/{run_id}/{ui_snapshot_hash}/universe` | Complete official member rows |
| `GET|HEAD /api/v1/runs/{run_id}/{ui_snapshot_hash}/securities/{security_id}` | Identity, features, decisions, portfolio, agent and provenance detail |
| `GET|HEAD /api/v1/runs/{run_id}/{ui_snapshot_hash}/portfolio` | Canonical current/target/delta positions and constraints |
| `GET|HEAD /api/v1/runs/{run_id}/{ui_snapshot_hash}/risk` | Canonical exposures, capacity, turnover and risk states |
| `GET|HEAD /api/v1/runs/{run_id}/{ui_snapshot_hash}/agent-reviews` | Cross-ticker normalized report-only agent status |
| `GET|HEAD /api/v1/runs/{run_id}/{ui_snapshot_hash}/operations` | Existing preview and reconciliation evidence summaries |
| `GET|HEAD /api/v1/runs/{run_id}/{ui_snapshot_hash}/provenance/{artifact_id}` | Snapshot and producer lineage without private local paths |

Every run-scoped URL/response binds `(run_id, ui_snapshot_hash)`. A run ID alone never
selects data.

API acceptance:

- The same snapshot produces byte-identical canonical JSON regardless of discovery order.
- Hash mismatch, path escape, duplicate authority, changed-during-read bytes, incomplete
  payload inventory, incompatible schema, and semantic conflict fail closed.
- A malformed snapshot becomes a quarantine record containing discovery identifier and
  typed error only; its payload is not rendered and valid snapshots remain available.
- The compatibility matrix identifies every supported producer/snapshot schema and
  transformation.
- Route enumeration permits only `GET` and `HEAD` and finds no order, broker, model,
  agent-run, file-write, configuration-write, or Linear-write endpoint.
- Import and runtime tests prove the viewer has no provider, model, broker, order, or
  orchestration client.

## 7. Information architecture

### 7.1 Persistent run context

Every run screen displays:

- run ID, `ui_snapshot_hash`, and `analysis_as_of`;
- run quality and completeness status;
- membership snapshot, exact count, and set hash;
- data/config/source-policy/code revisions;
- snapshot generation time and freshness state;
- synthetic-fixture badge when applicable.

Changing run changes the entire application context. Cross-run mixing is prohibited.
The run list describes snapshots discovered in the configured local root; it does not
claim a complete adversarially protected run history.

### 7.2 Overview

Cards and summaries:

- membership: official denominator and six member-status counts;
- eligibility: rank-eligible, selected, mandatory holdings, union review set and blocked;
- portfolio: current holdings, target holdings, cash, turnover and constraint blockers;
- agent batch: requested, valid, degraded, failed, blocked and missing;
- operations: prepared, previewed, rejected, partially filled and unreconciled;
- freshness and incompatible-schema blockers.

Every count links to the exact constituent rows.

### 7.3 Universe table

Default columns:

| Group | Fields |
|---|---|
| Identity | rank, ticker, company, share class, sector, industry |
| Deterministic signal | 12-1 momentum, rank percentile, versioned score if present |
| Portfolio | current weight, target weight, delta weight, holding/pending badges |
| Eligibility | membership, data, feature, rank, selected, selection reason |
| Review | review reason, mandatory holding, agent state, separate agent rating |
| Evidence | freshness, event flags, row hash, provenance link |

Expanded rows show identity keys, versioned features, units/precision, missingness/tie/
eligibility policies, capacity/constraint states, source IDs, and producer/snapshot hashes.

Sort, filter, and search are presentation-only. The official denominator, canonical
ranks, selections, target weights, and review set never change. Active filters and both
filtered/full row counts remain visible.

### 7.4 Security detail

“Security” here means the financial instrument. Sections:

1. Identity and membership history.
2. Deterministic feature values, units, status, window/cutoff, definition and lineage.
3. Rank, eligibility, selection and target decisions with reason codes.
4. Position, weight, capacity, constraints and reconciliation state.
5. Evidence packet sources and derived-payload lineage.
6. Agent role outputs, citations, receipts, errors and resource use.
7. Artifact provenance.

Agent prose is escaped plain text. It cannot execute HTML, create controls, or determine
status styling.

### 7.5 Portfolio and risk

Display canonical artifacts for current/target/delta weights, cash, turnover, costs,
capacity, single-name/sector/liquidity/concentration constraints, benchmarks, exposures,
and target-to-order readiness.

The UI does not calculate authoritative totals from browser rows. Producer totals are
displayed; optional presentation sums are labeled derived and must reconcile under the
registered display policy.

### 7.6 Agent reviews

Separate deterministic review-set reasons and mandatory holdings from agent opinions.
Show strict typed role results, model/schema identities, citations, receipts, errors,
latency, and resource use. The page must state that agent ratings cannot affect score,
rank, eligibility, target, or order hashes.

### 7.7 Runs and provenance

Display discovered snapshots, manifest/payload hashes, schema compatibility, source
freshness, code/config/data/policy lineage, conflicts, quarantine reason, and snapshot
builder revision. Do not label a snapshot “latest” as an authority claim; sort and label
by artifact `analysis_as_of` and generation time.

### 7.8 Preview and reconciliation

Display existing target-to-broker evidence without control buttons. Distinguish API
preview, paper/simulated evidence, observational evidence, and actual broker events.
Stale preview, account/environment mismatch, unknown open order, rejection, partial fill,
corrected/busted event, API outage, and unreconciled quantity/cash are `NON_ROUTABLE`.

## 8. Quantitative display rules

### 8.1 No client-side strategy math

The producer owns momentum returns, winsorization, ranks, ties, eligibility, scores,
weights, turnover, costs, capacity, P&L, tax, benchmarks, and validation statistics.

The offline snapshot builder may perform only registered presentation derivations. The
browser may perform presentation sorting/filtering/search using opaque keys, layout,
accessibility behavior, and exact constituent row counts. It may not parse canonical or
display decimals or recreate a strategy value.

### 8.2 Rounding reconciliation

For canonical Decimal `x`, scale `s > 0`, display precision `d`, and projected display
Decimal `y`:

```text
abs((y / s) - x) <= 0.5 * 10^(-d) / s
```

The registered rounding policy defines half ties, negative zero, percent scale,
missing/overflow states, and exact `display_text`. The browser displays `display_text`
verbatim and never infers units from magnitude.

### 8.3 Completeness

For `completeness_status=COMPLETE`:

```text
unique(displayed_security_ids) = manifest_membership_security_id_set
membership_set_sha256(displayed_security_ids) = manifest.membership_hash
count(unique(displayed_security_ids)) = manifest.membership_count
sum(member_status_counts) = manifest.membership_count
```

Filtered tables may show fewer rows, but the full denominator and active filter remain
visible. Non-valid members are never dropped.

### 8.4 Deterministic-versus-agent separation

No formula combines deterministic score and agent rating in v0.1. Any future influence
requires a separately preregistered deterministic policy, quantitative validation, new
ticket, and new architecture decision.

### 8.5 Chart reconciliation

- Every chart point identifies its snapshot row/key and unit.
- Table and chart values match the same registered display policy.
- Missing values create gaps or explicit missing states, never zeros.
- Axis truncation and log scaling are visible.
- Filtering cannot change canonical aggregate cards.

Charts requiring numeric conversion remain deferred until a differential test proves the
conversion presentation-only.

## 9. Freshness and terminal states

Quality precedence:

```text
CORRUPT
CONFLICTING
UNSUPPORTED_SCHEMA
INVALID
MISSING
BLOCKED
STALE
DEGRADED
VALID
```

Completeness is separately `CONFLICTING`, `INCOMPLETE`, or `COMPLETE`.

| Condition | State |
|---|---|
| Hash, byte, or parser failure under a recognized manifest | `CORRUPT` |
| Duplicate authority, set/count contradiction, or same identity/different hash | `CONFLICTING` |
| Unsupported schema version or unknown schema | `UNSUPPORTED_SCHEMA` |
| Known schema with unknown enum, illegal field, or invariant failure | `INVALID` |

Unknowns never become `VALID`, zero, false, blank, or `Hold`. Every state has text and an
accessible label; color is supplementary.

## 10. Local operation and safe rendering

- Bind only `127.0.0.1`; reject wildcard or proxy-derived remote binds.
- Disable debug, reload, Flask development server, directory browsing, API docs, stack
  traces and server-version disclosure.
- Use only configured snapshot roots and bounded regular files; reject traversal and
  root escape.
- Read payload bytes once, hash and parse those same bytes, and never write to source or
  snapshot roots.
- Use only bundled assets; no CDN, remote fonts, telemetry or service worker.
- Keep Jinja autoescape enabled and forbid `|safe`, artifact-derived HTML/style/class,
  arbitrary links, and inline executable content.
- Treat model, provider, source, filename, ticker and company text as untrusted plain text.
- Apply a basic restrictive CSP and `X-Content-Type-Options: nosniff`.
- Never place secrets, raw tokens, full account IDs, or private absolute paths in a
  browser payload, error, or URL.
- Mask account/environment identity by default for safer screenshots and diagnostics.
- Import no Webull, provider, model, TradingAgents, Linear, Git or cloud client.
- Expose no comparison/export or mutation surface in v0.1.

The local owner account and filesystem are trusted. This section is a robustness and
safe-rendering contract, not a multi-user access-control claim.

## 11. Accessibility, performance, and compatibility

Accessibility target: WCAG 2.2 AA through keyboard operation, focus management,
semantic tables/headings, text alternatives, non-color status cues, zoom/reflow,
automated checks and registered manual Windows screen-reader/browser evidence.

Provisional performance hypotheses for up to 200 rows:

- read-only API p95 <=500 ms;
- initial local screen usable <=2 seconds;
- sort/filter response p95 <=100 ms.

They become gates only after the spike records hardware, OS, browser, corpus, method,
sample size, warm/cold state and raw distributions. Browser and viewport matrices must
be registered before visual snapshots become acceptance evidence.

## 12. Delivery stages and acceptance criteria

### Stage 0 — Contracts and fixtures

- Freeze snapshot and endpoint schemas, field/source-pointer map, Decimal display policy,
  membership hash, state/completeness rules, compatibility matrix, resource limits and
  benchmark protocol.
- Create valid and adversarial fixtures for all registered states and edge cases.

Acceptance: every UI field maps to a producer pointer or registered presentation
derivation; no orphan calculation exists.

### Stage 1 — Deterministic snapshot builder

- Validate producer manifests and payload hashes.
- Apply field mapping, Decimal formatting, redaction and exact membership reconciliation.
- Publish content-addressed snapshots atomically.

Acceptance: identical inputs produce byte-identical snapshots; every output traces to a
producer field/hash; invalid hash/schema/set/path cases fail closed.

### Stage 2 — Local catalog and API

- Implement bounded same-byte loading, per-snapshot quarantine, frozen read models,
  canonical JSON, route inventory and compatibility behavior.

Acceptance: 100 randomized discovery orders produce identical responses; valid runs
remain usable when another directory is corrupt; only `GET`/`HEAD` routes exist.

### Stage 3 — Shell, overview and universe

- Implement run selector/context, overview counts and complete accessible universe table.
- Add presentation-only search, sort, filter and deep links.

Acceptance: the exact manifest member set renders unfiltered; every displayed value
matches snapshot `display_text`; filters alter no canonical hash or total.

### Stage 4 — Detail, provenance, portfolio and risk

- Implement security detail, source lineage, portfolio, risk, constraints, capacity,
  costs, turnover and reconciliation summaries.

Acceptance: each displayed canonical value traces to snapshot field, producer pointer,
schema and hash; totals match producer totals before display formatting.

### Stage 5 — Report-only agent views

- Render union review reasons, mandatory holdings, strict typed results, citations,
  receipts, errors and resource metrics.

Acceptance: partial/duplicate/conflicting/unknown/invalid agent results fail closed and
never alter deterministic hashes or become `Hold`.

### Stage 6 — Preview and reconciliation evidence

- Render existing target/prepared/preview/broker/fill/reconciliation lineage and
  non-routable discrepancies.

Acceptance: route/import tests prove there is no preview request or order action;
fixtures cover stale preview, duplicate retry, rejection, partial fill, late correction,
wrong account/environment and unresolved reconciliation.

### Stage 7 — Local release qualification

- Complete deterministic, accessibility, browser, performance, dependency, SBOM,
  packaging and clean-machine checks on the exact commit.

Acceptance: required checks pass for the exact release revision and unresolved
quantitative, read-only, accessibility, or reproducibility defects block release.

## 13. Test matrix

| Domain | Required cases |
|---|---|
| Snapshot | missing root, manifest hash/directory mismatch, extra/missing/duplicate payload, hash mismatch, changed-during-read, unsupported schema, traversal/root escape, bounded-size/depth failure |
| Catalog | zero/one/many runs, same run/different snapshot conflict, corrupt directory quarantine, randomized discovery order, explicit restart for new run |
| Run | complete/incomplete/conflicting, six-bucket identity, stale/degraded/missing placeholders, unknown policy |
| Universe | full basket, equal-count/wrong-member set, duplicate/missing member, tie, missing score, unknown unit, precision edge |
| Numeric | positive/negative half ties, negative zero, percent scale, null/missing variants, very small/large finite values, overflow |
| Portfolio | missing target, cap breach, capacity block, cash mismatch, producer-total mismatch |
| Agents | valid typed batch, partial batch, invalid schema, unknown role, missing citation/receipt, injection text, duplicate result, free-text fallback |
| Operations | stale preview, account mismatch, duplicate retry, rejection, partial fill, correction/bust, API outage evidence, unresolved cash/quantity |
| Routes/imports | only GET/HEAD, no filesystem/config write, no run/agent orchestration, no provider/model/broker/order/Linear client |
| Browser | keyboard, focus order, screen-reader names, zoom/reflow, responsive layout, fixed visual snapshots, CSP/XSS payloads, numeric-conversion prohibition |
| Packaging | clean offline Windows, no Python/Node/Git, paths with spaces/non-ASCII, port collision, restart, no residual process |

## 14. Non-goals for v0.1

- Live quotes or streaming market data.
- Strategy editing or parameter optimization.
- Client-side quantitative calculations.
- Agent prompting, chat, rerun, retry, or decision influence.
- Broker preview requests or order placement/replacement/cancellation/confirmation.
- Remote hosting, multiple users, or shared untrusted artifact roots.
- Mobile trading controls.
- A blended quant/agent score.
- Cross-run comparison or export.

Any future expansion requires its own quantitative contract, architecture decision,
tickets and acceptance evidence.
