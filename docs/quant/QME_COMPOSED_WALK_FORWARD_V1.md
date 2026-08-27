# QME composed walk-forward V1 — one deterministic sequence of composed folds

## Authority and status

- `ticket_id`: **PENDING_OWNER_ASSIGNMENT** (composition ticket D under gate
  NEE-108, lead plan 2026-08-25).
- `KERNEL_ID`: `QME-COMPOSITION-COMPOSED-WALK-FORWARD-V1`.
- `SCHEMA_VERSION`: `qme.composed_walk_forward.v1`.
- Change tier: `T0_FROZEN_CONTRACT` (runtime + test); the fixture is
  `T2_ENGINEERING`; this document is `T3_DOCUMENTATION`.

This module is a **T0** orchestration lane living under `qme/experiments/`. It
introduces **no owner-gated value**: every registry it touches ships EMPTY and
fails closed with each engine's own typed state, and the tests thread
`TEST_CONSTRUCTED` records only. No result of this run is a production,
prospective-consumption, empirical-performance, alpha, capacity-value,
production-readiness, owner-registration, independent-review, position-continuity
readiness, **exact-lot-carry**, or live-order claim. Those non-claims are carried
verbatim as explicit `False` in the module's `NON_CLAIMS` and in the fixture's
`nonclaims` block — including `position_level_continuity_established: False` and
`exact_lot_carry_supported: False`. This lane threads the predecessor's cash /
positions(shares) / receivables / NAV closing state into the successor, but that is
a mechanical property of TEST_CONSTRUCTED inputs and is **not** a readiness claim;
exact tax-lot carry (cost basis + acquisition) is **not** supported at all, and a
position-bearing successor fails closed rather than assert it.

The lane **orchestrates** and never reimplements: it runs composition ticket C
(`composed_fold_v1.compose_fold`) once per fold and reuses NEE-134
(`walk_forward_v1`) for its publication and type-wall primitives. It re-derives
no engine quantity and no composed fold — every fold outcome is a **consumed**
`ComposedFoldResult`.

## What this lane adds over one fold

A single composed fold (ticket C) threads seven engines for **one** rebalance. A
composed walk-forward threads a **schedule-ordered sequence** of composed folds
and adds exactly four things:

1. **Schedule order.** Each `FoldSlot` carries a strictly-increasing
   `event_ordinal`; folds run in that order. A caller may present them shuffled —
   the driver content-sorts by ordinal before anything else, so identity is
   permutation-invariant.
2. **Cross-fold ledger-state carry.** The closing cash / holdings(shares) /
   receivables / NAV a fold **closes** on is the state the next fold **opens** on,
   over consumed engine figures. Exact tax-lot carry is **not** supported: a
   successor of a fold that closed holding non-empty lots fails closed
   (`BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED`).
3. **Hash-chained fold identities**, with the carried closing-state identity bound
   in alongside the predecessor NAV (the predecessor's `open_lots` are bound only as
   tamper-evidence).
4. **A run identity over bound inputs only** on the **one shared session axis**,
   plus **deep-frozen** results and **receipt-verified** atomic publication. Execution
   (`execute_composed_walk_forward`) returns only the read-only result;
   `run_and_publish_composed_walk_forward` is the **only supported public publication
   entry** — it takes a plan, runs the engines internally, mints a **private** receipt,
   and verifies the **staged bytes on disk** against it, so a staged-file edit after
   staging, a `run_id_hex` swap, or a content replace fails closed. A public-API caller
   **cannot publish caller-supplied** content. The private receipt is tamper-**evidence**,
   not an independent authority; arbitrary access to the underscored internals is **out of
   contract** (see **[Trust boundary](#trust-boundary-the-trust-preserving-public-boundary)**
   below).

## The ONE unified session axis

Every fold runs on the SAME declared **`SessionAxis`** — `calendar_id`,
`calendar_sha256_grouped`, `timezone`, `session_ids_sha256_grouped` (the real
accepted XNAS calendar). The plan binds it once; `assert_declared_calendar_witnesses_injected`
asserts the INJECTED calendar witnesses every axis field before any fold runs, with
a stable typed reason per class of disagreement, and `_assert_shared_session_axis`
refuses at plan construction any fold whose declared axis differs (a fold whose
boundary sessions live on a different axis):

| Disagreement | Typed state |
|--------------|-------------|
| injected calendar id / grouped hash ≠ declared | `BLOCKED_CALENDAR_BINDING_MISMATCH` |
| injected timezone ≠ declared | `BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH` |
| injected ordered session-vector digest ≠ declared | `BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH` |
| a fold declares a different axis (fold boundaries disagree) | `BLOCKED_FOLD_BOUNDARY_SESSIONS_DISAGREE` |

Per-fold, `compose_fold` additionally refuses a consumed boundary session that is
not a member of the shared vector (`BLOCKED_SESSION_NOT_ON_SHARED_AXIS`), surfaced
verbatim. There is no synthetic ledger calendar: each fold's execution program runs
on the schedule event's own real XNAS sessions.

## Cross-fold ledger-state carry (lots are NOT carried)

`compose_fold` exposes the execution engine's IMMUTABLE closing portfolio
(`ValidComposedFold.closing_portfolio`) and consumed opening portfolio
(`.opening_portfolio`). For consecutive folds *i → i+1* in schedule order the driver
enforces the carry over **consumed, engine-computed** figures in three gates, book
value first, then composition, then the lot gate:

```
# 1. BOOK VALUE  (preserves the 5e-7 declared-vs-consumed regression)
fold[i+1].ledger_figures["initial_nav"]  ==  fold[i].ledger_figures["final_nav"]

# 2. COMPOSITION  (cash + held positions(shares) + receivables)
opening_portfolio[i+1].cash           ==  closing_portfolio[i].cash
opening_portfolio[i+1].held_positions ==  closing_portfolio[i].held_positions
opening_portfolio[i+1].receivables    ==  closing_portfolio[i].receivables

# 3. LOT GATE  (exact lot carry is unsupported this cycle)
closing_portfolio[i].open_lots is empty     # else BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED
```

The book-value and composition sides are **consumed** `compose_fold` outputs — the
predecessor's `cash_plus` / `positions_plus` / `receivables_plus` / `final_nav`, and
the successor's consumed opening state / engine-computed `initial_nav`. Numeric
equality is over `Decimal` values (no arithmetic operator; an AST scan proves no
`* / // % **` and no float literal anywhere in the source). The carry does **not**
read the successor's tolerant `declared_pre_trade_nav`; that value keeps its own
**anti-spoof** role at the targets stage (`INVALID_PRE_TRADE_NAV_IDENTITY`, within
`targets_v1.PRE_TRADE_NAV_IDENTITY_TOLERANCE` `1e-6`) but is never the carry
surface.

The book-value check runs first so the **5e-7 declared-vs-consumed NAV regression**
still produces `BLOCKED_LEDGER_STATE_CARRY_BROKEN`. The composition check catches a
**NAV-preserving** tamper — a swapped, missing, duplicated, or otherwise altered
holding whose total book value still equals the close — that the book-value check
alone cannot: this is why the position carry is load-bearing.

**Exact tax-lot carry is NOT supported (owner-authorized fail-closed remediation).**
The cash / positions(shares) / receivables / NAV checks above are the mechanics that
DO hold up. But the predecessor's published `open_lots` reconcile with
`positions_plus` only **inside** the execution engine, which is read-only this cycle
and exposes **no incoming-lot interface**. So the successor cannot inherit exact lots
(shares + cost basis + acquisition): if the execution engine re-seeds lots at the
successor's opening marks, a link that reported `CARRY_CONTINUOUS` would be asserting
a lot continuity that never happened. The **lot gate** therefore fails a successor
whose consumed predecessor CLOSING state carries non-empty `open_lots` closed
(`BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED`) **before** the fold is admitted valid. A
pure **cash carry** (empty predecessor lots) is unaffected and may carry validly. The
predecessor's `open_lots` are bound into `carried_state_identity` only as
**tamper-evidence**, never as a carried/consumed lot.

**No lot-bearing multi-fold carry is valid this cycle.** With the lot gate, a
successor that inherits held positions degrades `BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED`
at the lot gate, and the SAME fold reopened **flat** (the whole book liquidated to
cash) degrades earlier at the position check (`BLOCKED_POSITION_STATE_CARRY_BROKEN`),
so neither is a valid published fold. The engine-computed turnover of the internally
valid (but degraded) inherited fold is still strictly lower than the flat reopening's
(gross-trade-notional **12.50** vs **10100.00**) — a documented mechanical property of
the consumed figures, not a claim of a valid carried fold.

Carry failures are typed and fail closed — never fabricated:

| Situation | Retained state |
|-----------|----------------|
| Successor valid in isolation but its consumed opening **NAV** ≠ predecessor's close | `BLOCKED_LEDGER_STATE_CARRY_BROKEN` |
| Successor valid in isolation, NAV witnesses the close, but its consumed opening **composition** (cash / positions(shares) / receivables) is missing, altered, reordered, duplicated, or otherwise incompatible with the close | `BLOCKED_POSITION_STATE_CARRY_BROKEN` |
| Successor's NAV and composition witness the close, but the predecessor CLOSED holding **non-empty tax lots** (exact lot carry is unsupported) | `BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED` |
| Predecessor produced **no** close (it degraded or was unauthorized) | `BLOCKED_PREDECESSOR_FOLD_DEGRADED_NO_CARRY` |

The first fold has no predecessor and opens on its own consumed engine-computed
initial state (`CARRY_GENESIS`). Every link records a `carry_state` from a closed
set (`CARRY_GENESIS`, `CARRY_CONTINUOUS`, `CARRY_BROKEN`, `CARRY_POSITION_BROKEN`,
`CARRY_LOT_CARRY_UNSUPPORTED`, `CARRY_PREDECESSOR_DEGRADED`, `CARRY_NOT_ATTEMPTED_*`)
and the predecessor's exact `carried_state_identity` (a grouped digest over the
carried cash+positions+lots+receivables+action state, bound as tamper-evidence,
`None` at genesis).

## Identity: bound inputs vs derived artifacts

- **`run_id`** = grouped SHA256 over the canonical **bound-input manifest ONLY**.
  The manifest binds `schedule_identity`, `modes`, the shared **`session_axis`**
  (id + hash + timezone + ordered-vector digest), the `sample_fold_ordinal`, the
  sorted `authorized_fold_ids`, `ordered_folds` (each `(event_ordinal, fold_id)` in
  schedule order), and the seven `engine_identities`. Each `fold_id` is
  `compose_fold`'s **own bound-input digest** — a bound input, never a derived
  artifact. The field set is asserted (`BOUND_INPUT_MANIFEST_FIELDS`), so a derived
  artifact cannot leak in.
- **`chain_head`, every `result_identity`, the carry links (including each
  `carried_state_identity`), and the closing/opening portfolios** are DERIVED and
  bind only into `result_identity_document()` — **never** back into the manifest.
  `run_id` is therefore computable from bound inputs **without running any fold**; a
  test proves it. This is why the carry is not circular: fold *i+1*'s declared
  opening portfolio is a legitimate bound input of fold *i+1* (it happens to equal a
  **different** fold's already-determined closing output — ordinary walk-forward
  threading), and no fold's `fold_id` depends on its own derived output.
- **Provenance** (a wall-clock `wall_clock_started_utc`) lives in a separate block
  excluded from every identity. A run under a different clock, timezone, or
  `PYTHONHASHSEED` reproduces the same `run_id`, `chain_head`, and
  `result_identity`; a subprocess test proves it. Content-derived ordering (sort
  by `event_ordinal`) makes the run invariant to any fold-order permutation; a
  shuffle test asserts the shuffle really reordered the container while the
  identities held.

Every run carries a four-part **lineage** (input = `run_id`, config = the
engine-identity digest, code = this module's grouped source hash, schema = the
schema-descriptor digest).

## The fold hash-chain

Folds run in schedule order and each produces one **chain link**:

```
chain[-1] = GENESIS_CHAIN_HASH
chain[i]  = SHA256( predecessor = chain[i-1],
                    link  = (event_ordinal, fold_id, partition_state),
                    fold_identity_material = { carried_state_identity,
                                               <result_identity | reason_codes> } )
```

Because each link binds its predecessor's hash, this fold's identity material,
**and the predecessor's exact carried closing-state identity**, tampering any fold,
changing the sequence, **or altering any carried field** changes that link and
every link after it. This is how the carry-state identity binds into the chain
(Part 4.3): a change to any carried cash/position/lot/receivable/action value
changes the predecessor's `carry_identity`, which changes the successor's link, its
`chain_head`, and the derived run identity. The `chain_head` (last link) commits to
the entire ordered sequence and binds into the derived result identity. A test
changes only a later fold's program id (leaving NAV and the carry intact) and shows
the head diverges while the untouched prefix link is byte-identical; another
recomputes the head purely from the ordered identities and shows a forged identity
(or a tampered carried-state identity) yields a different head.

## The partition type wall

Each fold becomes exactly one partition:

- `ValidComposedPartition` — the fold reached a valid composed result **and**
  continued the carry. Carries the consumed `ValidComposedFold` and its chain
  link.
- `DegradedComposedPartition` — the fold was unauthorized, or `compose_fold`
  refused (its verbatim reason retained), or the carry broke. Carries the typed
  reason codes and any composed result it produced.

The two are **disjoint frozen types** (no shared base). `aggregate_valid_partitions`
accepts only `Sequence[ValidComposedPartition]`, so passing a degraded partition
is a `mypy --strict` error; the wall is proven by an in-test mypy probe and
re-checked at runtime by both `aggregate_valid_partitions` and
`require_valid_partition`, which reuse NEE-134's verbatim
`BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE` state. A degraded partition can never
be coerced into the valid aggregate.

## Fail-closed posture

With every owner-gated registry shipped empty, no fold can reach valid: the
composed fold degrades at the first required engine
(`BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY`), surfaced **verbatim** through
this driver, and the run completes `…_NO_VALID_PARTITIONS`. The driver invents no
threshold, coefficient, carry, or production value; a degraded predecessor never
fabricates a carry for its successor. The valid path is exercised only through
`TEST_CONSTRUCTED` records injected via the engines' override seams.

## Reuse, not reimplementation

- **Ticket C** is orchestrated via `compose_fold`; this module imports **none** of
  the seven quant engines directly (an AST test asserts no `qme.quant.*` import)
  and re-derives no engine quantity.
- **NEE-134** supplies the publication mechanics. The private publish helpers
  `_stage_run` / `_commit_run` / `_publish_run` (driven only by
  `run_and_publish_composed_walk_forward`) delegate the load-bearing durability and
  confinement primitives — path confinement (`_lexical_within`), durable exclusive-create
  writes (`_write_file_durable`), directory fsync (`_fsync_directory`), recursive cleanup
  (`_remove_tree`) — to `walk_forward_v1`, and reuse its verbatim `BLOCKED_RUN_DIRECTORY_*`
  states. A test asserts those primitives are delegated and that none of the durability
  mechanics is copied into this module.
- **Local-only execution** reuses NEE-134's import-closure egress walk
  (`transport_modules_reachable`) starting from this module; the driver reaches no
  transport, and a probe importing a transport makes the guard refuse.

## Result immutability and the publication receipt

A completed `ComposedWalkForwardResult` cannot be mutated, and a tampered copy
cannot be published under a genuine identity — a deep-freeze plus a **private,
result-derived** publication receipt:

1. **Deep-freeze at construction.** Every published table row and every nested
   structure (carry records, lineage, closing/opening portfolio documents, chain
   links) is a recursive immutable mapping (`types.MappingProxyType` over private
   dicts, sequences as tuples). A caller cannot set, for example, a completed fold's
   `folds`-table `final_nav`; the assignment raises. `table_document(...)` still
   returns a mutable **deep copy** for serialization, so `dict(row)` copies work.
2. **The private publication receipt.** `execute_composed_walk_forward` returns
   **only** the read-only result — no caller-usable receipt. Publication is driven
   internally by `run_and_publish_composed_walk_forward` (the **only supported public
   publication entry**), which mints a **private** `_PublicationReceipt` from the GENUINE
   result. It holds the `run_id`, the **derived** `run_id_hex` (the sole authority for the
   published directory name — never a caller-supplied `result.run_id_hex` field), and the
   exact expected **grouped sha256 of the canonical bytes** each table file and
   `manifest.json` will contain. Publication verifies the **staged bytes on disk** against
   this receipt, catching any tamper that reaches the internals and retains the receipt: a
   staged-file edit after staging, a `run_id_hex` swap, and a content replace all fail
   closed. This is tamper-**evidence**, not an independent authority; see **Trust
   boundary** below.

Two typed states enforce this:

- **`BLOCKED_RESULT_IDENTITY_TAMPERED`** — the in-memory result presented to
  publication does not witness the receipt: its `run_id`, its bound-input manifest,
  its `run_id_hex` field (derived and rejected on disagreement), a per-table byte-hash,
  or the manifest byte-hash diverged.
- **`BLOCKED_STAGED_ARTIFACT_TAMPERED`** — the exact staged file set or the re-read
  staged bytes do not witness the receipt immediately before the atomic rename (a
  tampered staged table or manifest, a missing or extra file, or an on-disk manifest
  whose own bound per-table hashes disagree).

### Trust boundary: the trust-preserving public boundary

`run_and_publish_composed_walk_forward` is the **only supported public publication
entry**. It takes a plan, runs the engines internally, and returns the read-only result
plus the published path; it **never** accepts a caller-supplied result, run, or receipt.
No exported symbol lets a caller hand publication a result — the receipt type
(`_PublicationReceipt`), the publish bundle (`_ComposedWalkForwardRun`), and the
`stage_run` / `commit_run` / `publish_run` helpers are all private (underscored) and
absent from `__all__` — so a public-API caller **cannot publish caller-supplied**
content. The published output is strictly engine-derived, with no window to interpose a
result between execution and publication.

This closes the **supported-public-API** trust boundary; it does **not** claim in-process
cryptographic trust. The private receipt is a **deterministic function of the result** —
tamper-**evidence** for a genuine pair, **not an independent** execution-captured
authority. Arbitrary access to the underscored internals (constructing a
`_PublicationReceipt` / `_ComposedWalkForwardRun` and calling `_publish_run`) is **out of
contract**: in one deterministic in-process library there is no secret with which to bind
an authority a result-holder cannot reproduce, so protection against **malicious
same-process code** that reaches those internals requires a **separate trusted process or
external signing authority**, not an in-process seal.

Pinned by `test_p1_1_no_public_publisher_accepts_caller_supplied_content` (no exported
publisher accepts a result/run/receipt; the separable publishers, the receipt, and the
bundle are not exported), `test_finding1_atomic_run_and_publish_is_the_trust_preserving_path`
(the safe path admits no caller-injected result), and
`test_p1_1_public_publication_boundary_is_documented` (this documentation states the
boundary and names the safe path).

## Publication

Publication is atomic, no-clobber, and confined to a caller-supplied runs root,
exactly as NEE-134, and is driven only by `run_and_publish_composed_walk_forward` via the
private `_stage_run` → `_commit_run` machinery over a private `_ComposedWalkForwardRun`
bundle. `_stage_run` first **asserts the in-memory result witnesses the receipt**
(`BLOCKED_RESULT_IDENTITY_TAMPERED` otherwise), derives the run directory name
`run-<derived run_id hexdigest>` from the receipt, confirms **both** the staging directory
and the final directory resolve strictly inside the runs root
(`BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT` otherwise, rejecting a symlink/`..` escape; each
artifact filename is a plain basename confined to the staging directory), and writes every
table file and the manifest into a private staging directory with durable writes.
`_commit_run` re-asserts the receipt, re-checks both confinements, then **verifies the
EXACT staged file set and RE-READ staged bytes against the receipt**
(`BLOCKED_STAGED_ARTIFACT_TAMPERED` otherwise — so a staged file edited after staging
publishes nothing), refuses if the final directory already exists
(`BLOCKED_RUN_DIRECTORY_EXISTS`, so a rerun never mutates a run), and otherwise publishes
with a single atomic rename followed by a root fsync.

## Output tables

Three tables, each row carrying a `lineage` block (`run_id`, `fold_id`, source
role and id, source self-hash):

- `folds` — one row per valid partition, a verbatim projection of the consumed
  composed fold (event consumed, selected count, ledger/scenario/benchmark
  identities, `ledger_figures`, the immutable **closing portfolio** and consumed
  **opening portfolio**, the `carry_state` and `carried_state_identity`,
  `result_identity`, the fold's grouped self-hash).
- `carry_chain` — one row per fold in schedule order: predecessor chain hash,
  this chain hash, carried-in NAV (the fold's **consumed** engine-computed
  `initial_nav`, or its declared opening when the fold never ran — unauthorized or
  engine-degraded), predecessor closing NAV, the predecessor's
  `carried_state_identity`, carry state.
- `warnings_errors` — one row per degraded partition with its verbatim reason
  codes and, when the composed fold itself refused, that engine's degraded stage
  and reason.

The manifest records, per table, the row count and the table's own grouped
digest; binds the seven engine identities; and declares the orchestrated composed
fold's kernel id and schema version.

## Reviewed risk surfaces (a)–(h)

| # | Surface | Guarantee | Named test |
|---|---------|-----------|------------|
| a | book-value carry correctness | **consumed** opening NAV threaded predecessor-close → successor-open, ledger-to-ledger (not the tolerant declared proxy; a non-canonical declared opening on the exact book value satisfies the book-value carry and reaches the lot gate) | `test_the_carry_mechanics_hold_but_a_lot_bearing_successor_degrades_lot_unsupported`, `test_carry_is_enforced_on_the_consumed_opening_not_the_declared_proxy`, `test_noncanonical_declared_opening_on_the_exact_book_value_reaches_the_lot_gate`, `test_a_successor_whose_consumed_opening_nav_differs_degrades_nav_carry_broken` |
| a′ | position(shares)-level carry (Part 4) | the successor opens on the predecessor's exact held positions(shares) + cash + receivables; a NAV-preserving tamper (missing / altered / reordered / duplicated) or a flat reopening is caught | `test_the_carry_mechanics_hold_but_a_lot_bearing_successor_degrades_lot_unsupported`, `test_the_flat_reopening_preserves_nav_but_loses_positions_and_degrades`, `test_a_nav_preserving_carry_tamper_degrades_position_state_carry_broken`, `test_the_position_carry_is_load_bearing_a_nav_only_check_would_pass_the_tamper`, `test_the_carry_state_identity_is_bound_into_the_successor_chain_link` |
| a-lot (P1-2) | **exact tax-lot carry unsupported (fail-closed)** | a position-bearing successor (predecessor closed holding non-empty `open_lots`) fails closed `BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED` rather than claim `CARRY_CONTINUOUS`; no lot-bearing multi-fold carry is valid this cycle; `open_lots` bound only as tamper-evidence | `test_the_carry_mechanics_hold_but_a_lot_bearing_successor_degrades_lot_unsupported`, `test_a_position_bearing_successor_of_the_genesis_fold_degrades_lot_unsupported`, `test_no_lot_bearing_multi_fold_carry_is_valid_this_cycle`, `test_the_incoming_lot_carry_unsupported_state_is_registered_and_typed` |
| a-bench (P1-3) | **benchmark capital alignment** | the genesis fold's benchmark control opens on the SAME initial capital (the fold's opening NAV), so its consumed initial NAV equals the strategy fold's; a mis-capitalized control is refused/degraded | `test_the_genesis_folds_benchmark_is_capital_aligned_to_its_opening_nav` (+ ticket-C `test_benchmark_control_is_capital_aligned_to_the_strategy_opening_nav`, `test_a_miscapitalized_control_degrades_benchmark_capital_not_aligned`) |
| a-seal (P1-1) | **result immutability + private publication receipt** | a completed result's published rows are deep-frozen (mutation raises); the publish driver (`run_and_publish_composed_walk_forward`) mints a separate frozen private `_PublicationReceipt` and publication verifies the in-memory result and the STAGED BYTES against it, refusing `BLOCKED_RESULT_IDENTITY_TAMPERED` on a tampered result and `BLOCKED_STAGED_ARTIFACT_TAMPERED` on a tampered staged file/set, before any write | `test_a_completed_results_published_table_row_cannot_be_mutated`, `test_a_completed_results_lineage_provenance_and_manifest_are_immutable`, `test_publishing_a_tampered_folds_table_copy_refuses_result_identity_tampered`, `test_publishing_a_tampered_nested_carry_record_refuses`, `test_publishing_a_forged_manifest_lineage_copy_refuses_result_identity_tampered`, `test_publishing_a_swapped_engine_binding_copy_refuses_result_identity_tampered`, `test_an_untouched_run_still_stages_verifies_and_publishes`, `test_commit_run_reverifies_the_result_against_the_receipt_before_the_rename`, `test_wall_clock_is_outside_the_run_identity_but_lives_in_the_manifest` |
| a-vectors (P1-1, as specified) | **the three demonstrated bypass vectors — with the genuine receipt retained — fail closed** (now exercised over the **private** publish machinery; the public API exposes no publisher) | (1) a staged file edited on disk after staging (plus a missing / extra staged file, and a tampered staged manifest) refuses `BLOCKED_STAGED_ARTIFACT_TAMPERED` and publishes nothing; (2) a `run_id_hex` swapped to 64 zeroes never publishes under `run-000…000` — the derived hex is authority and the swap refuses; (3) a content replace carried with the **retained** genuine receipt refuses `BLOCKED_RESULT_IDENTITY_TAMPERED` because the tampered content no longer witnesses it, and there is no on-result seal field to re-mint | `test_vector1_editing_a_staged_table_on_disk_after_staging_refuses_and_publishes_nothing`, `test_vector1_a_missing_staged_file_refuses_staged_artifact_tampered`, `test_vector1_an_extra_staged_file_refuses_staged_artifact_tampered`, `test_vector1_tampering_the_staged_manifest_refuses_staged_artifact_tampered`, `test_vector2a_swapping_run_id_hex_to_zeroes_never_publishes_under_run_zeroes`, `test_vector2b_content_replace_with_genuine_receipt_refuses_and_has_no_seal_field` |
| a-boundary (P1-1 closure) | **the trust-preserving public boundary: no public API publishes caller-supplied content** | `run_and_publish_composed_walk_forward` is the only supported public publication entry (takes a plan, runs the engines internally, returns the read-only result plus the published path, never a caller-supplied result/run/receipt); the receipt type, the publish bundle, and `stage_run`/`commit_run`/`publish_run` are private and absent from `__all__`, so a public-API caller cannot publish caller-supplied content. This closes the supported-public-API boundary; it does not claim in-process cryptographic trust — arbitrary access to underscored internals is out of contract (needs a separate trusted process or external signing authority, not an in-process seal) | `test_p1_1_no_public_publisher_accepts_caller_supplied_content`, `test_finding1_atomic_run_and_publish_is_the_trust_preserving_path`, `test_p1_1_public_publication_boundary_is_documented` |
| a″ | unified session axis (Part 5) | one shared XNAS axis; id/timezone/vector/fold-boundary mismatch each fails typed before any publish; real sessions, no second calendar | `test_calendar_binding_mismatch_is_refused`, `test_session_axis_timezone_mismatch_is_refused_before_any_fold`, `test_session_axis_session_vector_mismatch_is_refused_before_any_fold`, `test_a_fold_on_a_different_session_axis_disagrees_at_the_boundary`, `test_the_run_binds_the_full_shared_session_axis_and_real_sessions` |
| b | hash-chain predecessor binding / tamper | each link binds its predecessor and carried-state identity; a later change moves the head, not the prefix | `test_each_chain_link_binds_its_predecessor_hash`, `test_tampering_a_later_fold_changes_the_head_but_not_the_stable_prefix`, `test_the_chain_head_recomputes_from_the_ordered_fold_identities` |
| c | non-determinism leak into run_id / identity | clock/TZ/hashseed-invariant; wall clock only in provenance; fold-order permutation invariant | `test_run_id_chain_head_and_result_identity_are_clock_tz_and_hashseed_invariant`, `test_wall_clock_lands_only_in_provenance_never_in_any_identity`, `test_fold_order_permutation_does_not_change_identity_and_the_shuffle_reordered` |
| d | compose_fold / publication reimplemented vs called | ticket C orchestrated, NEE-134 primitives delegated; no engine or carry arithmetic | `test_the_module_orchestrates_composed_fold_and_imports_no_engine_directly`, `test_publication_reuses_walk_forward_primitives_and_reimplements_none`, `test_no_engine_or_carry_arithmetic_in_the_module` |
| e | publication atomic / no-clobber / confinement | atomic readback, no-clobber rerun, root confinement, interrupt-safe | `test_publication_is_atomic_and_readback_matches`, `test_rerun_never_mutates_an_existing_run_directory`, `test_publication_is_confined_to_the_runs_root`, `test_interruption_before_publish_leaves_no_final_directory` |
| f | empty-registry fail-open / fabricated degraded carry | verbatim engine states; degraded predecessor never fabricates a carry | `test_all_empty_registries_degrade_every_fold_verbatim_and_never_valid`, `test_a_degraded_predecessor_cannot_fabricate_a_carry` |
| g | degraded partition coerced into valid aggregate | disjoint types; mypy + runtime wall | `test_the_degraded_partition_wall_is_enforced_statically_by_mypy`, `test_aggregate_valid_runtime_guard_rejects_a_degraded_partition` |
| h | derived artifact declared as bound input (run_id circularity) | manifest field-set excludes derived; run_id computable without running a fold | `test_bound_input_manifest_field_set_excludes_every_derived_artifact`, `test_run_id_is_computable_from_bound_inputs_without_running_any_fold` |

## Files

| File | Tier | Purpose |
|------|------|---------|
| `qme/experiments/composed_walk_forward_v1.py` | T0 | the orchestration module |
| `tests/experiments/test_composed_walk_forward.py` | T0 | acceptance tests |
| `tests/fixtures/experiments/composed-walk-forward-v1.json` | T2 | the pinned `TEST_CONSTRUCTED` inputs and golden facts |
| `docs/quant/QME_COMPOSED_WALK_FORWARD_V1.md` | T3 | this document |

`composed_fold_v1.py`, `walk_forward_v1.py`, and the seven engine modules under
`qme/quant/` are READ-ONLY inputs: they are imported and hashed, never modified.

## Gates

`tests/quant tests/data tests/architecture tests/foundation/test_repository_policy.py tests/experiments`
· `ruff check` · `mypy` · `python -m qme.foundation.change_tiers .` · `git diff --check`.
