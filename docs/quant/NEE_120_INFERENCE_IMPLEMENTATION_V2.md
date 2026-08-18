# NEE-120 inference V2 — strict canonical-decimal input adapter

Status: `BOUNDED_INFERENCE_STRICT_INPUT_ADAPTER_CANDIDATE_BLOCKERS_RETAINED`.

This packet adds a **versioned strict input adapter**,
`qme/stats/nee120_inference_v2.py`, in front of the unchanged V1 numerical
kernel `qme/stats/nee120_inference.py`. It corrects the lead-confirmed A2 P1:
V1's `_parse_series` and `holm_step_down` build `Decimal` values directly from
caller strings, so non-canonical decimal text is silently accepted. V2 validates
every externally supplied decimal string against the repository's authoritative
canonical-decimal grammar before any `Decimal()` construction and then delegates
canonical inputs to V1 unchanged. It does **not** re-implement the numerical
kernel, amend V1, change any registered statistical method, or clear a blocker.

## Authoritative entrypoints (no bypass)

`run_inference_v2` and `holm_step_down_v2` are the **authoritative accepted
entrypoints** for NEE-120 inference; callers must use them. The V1 symbols
`run_inference` and `holm_step_down` in `qme/stats/nee120_inference.py` remain
importable and, being hash-bound, are not removed — but they still *silently
accept* non-canonical decimal input (the A2 P1 defect), so importing V1 directly
bypasses this validation gate. V1 must not be called with unvalidated external
strings. The forthcoming T0 correction record names the V2 entrypoints
authoritative; until that record lands, V2 is the strict candidate entrypoint.

## What V2 changes and what it does not

- `IMPLEMENTATION_ID = "QME-NEE120-INFERENCE-IMPLEMENTATION-V2"`. The registered
  statistical method ids (paired-delta point estimate, uncentered percentile
  stationary bootstrap, Newey--West diagnostic, Holm step-down) are unchanged.
- V1 is hash-bound by the merged owner-decision record. It is imported, never
  edited. The registered sibling grammar module
  `qme/stats/effective_trials_uncertainty.py` is likewise imported, never edited.
- The two new public entrypoints are strict wrappers only:
  - `run_inference_v2(deltas)` validates that `deltas` is a non-empty list whose
    every element is a bounded canonical decimal string that is not negative
    zero, then returns `run_inference(deltas)` unchanged.
  - `holm_step_down_v2(p_values, *, alpha="0.05")` validates every p-value string
    and `alpha` against the same grammar, additionally enforces the
    significance-level validity domain `0 < alpha < 1` (evaluated only after the
    lexical grammar check), then returns `holm_step_down(p_values, alpha=alpha)`
    unchanged. The p-value `[0, 1]` range check remains V1's responsibility and
    still fires after delegation.

## Grammar binding (bind, do not fork)

The canonical-decimal grammar is **bound by reference** to the in-repo registered
authority `qme/stats/effective_trials_uncertainty.py`, the module behind the
`ppw-bootstrap-uncertainty-authority-v1` kernel. Its `_DECIMAL_PATTERN`, the
negative-zero rule, and the `len(value) > 128` bound (all applied together in
`_parse_matrix`) are the authoritative source:

```text
_DECIMAL_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
```

with an explicit negative-zero rejection (`must not be negative zero`).

V2 does not introduce a second regex. It imports that module's compiled
`_DECIMAL_PATTERN` object and uses it directly. A pinned literal
`_EXPECTED_CANONICAL_DECIMAL_PATTERN` records the exact reviewed pattern string
so that any silent drift in the registered grammar fails this module closed at
import time; a test asserts the imported pattern is the same object and that its
pattern string is byte-identical to the pinned literal. V2 also mirrors the two
companion guards applied alongside the same grammar: a bounded string length
(`len(value) > 128`, a bounded-input / parse-cost guard that mirrors the sibling
rule — not overflow prevention, since a canonical decimal of at most 128
characters cannot overflow `Decimal`) and the negative-zero rule. The
negative-zero check is a pure-string equivalent of the sibling's `is_zero() and
startswith('-')` test, evaluated without constructing a `Decimal`.

## Alpha significance domain

`holm_step_down_v2` enforces the significance-level validity domain
`0 < alpha < 1` — an open interval — for the Holm `alpha`. The check is semantic,
not lexical: it is evaluated only **after** the canonical-decimal grammar check,
by constructing a `Decimal` from the already-validated canonical string (never
before), and rejects `alpha <= 0` or `alpha >= 1` with the typed
`Nee120InferenceError("NO_GO_FAIL_CLOSED", …)`. This is parallel to V1's
registered p-value domain `[0, 1]`. No narrower numeric alpha range is registered
anywhere in the repository; any registered range is to be bound in the T0
correction record. Valid alpha values (for example `0.05`, `0.10`, `0.005`)
delegate to a byte-identical `HolmResult` versus V1; only out-of-domain alpha
(for example `2.0`, `1`, `0`, `-12.500`) is rejected. This check affects only
`holm_step_down_v2` on invalid alpha; `run_inference_v2` is unaffected and
remains byte-identical to V1 on canonical inputs.

## Behavior

The grammar rejects exactly the owner's list, each before any `Decimal()`:
`1E-3`, `1e-3` (no exponent form); `+0.001` (only an optional leading `-`);
`" 0.001"`, `"0.001 "` (no surrounding whitespace); `.5` (an integer part is
required); `5.` (a dot requires trailing digits); `NaN`, `Infinity`,
`-Infinity`; `0.00_1` (no underscores); `-0`, `-0.0` (negative zero); leading
zeros such as `0123`; non-string elements; the empty list; and an
overflow-shaped string such as `1e999999` (stopped by the grammar, so no untyped
`decimal.Overflow` / `InvalidOperation` can escape). Only the typed
`Nee120InferenceError` with reason `NO_GO_FAIL_CLOSED` leaves the adapter.

On canonical inputs V2 is byte-identical to V1. Canonical strings include `0`,
`-1`, negative values, and trailing fractional zeros such as `0.006650` and
`1.50` (trailing fractional zeros are canonical). Because V2 only pre-validates
and then delegates, `run_inference_v2` returns a `Nee120InferenceResult` equal
field-by-field to `run_inference` — the same point estimate, one-sided LCB,
two-sided interval, block selection, Newey--West diagnostic, and the same
bootstrap distribution canonical hash. The tests prove this on the NEE-120 KAT
fixture `tests/fixtures/stats/nee120-inference-v1.json` and on additional
canonical series, and pin the A2 defect directly: V1 (permissive) still accepts
the non-canonical spellings and even reproduces the identical result for
value-preserving forms, while V2 rejects the same input as the strict entrypoint.

## Files

- `qme/stats/nee120_inference_v2.py` — the strict adapter (T1).
- `tests/stats/test_nee120_inference_v2.py` — grammar-binding, reject-list,
  byte-identity, edge-case (leading zeros, negative zero, trailing fractional
  zeros, the `len > 128` bound), alpha significance-domain, Holm-delegation, and
  A2 regression-pin tests (T1).
- `docs/quant/NEE_120_INFERENCE_IMPLEMENTATION_V2.md` — this document (T3).

## Gates

`ruff check`, strict `mypy` on the module, `pytest tests/stats`, the change-tier
classification (`T1`/`T1`/`T3`), the tracked-file secret scan, and
`git diff --check` with LF-only endings.

## Explicit nonclaims

This packet does not claim:

- clearance of any Specification Freeze V4 blocker; all 13 remain active and
  `NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE` stays ACTIVE;
- any change to the registered statistical method or numerical kernel; V2 only
  hardens input validation and delegates;
- any modification of V1 or of the registered sibling grammar module;
- a promotion decision, empirical result, alpha, M0 completion, production
  readiness, or live-order authority.

V2 is a same-Claude-lineage candidate. `formal_independent_review_satisfied`
remains false and `milestone_m0_complete` remains false; external independent
acceptance is required before any T0 binding.
