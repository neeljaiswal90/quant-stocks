<!-- Change-tier policy: configs/governance/change-tier-policy-v1.json
     Local check: python -m qme.foundation.change_tiers . -->

## Tier

Highest tier touched by this PR (first-matching glob wins; the architecture test
`tests/foundation/test_change_tier_policy.py` classifies every file):

- [ ] **T0 FROZEN_CONTRACT** — full ceremony: independent review recorded, manifest rebind if an artifact changed, ledger event, Linear reconcile, receipt.
- [ ] **T1 ACCEPTED_KERNEL** — PR + CI. If a KAT fixture changed, the rationale is in this description. No new self-pins. Receipt only if this PR is an acceptance milestone.
- [ ] **T2 ENGINEERING** — PR + CI only. No manifests, self-pinned digests, sealed result types, forge guards, or receipts were added (the architecture test rejects them).
- [ ] **T3 DOCUMENTATION** — PR + CI.

## Gate

- [ ] ruff, strict mypy, pytest, locks, secret scan pass locally
- [ ] `python -m qme.foundation.change_tiers .` reports `status: OK`
- [ ] Protected-main exact-SHA CI run ID recorded after merge

## Non-claims

This PR does not claim any empirical result, freeze-blocker resolution, or
production readiness unless a T0 registration in this same PR says so.
