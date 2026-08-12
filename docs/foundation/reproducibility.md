# Foundation reproducibility contract

Status: implemented locally and connected to the project GitHub repository; an exact-SHA
remote CI run is still required for acceptance.

## Supported environment

- CPython 3.12 only for v0.1.
- `requirements-runtime.lock` contains the exact, hashed runtime dependency set.
- `requirements-dev.lock` contains the exact, hashed build and verification toolchain.
- `requirements-agents.lock` contains the exact, hashed optional TradingAgents source archive
  and all resolved transitive dependencies. Installing it does not enable agent execution.

Regenerate a lock only in CPython 3.12, review its dependency diff, and run:

```powershell
py -3.12 -m piptools compile pyproject.toml --generate-hashes --allow-unsafe --strip-extras --output-file requirements-runtime.lock
py -3.12 -m piptools compile pyproject.toml --extra dev --generate-hashes --allow-unsafe --strip-extras --output-file requirements-dev.lock
py -3.12 -m piptools compile requirements-agent-build.in --generate-hashes --allow-unsafe --strip-extras --output-file requirements-agent-build.lock
py -3.12 -m piptools compile pyproject.toml --extra agents --generate-hashes --allow-unsafe --strip-extras --output-file requirements-agents.lock
py -3.12 scripts\verify_lock.py requirements-agent-build.lock requirements-runtime.lock requirements-dev.lock requirements-agents.lock
```

The TradingAgents source requirement uses both the audited commit in its URL and the
SHA-256 of the downloaded source archive. A Git commit alone is not described as a file
hash. Install the hashed build lock first and use `--no-build-isolation` for the source
archive so an unpinned build environment cannot be downloaded. Optional agent installation remains runtime-blocked by its separate packet-native
backend, evidence-policy, structured-schema, and subprocess-attestation gates.

## Data root

Set `QME_DATA_ROOT` to an absolute local path outside this repository. `D:\qme-data` is
an example, not a hidden default. The foundation CLI rejects relative paths, filesystem
roots, repository-contained paths, UNC paths, and existing symlink/junction crossings.

```powershell
$env:QME_DATA_ROOT = 'D:\qme-data'
py -3.12 -m qme.cli.foundation init-data-root --data-root $env:QME_DATA_ROOT --repository-root . --dry-run
```

Canonical artifacts use logical IDs relative to the data root. A machine-specific
absolute path therefore does not change an artifact hash.

## Operational configuration

`configs/qme.example.json` is the strict `qme.config.v1` policy, not a source of hidden
runtime defaults. `schemas/qme-config-v1.schema.json` and
`qme.foundation.load_qme_config` require the exact seven registered fields and reject
unknown fields, duplicate keys, non-finite values, type coercion, network-enabled
backtests, and unconfirmed live orders. The example `D:\qme-data` path is documentation
only: runtime always requires an explicit `QME_DATA_ROOT` environment value and never
creates the root while loading configuration.

The loader reads one regular repository-owned file once, hashes its exact bytes, resolves
the data root through the existing outside-repository contract, and emits a manifest
record that omits both the machine-specific root and example path. Loading configuration
does not initialize storage, contact Alpha Vantage, connect to Webull, or authorize an
order.

Validate the policy and configured root without creating any directories:

```powershell
$env:QME_DATA_ROOT = 'D:\qme-data'
py -3.12 -m qme.cli.foundation validate-config --config .\configs\qme.example.json --repository-root .
```

## Canonical fixture manifest

`qme-foundation manifest` hashes the exact lock, configuration, schema, data, and output
bytes and records Git commit/dirty state plus Python/platform identity. Canonical JSON is
UTF-8, sorted, compact, newline-terminated, and rejects non-finite numbers. The manifest
does not contain its own hash, avoiding self-reference; CI computes and publishes that
hash externally. Existing manifest paths are never replaced.

## Verification

After installing the development lock, run:

```powershell
.\scripts\verify.ps1 -Python 'py -3.12'
```

The GitHub workflow installs the hashed runtime lock into a clean environment before
installing the wheel with dependency resolution disabled. It then repeats the lock,
wheel, CLI, dependency-consistency, lint, strict typing, unit,
architecture, secret, deterministic-fixture, and clean-worktree checks. A local pass is
not a substitute for a green exact-SHA remote run and required branch checks.

The existing `tools/` tree contains user-owned legacy scripts, nested Webull Git
checkouts, and local virtual environments. It is intentionally ignored and is not part
of the reviewed QME source, dependency closure, or release artifact. Required provider
and broker capabilities must be reimplemented under the typed `qme` package and pass
their own Linear gates rather than importing these copies.
