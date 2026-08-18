"""NEE-120 inference V2 — strict canonical-decimal input adapter.

This module is a **versioned strict input adapter** placed in front of the
UNCHANGED V1 numerical kernel :mod:`qme.stats.nee120_inference`.  V1 is
hash-bound by the merged owner-decision record and is imported, never modified.
The registered statistical methods (paired-delta point estimate, uncentered
percentile stationary bootstrap, Newey--West diagnostic, Holm step-down) and the
numerical kernel are **not re-implemented** here; only the external-input
validation is hardened.

Authoritative entrypoints — no bypass
-------------------------------------
:func:`run_inference_v2` and :func:`holm_step_down_v2` are the **authoritative
accepted entrypoints** for NEE-120 inference; callers MUST use them.  The V1
symbols :func:`qme.stats.nee120_inference.run_inference` and
:func:`qme.stats.nee120_inference.holm_step_down` remain importable and, being
hash-bound, are **not** removed — but they still *silently accept* non-canonical
decimal input (the A2 P1 defect), so importing V1 directly bypasses this
validation gate.  Do not call V1 with unvalidated external strings.  The
forthcoming T0 correction record names the V2 entrypoints authoritative; until
that record lands V2 is the strict candidate entrypoint.

The lead-confirmed A2 P1 is that V1's ``_parse_series`` and ``holm_step_down``
build ``Decimal`` values straight from caller strings, so non-canonical decimal
text (``1E-3``, ``+0.001``, ``" 0.001"``, ``.5``, ``5.``, negative zero, …) is
silently accepted.  V2 closes that gap: every externally supplied decimal string
(paired monthly deltas, Holm p-values, and the Holm ``alpha``) must ``fullmatch``
the repository's authoritative canonical-decimal grammar and must not be
negative zero, rejected with the typed :class:`Nee120InferenceError`
(``NO_GO_FAIL_CLOSED``) **before any** ``Decimal()`` construction.  Canonical
inputs are then delegated to V1 and its result is returned unchanged, so V2 is
byte-identical to V1 on every canonical series (including the bootstrap
distribution canonical hash).

Grammar binding
---------------
The grammar is **bound by reference** to the in-repo registered authority
:mod:`qme.stats.effective_trials_uncertainty` (the module behind the
``ppw-bootstrap-uncertainty-authority-v1`` kernel).  V2 reuses that module's
compiled ``_DECIMAL_PATTERN`` object directly rather than forking a second
regex, and mirrors the two companion rules it applies alongside the same grammar
in ``_parse_matrix``: the negative-zero rejection and the ``len(value) > 128``
bound.  ``_EXPECTED_CANONICAL_DECIMAL_PATTERN`` only pins the exact reviewed
pattern string so that a silent drift in the registered grammar fails this
module closed at import time; a test asserts the byte-identity of the two
pattern strings.

Alpha significance domain
-------------------------
:func:`holm_step_down_v2` additionally enforces the significance-level validity
domain ``0 < alpha < 1`` (an open interval), evaluated only AFTER the lexical
grammar check, parallel to V1's registered p-value domain ``[0, 1]``.  No
narrower numeric alpha range is registered anywhere in the repository; any
registered range is to be bound in the T0 correction record.

Nonclaims
---------
No Freeze V4 blocker is cleared and
``NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE`` stays ACTIVE.  This is a
same-Claude-lineage candidate; external independent acceptance is required
before any T0 binding.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final, cast

from qme.stats.effective_trials_uncertainty import _DECIMAL_PATTERN as _REGISTERED_DECIMAL_PATTERN
from qme.stats.nee120_inference import (
    HolmResult,
    Nee120InferenceError,
    Nee120InferenceResult,
    holm_step_down,
    run_inference,
)

IMPLEMENTATION_ID: Final = "QME-NEE120-INFERENCE-IMPLEMENTATION-V2"

# The reviewed, owner-bound canonical-decimal grammar, bound by reference to the
# in-repo registered authority
# ``qme/stats/effective_trials_uncertainty.py::_DECIMAL_PATTERN``.  This literal
# is pinned only so that a silent change to that registered grammar fails this
# module closed at import time; the actual validation reuses the sibling's
# compiled object below.
_EXPECTED_CANONICAL_DECIMAL_PATTERN: Final = r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"

if _REGISTERED_DECIMAL_PATTERN.pattern != _EXPECTED_CANONICAL_DECIMAL_PATTERN:
    # Fail closed: never validate against an unreviewed grammar.
    raise Nee120InferenceError(
        "NO_GO_FAIL_CLOSED",
        "registered canonical-decimal grammar drifted from the reviewed NEE-120 V2 binding",
    )

# The single validation authority: the registered sibling's compiled pattern
# object itself (bound by reference, not forked).
_DECIMAL_PATTERN: Final = _REGISTERED_DECIMAL_PATTERN

# Companion bounds mirrored from the registered sibling's application of the same
# grammar in ``effective_trials_uncertainty._parse_matrix``: a bounded string
# length (``len(value) > 128``) and rejection of negative zero.  The length bound
# is a bounded-input / parse-cost guard mirroring the sibling's ``len > 128``
# rule; it is NOT overflow prevention (a canonical decimal of at most 128
# characters cannot overflow ``Decimal``).  It sits far above every real delta,
# p-value, or alpha and so never affects a canonical input.
_MAX_CANONICAL_DECIMAL_LENGTH: Final = 128
_NONZERO_DIGITS: Final = frozenset("123456789")


def _require_canonical_decimal(value: object, *, field: str) -> str:
    """Return ``value`` iff it is a bounded canonical decimal string that is not
    negative zero; otherwise raise the typed fail-closed error.

    All checks are pure string operations performed **before** any ``Decimal()``
    construction, so no untyped ``decimal`` exception can escape the adapter.
    """

    if type(value) is not str:
        raise Nee120InferenceError(
            "NO_GO_FAIL_CLOSED", f"{field} must be a canonical decimal string"
        )
    if len(value) > _MAX_CANONICAL_DECIMAL_LENGTH or _DECIMAL_PATTERN.fullmatch(value) is None:
        raise Nee120InferenceError(
            "NO_GO_FAIL_CLOSED", f"{field} is not a bounded canonical decimal string"
        )
    # A grammar-conforming string is zero iff it contains no nonzero digit, so it
    # is negative zero iff it is additionally signed.  This is the pure-string
    # equivalent of the sibling's ``is_zero() and startswith('-')`` rule.
    if value.startswith("-") and not any(character in _NONZERO_DIGITS for character in value):
        raise Nee120InferenceError(
            "NO_GO_FAIL_CLOSED", f"{field} must not be negative zero"
        )
    return value


def _require_canonical_series(values: object, *, kind: str, field_prefix: str) -> None:
    """Validate that ``values`` is a non-empty list of bounded canonical decimals."""

    if type(values) is not list or not values:
        raise Nee120InferenceError("NO_GO_FAIL_CLOSED", f"{kind} must be a non-empty list")
    for index, value in enumerate(values):
        _require_canonical_decimal(value, field=f"{field_prefix}[{index}]")


def run_inference_v2(deltas: object) -> Nee120InferenceResult:
    """Strict-input NEE-120 inference.

    Validate every paired monthly delta string against the bound canonical-decimal
    grammar (rejecting non-canonical forms and negative zero before any
    ``Decimal()``), then delegate UNCHANGED to V1 :func:`run_inference` and return
    its result.  On canonical inputs this is byte-identical to V1.
    """

    _require_canonical_series(deltas, kind="paired-delta series", field_prefix="delta")
    return run_inference(deltas)


def holm_step_down_v2(p_values: object, *, alpha: str = "0.05") -> HolmResult:
    """Strict-input Holm step-down.

    Validate every p-value string and ``alpha`` against the bound canonical-decimal
    grammar before any ``Decimal()``.  Additionally enforce the significance-level
    validity domain ``0 < alpha < 1`` (an open interval), evaluated only AFTER the
    lexical grammar check and parallel to V1's registered p-value domain
    ``[0, 1]``.  Then delegate UNCHANGED to V1 :func:`holm_step_down` (which still
    enforces ``[0, 1]`` on the p-values).
    """

    _require_canonical_series(p_values, kind="Holm family", field_prefix="p_value")
    checked_alpha = _require_canonical_decimal(alpha, field="alpha")
    # Significance-level domain 0 < alpha < 1.  The Decimal is built only AFTER the
    # lexical grammar check above, so it is constructed from a validated canonical
    # string (never before).  No narrower numeric alpha range is registered in the
    # repository; any registered range is to be bound in the T0 correction record.
    alpha_value = Decimal(checked_alpha)
    if alpha_value <= 0 or alpha_value >= 1:
        raise Nee120InferenceError(
            "NO_GO_FAIL_CLOSED", "alpha must be a significance level in the open interval (0, 1)"
        )
    return holm_step_down(cast(list[str], p_values), alpha=checked_alpha)


__all__ = [
    "IMPLEMENTATION_ID",
    "holm_step_down_v2",
    "run_inference_v2",
]
