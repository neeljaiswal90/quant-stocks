# Sample and holdout governance v1

Status: frozen contract; production prospective and label construction remain blocked on named registrations  
Authority: Linear NEE-121  
Contract ID: `NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1`

## Evidence classification, not a pristine-holdout claim

The fixed formation-date classifications are:

| Formation window | Classification | Permitted claim |
|---|---|---|
| 2011-01-01 through 2018-12-31 | Development | Reusable development/tuning evidence |
| 2019-01-01 through 2021-12-31 | One-time historical confirmation | Confirmation only; prior access is unknown |
| 2022-01-01 through the final registered freeze timestamp | Retrospective external stress | External stress, never a pristine holdout |
| Strictly after the final registered freeze timestamp | Prospective after freeze | Prospective paper/forward evidence under the frozen version |

No preexisting append-only access ledger proves that 2019-2021 was unviewed.
Its initial provenance is therefore
`UNKNOWN_BLOCKED_NO_PREEXISTING_ACCESS_LEDGER`, not pristine. Reading 2022+
retrospective data cannot improve its classification. The first successful read
records that window as spent for v0.1, and later versions may not relabel it as
an independent holdout.

The repository does not yet contain a registered production freeze timestamp.
It also contains no registered prospective duration, observation-count, or
information threshold. Prospective go/no-go is
`BLOCKED_PROSPECTIVE_EVIDENCE_REQUIREMENT_UNREGISTERED`. Synthetic timestamps in
fixtures test mechanics only.

## Exact session coordinates

Calendar dates describe the high-level windows but are not production timing
authority. Every fold binds:

- exchange calendar ID and SHA-256;
- canonical ordered-session-vector SHA-256;
- exchange timezone ID;
- exact formation-window start/end session IDs and timestamps at `CLOSE`;
- exact fold-end session ID and timestamp at `OPEN`; and
- the historical decision cutoff `analysis_as_of`.

Formation occurs at a bound session close. Tradable forward-label endpoints and
the fold comparator are eligible raw opens. These coordinates are distinct.
`analysis_as_of` means the historical decision cutoff, never the current run
time. Nearest-session, holiday, next-session, previous-session, timezone-naive,
or bare-date substitution is prohibited.

The production methods that derive exact 1M, 3M, and 6M endpoints are not
registered. No calendar-day or assumed session-count approximation is allowed.
Production label construction therefore blocks until each method ID and hash is
registered. Hand fixtures supply explicit synthetic endpoint registrations; they
do not select a production convention.

No embargo value is registered. The active contract is
`NOT_REGISTERED_NOT_ACTIVE`; zero, one session, or any other default must not be
inferred.

## Independent label purging

For every formation observation, the 1M, 3M, and 6M labels are decided
independently:

```text
retain horizon h in fold f  iff  label_end[h] <= fold_end[f]
```

Equality is retained. The smallest representable timestamp after the boundary
is purged. The implementation does not purge all three merely because one
horizon crosses the fold. Every label records formation session, endpoint
sessions, phases, calendar identity, ordered-vector hash, endpoint-method ID and
hash, disposition, and reason.

The authoritative tradable coordinate is
`TRADABLE_T_PLUS_1_OPEN_TO_T_PLUS_1_OPEN`. Close-to-close labels may appear only
as `DIAGNOSTIC_CLOSE_TO_CLOSE_NOT_AUTHORITY`; they cannot replace tradable
labels in portfolio or execution evidence.

For a tradable label, the bound ordered-session-vector ordinal of the label
start must equal the formation-session ordinal plus one. This proves exact T+1
without guessing a calendar day or substituting a nearby session. Horizon end
ordinals remain supplied by the separately registered 1M/3M/6M endpoint
methods; this contract does not invent them.

## Availability and vintage isolation

For each feature, action, filing, fill, model input, evidence object, data
vintage, membership snapshot, and reflection-memory item:

```text
available_at = max(published_at,
                   vendor_available_at,
                   local_accepted_at,
                   revision_at)

available_at <= analysis_as_of
```

The manifest preserves, rather than collapses, these coordinates:
`effective_at`, `published_at`, `vendor_available_at`, `local_accepted_at`, and
`revision_at`. It also binds observation end, content SHA-256, and data-vintage
SHA-256. Data-vintage, membership, filing, and reflection inputs have additional
typed cutoff timestamps. A later amendment or revision creates later evidence;
it cannot be projected backward because an earlier effective date exists.

Development and confirmation successes cannot access post-2018 and 2022+
observations respectively. This upper bound does not ban legitimate feature
lookback before the formation-window start. For example, a cutoff-valid t-252
input may predate 2011. Labels and realized outcomes, unlike feature inputs,
must remain inside their bound fold.

## Append-only access ledger

Every access attempt, success, denial, and retry is a distinct immutable event.
The strict event schema requires trial ID, run ID, query ID, historical cutoff,
data-vintage timestamp and hash, request-content hash, causal parent hash,
previous chain hash, artifact hashes, sample range, event type, and event hash.

The canonical event hash is SHA-256 over every event field except itself.
`previous_event_hash` forms the append-only chain. An attempt uses the genesis
causal parent. A success or denial parents an attempt or retry. A retry parents
the denial it retries. Retries receive new IDs, sequence numbers, and hashes;
they never overwrite the denied event. Only a successful access marks a window
spent.

## Version freeze and restart

Documentation-only and infrastructure-only changes may continue without a
restart only when they cannot change data, labels, decisions, or results.
Specification, feature, label, portfolio, data-method, or threshold changes
require a new version, new protocol hash, and strictly later freeze timestamp.
All observations collected under the predecessor remain predecessor evidence;
the restart cannot reset spent windows or treat prior observations as independent
for the new version.

## Artifacts and limits

The source-controlled authority is
`configs/governance/sample-holdout-v1.json`. Strict schemas cover the frozen
configuration, dynamic fold manifests, and append-only access events. Synthetic
fixtures cover all three horizons immediately before, exactly on, and
immediately after the boundary; availability coordinates; vintage and identity
cutoffs; spent-window transitions; retries; and restart rules.

Passing these contract tests proves deterministic timing and state transitions.
It does not prove that production labels, calendar evidence, historical access
provenance, a freeze timestamp, or adequate prospective evidence exist.
