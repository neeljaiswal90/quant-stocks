# NEE-169 Stage 0 — deterministic local UI contract

Status: `IMPLEMENTED_BOUNDED_CANDIDATE`

Evidence scope: `SYNTHETIC_CONTRACT_ONLY`

Production activation: `BLOCKED_UNTIL_PRODUCER_SCHEMAS_ACCEPTED`

## Outcome

This slice freezes the presentation boundary required before building the local ticker and
score interface. It supplies strict schemas, a field/source-pointer registry, exact Decimal
display rules, the domain-separated membership-set construction, total run-quality and
completeness states, bounded resource policy, synthetic compatibility declaration, and
valid/adversarial fixtures.

It does not implement Flask, Waitress, routes, HTML, snapshot publication, a production
producer adapter, Nasdaq membership acquisition, strategy calculations, or agent execution.

## Authority boundary

The producer remains the only authority for identity, membership, scores, momentum, rank,
selection, portfolio, risk, cost, capacity, P&L, and agent review-set membership. The UI
contract permits only:

- `COPY` of producer-owned values;
- `FORMAT_DECIMAL` under the exact half-even display policy;
- `REDACT` for registered presentation-only fields; and
- `MAKE_SORT_KEY` for registered presentation-only ordering.

Quantitative fields may only use `COPY` or `FORMAT_DECIMAL`. Every registered output field
names its source artifact, JSON pointer, source schema, transform and version, authority
class, numeric metadata, and missing policy. There is no default field.

## Exact membership

The registered construction is:

```text
SHA-256(
  UTF8("QME_MEMBERSHIP_SET_V1\0") ||
  canonical_json(sort_utf8(NFC(security_ids)))
)
```

IDs preserve case. Duplicate, NFC, or case-fold collisions are conflicting. Complete
membership requires exact set equality, the registered hash, the exact count, and the six
status buckets summing to the same count. Equal count with a wrong member fails.

## Decimal display

For canonical Decimal `x`, positive scale `s`, precision `d`, and display Decimal `y`:

```text
abs((y / s) - x) <= 0.5 * 10^(-d) / s
```

The implementation uses Decimal precision 50 and `ROUND_HALF_EVEN`. Present values include
both decimals. Missing, blocked, invalid, and stale values include neither. Negative zero is
normalized. Browser numeric parsing remains forbidden; a later viewer must display the
registered `display_text` verbatim.

## Fail-closed state algebra

Quality precedence, earliest applicable state wins:

```text
CORRUPT > CONFLICTING > UNSUPPORTED_SCHEMA > INVALID > MISSING > BLOCKED > STALE > DEGRADED > VALID
```

Completeness is separately `CONFLICTING`, `INCOMPLETE`, or `COMPLETE`. Unknown values never
become `VALID`, zero, blank, false, or `Hold`.

## Bounded fixture contract

The only compatible source is `qme.synthetic.ui_source.v1`. Production adapters remain
blocked. Snapshot envelopes are limited to 16 MiB, individual payloads to 8 MiB, 32 payloads,
200 members, JSON depth 32, and 100,000 untrusted text characters. Stage 1 must enforce the
same limits while reading and publishing actual files.

Runtime validators reject:

- unsupported schemas or evidence-state promotion;
- source-field defaults or quantitative redaction/recalculation;
- non-finite/noncanonical decimals, nonpositive scale, wrong rounding, and display tampering;
- duplicate/case/NFC member identities, equal-count wrong-member sets, and six-bucket drift;
- missing, extra, duplicate, oversized, path-escaping, size-mismatched, or hash-mismatched
  payloads; and
- generation timestamps preceding the analysis cutoff.

## Next gate

Stage 1 may begin only from this reviewed contract. It must implement bounded same-byte
producer validation, deterministic projection, exact membership reconciliation, and atomic
content-addressed publication. No production compatibility claim is permitted until the
producer tickets publish accepted schemas and evidence identities.
