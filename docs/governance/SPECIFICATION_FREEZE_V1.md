# NEE-110A specification-freeze candidate

Contract status: bounded candidate mechanics only; production closure is `BLOCKED_UNRESOLVED_INPUTS`.

## Purpose and authority

This bounded slice assembles the six committed M0 quantitative and governance artifact sets into one deterministic, offline evidence view. It verifies the exact bytes of every master manifest and all 51 registered leaf references. It does not approve the production strategy, resolve owner decisions, call Linear, read market data, or authorize NEE-123 through NEE-128.

The deterministic producer remains authoritative for membership, features, scores, ranking, eligibility, weights, costs, accounting, and portfolio decisions. This kernel only evaluates whether the reviewed specification inputs are complete enough to freeze. Today they are not.

## Inputs

The policy at `configs/governance/specification-freeze-policy-v1.json` binds:

- NEE-116A bounded synthetic two-rebalance fixture;
- NEE-118 accounting and execution equations;
- NEE-119 v0.1 quantitative contract;
- NEE-120 economic promotion and abort governance;
- NEE-121 sample and holdout governance;
- NEE-122 append-only experiment registry.

Each source is `COMMITTED_UNVERIFIED`. A source manifest's prose status is preserved as observed data and cannot promote evidence maturity. This is important for NEE-116A, whose embedded review wording is stale even though later repository evidence records an independent adversarial review.

## Fail-closed rules

The kernel rejects missing files, incorrect outer or nested hashes, duplicate JSON keys, non-finite JSON numbers, unsupported manifest shapes, wrong leaf counts, absolute or traversing paths, non-NFC paths, case-fold collisions, reparse/symbolic-link components, manifest self-reference, and cross-manifest path/hash conflicts.

Policy v1 requires the exact six artifact-set identities and all 27 named blockers. Its complete canonical semantic hash is immutable, and the public policy schema registers the exact same objects: changing a ticket, path, leaf count, source hash, category, or description requires a new reviewed contract version. It accepts only `COMMITTED_UNVERIFIED` source maturity. Removing or relabeling a blocker, adding an unknown artifact set, or asserting `ACCEPTED` is invalid input rather than progress.

The export is canonical UTF-8 JSON with sorted keys and a terminal newline. Its 51 verified leaf records are reduced to a registered content-addressed artifact index rather than duplicated as mutable provenance rows. Blockers, claims, checks, artifact-set results, and the effective-trials envelope have exact v1 schema/runtime identities. The builder returns an application-sealed export; the serializer accepts only the exact builder type, revalidates and recanonicalizes its state, and rejects caller-created mappings, subclasses, reassigned slots, and inconsistent cached bytes/hashes. This is a deterministic application-integrity boundary, not a cryptographic defense against malicious code that can arbitrarily rewrite Python process memory. No current time, request order, network result, or Linear workflow state contributes to identity. Reordered policy arrays must yield byte-identical output.

`configs/governance/specification-freeze-v1.hashes.json` is the external publication envelope for the policy, code, schemas, documentation, and tests. Its own digest is recorded in the evidence ledger and Linear after commit; it is intentionally not embedded in the export or in a file that it hashes, which would create a recursive self-hash.

## State dimensions

The candidate keeps integrity, evidence maturity, and closure separate:

- Integrity: current bounded kernel proves `HASH_VERIFIED` for registered bytes. Full schema and cross-contract semantic approval remain explicit blockers.
- Evidence maturity: dirty or unborn work is `LOCAL_UNCOMMITTED`; all clean-commit inputs remain `COMMITTED_UNVERIFIED` in v1. A caller-provided CI mapping is labeled `CALLER_ASSERTED_UNVERIFIED` and cannot raise maturity. A future version needs an independently authenticated repository/workflow/run/check/artifact verifier.
- Closure: the v1 result is always `BLOCKED_UNRESOLVED_INPUTS`, `accepted=false`, and `downstream_start_authorized=false` while the registered blockers exist.

Linear status is never evidence authority and cannot promote these states.

## Claim taxonomy

Claims use only `SUPPORTED_BOUNDED`, `SUPPORTED_WITH_LIMITATION`, `BLOCKED`, or `FORBIDDEN`.

Supported bounded claims are local quantitative mechanics and synthetic arithmetic conformance. The Alpha Vantage common-stock proxy claim is blocked because no reviewed proxy artifact is bound; even after evidence exists it may be described only as survivorship-reduced and explicitly not as authoritative point-in-time Nasdaq-100 membership.

The candidate blocks production specification acceptance, official point-in-time NDX membership, production coverage/freshness, exact-SHA remote CI, Nasdaq-100 readiness, and data-spine start authorization. It forbids empirical-performance, prospective-holdout, effective-trials, DSR, and authoritative portfolio-capacity claims from this evidence.

## Effective-trials boundary

No `N_eff` formula is selected. `N_eff=m`, independence, average-correlation interpolation, spectral rank, integer rounding, clipping, bootstrap details, and solver bounds are all unauthorized until prospectively registered.

The export therefore keeps `estimate=null`, `estimator=null`, and lists the complete registration envelope: semantic target; estimator and code hash; family and Holm `m` binding; immutable joint return matrix; frequency, benchmark, cost, calendar, and missingness policies; null centering and standardization; dependence/resampling method; block rule, seed, and replicates; PSD/regularization, bounds, negative-correlation, and single-trial rules; Monte Carlo uncertainty; and independent fixture hash.

Holm's integer comparison-family size remains distinct from any real-valued DSR diagnostic. Failed, skipped, abandoned, retry, and off-grid opportunities remain experiment-registry evidence and cannot disappear to lower multiplicity.

## Known blockers

The checked-in policy records all presently known unresolved inputs, including remote exact-SHA CI, real point-in-time data, official-open fallback policy, asymmetric costs, authoritative historical NDX membership, minimum breadth, source-class freshness, human approval, capacity and tax-lot methods, corporate-action edge cases, promotion mandate, preregistration approval and inference settings, calendar/session and label-endpoint registrations, historical-access provenance, final freeze timestamp, prospective evidence sufficiency, production family and complete access-chain policy, and the dependence-estimator/correlated-fixture package.

The missing strict operational `qme.config.v1` loader is also a closure blocker. This slice intentionally does not invent that separate contract.

## Acceptance boundary

The bounded NEE-110A mechanics are acceptable only when:

1. all six master manifests and all registered leaves rehash exactly;
2. strict policy and export schemas validate;
3. every unavailable input remains visible as a sorted blocker;
4. no production, empirical, official-NDX, `N_eff`, DSR, capacity, or CI claim is promoted;
5. adversarial tamper, path, duplicate, escalation, permutation, dirty-tree, and wrong-CI fixtures pass;
6. full tests, lint, strict typing, lock verification, secret scan, build, and independent review pass.

Even after those mechanics pass, NEE-110 remains open. NEE-114 and NEE-123 through NEE-128 remain blocked until the underlying evidence is registered and a later reviewed contract version can legitimately close the phase gate.
