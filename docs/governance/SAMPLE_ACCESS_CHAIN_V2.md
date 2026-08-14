# NEE-176 bounded sample-access-chain V2 candidate

## Scope and authority

This all-new slice implements a bounded, deterministic content-addressed export,
ordered index, Merkle inclusion proof, immutable resolver, and versioned compact
registry consumer for the existing NEE-121 V1 access-event format. It preserves
V1 bytes and V1 event-hash semantics. NEE-121 V2 remains the active governance
authority; the proposal, M0 registration, experiment-family registration,
Specification Freeze V3, and experiment-registry V1 artifacts are provenance
inputs bound through their reviewed manifests. Every V1 file, including the
manifest-bound `qme/experiments/__init__.py`, remains byte-exact and unmodified.

The status is `BOUNDED_ACCESS_CHAIN_IMPLEMENTATION_CANDIDATE`. This synthetic
engineering evidence makes no production, prospective, alpha, M0-completion, or blocker-clear claim.
In particular, all 14 blockers remain active and this slice
does not clear `NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION`. A reviewed production-scale export,
source receipt, and protected-main acceptance evidence remain unavailable.

## Frozen byte rules

Every event is strict UTF-8 JSON with duplicate keys and nonfinite constants
rejected. The inherited V1 event payload is canonicalized with sorted keys,
compact separators, and UTF-8; its existing `event_hash` remains the raw SHA-256
of that payload. Protected V1 text parity requires nonempty trimmed Unicode NFC,
forbids NUL, and caps each text field at 4,096 characters; `contract_version`
is exactly `v1`. Before an export root is computed, the verifier checks every
event, every V1 hash, contiguous sequence numbers, global `previous_event_hash`,
monotone access time, unique ID, causal parent type/order, sample boundaries,
and canonical artifact bindings.

The V2 content and tree rules are:

- object: `SHA256("qme.sample_access_chain.v2.object\\0" || canonical full event)`;
- leaf: `SHA256("qme.sample_access_chain.v2.leaf\\0" || uint64-be sequence || object digest)`;
- node: `SHA256("qme.sample_access_chain.v2.node\\0" || left digest || right digest)`;
- root: `SHA256("qme.sample_access_chain.v2.root\\0" || canonical chain lineage || event-tree root)`, binding chain ID, genesis, and predecessor into the registered root;
- odd node: duplicate the final digest at each tree level;
- proof: ordered leaf-to-root sibling list with an explicit `LEFT` or `RIGHT` side,
  accepted only against a trusted exact export identity, chain identity, genesis,
  count, head, index, root, and full-export commitment;
- chain lineage: stable chain ID and zero genesis plus either the exact genesis
  sentinel or a predecessor commitment containing prior export hash, root,
  count, and head;
- extension: the new export must be longer and its ordered-index and content-object
  prefixes must deep-equal the complete prior export. Prior-root membership alone
  is not an extension proof.

Each event must be strictly smaller than 2 MiB, each export strictly smaller
than 64 MiB, and each compact registry strictly smaller than 2 MiB. Repository
verification uses confined, non-symlink, non-reparse, regular files and reads
the verified bytes through the same opened handle.

## Registry replay and non-erasure

`SampleAccessChainResolver` accepts an export only after full validation and
stores private canonical bytes plus a content commitment. Its file constructor
uses a confined regular-file path and the same verified open handle for the
bounded read, eliminating a path-check/read-handle gap. The V2 compact
consumer requires that exact immutable resolver. Replay resolves every ordered
event and reproduces the V1 outcome-relevant projection: all success event
hashes, current-run success hashes, latest-success timestamp, and global
seven-field exposed-window identities. The generic compact object does not
claim these identities are registered window IDs or outcome citations; only the
versioned adapter resolves identities to registered IDs and validates actual
current-run citations. Compaction therefore cannot hide earlier successful
access or silently replace current-run citations. Latest-success selection is
by parsed UTC instant while returning the original event text; equal instants
retain the first event, matching protected replay's deterministic `max` tie.

The versioned registry consumer accepts no caller-authored context and no
embedded access chain. Its source is an all-new V2 event chain with its own
domain-separated event hashes, contiguous sequence, and `previous_event_hash`.
Legacy policy, trial-registration, start, outcome, and terminal payloads retain
the protected V1 business syntax. The V2 schema has an exact payload and
`trial_id` conditional for each of all nine allowed event types, including the
four terminal types, and the adversarial schema corpus is checked against the
runtime validator. Access binding uses only
`SAMPLE_ACCESS_BOUND_COMPACT`: the external export identity, content-derived
path, full export commitment, compact-registry hash, current run, registered
holdout binding, and translated V1 registration hash. The V2 source event or
export is rejected if `access_event_chain` occurs anywhere in its object tree.
Registry storage therefore grows with compact receipts, not by copying the
cumulative access chain at every binding.

The public V2 event constructor accepts only an exact `datetime` instance with
a non-null UTC offset. Naive datetimes, datetime subclasses, booleans, and other
types fail closed before replay; aware inputs are converted to canonical UTC
`Z`, so equivalent instants with different offsets produce identical bytes and
hashes without consulting the host timezone.

Replay validates the V2 source chain independently, opens every referenced
access export through an immutable content-addressed resolver, and validates
the complete external history. It then constructs a complete V1 shadow event
chain only in memory. A compact receipt becomes a V1 `SAMPLE_ACCESS_BOUND`
shadow containing the resolved chain; every later shadow event points to the
recomputed V1 shadow hash, never the different V2 event hash. The complete
shadow is passed to protected `qme.experiments.registry.replay_registry`, whose
exact source hash is bound as the business-rule version. No shadow event or
full access chain is serialized back into the V2 input or export.

Linux replay does not use the normal `qme.experiments` package import because
that protected V1 initializer intentionally imports the Windows-only durable
store. The NEE-176 runtime instead confines and reads the sibling
`qme/experiments/registry.py` through one regular, non-link handle, requires its
exact protected SHA-256, decodes those captured bytes as strict UTF-8, compiles
them with `dont_inherit=True` and `optimize=0`, and directly executes that code
in an exact private `ModuleType` named
`_qme_nee176_protected_experiment_registry_v1`. No import specification,
`SourceFileLoader`, source-path reopen, or bytecode cache supplies executable
content. The module is inserted in `sys.modules` before execution as required
by its dataclasses; a second confined source read after execution is tamper
detection only. On every cache access, the runtime rechecks source bytes,
private-module identity, and the exact captured objects, kinds, and module names
of every required protected API through an immutable facade. Unused additional
module attributes carry no authority. The protected source imports only
`canonical_json_bytes` from `qme.foundation.lineage`. That dependency is now an
explicit NEE-176 manifest leaf at protected SHA-256
`edb64ebb:1edcdb31:c4e4620c:c90dca99:489e98d3:1f224872:81754cce:05439de6`.
The private execution boundary intercepts only that exact import and supplies a
frozen equivalent of the protected algorithm: `json.dumps` with
`ensure_ascii=False`, `allow_nan=False`, `sort_keys=True`, compact separators,
UTF-8 encoding, and one trailing LF. It captures the original standard-library
`json.dumps` and import callable and revalidates them, the private builtins map,
and the registry module's captured canonicalizer on every access. It never
consults ambient `qme.foundation.lineage`, its `sys.modules` entry, or a meta
path finder. Neither `qme.experiments`, its store, nor `msvcrt` is imported. The
protected V1 manifest therefore continues to replay all of its leaves exactly,
with no supersession exception.

The returned commitment binds a SHA-derived non-ambient path, raw SHA-256, V2
event count and causal head, V1 shadow-state hash, V2 business-projection hash,
and V2 export hash. Missing resolvers, altered receipts, embedded fallbacks,
cumulative duplication, reordered or forked events, translated-hash mismatch,
or an invalid post-compact outcome fails closed. This bounded V2 consumer
accepts exactly one replayed outcome for the selected RUNNING trial; zero or
multiple outcomes fail closed rather than selecting one.

The derived state then applies the protected replay gates: active NEE-121 V2
config and manifest, exact prior access-export identity/chain/genesis/count/
head/index/root/full-export commitment, strictly extending acknowledged global
prefix, current trial/run suffix ownership, registration/run/binding times,
seven-field registered-window identity to `window_id`, frozen artifacts and
data vintage, and current-run citations. The compact result is independently
exact-compared with the protected V1 replay-derived projection. Current-run citations stay separate,
while all successes, the latest success, and exposed windows accumulate across
every run of the same trial exactly as protected V1 replay does. Both the cited
and all-exposed window sets must equal the frozen plan. Two sequential compact
bindings prove prefix non-erasure and protected-business state parity even
though V2 and in-memory V1 shadow event hashes intentionally differ.

The export and proof schemas share identical structural event definitions.
They constrain canonical non-padded text, actual ISO dates, offset-aware ISO
date-times, exact enums, and literal contract version. Schema checks use a
Draft 2020-12 format checker. Unicode NFC and semantic relations such as date
ordering, causal equality, unique binding identifiers, and content hashes
remain runtime checks; the slice does not claim JSON Schema can express those
runtime-only constraints.

The public verification result is exact-type immutable data, not a security
capability. Serialization independently revalidates the export bytes, exact
prior resolver and lineage, content-addressed resolver, and compact registry,
then exact-compares every result field. There is no closure capability,
attestation dictionary, or mutable in-process authority. A forged object that
does not match the independently verified artifacts is rejected; an object with
the exact same verified values is harmlessly equivalent. Direct construction,
copy mutation, replacement, raw allocation, subclassing, slot mutation,
wrong resolver type, altered content, path escape, malformed JSON, oversized
bytes, index/root/head mismatch, invalid proof, and partial-prefix extension all
fail closed.

## Deterministic known answer

The generator creates 10,000 synthetic events in a repeated
attempt/denial/retry/success causal pattern. Successful events carry two sorted
artifact bindings, so the fixture covers multi-binding extension behavior,
denial/retry ancestry, and a long prior-success history. Its second export binds
the exact 9,000-event predecessor and adds a 1,000-event current-run suffix while
preserving the complete index and object prefix. The checked-in compact fixture
records first/head hashes, ordered-index hash, export hash, Merkle root, six
proof-set hash, one terminal proof, size maxima, and the complete outcome
projection hash/counts. It also builds a separate actual 10,000-event external
chain, two successive compact V2 registry receipts, a protected in-memory V1
shadow replay, and frozen V2/shadow business hashes. The seven-event V2 registry
remains below 64 MiB, every compact receipt remains below 2 MiB, the second
receipt remains bounded independently of cumulative chain size, and V2 registry
bytes contain no `access_event_chain` key. Tests regenerate the complete oracle twice;
the dedicated Linux workflow repeats the same oracle on Python 3.12.10 without
importing the Windows-only registry store.

## Explicit limitations

- Synthetic events are not evidence that production access was captured.
- The fixture is bounded at 10,000 events; it is not a production volume claim.
- Content hashes establish byte integrity, not actor authenticity or source truth.
- Merkle membership does not replace complete contiguous-chain or full-prefix validation.
- This slice does not authorize data access, prospective consumption, promotion,
  live orders, production readiness, or any empirical performance statement.
