# Alpha Vantage common-stock proxy review V2

Status: `V2_AUTOMATED_CORRECTION_COMPLETE_INDEPENDENT_SAMPLE_PENDING`.

This is an additive correction and review packet for the immutable 2026-07-31
V1 proxy snapshot. It does not modify V1 or claim an authoritative US common-
stock universe.

## Source evidence

- V1 snapshot: SHA-256
  `151f89d9:f1b533f5:235f12ae:0665a7af:b417a5ae:5412d70b:43ba86bb:87d0ea77`,
  786,564 bytes, 5,655 included rows.
- V1 review log: SHA-256
  `d34e6345:251d17b4:ee29b086:28b4626b:14c9e78f:7081d487:e274b37d:66c89daf`,
  1,274,926 bytes, 1,724 LF-terminated JSON objects.
- V2 candidate: `tests/fixtures/governance/av-proxy-review-candidate-v2.json`.
- Deterministic independent-review sample:
  `tests/fixtures/governance/av-proxy-independent-review-sample-v2.json`.

## Correction

V1 intentionally defaulted unrecognized vendor `Stock` rows into a common-
stock proxy. The production-sized snapshot showed obvious non-common forms in
that default bucket. V2 applies four ordered, fail-closed exclusions:

1. an explicit standalone `ETF` name token → `ETF`;
2. an explicit `ETN` or `ETNs` name token → `ETN`;
3. an explicit listed-note/debenture name → `DEBT_SECURITY`;
4. a V1 `NASDAQ_FIFTH_CHARACTER_MISCELLANEOUS_KEPT` review item →
   `AMBIGUOUS_IDENTITY`, except `GOOGL`, which is explicitly retained as the
   separately represented Alphabet Class A common share corroborated by the
   exact Nasdaq GIW evidence.

The result excludes 201 V1-included rows: 118 ETF, nine ETN, 18 debt, and 56
ambiguous fifth-character rows. It retains 5,454 proxy rows.

## Complete review-log disposition

Every one of the 1,724 V1 review entries has a deterministic disposition:

- 1,658 were already excluded or were non-active in V1;
- 65 are excluded by V2;
- one (`GOOGL`) is retained by the explicit common-class allowlist.

No review item is silently dropped. The candidate still reports
`proxy_snapshot_reviewed=false` because the independently selected 64-row
sample has not been signed by a different reviewer.

## Independent sample gate

The sample is not author-chosen. Within each stratum, it uses the lowest
SHA-256 of
`QME-AV-PROXY-V2-INDEPENDENT-SAMPLE-2026-07-31 || NUL || symbol`, plus explicit
`GOOGL`. It contains 10 ambiguous, 10 debt, 10 ETF, all nine ETNs, and 25
retained rows. A qualifying reviewer must record reviewer identity, exact
artifact hashes, scope, P0/P1/P2 counts, and a GO/NO_GO disposition.

## Nonclaims

This packet does not claim complete historical PIT membership, complete ADR or
REIT classification, broker or production readiness, M0 completion, alpha,
capacity, or live-order authority. Some closed-end funds, BDCs, and MLP forms
may remain by the owner's registered limited-proxy boundary. No freeze blocker
changes until independent review, exact-byte owner signoff, protected CI, a
successor freeze, and a causally later receipt.
