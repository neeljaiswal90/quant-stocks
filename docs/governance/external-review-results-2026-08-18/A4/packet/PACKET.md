# External-review packet — A4 only

Artifact: A4 — XNAS calendar / session vector
Repository: neeljaiswal90/quant-stocks
Worktree: /workspace/QME-external-review/A4
Reviewed commit: d890078803c58f3ca995ff80004b025583fe6b2e
Reviewed tree:   0d00c7b1ac87409c67ec32cbd0cde29c316d8334

This packet is reconstructed from committed registered sources because the
operator-local Windows packet directory is not present in this review
environment. Source: docs/governance/INDEPENDENT_REVIEW_PACK_2026-08-16.md
(A4 section) plus the bound files at the reviewed commit.

Do not treat this reconstruction note as a finding against the artifact.

## Independence

- Non-Claude-lineage reviewer.
- Did not author the artifact.
- Do not rely on conclusions from Claude, the lead engineer, or another reviewer.
- Review only the exact commit, tree, files, and this packet.
- Do not modify the repository, create commits, open pull requests, update
  Linear, or alter any file inside the worktree.
- Work read-only against the worktree. Write outputs only under
  /workspace/QME-external-review/outputs/A4/

## First verify

1. HEAD equals d890078803c58f3ca995ff80004b025583fe6b2e
2. HEAD^{tree} equals 0d00c7b1ac87409c67ec32cbd0cde29c316d8334
3. Every packet-listed artifact SHA-256 matches the checked-out bytes
4. The working tree remains unchanged
5. No secret, credential, local raw-data, or broker-log material is included

Hash convention: grouped SHA-256 = eight lowercase 8-hex groups joined by `:`.

```
python -c "import hashlib,sys; h=hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest(); print(':'.join(h[i:i+8] for i in range(0,64,8)))" <path>
```

## Bound files (must re-hash)

| grouped sha256 | path |
|---|---|
| `348e67d9:92183c49:4f625f90:ada2bb58:e90165f5:3fd90419:3e6d8584:7eb0e290` | `configs/governance/xnas-session-calendar-evidence-v1.json` |
| `31077a2d:6b7a6eb9:f974b343:b91c99d5:b48c2049:d38758a2:a455c10d:5ca2f453` | `configs/governance/xnas-session-calendar-evidence-v1.hashes.json` |
| `cc19e055:15434882:2260f6cf:f3763218:c487f751:50ab933f:9fb06155:e827c6f2` | `schemas/governance/xnas-session-calendar-evidence-v1.schema.json` |
| `c70349fb:df114824:918a6bd9:8ab1f8d9:7a51faf1:ab806134:a58841e7:e9be64aa` | `qme/governance/xnas_calendar_evidence_v1.py` |
| `8ce09033:276ea754:5903f93c:1fef236a:2483ac98:516eefba:2e4e1fbb:0adf1daa` | `tests/governance/test_xnas_calendar_evidence_v1.py` |
| `a414d89a:2d18a3e2:27c7cfab:05c271c8:209490e3:beb49bf0:bb1a00f1:9ecd2a5e` | `tests/fixtures/governance/xnas-session-calendar-2010-2027-v1.candidate.json` |
| `d9646f29:8439975d:f8a9ab77:45662b8b:b0b74625:591c1144:96570031:b684e2d8` | `tests/fixtures/governance/xnas-session-calendar-v1.official-cases.json` |
| `79750595:76fd61ef:4b82be9b:4cdf2c83:cb0e4e83:349fbe13:f6d7cc64:4e5e37e5` | `scripts/materialize_xnas_calendar_v1.py` |
| `6b4e0591:4b9c5a48:5dc4fcd7:a59aaef2:6c7c1b63:cf7a8232:81617b40:b7af8b7c` | `requirements-xnas-calendar-generator.in` |
| `e040a582:49116e7a:7442e438:ffb6cd48:b745664a:3c034d96:6d75da6a:08588278` | `requirements-xnas-calendar-generator.lock` |
| `7cef8951:814d8ef8:5cc3f526:0c894da6:c6bc436c:ed8e3188:a3901c90:8f494571` | `requirements-xnas-calendar-generator-linux.lock` |
| `74869cc5:ba427a9a:7909ac96:e0448d25:3697d5ed:f5897fe0:a89fd679:31043c66` | `.github/workflows/xnas-calendar-linux.yml` |

Also re-hash the ordered session-vector candidate if present:

- `tests/fixtures/governance/xnas-ordered-session-vector-2010-2027-v1.candidate.json`

and confirm the hashes recorded inside the evidence JSON.

## Required independent work

1. Every bound file hash must match.
2. Independent regeneration from the pinned dependencies, if the
   environment can do so honestly. This sandbox is CPython 3.10; the
   registered generator is CPython 3.12.10. Do not invent a byte-identical
   regeneration you did not perform. If regeneration is blocked by the
   Python version, record BLOCKED-or-partial status for that *step* and
   still complete every check that does not require regeneration.
3. Byte comparison of calendar and ordered session-vector outputs when
   regeneration is possible.
4. Independent verification of closures and half-days against the official
   case fixture and against the candidate calendar bytes.
5. The complete official-case interval comparison.
6. Verification of the protected-main Linux replay evidence, including
   workflow `.github/workflows/xnas-calendar-linux.yml` and protected-main
   run `31922669149` (inspect via GitHub if reachable).
7. Confirmation that a GO only establishes evidence sufficiency; it does
   not itself flip `linux_generator_hash_lock_available` or
   `windows_linux_byte_replay_verified`. Those flags are false in the
   reviewed evidence file; flipping them is a later T0 cascade, not this
   review.

A test-suite rerun alone is insufficient.

## Expected / boundary (from the registered pack)

- The evidence file currently carries
  `linux_generator_hash_lock_available = false` and
  `windows_linux_byte_replay_verified = false`.
- The review confirms whether the Linux hash-lock + byte-replay evidence
  is sufficient to *support a later flip*. This review does not flip them.
- Any future correction must produce `XNAS_CALENDAR_V2`; V1 is never
  overwritten in place.
- `complete_official_history_verified` stays false (known retained
  limitation — not required for GO).

## Scope

Byte-reproducibility of the calendar + ordered session vector, the pinned
generator lock chain, bounded official-case checks, and the Linux
protected-main replay evidence.

## Exclusions

- Complete official historical calendar authority (not claimed)
- Future published schedules as observed-market authority
- Flipping the two freeze flags
- Blocker clearance, M0 completion, production readiness, live orders
- Any other artifact (A1, A2-V2, A3-V2)

## Classification

P0 = unsafe, corrupting, or invalidates the evidence boundary
P1 = material correctness or contract failure
P2 = nonblocking defect or completeness issue
NOTE = informational only

Disposition: one of GO / NO_GO / BLOCKED

A GO means only that the supplied evidence is sufficient for the reviewed
scope. It does not clear a Freeze V4 blocker, complete M0, establish alpha,
establish production capacity, establish production readiness, or authorize
live orders. It does not itself flip the two freeze flags.

Do not issue an omnibus decision for any other artifact.
