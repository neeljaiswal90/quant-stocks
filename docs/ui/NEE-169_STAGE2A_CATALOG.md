# NEE-169 Stage 2A — Immutable local snapshot catalog

## Status and scope

This bounded slice implements the framework-independent startup catalog and immutable
read models for the local ticker-scores UI. It remains `SYNTHETIC_FIXTURES_ONLY` and is
not the web viewer, a production-data compatibility approval, or trading authority.

The catalog performs no write, network, provider, model, broker, order, Git, or Linear
operation. It does not watch the filesystem. A restart is required to discover a newly
published snapshot.

## Authority boundary

`load_snapshot_catalog(snapshot_root)` accepts only an existing absolute local directory.
At startup it:

1. enumerates direct children in deterministic UTF-8 byte order;
2. treats unfinished `.qme-ui-staging-*` directories as quarantined partial publication;
3. accepts a candidate only when its directory is a lowercase SHA-256 identity;
4. rejects symlinks and junctions at the root, snapshot, and file boundaries;
5. reads the manifest and indexed universe file once through bounded open handles;
6. verifies pre/post file identity, size, and modification coordinates;
7. requires the exact two-file Stage 1 inventory;
8. validates canonical bytes, directory/manifest hash identity, payload hash and schema,
   exact membership set/hash/count, all six status counts, and run/payload cross-bindings;
9. converts the validated documents to frozen tuple-backed read models; and
10. never rereads the snapshot root or payloads through that catalog instance.

Malformed entries become `QuarantineRecord` values with an opaque discovery digest and
one registered reason code. Their filename, local path, bytes, and parsed content do not
enter the catalog response. A malformed entry does not hide a valid snapshot.

## Lookup and conflict rules

The only lookup key is `(run_id, snapshot_hash)`. There is no run-ID-only lookup and no
`latest` or `current` claim. If two valid hashes carry the same run ID, both remain
available and that run ID appears in `conflicting_run_ids`; neither is selected
implicitly.

Catalog summaries use `qme.ui.catalog.v1`. Security detail remains in the frozen
`SnapshotReadModel.rows` tuple and uses the exact producer-derived display strings and
sort keys from the Stage 1 snapshot. Browser numeric parsing and quantitative
recalculation remain forbidden.

## Explicit exclusions

- Flask, Waitress, HTML, route, browser, accessibility, and performance implementation;
- production Nasdaq-100 membership or producer-schema acceptance;
- database, mutable cache, watcher, background refresh, or cross-run calculation;
- agent activation, score changes, portfolio changes, or order controls.

Those remain later NEE-169 stages and keep the issue open.
