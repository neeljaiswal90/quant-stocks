# REVIEW2 — NEE-120 inference V2 — SPECIFICATION & QUALITY (fresh, adversarial)

review_class = SAME_CLAUDE_LINEAGE_INTERNAL_QA
formal_independent_review_satisfied = false
INTERNAL_CLAUDE_QA_NOT_INDEPENDENT

Scope: read-only inspection of `D:\QME-worktrees\NEE-120-inference-v2` (HEAD =
base `43a84301f01a1f3f7a9ee634ebe22bbd97cb2b7d`, 3 untracked new files). Focus:
spec conformance, no-bypass authority, grammar-lineage disclosure, doc/test
adequacy. A separate reviewer did the numerical pass; I re-verified the matrix
independently (`rev2_matrix.py`) but spent effort on spec/quality. This is
same-Claude-lineage internal QA, NOT a formal independent review.

Files under review (untracked):
- `qme/stats/nee120_inference_v2.py` (T1)
- `tests/stats/test_nee120_inference_v2.py` (T1)
- `docs/quant/NEE_120_INFERENCE_IMPLEMENTATION_V2.md` (T3)

Read-only reference: V1 `qme/stats/nee120_inference.py`, sibling
`qme/stats/effective_trials_uncertainty.py`, task packet
`taskpacket-NEE-120-inference-v2.yaml`.

---

## Checks 1–9

### CHECK 1 — Authoritative entrypoints / no bypass — PASS (with P2)
- Public `run_inference_v2` (v2 py:120) and `holm_step_down_v2` (v2 py:133);
  both in `__all__` (v2 py:146–150).
- Same result types as V1: both types imported from V1 (v2 py:48–54);
  `run_inference_v2 -> Nee120InferenceResult` returns `run_inference(...)`
  (v2 py:130); `holm_step_down_v2 -> HolmResult` returns `holm_step_down(...)`
  (v2 py:143). Types are V1's own dataclasses (v1 py:117, 125).
- Genuine gate: validation runs strictly before delegation (verified below and
  in `rev2_matrix.py`), so V2 is a real strict boundary, not a rename.
- GAP (P2-1): neither the module docstring nor the doc **designates V2 as the
  authoritative accepted path** or warns that V1 remains publicly importable and
  will silently accept non-canonical input if called directly. V1's
  `run_inference`/`holm_step_down` stay exported (v1 py:442–453) and — because
  only 3 new files exist — nothing in the repo is re-wired to route through V2
  (the T0 boundary evaluator still consumes V1). The gate is genuine, but its
  authoritativeness is asserted nowhere in-artifact; the forthcoming T0 record
  that "names the V2 entrypoint authoritative" is not reinforced here. This does
  not undercut correctness (candidate status is disclosed), but the no-bypass
  designation is missing.

### CHECK 2 — Validation coverage — PASS (with P2 on alpha semantics)
- Every paired delta: `run_inference_v2 -> _require_canonical_series(deltas)`
  (v2 py:129) -> `_require_canonical_decimal` per element (v2 py:116–117).
- Every Holm p-value: `_require_canonical_series(p_values)` (v2 py:141).
- Holm alpha, incl. the default: `_require_canonical_decimal(alpha)` (v2 py:142)
  — alpha is validated on every call even when not externally supplied (default
  "0.05" is validated too).
- All checks are pure string ops **before any `Decimal()`**: type-is-str
  (v2 py:93), length+`fullmatch` (v2 py:97), negative-zero (v2 py:104) — no
  `Decimal()` appears in the adapter at all. Confirmed by reading code paths,
  not just tests.
- Semantic ranges after lexical validation:
  - p-value `[0,1]`: **enforced**, delegated to V1 (v1 py:368). PASS.
  - alpha "registered range": **NOT enforced anywhere.** V1 only does
    `Decimal(alpha)` (v1 py:362) and `alpha_d / Decimal(m-rank)` (v1 py:377);
    there is no alpha bound in V1 or V2. A canonical out-of-range alpha
    (e.g. `"2.0"`, `"-12.500"`) passes V2's lexical gate and V1's non-check and
    is used directly in the Holm thresholds. See finding P2-2. (Note: the doc
    does **not** overclaim an alpha range — its "[0,1] range check" refers to
    p-values only — so this is a coverage gap, not a false statement.)

### CHECK 3 — Typed failure contract — PASS
- Every rejection raises `Nee120InferenceError("NO_GO_FAIL_CLOSED", ...)`:
  import-time drift guard (v2 py:66), non-str (v2 py:94), length/grammar
  (v2 py:98), negative-zero (v2 py:105), shape (v2 py:115). Grep of the module
  shows **no** `raise ValueError`/`raise Exception`/bare raise and **no**
  `except`/`assert`. `Nee120InferenceError` carries `.reason` (v1 py:71–77).
- No untyped `decimal` exception can escape: all validation precedes `Decimal()`;
  `rev2_matrix.py` drove `1e999999`, `1E-3`, `NaN`, `Infinity`, `0.00_1` and a
  129-char canonical string through all three public surfaces — **zero untyped
  escapes** (TOTAL_UNEXPECTED: 0).

### CHECK 4 — Grammar lineage disclosure — PASS (with P2 on missing NOTE)
- Bind-by-reference is real: `from ...effective_trials_uncertainty import
  _DECIMAL_PATTERN` (v2 py:47) then `_DECIMAL_PATTERN = _REGISTERED_DECIMAL_PATTERN`
  (v2 py:73). `rev2_matrix.py` proves `v2._DECIMAL_PATTERN is
  sibling._DECIMAL_PATTERN` -> **True** (same compiled object; sibling py:108).
- Fail-closed pin: `_EXPECTED_CANONICAL_DECIMAL_PATTERN` (v2 py:62) + import-time
  drift guard raising typed error (v2 py:64–69). Pinned literal == sibling
  pattern -> **True**.
- Disclosure present in module (docstring "Grammar binding", v2 py:23–33) and
  doc (v2 doc:32–54), both stating it is a **bound/replicated** authority from
  the sibling and explicitly **not a new regex** ("V2 does not introduce a second
  regex", doc:45). Negative-zero rule disclosed as the pure-string equivalent of
  the sibling's `is_zero() and startswith('-')` (v2 py:102–103; sibling py:373).
  Not presented as an independently chosen regex.
- GAP (P2-3): both the module (v2 py:29 and py:58) and the doc (doc:37) cite
  `NOTE-inference-v2-canonical-grammar.md` as the record that *determined* the
  authoritative source; that file **does not exist anywhere in the worktree**
  (Glob + `git ls-files` + filesystem all negative). Dangling citation / missing
  prerequisite artifact. The binding is still substantively provable in-code, so
  this is traceability, not correctness.

### CHECK 5 — Accept/reject matrix (independent, `rev2_matrix.py`) — PASS
Ran my own script against the public surfaces AND the shared gate. Results:
- REJECT (all -> `REJECT[NO_GO_FAIL_CLOSED]` lexically and
  `NO_GO_FAIL_CLOSED` on delta, p-value, and alpha surfaces):
  `1E-3, 1e-3, +0.001, " 0.001", "0.001 ", .5, 5., NaN, Infinity, -Infinity,
  -0, -0.0, -0.000, 0123, 0.00_1, 1e999999, 00, -01, "" (empty),` and a
  **129-char** canonical-shaped string (`len>128`).
- ACCEPT (lexical, unmutated): `0, 0.0, 0.001, 1, 1.0, 1.2300, -0.001, -12.500,
  1.50, 0.006650, -0.002210, 10.00,` and a **128-char** canonical string.
- Non-str/shape (`[]`, `None`, `["0.01",0.02]`, `["0.01",0]`, `"0.01"`,
  `("0.01",)`, `[None]`) -> `NO_GO_FAIL_CLOSED` on all.
- Confirmed on all THREE surfaces. TOTAL_UNEXPECTED: 0.

### CHECK 6 — No V1 / sibling change — PASS
- `git hash-object` of working-tree files == base blob (`git rev-parse
  43a8430:<path>`) for **both**:
  - `qme/stats/nee120_inference.py` -> `719891a3…` MATCH
  - `qme/stats/effective_trials_uncertainty.py` -> `4cd2df71…` MATCH
- `git status --porcelain` shows only the 3 new untracked files; HEAD == base.
- V2 re-implements no numerics: it constructs no `Decimal`, runs no bootstrap/
  PW/NW/Holm math; it delegates (v2 py:130, 143). PASS.

### CHECK 7 — Doc quality — PASS
- Implementation id stated: `QME-NEE120-INFERENCE-IMPLEMENTATION-V2` (doc:17;
  module const v2 py:56; test v2 test:90).
- Statistical methods/kernel unchanged: doc:12–13, 17–19, 99–100.
- Candidate/nonclaim status: doc:93–107 — no blocker cleared, all 13 active,
  EVIDENCE stays ACTIVE, `formal_independent_review_satisfied` false,
  `milestone_m0_complete` false, external acceptance required before T0. Status
  string is appropriately hedged (`…CANDIDATE_BLOCKERS_RETAINED`, doc:3). Module
  carries matching nonclaims (v2 py:36–40).
- Grammar lineage: doc:32–54. No overclaim of acceptance/clearance detected.

### CHECK 8 — Test adequacy — PASS (with P2 mutation gap)
Present and correct:
- Grammar-binding equality incl. **same-object** identity (test:79–86).
- Reject matrix on all 3 surfaces (deltas test:98–104; p-values test:107–111;
  alpha test:114–118).
- Byte-identity incl. bootstrap hash: KAT fixture (test:208–223) + 3 canonical
  series (test:226–235); `bootstrap_distribution_sha256` asserted (test:205).
- Negative-zero (test:170–180), leading-zero (test:160–167), trailing-zero
  (test:183–185).
- Holm delegation identity (test:243–251) + out-of-[0,1] via V1 (test:254–263).
- A2 regression pin, V1 permissive vs V2 strict, both surfaces (test:276–287,
  290–298).
- Gaps: (P2-4) the `len>128` bound (v2 py:81,97) is **not exercised** by any
  project test — the reject-list `"1e999999"` (test:70) is caught by the regex
  ('e'), not by the length branch, so a mutation deleting the length guard
  survives the suite (I verified rejection independently in `rev2_matrix.py`).
  (Related) no test covers alpha out-of-range (ties to P2-2, since V1 has no
  alpha bound to assert against).

### CHECK 9 — Gates — PASS
- `ruff check qme/stats/nee120_inference_v2.py tests/stats/test_nee120_inference_v2.py`
  -> `All checks passed!` (exit 0).
- `mypy qme/stats/nee120_inference_v2.py --strict`
  -> `Success: no issues found in 1 source file` (exit 0).
- `pytest tests/stats/test_nee120_inference_v2.py -q`
  -> `68 passed in 5.43s` (exit 0).
- (Did not run the full `tests/stats`; V1/sibling are byte-identical to base, so
  no V1 impact is expected.)

---

## Findings

| ID | Sev | Location | Issue | Fix |
|----|-----|----------|-------|-----|
| P2-1 | P2 | v2 py:23–33 docstring; doc (all) | No-bypass authority not designated: neither module nor doc states V2 is the authoritative accepted entrypoint or warns that V1 stays importable and silently accepts non-canonical input; V2 is not wired into any caller. | Add an "Authoritative entrypoint — do not bypass" statement to the module docstring and the doc; note that external decimal input must go through V2 and that direct V1 calls are the pre-correction permissive path. |
| P2-2 | P2 | v2 py:142; v1 py:362,377 | Alpha has no semantic range check anywhere (V1 does not bound alpha; V2 only validates it lexically). A canonical out-of-range alpha (`"2.0"`, `"-12.500"`) passes both layers into the Holm thresholds. Matters once the family grows beyond m=1. | Add an explicit alpha bound in `holm_step_down_v2` (e.g. `0 < alpha <= 1` or the registered range) before delegation, since V1 will not enforce it; add a test. |
| P2-3 | P2 | v2 py:29,58; doc:37 | `NOTE-inference-v2-canonical-grammar.md` is cited as the grammar-authority determination record but does not exist in the worktree (untracked/absent). Dangling citation / missing prerequisite artifact. | Add the NOTE (the authority-selection record), or replace the citation with the in-repo provable binding (same-object import + pinned literal + binding test). |
| P2-4 | P2 | v2 py:81,97; test (reject list) | The `len>128` guard is not exercised by any project test (`"1e999999"` is regex-caught, not length-caught); a mutation removing the bound survives the suite. | Add a `>128`-char canonical-shaped reject case to the reject parametrization on all three surfaces. |
| NOTE-1 | NOTE | v2 py:78–80 | Length-bound rationale claims it prevents "untyped decimal overflow," but a `<=128`-char canonical decimal (`<=~10^127`) cannot overflow Decimal (Emax 999999); the bound is really a parse-cost/DoS sanity limit. Harmless but imprecise. | Reword the rationale (bounded input size / defense-in-depth), or drop the overflow justification. |
| NOTE-2 | NOTE | test:183–185, 226–235 | Project accept-tests use representative values; the task's exact accept list (`"1.2300"`, `"-12.500"`) is not each asserted. Independently confirmed accepted in `rev2_matrix.py`. | Optional: extend the accept parametrization to the full owner list. |

No P0 or P1 findings.

---

## Overall: PASS

The V2 strict adapter is spec-conformant on the load-bearing requirements: a
genuine pre-`Decimal()` gate on all three surfaces (deltas, Holm p-values, Holm
alpha), typed fail-closed rejection with `NO_GO_FAIL_CLOSED` and zero untyped
escapes, a grammar bound by reference to the **same compiled object** as the
registered sibling with a fail-closed drift pin, byte-identical V1/sibling source
(no re-implemented numerics), and honest candidate/nonclaim documentation. All
gates (ruff, mypy --strict, 68 V2 tests) are green and my independent matrix
matches. Remaining items are P2/NOTE hardening: designate the no-bypass authority
(P2-1), bound alpha semantically since V1 does not (P2-2), supply or de-cite the
missing grammar-authority NOTE (P2-3), and close the `len>128` mutation gap
(P2-4). None gate the result to FAIL under the P0/P1 rule, but P2-1 and P2-3 are
the ones most worth closing before the T0 record names V2 authoritative.

Reminder: this is same-Claude-lineage internal QA; external independent
acceptance is still required before any T0 binding.

review_class = SAME_CLAUDE_LINEAGE_INTERNAL_QA
formal_independent_review_satisfied = false
