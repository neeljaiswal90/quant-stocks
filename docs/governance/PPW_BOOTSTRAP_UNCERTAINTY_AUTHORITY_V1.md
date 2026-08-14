# PPW bootstrap uncertainty authority V1

Status: `SOURCE_EQUATIONS_REGISTERED_OWNER_SELECTIONS_UNRESOLVED_NO_EXECUTION`

This NEE-204 packet registers a reviewed source/equation boundary for the corrected
Politis–White stationary-bootstrap block selector. It does not implement the
selector, generate a bootstrap distribution, compute `N_eff_used`, or change a
Specification Freeze V4 blocker. The verifier is deliberately network-free: source
URLs are provenance, while exact reviewed byte sizes and grouped SHA-256 values are
the immutable identities.

## Governing and non-governing authority

The protected project authority is the M0 proposal §§4.2–4.3, experiment-family
registration, owner supplement A0, NEE-120 V2, the NEE-175 point-only kernel, the
deterministic statistics kernel manifest, and Specification Freeze V4. Their exact
paths and hashes are bound in the config and their available manifests are replayed
through every recorded leaf.

The method sources are:

- Politis and White (2004), *Automatic Block-Length Selection for the Dependent
  Bootstrap*, Econometric Reviews 23(1), 53–70,
  [DOI 10.1081/ETC-120028836](https://doi.org/10.1081/ETC-120028836), with the
  reviewed [author-hosted paper](https://www.math.ucsd.edu/~politis/SBblock-revER.pdf).
- Patton, Politis, and White (2009), *Correction to Automatic Block-Length
  Selection for the Dependent Bootstrap*, Econometric Reviews 28(4), 372–375,
  [DOI 10.1080/07474930802459016](https://doi.org/10.1080/07474930802459016),
  with the reviewed [author-hosted correction](https://public.econ.duke.edu/~ap172/SBblockCORRECTION_jan08.pdf).
- Andrew Patton's reviewed
  [corrected December 2007 Matlab code](https://public.econ.duke.edu/~ap172/opt_block_length_REV_dec07.txt)
  and its [lag dependency](https://public.econ.duke.edu/~ap172/mlag.m.txt).

The papers and author code validate method provenance. They do not select QME owner
policy. In particular, the author code emits one selector value per input column,
uses its own finite-sample defaults and cap, and notes that the stationary-bootstrap
expected block length need not be an integer. QME's protected NEE-120 overlay instead
requires ceiling, a floor of 3, a cap of `floor(n / 4)`, `n >= 12`, and no fallback.
The raw selector and the registered overlay therefore must be implemented as
distinct, testable stages.

## Corrected common equations

Only the common corrected symbolic core is registered here:

```text
lambda(u) = 1                              when |u| <= 1/2
          = 2 * (1 - |u|)                 when 1/2 < |u| <= 1
          = 0                              otherwise

G_hat   = sum[k=-M..M] lambda(k/M) * |k| * gamma_hat(k)
g_hat_0 = sum[k=-M..M] lambda(k/M) * gamma_hat(k)
D_hat_SB = 2 * g_hat_0^2
b_hat_SB_raw = (2 * G_hat^2 * n / D_hat_SB)^(1/3)
```

The 2009 correction replaces the original stationary-bootstrap denominator with
`D_hat_SB = 2 * g_hat_0^2`. No numeric result is authorized if the denominator is
zero, non-finite, or undefined. This packet intentionally does not resolve the
centering, autocovariance denominator, finite-sample lag mechanics, or any other
implementation choice hidden behind the symbols.

## Required owner selections

Nine typed entries remain unresolved:

1. how 96 column-specific selector outputs become one common block length;
2. centering, covariance denominator, lag-window, numeric, and finite-sample rules;
3. `K_N`, `m_max`, significance-run boundaries, and empty/no-significant fallback;
4. constant, nearly constant, non-finite, short, zero-denominator, and invalid input actions;
5. one shared row-index vector across all 96 columns and full-estimator refit semantics;
6. PPW selected once versus reselected per replicate, selector-to-integer-kernel
   handoff, RNG stream, restart threshold, and draw order;
7. the exact `P97.5` order-statistic/interpolation/tie rule for 2,000 replicates;
8. invalid-replicate handling and its separation from the point-failure `m=96` rule;
9. independent expected distribution hash, interval, and `N_eff_used` for the seeded
   60-by-96 fixture after selections 1–8 are approved.

No maximum, median, mean, quantile, or first-column aggregation is implied. No
replicate may be silently deleted or retried. NEE-120's 10,000-replicate percentile
interval and order statistics are not substitutes for NEE-122's 2,000-replicate
`N_eff` uncertainty rule. Holm family size `m` remains distinct from DSR `N_eff`.

## Fixture and verifier boundary

The source-equation fixture proves the corrected symbolic denominator, raw block
formula, flat-top kernel boundary values, and the existence of source/overlay
variant conflicts. Constant, IID, negatively correlated, short-series, 96-column,
invalid-replicate, quantile, and end-to-end cases remain explicitly non-executable;
they contain no invented expected selector or interval values.

The verifier rejects duplicate JSON keys, non-finite JSON tokens, invalid UTF-8,
oversized/nonregular files, path escape, links/reparse points, mid-read mutation,
incorrect grouped digests, altered authority bytes, altered transitive manifest
leaves, changed formulas, missing/relabeled owner selections, reordered or changed
Freeze V4 blocker rows, changed claims, and outer-manifest drift.

Selection 6 also leaves unresolved whether PPW is selected once on the original
aligned matrix or reselected inside every bootstrap replicate. The protected
full-estimator refit rule establishes Ledoit–Wolf refitting; it does not silently
choose PPW reselection timing.

The public verification result is immutable data, not an in-process security
capability. Its constructor and subclass paths reject direct use. The serializer
reopens and independently replays the repository, then exact-compares every supplied
field with a private state tuple before emitting canonical bytes derived directly
from that fresh repository state. Public result properties are never the
authoritative serialization source. Consequently property poisoning, an
`object.__new__` forgery, or post-verification slot mutation cannot authorize
altered output; a byte-for-byte equivalent forged value conveys no authority beyond
the independent repository replay.

## Exact nonclaims

- no executable PPW selector;
- no bootstrap interval or `N_eff_used`;
- no DSR or Holm output;
- no empirical or production inference result;
- no removal of any of the 13 Specification Freeze V4 blockers;
- no alpha, production-readiness, M0-completion, or live-order authority.

NEE-120's inference-implementation blocker and NEE-122's correlated-fixture and
dependence-estimator implementation blockers therefore remain active. A later
versioned owner registration, executable implementation PR, independent Windows and
Linux known-answer replay, successor freeze, and separate protected receipt are
still required before blocker retirement can be considered.
