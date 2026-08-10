# Quant Momentum Equities (QME)

QME is the deterministic system of record for market data, ranking, accounting,
portfolio constraints, and order preparation. TradingAgents is integrated only as
a bounded, report-only evidence review dependency.

## Development setup

QME v0.1 supports CPython 3.12. Bootstrap from the reviewed, fully hashed
development lock and then run the canonical verifier:

```powershell
.\scripts\bootstrap.ps1 -Python 'py -3.12'
.\scripts\verify.ps1 -Python '.\.venv\Scripts\python.exe'
```

Set `QME_DATA_ROOT` to an absolute local path outside this repository. There is
no implicit machine-specific default; `D:\qme-data` is only an example.

Install the exact audited TradingAgents revision only when developing the agent
adapter:

```powershell
python -m pip install --require-hashes -r requirements-agent-build.lock
python -m pip install --no-build-isolation --require-hashes -r requirements-agents.lock
```

The dependency is pinned to commit `a33fd4c0f134485a43553a2c23a63cb14adbd88f`,
the exact source archive is SHA-256 constrained, and its CPython 3.12 Windows
transitives and build backend have been resolved into hashed locks. This validates
installation inputs only; runtime remains disabled until the packet-native backend,
evidence-policy, structured-schema, and supervisor gates are accepted.

Foundation details, lock regeneration, data-root constraints, and canonical
fixture-manifest rules are recorded in
[the reproducibility contract](docs/foundation/reproducibility.md).

## Agent safety boundary

- Inputs must be immutable, cutoff-valid evidence packets.
- Agent influence is `report_only`.
- Every artifact has `trade_eligible=false`.
- Agent output cannot change deterministic ranks, weights, or orders.
- The unmodified upstream graph is rejected because it can fetch live data, share
  memory/configuration, and fall back from structured output to free text.
- A concrete packet-native subprocess supervisor must enforce process/network
  isolation, attest its exact fork revision, and replay packet-tool receipts before
  runtime execution can be enabled.
- Authoritative source-class freshness and raw-source-to-model-payload derivation
  lineage are still blocking data-contract work; packet-declared ages and inline
  source IDs are not sufficient for production activation.

See [the TradingAgents integration note](docs/agents/TRADINGAGENTS_INTEGRATION.md)
for the audited boundary and remaining work.

## Project memory and UI plan

- [Living implementation memory](docs/implementation/QME_IMPLEMENTATION_MEMORY.md) —
  reconciles repository, CI/runtime evidence, and live Linear state. Update it only
  after verifying the underlying evidence; planning status is not implementation proof.
- [Ticker scores UI specification](docs/ui/QME_TICKER_SCORES_UI_SPEC.md) — defines the
  planned local, read-only dashboard for complete Nasdaq-100 membership, deterministic
  scores/ranks, portfolio state, report-only agent reviews, and provenance.
- [ADR-001: local UI architecture](docs/ui/ADR-001_LOCAL_UI_ARCHITECTURE.md) — selects a
  deterministic content-addressed JSON snapshot plus a Flask/Jinja/Waitress viewer on
  `127.0.0.1` for one trusted local user.
- [Independent UI architecture review](docs/ui/UI_ARCHITECTURE_INDEPENDENT_REVIEW.md) —
  records the competing SPA/server-rendered proposals, the local-only scope correction,
  and the retained quantitative, reproducibility, read-only, and delivery controls.

No UI implementation or production full-universe score artifact exists yet. The UI
workstream begins with frozen producer/snapshot fixtures and cannot be accepted until
schemas, checksums, exact membership reconciliation, Decimal display rules, lineage,
read-only/no-order tests, accessibility, and measured performance are validated on an
exact committed SHA. The owner account and configured local artifact root are trusted;
remote, shared, multi-user, and mutation-capable deployments are out of scope.
Architecture approval is planning evidence, not implementation evidence.
