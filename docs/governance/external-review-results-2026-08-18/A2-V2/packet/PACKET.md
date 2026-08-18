# External-review packet — A2-V2 only

Artifact: A2-V2 — inference adapter and retained numerical kernel
Repository: neeljaiswal90/quant-stocks
Worktree: /workspace/QME-external-review/A2-V2
Reviewed commit: 4848a7f899624288ad0d34ef3bce47070de0e1f5
Reviewed tree:   d911bf583c748aac9aba76bb5c69045a08f17564

This packet is reconstructed from committed registered V2 sources because
the operator-local Windows V2 packet directory is not present in this
review environment. Sources used:

- docs/quant/NEE_120_INFERENCE_IMPLEMENTATION_V2.md
- docs/governance/OWNER_IMPLEMENTATION_CORRECTION_2026_08_17_V1.md
  (A2 correction lineage / defect history only)

Do not treat this reconstruction note as a finding against the artifact.

## Independence

- Non-Claude-lineage reviewer.
- Did not author the artifact.
- Do not rely on conclusions from Claude, the lead engineer, or another reviewer.
- Review only the exact commit, tree, files, and this packet.
- Do not modify the repository, create commits, open pull requests, update
  Linear, or alter any file inside the worktree.
- Work read-only against the worktree. Write outputs only under
  /workspace/QME-external-review/outputs/A2-V2/

The internally discovered V1 defect history is included because it is part
of the registered correction lineage. Independently reproduce the result.
Do not treat the correction record's narrative as the verdict.

## First verify

1. HEAD equals 4848a7f899624288ad0d34ef3bce47070de0e1f5
2. HEAD^{tree} equals d911bf583c748aac9aba76bb5c69045a08f17564
3. Every packet-listed artifact SHA-256 matches the checked-out bytes
4. The working tree remains unchanged
5. No secret, credential, local raw-data, or broker-log material is included

Hash convention: grouped SHA-256 = eight lowercase 8-hex groups joined by `:`.

```
python -c "import hashlib,sys; h=hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest(); print(':'.join(h[i:i+8] for i in range(0,64,8)))" <path>
```

## Bound files (must re-hash)

From the registered correction-record lineage (A2-relevant rows):

| grouped sha256 | path |
|---|---|
| `4bf93af1:47321f8e:0b2575a4:b49b8a29:c434182b:d4cf29cf:15b67bca:85f9ed31` | `qme/stats/nee120_inference_v2.py` |
| `d1496cff:a965f28d:6d5070b2:ce2822d8:883806f9:96e479e8:2e213b12:c7ea4ce3` | `tests/stats/test_nee120_inference_v2.py` |
| `64f34d4e:234b8321:fa1712a6:e47a8ffc:23b27b21:5f15571f:6069f69b:6bf0dddc` | `docs/quant/NEE_120_INFERENCE_IMPLEMENTATION_V2.md` |
| `d3a381a8:f8a7eeb6:c2f7e226:9b378498:eda5dbe7:171710d7:93d863ed:91494cff` | `qme/stats/nee120_inference.py` (V1 kernel; retained) |
| `209a9289:0fdcb191:9eddb077:93ee75e6:258b10af:2f6c5042:31a55874:d33c9f7a` | `qme/stats/effective_trials_uncertainty.py` (sibling grammar) |
| `21f402c0:d0764c33:fb4120da:853434fa:98d3a127:2e3691ea:59f9b588:4110b6c6` | `qme/stats/bootstrap.py` |
| `9f8ad5df:c03dd183:f04e9c9a:496912df:b4c7616a:40747be2:476619cd:f1ba462d` | `qme/stats/rng.py` |
| `602d4fa5:8ed3cb0d:e30393d1:4ee4c3c9:21f9c52b:10520c89:b310cb7e:3151c274` | `tests/fixtures/stats/nee120-inference-v1.json` |

Also re-hash any additional files you rely on. Record mismatches.

## Required independent work

Independently verify:

1. Rejection of exponent notation, leading plus, whitespace, leading-dot,
   trailing-dot, nonfinite values, and negative zero.
2. Holm alpha satisfies `0 < alpha < 1`.
3. Canonical inputs delegate to V1 and produce byte-identical:
   - point estimate
   - block length
   - bootstrap distribution hash
   - one-sided LCB
   - two-sided interval
   - Newey–West output
   - Holm output
4. The independent bootstrap recomputation does **not** import the
   production inference function (`run_inference` / `run_inference_v2`).
5. The V1 numerical kernel is retained; only its permissive input path is
   superseded.

A test-suite rerun alone is insufficient. Independently perform at least
one of: independently written implementation; exact Fraction or Decimal
recomputation; independently derived known-answer test; bounded brute-force
parity check; cross-platform byte replay; hand-worked calculation.

Do not import the production function into an alleged independent oracle.

## Defect history included as correction lineage (not as a verdict)

Registered A2 P1 (V1): `_parse_series` and `holm_step_down` construct
`Decimal` values from caller strings after only type/finite checks, so
non-canonical spellings (`1E-3`, `+0.001`, leading/trailing whitespace,
`.5`, `5.`, `-0`, …) are accepted. The registered correction states there
is no numeric impact on canonical inputs; the fail-closed boundary claim
was falsifiable. V1 kernel bytes are retained; V2 is a strict adapter.

Independently reproduce acceptance/rejection and byte-identity. Do not
adopt the correction narrative as your disposition.

## Scope

Executable conformance of the V2 strict adapter and retained V1 kernel:
input rejection, alpha domain, canonical delegation / byte-identity, and
an independent numerical recomputation of the registered inference outputs.

## Exclusions

- Empirical paired monthly ledger returns (M3)
- Newey–West null / p-value (diagnostic only)
- After-tax co-primary
- Blocker clearance, M0 completion, production readiness, live orders
- Any other artifact (A1, A3-V2, A4)

## Classification

P0 = unsafe, corrupting, or invalidates the evidence boundary
P1 = material correctness or contract failure
P2 = nonblocking defect or completeness issue
NOTE = informational only

Disposition: one of GO / NO_GO / BLOCKED

A GO means only that the supplied evidence is sufficient for the reviewed
scope. It does not clear a Freeze V4 blocker, complete M0, establish alpha,
establish production capacity, establish production readiness, or authorize
live orders.

Do not issue an omnibus decision for any other artifact.
