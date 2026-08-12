# NEE-169 Stage 1 — deterministic UI snapshot builder

Status: `IMPLEMENTED_BOUNDED_CANDIDATE`

Evidence scope: `SYNTHETIC_PRODUCER_FIXTURE_ONLY`

Production activation: `BLOCKED_UNTIL_PRODUCER_SCHEMAS_ACCEPTED`

## Outcome

Stage 1 turns one finalized synthetic producer bundle into one deterministic,
content-addressed local UI snapshot. The builder is presentation-only. It does not fetch
data, calculate a signal, change membership, rank securities, decide selection, invoke an
agent, contact a broker, or create an order.

The implementation lives in `qme.ui_snapshot.builder`. The bounded command is
`python -m qme.cli.ui_snapshot`. A packaged console-script registration is deliberately
deferred because `pyproject.toml` is frozen inside the accepted NEE-122/NEE-110 evidence
chain and must not be changed incidentally by this UI slice.

## Accepted input bundle

The builder reads exactly three canonical UTF-8 JSON files:

1. `producer-manifest.json` — `qme.synthetic.ui_producer_manifest.v1`;
2. `run.json` — artifact `producer.run_manifest.v1` using
   `qme.synthetic.ui_source.v1`;
3. `universe-scores.json` — artifact `producer.universe_scores.v1` using
   `qme.synthetic.ui_source.v1`.

The manifest must be finalized and must index exactly the two payloads by artifact ID,
path, schema, byte count, and SHA-256. The builder reads bounded bytes once, requires the
foundation canonical JSON representation, rejects extra or missing files in the supplied
payload map, and validates all cross-bindings.

The run payload owns:

- run ID and analysis cutoff;
- point-in-time membership snapshot ID, exact security-ID set, count, and membership hash;
- all six member-status counts;
- run quality and completeness.

The universe payload owns each security identity, ticker, company name, status, canonical
rank and 12-1 momentum value (or explicit null), selection flag, review reason codes, and
the canonical source-row hash. A source row hash binds the producer fields without the hash
field itself. It is an integrity check, not a new quantitative calculation.

Only `VALID` and `DEGRADED` rows may carry finite canonical numeric strings. `STALE`,
`MISSING`, `BLOCKED`, and `INVALID` rows must carry null numeric values so absence cannot be
rendered as zero.

## Deterministic projection

The registered field map is exact and closed. Stage 1 permits only the frozen `COPY`,
`FORMAT_DECIMAL`, and presentation-only `MAKE_SORT_KEY` mappings used by this snapshot.
There is no default value or unknown-field passthrough.

The builder:

1. validates the registered Stage 0 policy and exact field map;
2. validates producer manifest and payload bytes, schemas, row hashes, timestamps, and
   membership/status identities;
3. sorts output rows by NFC canonical `security_id` UTF-8 bytes;
4. copies producer-owned identities, statuses, selections, reasons, and row hashes;
5. formats rank and momentum with Decimal precision 50 and `ROUND_HALF_EVEN`;
6. creates lexicographic presentation sort ordinals from canonical Decimal values with
   `security_id` as the stable tie breaker;
7. emits canonical `universe.json` bytes;
8. emits a canonical `qme.ui.snapshot_manifest.v1` binding the exact producer manifest,
   producer data policy, UI projection policy, field map, code/config identities, output
   bytes, membership, and statuses.

`run.membership_count` is copied as an integer envelope identity. It is not formatted into
a numeric display object. Numeric row objects carry their presentation sort key through the
two registered `MAKE_SORT_KEY` mappings.

The snapshot hash is SHA-256 over the exact canonical snapshot-manifest bytes. Reordering
the caller's payload-discovery mapping does not change output bytes.

## Atomic publication

Publication uses a sibling staging directory inside the configured snapshot root, so the
final rename remains on one volume:

1. validate the immutable `SnapshotBuild` again at the write boundary;
2. create a unique `.qme-ui-staging-*` directory;
3. write and fsync every indexed payload;
4. write and fsync `snapshot-manifest.json` last;
5. rename the staging directory once to `<snapshot_root>/<snapshot_hash>`;
6. validate the exact final inventory and bytes;
7. never overwrite or delete an existing published snapshot.

Re-publishing identical bytes returns the existing snapshot. An existing extra, missing, or
changed file is a blocking conflict. A failure before rename removes only the registered
staging directory. A failure after the rename leaves a complete snapshot that can be
validated and reused. Symlink/junction paths, UNC roots, relative roots, path traversal,
unindexed payloads, and control-file recursion are rejected.

## CLI

Example for the bounded fixture contract:

```powershell
python -m qme.cli.ui_snapshot `
  --producer-root C:\qme-data\runs\synthetic-ui-001 `
  --snapshot-root C:\qme-data\ui-snapshots `
  --policy configs\ui\ui-stage0-policy-v1.json `
  --field-map configs\ui\ui-field-map-v1.json `
  --builder-revision <40-character-git-sha>
```

Success emits `UI_SNAPSHOT_READY`, the content hash, immutable directory, and whether this
call created it. Contract, file, and publication failures emit `UI_SNAPSHOT_ERROR` and exit
with code 2. The CLI does not mutate producer files.

## Qualification evidence required before Stage 1 acceptance

- public Draft 2020-12 schemas validate the producer manifest and both source artifact
  variants;
- the static projection known answer matches exactly;
- 100 randomized payload discovery orders produce identical snapshot bytes;
- source hash/size/schema/path/finalization/cutoff/membership/status/row attacks fail closed;
- publisher manifest-last, idempotency, conflict, pre/post-rename recovery, reparse, and
  concurrent-writer tests pass;
- CLI success, idempotency, failure-before-publication, and source immutability pass;
- full repository tests, Ruff, strict mypy, four lock checks, wheel/CLI smokes, compile,
  tracked/staged secret scans, and exact-SHA protected CI pass.

This document does not claim a production producer adapter, accepted official Nasdaq-100
membership, the Stage 2 catalog/viewer, browser accessibility/performance evidence, agent
review UI, or broker/reconciliation UI.
