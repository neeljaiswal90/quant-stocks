# QME UI Architecture — Independent Review Record

Review date: 2026-08-10

Final verdict: `PREFERRED FOR LOCAL V0.1; IMPLEMENTATION REMAINS PLANNING_ONLY`

Reviewed decision: [ADR-001](ADR-001_LOCAL_UI_ARCHITECTURE.md)

## Scope correction

The owner clarified that QME v0.1 is a single-user application used only on the owner's
local computer. The prior review assumed stronger boundaries appropriate to shared,
remote, or mutually untrusted environments. That assumption is superseded.

The current trust model accepts the local Windows user, repository, and configured
artifact root as trusted. The viewer still has to detect corruption, partial publication,
schema incompatibility, wrong-run selection, missing Nasdaq members, and quantitative
display drift. It does not claim protection against a malicious same-user process,
administrator, or compromised operating system.

Consequently, the following are not v0.1 acceptance requirements:

- application login, launch nonce, cookie, or session state;
- cryptographic artifact-origin infrastructure or separate key lifecycle;
- persistent catalog rollback state;
- a dedicated Windows execution account or sandbox;
- operating-system firewall enforcement for the viewer.

Those controls require a new ADR if the application later supports remote binding,
multiple users, shared/untrusted artifact roots, or write authority.

## Review method

The review compared:

1. FastAPI plus React/TypeScript/Vite;
2. server-rendered Flask/Jinja/Waitress;
3. a generated static report;
4. PySide6 and desktop-shell alternatives.

It then evaluated the preferred design against the repository's actual scope: one local
operator, at most 200 universe rows per run, immutable artifacts, no live quotes, no
order controls, and no client-side quantitative calculations.

## Framework decision

Flask/Jinja/Waitress remains the preferred v0.1 stack.

Reasons:

- The registered dataset does not require SPA state or virtualization.
- HTML and JSON can share one frozen Python read model.
- A native semantic table is straightforward and accessible.
- The runtime avoids Node, a second schema implementation, and a frontend dependency
  graph.
- Waitress provides a direct local Windows deployment path.
- Stable JSON GET routes preserve a future migration boundary.

The framework choice is not evidence of implementation. No UI dependency or source code
exists yet.

## Independent disposition matrix

| Requirement | Disposition | Required evidence |
|---|---|---|
| Content-hashed snapshot integrity | Retain | Manifest/payload hashes, same-byte load, partial/changed-file fixtures |
| Projection authority | Retain | Source-pointer allowlist, no defaults or quant math, deterministic Decimal display |
| Exact Nasdaq membership | Retain | Exact security-ID set/hash equality and six-bucket count reconciliation |
| Read-only/no-order authority | Retain | GET/HEAD route inventory, forbidden-import audit, source hashes unchanged after UI use |
| Schema/state determinism | Retain | Versioned schemas, unknown-state mapping, HTML/JSON parity, DOM provenance |
| Local loopback | Retain | Explicit `127.0.0.1` bind, no wildcard, debug/reload off |
| Safe rendering/offline assets | Retain | Autoescape, no `safe`, plain-text model content, bundled assets, basic CSP |
| Accessibility | Retain | Native table, keyboard, zoom/reflow, contrast and screen-reader evidence |
| Performance | Retain | Registered cold/warm measurements and reviewed budgets |
| Reproducible packaging | Retain | Locked wheel, optional `onedir`, SBOM and clean offline Windows test |
| User authentication/session | Remove | Outside the local single-user scope |
| Artifact-origin key infrastructure | Remove | Local account and artifact root are trusted |
| Persistent catalog rollback protection | Remove | Run history is a discovered local set, not an adversarial authority claim |
| Dedicated Windows identity/firewall | Remove | No separate trust domain; enforce no-network/no-write through code boundaries and tests |

## Quantitative corrections retained from the earlier review

The scope correction does not weaken the following findings:

- Count equality does not prove membership equality. The displayed security-ID set and
  registered membership-set hash must match the run manifest exactly.
- Completeness is orthogonal to run quality. A stale or degraded member remains in a
  complete official basket.
- Present numeric values carry canonical and display Decimal strings, positive scale,
  precision, rounding mode, exact `display_text`, source pointer, and source hash.
- Browser code may not parse canonical/display decimals or recreate ranks, scores,
  targets, totals, or review-set membership.
- Agent output remains report-only and cannot alter deterministic hashes.
- The viewer may display an existing immutable broker-preview or reconciliation artifact
  but cannot request, generate, refresh, submit, place, replace, cancel, or confirm it.

## Final verdict

The local Flask/Jinja/Waitress architecture is proportionate and has no inherent
disqualifier under the corrected trust model. It is preferred for implementation after
the deterministic snapshot, membership, numeric-display, read-only, accessibility, and
performance contracts are frozen.

The evidence state remains `PLANNING_ONLY`. A local document, Linear status, or passing
legacy unit test does not establish that the UI, snapshot builder, or production
Nasdaq-100 artifact exists.
