# Change Tier Policy V1

Artifact: `configs/governance/change-tier-policy-v1.json`
Schema: `schemas/governance/change-tier-policy-v1.schema.json`
Enforcement: `qme/foundation/change_tiers.py` via `tests/foundation/test_change_tier_policy.py` (every CI run)
Status: `ACTIVE_ENFORCED_BY_ARCHITECTURE_TEST`
Resolves: `PROJECT_STATE_AUDIT_2026-08-14` finding **F1 — governance is outrunning the thing it governs**

## Problem

At `32253e8`, 74% of tracked lines were frozen-contract governance, 14% accepted
kernels, 7% engineering, 5% docs — and 0% strategy pipeline. Every slice,
including candidate kernels that will change once real data exists, paid the full
T0 ceremony: branch → PR → independent review → protected-main exact-SHA CI →
manifest rebind → hash-sealed self-verification → ledger event → Linear reconcile
→ receipt PR. Applied to M1–M3 (ingest → identity → signal → ledger → backtest)
that cadence puts first empirical results months out.

## Fix

Process weight becomes proportional to what a change governs, and the rule is
enforced by an architecture test rather than by convention.

| Tier | Governs | Per-slice process | Self-pin / manifests / receipts |
|---|---|---|---|
| **T0 FROZEN_CONTRACT** | mandate values, freeze policy, holdout/access-chain, decision math, receipts, schemas, governance fixtures/docs, workflows, locks | full ceremony (unchanged) | allowed |
| **T1 ACCEPTED_KERNEL** | deterministic numeric kernels pinned by KATs (`qme/stats`, `qme/quant`) | PR + CI; KAT change needs rationale in the PR | **not allowed in new code** (existing modules grandfathered by explicit list); receipt only at acceptance milestones |
| **T2 ENGINEERING** | pipeline & product code: `qme/**` fallback (data, identity, universe, signal, ledger, backtest, execution, UI, CLI, agent boundary, foundation), `scripts`, non-fixture tests | **PR + CI only** | **forbidden** |
| **T3 DOCUMENTATION** | plans, memory, notes | PR + CI | forbidden |

Classification is first-matching glob over `ordered_rules`; **every tracked path
must classify** so a new directory is placed deliberately, not by accident.

What the checker rejects outside T0 (and in new T1 code):

- self-pinned digests (`EXPECTED_*_SHA256 =`, `_PREDECESSOR_HASHES =`, `RUNTIME_SELF_PIN`),
- capability-sealed result types (`*_CAPABILITY = object()`), forge guards (`FORGED_*`),
- `_snapshot_global_graph(...)`, `def _confined_bytes(`,
- `*.hashes.json`, `*.manifest.json`, `*-receipt-v*.json` files.

Those constructs are exactly right for T0 verifiers. They are the wrong tool for
pipeline code, and the checker makes it impossible for M1–M3 to inherit them.

## The M1–M3 engineering stream

M1 (market-data spine), M2 (identity / corporate actions / coverage), and M3
(signal / ledger / backtest) are **one T2 engineering stream**:

- Contracts are already frozen (NEE-118/119, T0). They do not change per slice.
- Code iterates under the T2 gate: ruff, strict mypy, pytest, locks, secret scan,
  protected-main exact-SHA CI. Nothing else.
- Not required per slice: hashes manifest, self-pin digest, receipt PR, ledger
  event, crosswalk version, independent frozen-byte review.
- The stream produces exactly **one** governed artifact: a promoted walk-forward
  run manifest on pinned data. Promotion is a T0 registration → independent
  review → receipt. Until then, results are engineering outputs under
  `QME_DATA_ROOT`, not evidence.
- Target: first walk-forward v0.1 backtest on pinned data within 6–8 weeks of M0
  closure.

## What this does not do

- It does **not** relax any existing T0 control (`claims.reduces_existing_t0_ceremony = false`).
- It does **not** modify any frozen artifact (`claims.changes_any_frozen_artifact = false`);
  the policy, schema, checker, test, and this document are new files only.
- It does not reclassify or move superseded verifiers (`specification_freeze` v1–v3,
  crosswalk v1–v2, …); they remain T0 and hash-pinned by later artifacts.
- It does not touch `ci.yml`, `pyproject.toml`, `check_secrets.py`, or
  `test_repository_policy.py`, all of which are hash-pinned by freeze/registry
  manifests. Enforcement rides the existing `pytest` step.

## Local use

```bash
python -m qme.foundation.change_tiers .
```

prints the tier balance (files / lines / share) and any violations; exit 1 on
failure. Watch the T2 share rise as M1–M3 lands — that is the metric this policy
exists to move.
