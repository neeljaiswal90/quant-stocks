# NEE-134 — Deterministic Walk-Forward Backtest Driver (V1)

Status: `REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE` · Change tier: `T1_ACCEPTED_KERNEL`
· Review status: `PENDING_INDEPENDENT_REVIEW` · Reviewer identity: none recorded.

Runtime: `qme/experiments/walk_forward_v1.py`. Tests:
`tests/experiments/test_walk_forward_driver.py`. Pinned inputs and golden anchors:
`tests/fixtures/experiments/walk-forward-v1.json`.

This document describes an engineering harness over synthetic, test-only inputs.
It does not demonstrate alpha, establish capacity value, measure empirical
performance, record an owner registration or independent review, authorize a
live trading order, or assert deployment readiness. Every such non-claim is carried as an
explicit `False` in `NON_CLAIMS` and in the fixture's `nonclaims` block.

## 1. Objective

Execute the deterministic walk-forward engine from local, pinned inputs and
publish immutable, replayable outputs. The driver **orchestrates** the five M2
engines and never re-implements any of their logic:

| Role | Module | Identity |
|---|---|---|
| universe | `qme.quant.universe_v1` | `QME-NEE133-POINT-IN-TIME-BROAD-UNIVERSE-BUILDER-V1` |
| signal | `qme.quant.signal_v1` | `QME-NEE131-SIGNAL-RANK-SELECTION-ENGINE-V1` |
| execution | `qme.quant.execution_v1` | `QME-NEE129-RAW-PRICE-EXECUTION-SELF-FINANCING-ENGINE-V1` |
| scenarios | `qme.quant.scenarios_v1` | `QME-NEE132-…-CAPACITY-SCENARIO-ENGINE-V1` |
| benchmarks | `qme.quant.benchmarks_v1` | `QME-NEE130-BENCHMARK-ABLATION-CONTROLS-ENGINE-V1` |

Each engine is invoked at exactly one explicit call site
(`run_universe_stage`, `run_signal_stage`, `run_execution_stage`,
`run_scenarios_stage`, `run_benchmarks_stage`). The engines are read-only inputs
to this lane; where one is wrong it is reported, never patched.

## 2. Run identity

```
run_id = SHA256(canonicalized input manifest)
```

The canonical input manifest is `BoundInputs.identity_material()` — a dictionary
serialized by the repository canonical JSON encoder (`sort_keys=True`, one
trailing newline). `run_id` is rendered as eight lowercase 8-hex groups joined by
colons; the underlying digest is the SHA-256 of the exact manifest bytes.

The manifest binds every bound input class, and only bound input classes:

1. `walk_forward_engine_version`
2. `repository_commit`
3. `dirty_worktree` (the worktree dirty flag)
4. `config_sha256_grouped` — the frozen owner-gated config/spec (the registry bundle)
5. `schema_sha256_grouped` — the driver, output, and engine schema versions
6. `data_manifest_sha256_grouped` — the versioned, content-addressed data manifests
7. `initial_state_sha256_grouped` — the initial portfolio state per fold
8. `sample_fold_id`
9. `authorized_fold_ids` — the sorted set of folds authorized to become valid
   partitions; it shapes which folds are run and which are retained degraded, so
   two runs that authorize different folds are distinct runs and must not collide
10. `share_mode` (execution mode)
11. `regulatory_fee_mode`
12. `cost_policy_id` (cost mode)
13. `transaction_tax_policy_id` (tax mode)
14. `transaction_tax_policy_sha256_grouped`
15. `benchmark_control_ids`
16. `calendar_id`
17. `calendar_sha256_grouped`
18. `engine_bindings` — each orchestrated engine's `(id, schema_version)`

A test parametrized over `bound_input_field_names()` mutates each field in turn
and asserts the `run_id` changes; every one of the eighteen classes is
identity-bearing.

The declared `calendar_id` / `calendar_sha256_grouped` are not taken on trust:
`assert_declared_calendar_witnesses_injected` refuses the run
(`BLOCKED_CALENDAR_BINDING_MISMATCH`) unless the separately-injected trading
calendar's own `calendar_id` and grouped byte-hash equal the declared values, so
the calendar identity bound into `run_id` is a witness of the calendar that
actually computed the signals rather than a caller assertion.

### Timestamps never enter identity

No engine reads a clock, and the driver reads one only through the injected
`clock` callable, whose value lands solely in a `provenance` block. The
`provenance` block is excluded from `identity_material()`, from
`result_identity_document()`, and from every output-table document. A run under a
different clock or timezone therefore yields a byte-identical `run_id`,
`result_identity_sha256_grouped`, and set of table digests; only the recorded
wall-clock string differs. This is proven directly in
`test_clock_and_timezone_variation_never_change_identity`.

### Content-derived ordering and permutation invariance

Every unordered input collection (signal cross-section rows, universe candidates,
required listings, liquidity evidence) is content-sorted before its digest is
taken, exactly as the engines sort internally. A shuffle of any such collection —
and of the fold order — leaves the `run_id`, the result identity, and all table
digests unchanged; the shuffle test asserts the collections were in fact
reordered before checking invariance. Inherently ordered sequences (execution
stages, the session axis) are not reordered.

## 3. Partitions, the type wall, and aggregation

Folds run in a deterministic, content-derived order. Each fold produces exactly
one partition:

* `ValidPartition` — every REQUIRED stage (`universe`, `signal`, `execution`,
  `scenarios`) produced a typed-OK result. It carries the engine outputs and the
  bound engine identities.
* `DegradedPartition` — at least one REQUIRED stage refused, or the fold was not
  authorized. It carries the typed reason codes and stage outcomes.

The two types are disjoint — they share no base class. `aggregate_valid` accepts
only `Sequence[ValidPartition]`, so passing a `DegradedPartition` is a
`mypy --strict` type error; the wall is proven by an in-test mypy probe
(`test_the_degraded_partition_wall_is_enforced_statically_by_mypy`) and re-checked
at runtime by both `aggregate_valid` and `require_valid`. A degraded partition is
retained with its reason codes in the `warnings_errors` table and can never be
coerced into the valid aggregate.

A fold degrades — it never aborts the run — no matter how its REQUIRED stage
refused. Each stage call site converts the orchestrated engine's typed refusal
into a blocked stage outcome, **including refusals an engine surfaces from a
lower store unchanged**: the signal engine deliberately passes through the
calendar store's own states (a malformed date, a date outside coverage, a
non-session date, an offset that leaves coverage, a missing calendar) as
`MarketStoreError` rather than renaming them to `SignalError`, so `run_signal_stage`
catches that surfaced error too and degrades only the offending fold. A bad input
in one fold therefore leaves its sibling folds untouched;
`test_a_malformed_fold_signal_input_degrades_only_that_fold` drives an engine
raising mid-fold and asserts the sibling still produces a valid partition.

Benchmark controls are comparison artifacts, not part of the strategy's own
accounting: a benchmark refusal is retained as a control warning and does not, by
itself, degrade a partition. Every REQUIRED stage must still succeed.

## 4. Fail-closed posture

The driver threads the engines' owner-gated registries through a `RegistryBundle`
whose fields default to the engines' **shipped EMPTY** registries. With the
defaults, a real run fails closed with the engines' own typed states — the
universe threshold registry is the first REQUIRED gate to refuse
(`BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS`), and no partition is ever valid
(`WALK_FORWARD_RUN_COMPLETED_NO_VALID_PARTITIONS`). The driver invents no
threshold, coefficient, or production value; it surfaces each `BLOCKED_*` state
verbatim with the affected identity from the engine error's `to_json_dict()`.

The valid-orchestration path is exercised only through `TEST_CONSTRUCTED` records
injected via the engines' explicit override seams and locally-pinned,
content-addressed fixtures. The empty-registry states are enumerated in the
fixture's `registry_state_under_test` block and are asserted by the fail-closed
tests. Four preconditions each prevent execution as valid, each with its own
test: a missing required hash, missing required data, an unauthorized fold, and a
missing registered threshold.

## 5. Local-only execution and network denial

`qme.experiments` is a research package that the repository import-boundary suite
(`tests/architecture/test_import_boundaries.py`) forbids from importing any
network client or network standard-library module, directly or transitively. The
driver adds a runtime proof: `assert_network_egress_denied` walks the driver's own
first-party import closure and refuses the run
(`BLOCKED_NETWORK_EGRESS_REACHABLE`) if any transport is reachable. The driver
reaches none, so the guard passes; a probe module that imports a transport is
shown to make the guard refuse.

The operative local-only guarantee is this structural egress proof. The driver
consumes pre-materialized, already-typed engine inputs that carry no data-locator
fields, so there is no consumed locator for the run path to resolve;
`assert_local_input_locator` is therefore a defensive guard for a future
composition root that threads raw string locators, not a step on this driver's
execution path. It refuses any locator naming a non-local scheme with
`BLOCKED_NON_LOCAL_INPUT_LOCATOR`, proven in isolation by
`test_non_local_input_locator_is_refused`.

## 6. Publication

Publication is atomic, no-clobber, and confined to a caller-supplied runs root.
The discipline mirrors the raw-pull store (`qme/data/alpha_vantage/store.py`) and
the foundation manifest writer:

1. `stage_run` writes every table file and the manifest into a private staging
   directory, flushing and `fsync`-ing each file and then the directory. Nothing
   appears at the final run directory yet, so an interruption before the commit
   leaves only the staging directory — never a partial published run.
2. `commit_run` confirms the final directory resolves inside the runs root
   (`BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT` otherwise), refuses if it already exists
   (`BLOCKED_RUN_DIRECTORY_EXISTS`, so a rerun never mutates an existing run), and
   otherwise publishes with a single atomic directory rename followed by a root
   `fsync`.

The run directory is named `run-<sha256 hexdigest of the input manifest>`, a
filesystem-safe rendering of the same digest the grouped `run_id` renders.

## 7. Outputs

Each valid partition is projected — never recomputed — into the named output
tables: `signal_rank`, `universe_rows`, `universe_coverage`, `nav`, `cash`,
`receivables`, `holdings`, `targets_orders_fills`, `lots`, `actions`,
`session_close`, `costs`, `turnover`, `capacity`, `benchmarks`, and the
driver-owned `warnings_errors`. Every row carries a `lineage` block naming the
`run_id`, the `fold_id`, the source engine role and id, and that engine's grouped
self-hash. The manifest records, per table, the row count, the table's own grouped
digest, and its source roles; and it lists every bound engine identity per fold.
`test_every_output_table_resolves_to_the_manifest_and_source_hashes` re-derives
each table digest and checks that every engine-sourced row cites a self-hash bound
in the manifest.

## 8. Scope notes

* The KAT fold assembles each engine's inputs from its own proven known-answer
  values. The driver binds each engine's pinned inputs independently and does not
  re-derive cross-engine session coherence; wiring a single coherent session axis
  across all five engines is the responsibility of a future composition root, not
  of this identity-and-publication harness.
* The benchmark control stage is held at its shipped fail-closed posture in the
  KAT (the control registry is empty), so the `benchmarks` table records the
  control refusal as a warning. The benchmark engine's own valid path is covered
  by its NEE-130 suite, which this lane's gates run unchanged.

## 9. Gates

`tests/quant tests/data tests/architecture tests/foundation/test_repository_policy.py tests/experiments`
· `ruff check` · `mypy` · `python -m qme.foundation.change_tiers .` · `git diff --check`.
