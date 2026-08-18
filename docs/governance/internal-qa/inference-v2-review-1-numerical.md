# REVIEW — QME NEE-120 inference V2 (strict canonical-decimal input adapter)

review_class = SAME_CLAUDE_LINEAGE_INTERNAL_QA
formal_independent_review_satisfied = false
INTERNAL_CLAUDE_QA_NOT_INDEPENDENT

Reviewer: fresh internal QA (spec + numerical), adversarial, did NOT author the code.
Worktree: `D:\QME-worktrees\NEE-120-inference-v2` @ HEAD `43a84301f01a1f3f7a9ee634ebe22bbd97cb2b7d` (== declared base).
Python: 3.12.10. Read-only inspection; runtime-only instrumentation in the review packet dir; no worktree edits, no mutating git.
Evidence scripts (this dir): `rev_checks.py`, `rev_nonvacuity_test.py`.

## Overall: PASS

No P0/P1/P2. Two NOTEs (process/packet, not code defects). V2 is a faithful strict pre-validation
gate that binds — not forks — the registered grammar and delegates canonical inputs to the UNCHANGED
V1 kernel byte-identically (including the bootstrap distribution hash). This is same-Claude-lineage
internal QA and does NOT satisfy the formal independent-review requirement; external independent
acceptance is still required before any T0 binding. No Freeze V4 blocker is cleared.

---

## Checks A–G

### A. SCOPE — PASS
- `git rev-parse HEAD` = `43a84301…b2b7d` (declared base).
- `git status --porcelain` = exactly 3 untracked new files, nothing modified/staged:
  `qme/stats/nee120_inference_v2.py`, `tests/stats/test_nee120_inference_v2.py`,
  `docs/quant/NEE_120_INFERENCE_IMPLEMENTATION_V2.md`.
- Protected files byte-identical to base: `git diff --stat 43a8430 -- qme/stats/nee120_inference.py
  qme/stats/effective_trials_uncertainty.py` → empty. Both V1 and the sibling grammar are untouched.
- `qme.foundation.change_tiers .` → `status: OK`. Path-deterministic tiers: v2 module `qme/stats/**`=T1,
  v2 test `tests/stats/**`=T1, docs `docs/**`=T3.

### B. GRAMMAR BINDING (bind, not fork) — PASS
- Same compiled object: `nee120_inference_v2._DECIMAL_PATTERN IS
  effective_trials_uncertainty._DECIMAL_PATTERN` → True (v2.py line 47 import + line 73 alias). Not a
  second regex.
- Fail-closed import-time pin (v2.py lines 62–69): if `_REGISTERED_DECIMAL_PATTERN.pattern !=
  _EXPECTED_CANONICAL_DECIMAL_PATTERN` (`^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$`), the module raises
  `Nee120InferenceError("NO_GO_FAIL_CLOSED", …)` at import. Verified the literal equals the registered
  pattern string.
- Full validation mirrored from the sibling's application in `_parse_matrix` (sibling lines 361–374):
  `type is str`, `len(value) > 128` (line 363), `_DECIMAL_PATTERN.fullmatch` (line 364), negative-zero
  rejection (line 374). The `len>128` bound is PART of the registered validation, not an invention.
- Negative-zero equivalence proven: V2 uses a pure-string test
  (`startswith('-') and no digit in 1–9`); across a 344-string fuzz it agrees byte-for-byte with the
  sibling's `Decimal(value).is_zero() and value.startswith('-')` (0 mismatches).
- No rule ADDED beyond the sibling. V2 omits the sibling's redundant explicit `{NaN,Infinity,…}`
  set-check and post-`Decimal` `is_finite()` check; both are subsumed by the pattern, so the omission
  changes no accept/reject outcome (NaN/Infinity/-Infinity still rejected — see reject table).

### C. STRICT REJECTION (independent, ordering-proven) — PASS
Independent script `rev_checks.py` (Sections 3–4). Every listed input is rejected with a **typed**
`Nee120InferenceError(reason="NO_GO_FAIL_CLOSED")`, with **no** untyped `decimal` exception leaking
(the harness distinguishes `DECIMAL_LEAK:*`), on all three externally-supplied surfaces
(`run_inference_v2` deltas, `holm_step_down_v2` p-values, `holm_step_down_v2` alpha).

Ordering: `v2.run_inference` and `v2.holm_step_down` were monkeypatched to recording sentinels.
For every rejected input the sentinel is **never** called (gate precedes delegation); for a canonical
input the sentinel **is** called (non-vacuity). Proven a rejected value never reaches V1.

Independent reject table (each → `Nee120InferenceError / NO_GO_FAIL_CLOSED`, V1 not reached):

| input | run_inference_v2 | holm p-value | holm alpha | reached V1? |
|---|---|---|---|---|
| `1E-3` | reject | reject | reject | no |
| `1e-3` | reject | reject | reject | no |
| `+0.001` | reject | reject | reject | no |
| `" 0.001"` | reject | reject | reject | no |
| `"0.001 "` | reject | reject | reject | no |
| `.5` | reject | reject | reject | no |
| `5.` | reject | reject | reject | no |
| `NaN` | reject | reject | reject | no |
| `Infinity` | reject | reject | reject | no |
| `-Infinity` | reject | reject | reject | no |
| `0.00_1` | reject | reject | reject | no |
| `-0` | reject | reject | reject | no |
| `-0.0` | reject | reject | reject | no |
| `0123` (leading zero) | reject | reject | reject | no |
| 131-char canonical (len>128) | reject | reject | reject | no |
| `1e999999` (overflow-shaped) | reject | reject | reject | no |
| `[]`, `None`, `"0.01"`, `("0.01",)` | reject | reject | — | no |
| `["0.01", 0.02 / 0 / None / b"…"]` (non-str elem) | reject | reject | — | no |

### D. BYTE-IDENTITY vs V1 (independent) — PASS
Independent script (Section 5). `run_inference_v2(series)` equals `run_inference(series)` field-by-field
(`series_length, point_estimate, one_sided_95_lcb, two_sided_90_interval, block_selection,
newey_west, replicate_count, seed, status`) **including** `bootstrap_distribution_sha256`, and the whole
frozen dataclass `==`.

| series | n | bootstrap_distribution_sha256 | v2==v1 (all fields) |
|---|---|---|---|
| KAT fixture `nee120-inference-v1.json` | 36 | `37c479ad:82f994a6:9357fb93:e8a60a7b:14a7319c:0efb1bb6:9ddfd5c8:ca6ac4fb` | YES — also == frozen fixture `expected` (hash + point_estimate) |
| independent generator (seed 101) | 16 | `dad37107:09cf4af1:70bff0b9:a381394b:f321a33a:6dab79be:fed74491:0d4aa837` | YES |
| independent generator (seed 202) | 48 | `fa47e8b6:8dd90ee7:8816d37a:3e6d5b2e:86706b0b:c75f0f50:3f5100d7:5de07772` | YES |

- `holm_step_down_v2` == V1 `holm_step_down` on canonical p-values + alpha (family `["0.04","0.01","0.03","0.20"]`).
- Out-of-`[0,1]` `"1.5"` passes the grammar but is still rejected via V1 with `NO_GO_FAIL_CLOSED`.
- Canonical ACCEPTANCE incl. trailing fractional zeros (`1.50`, `0.006650`, `10.00`, `0.000000`),
  `0`, negatives (`-1`, `-0.002210`); leading zeros (`0123`,`00`,`01`,`-01`) and negative zero
  (`-0`,`-0.0`) REJECTED. No canonical input V1 accepts is rejected by V2 other than the sibling-bound
  `len>128` and negative-zero — i.e. **no P1 divergence**.

### E. TEST QUALITY — PASS
- Byte-identity test really compares the bootstrap hash (test lines 205 and 217 vs frozen fixture),
  not just point estimates.
- Reject tests assert the typed error reason `NO_GO_FAIL_CLOSED` (lines 104/111/118) and
  `test_only_typed_error_escapes_no_untyped_decimal_exception` guards against untyped decimal leaks.
- Regression pin imports V1 and shows V1 (permissive) accepts value-preserving noncanonical spellings
  (`" 0.003970"`, `"+0.003970"`, `"3.970E-3"`) producing the identical result, while V2 rejects them
  (tests lines 273–298). Independently reproduced in Section 6 (V1 result == KAT; V2 rejects).
- Non-vacuity (`rev_nonvacuity_test.py`): real premise passes; two mutated premises FAIL
  (`DID NOT RAISE`; equality-incl-bootstrap-hash mismatch) → assertions have teeth. Result: `2 failed, 1 passed`.
- No `skip` / `xfail` / broad-except / `# type: ignore` in the test file (grep clean).

### F. NO KERNEL CHANGE — PASS
V2 imports `run_inference` and `holm_step_down` from V1 and delegates; it re-implements no numerics
(no bootstrap, PW selector, NW, or Holm arithmetic in v2.py). V1 is byte-identical to base (Check A),
and V1 numerical behavior is unchanged — proven by the byte-identical results reproducing the frozen KAT.

### G. GATES — PASS
- `pytest tests/stats/test_nee120_inference_v2.py -q` → `68 passed in 5.62s`.
- `ruff check qme/stats/nee120_inference_v2.py tests/stats/test_nee120_inference_v2.py` → `All checks passed!`.
- `mypy qme/stats/nee120_inference_v2.py --strict` → `Success: no issues found in 1 source file`.
  (V2 is a new leaf module imported by nothing but its own test, so repo-scope mypy cannot regress from it.)
- Secret scan gate: `scripts/check_secrets.py` (tracked scan) → `secret scan passed: 397 reviewed file(s), 0 findings`.
  Independent `detect_secrets scan --force-use-all-plugins` on the 3 new files → 0 findings (27 plugins). See NOTE-1.
- LF-only: all 3 new files `CRLF=0, bareCR=0`, final newline present.
- (Full `pytest tests/stats` not run — packet notes ~4.5 min; V2 is a leaf and does not touch V1 tests.
  `pytest_v2.txt` in the packet shows the author's `68 passed`.)

---

## Findings

| id | sev | location | issue | evidence | fix |
|---|---|---|---|---|---|
| NOTE-1 | NOTE | gate workflow / `scripts/check_secrets.py` | Unstaged `check_secrets.py` scans only Git-tracked files (`git ls-files`); the 3 new files are UNTRACKED, so they are NOT covered until `git add`+`--staged`. Not a code defect. | check_secrets.py lines 66–71; tracked run covered 397 files but not the new trio; I independently scanned the trio → 0 findings. | Author must `git add` the 3 files and run `python scripts/check_secrets.py --staged` for the real gate (packet already prescribes this). |
| NOTE-2 | NOTE | review packet `full_diff.patch` | The supplied `full_diff.patch` is empty (0 bytes) because the 3 files are untracked; a reviewer relying on it alone would see no change. | Read of `full_diff.patch` → empty; `git status` shows the 3 `??` files. | Regenerate the packet diff with `git add -N` / `git diff --no-index` against `/dev/null`, or include the file bodies. |

No P0/P1/P2 issues. No divergence: the only grammar-matching inputs V2 rejects are the sibling-bound
`len>128` bound and negative zero — both explicitly authorized by the binding and the packet.

---

## Independent divergence check
Searched for any CANONICAL input (grammar-fullmatch, `len<=128`, not negative zero) that V1 accepts but
V2 rejects: NONE found (344-string fuzz + full reject/accept sweep). No P1 divergence to report.

Overall: **PASS** (no P0/P1). Same-Claude-lineage internal QA only.

review_class = SAME_CLAUDE_LINEAGE_INTERNAL_QA
formal_independent_review_satisfied = false
