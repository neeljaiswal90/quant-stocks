# QME Experiment Registry v1

Status: `COMMITTED_UNVERIFIED` implementation slice for NEE-122. Production statistical
activation is `BLOCKED_UNREGISTERED_FAMILY_AND_DEPENDENCE_METHOD`.

This contract defines the deterministic, append-only experiment registry used to count
research degrees of freedom. It is a governance ledger, not a backtest engine, promotion
decision, source of market data, or authentication mechanism.

## 1. Authority and boundaries

The only mutable action is appending a new immutable event. Current trial state, counts, and
exports are replayed projections. No event may be edited or deleted. A correction creates a
new trial or registry version; it does not rewrite history.

The authoritative chain lives outside the repository under the configured QME data root:

```text
governance/experiment_registry/<registry_id>/
  ledger.lock
  events/
    <20-digit-sequence>-<event-sha256>.json
  exports/
    <head-event-sha256>.registry-export.json
```

There is no mutable `HEAD` file. The sole head is the last member of the one contiguous,
validated chain. JSONL, a mutable JSON document, SQLite, or a directory listing are not
authoritative substitutes. SQLite may be added only as a disposable projection keyed by the
verified registry head.

This local, single-user contract uses SHA-256 for identity, replay integrity, and
reproducibility. It does not claim signer identity, hostile-user tamper resistance, regulatory
non-repudiation, or protection from a user or process that can replace the entire ledger and
all previously recorded heads.

## 2. Canonical event identity

Every event uses `qme.foundation.canonical_json.v1`, which is the exact output of
`qme.foundation.lineage.canonical_json_bytes`: sorted JSON object keys, compact separators,
UTF-8, non-ASCII preserved, non-finite numbers rejected, and one final newline.

For an event document `E`, remove only `event_hash` to obtain `E_without_hash`. Then:

```text
event_hash = SHA256(
  UTF8("QME_EXPERIMENT_REGISTRY_EVENT_V1\0")
  || canonical_json_bytes(E_without_hash)
)
```

Sequence 1 references 64 zeroes. Every later event references the immediately preceding
event hash. The filename sequence and hash must equal the document. Raw file bytes must equal
the canonical bytes of the parsed document. Replay rejects gaps, duplicates, forks,
noncanonical bytes, unexpected files, unknown fields, or a mismatched previous hash.

`event_id` is an idempotency key. An exact retry with the same event ID and exact canonical
event content returns the existing event. Reusing an event ID for different content is an
error.

All IDs must already be Unicode NFC, must be nonempty bounded strings, and may not contain
path separators. Mathematical decimals, if introduced in a future schema version, must be
canonical finite base-10 strings rather than binary JSON floats.

## 3. Causal lifecycle

The registry sequence is the causal authority; wall-clock timestamps are annotations. A
timezone-aware timestamp that moves backward is disclosed as a clock anomaly but cannot
reorder events. Causal timestamps are nevertheless enforced: registration must not follow a
run start, embedded sample access must not follow its registry binding, and an outcome must not
precede registration, the current run start, or any successful access available to that run.

The v1 trial lifecycle is:

```text
TRIAL_REGISTERED -> TRIAL_STARTED -> TRIAL_COMPLETED
                                  -> TRIAL_FAILED
                                  -> TRIAL_ABANDONED
```

`SAMPLE_ACCESS_BOUND` and `OUTCOME_RECORDED` are allowed only after `TRIAL_STARTED` and before
the terminal event. Completion requires every prospectively frozen planned outcome. A terminal
trial is immutable.
The parent or superseded trial must already exist. Since a parent is earlier in the global
sequence, a parent cycle is impossible.

Every trial in one policy family must be registered before that policy's first
`TRIAL_STARTED` event. That first execution records an immutable `family_frozen_sequence` with
cause `FIRST_TRIAL_STARTED`; later registrations under the policy are rejected, including after
sample access but before an outcome is written.

An access binding contains the complete validated NEE-121 global chain plus the access-contract,
head, and trial-registration hashes. The first binding begins at genesis. Every later binding
must be a strict exact-prefix extension of the one acknowledged chain, and its new suffix must
belong to the current trial/run. Causal parent and child events must agree on trial, run, query,
request, window, cutoff, vintage, purpose, contract v1, and artifact evidence. Every bound event
must match a registered sample window and contain exactly the trial's frozen DATA and UNIVERSE
bindings plus the NEE-122 trial-registration-event binding; its vintage hash must be a frozen DATA
hash. An outcome names the exact previously bound `ACCESS_SUCCESS` hash or hashes it consumed.

The v1 full-chain embedding is bounded by the event-size limit and is not a scalable
inclusion-proof design; production activation requires replacement by a content-addressed chain
export or an equivalently verified inclusion proof. Outcome events bind immutable output
artifacts and their frozen plan IDs. A technical retry remains under the same trial only when the
registered specification and exposed research choices are unchanged. It creates a unique run ID,
requires a nonempty reason, and marks the prior run
`TECHNICAL_RETRY_SUPERSEDED`; otherwise the retry is a new trial with explicit lineage.

Registration freezes, at minimum, family/hypothesis identity, owner, parent/supersession,
repository commit and dirty-state evidence, config/schema/data/universe identities, planned
sample windows, signal, holding, rebalance, filters, costs, tax, benchmarks, selection rule,
agent/model overlay (`NONE` when absent), and grid/off-grid classification.
The v1 outcome plan contains exactly one output per registered cost scenario. Each output declares
`PRIMARY_SELECTION`, `REPORTING_ONLY`, or `UNREGISTERED_BLOCKER`; the role must agree with the
registered cost-selection policy and prospectively names the exact sample-window IDs it requires.
An outcome's cited successes must come from the current run and cover that exact window set—no
missing or extra window is accepted. Every successful access exposed anywhere in the trial,
including a superseded technical-retry run, must belong to that same set.
Window IDs remain provenance labels: semantic duplicate detection resolves them to the full
classification/start/end/access-mode/analysis-cutoff/vintage-time/vintage-hash tuples. Extra
metrics or benchmarks cannot enter the frozen 96/288 family without a later policy version that
explicitly expands multiplicity.

## 4. Counting mathematics

The ticket-specified structural arithmetic is:

```text
S = |L| * |H| * |R| * |F|
  = 4 * 3 * 2 * 4
  = 96 structural configurations

O = S * |C|
  = 96 * 3
  = 288 reported cost outputs
```

These do not, by themselves, determine a production hypothesis family. The registry exports
separate quantities:

- `structural_configuration_count`: the registered Cartesian cells;
- `reported_output_count`: structural cells multiplied by reported cost views;
- `selection_hypothesis_count_m`: hypotheses actually eligible to influence selection under a
  preregistered family and selection rule;
- `registered_trial_count`: all registered on-grid and off-grid/manual trials;
- `execution_run_count`: all execution attempts, including reasoned technical retries;
- `minimum_exposed_selection_opportunity_count`: the explicit `PRIMARY_SELECTION` plans plus
  every off-grid primary-selection plan;
- `effective_trial_estimate`: a separately registered dependence-model output.

If exactly one cost treatment is selected prospectively and other cost views cannot affect
ranking, exclusion, tie-breaking, promotion, or narrative selection, a registered policy may
define one selection unit per structure. If cost views can affect any selection decision, the
policy must count a selection unit per structure and cost view. Neither rule is selected by
the current production configuration because the production cost and selection registrations
are absent.

Holm uses the registered family size `m`; it never uses `N_eff`. DSR may use `N_eff` only from a
prospectively registered dependence estimator, implementation hash, input policy, and frozen
trial-return matrix. Independence is never the default. Failed, abandoned, skipped, off-grid,
and unavailable trials remain visible and cannot disappear from the ledger. Every trial under a
successor policy must name a parent in the immediately preceding policy and preserve the family
and hypothesis identity. A repeated specification must additionally name its latest matching
trial as parent. Cumulative family disclosure spans all policy versions and cannot reset `m`
through coordinate, family, hypothesis, or policy relabeling.

Only the four filter IDs below have source-controlled identities in the current quantitative
contract:

```text
NONE
QQQ_TR_SMA_14
QQQ_TR_SMA_200
SPY_TR_SMA_200
```

The ticket gives the other axis cardinalities but the mandate has not registered their exact
production values. A compact synthetic grid may exercise the 96/288 arithmetic in tests; it
is labeled `TEST_ONLY` and cannot become production authority.

## 5. Append and recovery protocol

The Windows store holds an operating-system file lock for the entire append transaction:

1. Acquire the lock and keep the descriptor open.
2. Replay and validate the complete chain under the lock.
3. Apply idempotency, lifecycle, policy, parent, and artifact checks.
4. Allocate the next sequence and prior hash.
5. Canonicalize and hash the event.
6. Publish it without replacement using the foundation no-clobber primitive.
7. Read the published bytes once and verify canonical bytes, filename, and hash.
8. Release the lock.

If a process exits before publication, no event exists. If it exits after publication, the
event is committed and an exact retry discovers it. The operating system releases the lock on
process exit; v1 never deletes a guessed "stale" lock. A linked invalid event, competing child,
or unexpected authoritative file blocks further writes.

The no-clobber primitive provides process-crash safety. Sudden power-loss durability is not
claimed without a separately measured filesystem metadata-flush capability test.

## 6. Deterministic export

The export is a derived, no-clobber JSON projection keyed by the validated head hash. It is
sorted by stable IDs and contains no request timestamp, absolute path, machine hostname, or
directory enumeration order. It records:

- registry ID, version definitions, event count, head sequence and head hash;
- the exact policy/config/schema bindings;
- each policy's family-freeze sequence and `FIRST_TRIAL_STARTED` cause;
- every trial and terminal reason, including not-run and failed states;
- NEE-121 access-event/head bindings and outcome bindings;
- structural, output, attempt, terminal, missing-cell, duplicate-cell, and off-grid counts;
- production blockers and the explicit absence of `m` and `N_eff` where unregistered;
- the export content hash.

Every later validation, promotion, UI, or report artifact that consumes registry state must
bind the registry ID/version, head event hash, export hash, and counting-policy hash. The UI is
read-only and may consume an export; it cannot append an event or recompute authoritative
counts.

## 7. Fail-closed acceptance

Acceptance requires adversarial fixtures for tampering, noncanonical JSON, missing/wrong
hashes, gap/duplicate/forked sequence, duplicate IDs, same-ID/different-content retries,
illegal transitions, terminal edits, missing/future parents, access or outcomes before start,
NEE-121 causal-chain or registration mismatch, dirty repositories without patch and untracked
evidence, independent/forked NEE-121 histories, cross-trial causal parents, wrong windows,
unregistered data/universe/vintages, uncited successes, incomplete/unplanned/extra outcome sets,
reasoned technical retries, family expansion after first execution, relabeled semantic duplicates,
three-version parent lineage, 96/288 reconciliation, selection-affecting and off-grid costs,
failed/abandoned trials, unregistered `N_eff`, oversized-event preflight, before/after-link crash
recovery, export conflict detection, randomized discovery order, fresh-store lock races, and
concurrent Windows append. A standards validator must validate complete event and export
documents with an explicit offline schema registry.

The implementation may not emit a production `m`, `N_eff`, DSR, prospective-evidence claim,
or promotion decision until NEE-120/121/140 register and bind the missing statistical policy.
NEE-117's absent remote exact-SHA CI evidence remains an independent repository-verification
blocker.
