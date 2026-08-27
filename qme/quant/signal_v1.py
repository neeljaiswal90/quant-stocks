"""NEE-131 session-anchored momentum feature, cross-sectional rank, ties, selection.

This module turns a point-in-time total-return cross-section into one immutable
row per required security and signal session, carrying the feature value, a
typed feature status, a typed eligibility state, a deterministic rank, its tie
group, the selected flag, the selection reason, and the full lineage of the run.

The feature equation, verbatim from the ticket
----------------------------------------------

For a registered lookback ``L`` and skip ``S`` measured in **exchange
sessions**::

    M_(L,S),i,t = ln(TR_i,t-S / TR_i,t-L)

Primary v0.1 uses ``(L, S) = (252, 21)``. Both offsets are session counts, never
calendar days: the anchors are resolved with
:meth:`qme.data.stores.calendar_v1.TradingCalendar.offset`, which fails closed at
the coverage edge and never clamps. Every per-security observation session is
resolved with :meth:`~qme.data.stores.calendar_v1.TradingCalendar.session`, an
exact lookup whose own refusal message states that it never substitutes a nearby
date. There is no nearest-date path in this module at all: the only substitution
API in the calendar store is ``next_eligible_session`` and nothing here calls it.

Why ``ln`` is irrational and what this module does about it
-----------------------------------------------------------

``TR`` anchors arrive as canonical base-10 decimal strings and are lifted to
exact :class:`~fractions.Fraction` values. Their ratio ``R = TR[t-S] / TR[t-L]``
is therefore an **exact rational**. Its natural logarithm almost never is: for a
rational ``R != 1``, ``ln(R)`` is transcendental (Lindemann), so no finite
decimal, and no rational, represents it. A ranking that compared rounded
logarithms would therefore be deciding real economic order with a rounding
artifact whenever two ratios differ by less than the artifact quantum.

This module splits the two jobs and never lets the rounded one decide anything:

* **Ranking** compares ``R`` itself, as an exact :class:`~fractions.Fraction`.
  ``ln`` is strictly increasing on ``(0, inf)``, so for positive anchors
  ``R_a > R_b`` if and only if ``ln(R_a) > ln(R_b)``. The exact comparison is
  therefore the *same* total order as the logarithmic one, decided without a
  single rounding. :data:`RANKING_COMPARISON` states this in the artifact.
* **Reporting** computes ``M = ln(R)`` once, under an explicit
  :class:`decimal.Context` created fresh per call by :func:`decimal_context`
  (:data:`DECIMAL_CONTEXT_PRECISION` significant digits, ``ROUND_HALF_EVEN``,
  with ``InvalidOperation``, ``DivisionByZero`` and ``Overflow`` trapped), in the
  registered ``decimal_ratio_then_natural_log`` order, and renders it once at
  :data:`SIGNAL_ARTIFACT_SCALE` decimal places.

The consequence is testable and is tested: two securities whose 18-place feature
strings are byte-identical still receive different ranks when their exact ratios
differ, and the ordering follows the exact ratios.

Error bound of the reported logarithm
-------------------------------------

Write ``R = n/d`` exactly, ``P =`` :data:`DECIMAL_CONTEXT_PRECISION`, and
``M = ln(R)``.

1. ``Decimal(n)`` and ``Decimal(d)`` are exact for any integers, so the operands
   enter the context without error.
2. ``Context.divide`` is correctly rounded, so the computed ratio is
   ``R(1 + e)`` with ``|e| <= 5 * 10**-P``.
3. ``ln(R(1 + e)) - ln(R) = ln(1 + e)``, and ``|ln(1 + e)| <= |e| / (1 - |e|)``,
   which is below ``5.1 * 10**-P`` for every ``|e|`` in that range. This term is
   **absolute**, and it does not grow when ``R`` approaches ``1``: near-tied
   securities are exactly where a naive difference-of-logs would lose digits, and
   the registered ratio-then-log order avoids that cancellation.
4. ``Decimal.ln`` is documented as correctly rounded under ``ROUND_HALF_EVEN``,
   contributing at most half an ulp: ``<= 5 * 10**-P * |M|``.

Over the accepted magnitude :data:`MAX_ABSOLUTE_LOG_MOMENTUM` (a ratio further
from ``1`` than that is refused with
``BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE`` rather than reported outside the
bound), the total absolute error before rendering is below
``5.1 * 10**-P + 5 * 10**-P * 100``, i.e. below ``6 * 10**(-P+3)``. At ``P = 50``
that is ``6e-48``: thirty orders of magnitude below the ``1e-18`` artifact
quantum, so the rendered string is the correct rounding of the true value except
within ``6e-48`` of an exact ``1e-18`` tie -- a residual this module states
rather than denies. :data:`NATURAL_LOG_ERROR_BOUND` carries it as a citable
string and the known-answer fixture pins the rendered values.

No binary float appears anywhere in this module. Every value is a
:class:`~fractions.Fraction`, an exact :class:`~decimal.Decimal` under the
declared context, an ``int``, or a canonical base-10 string.

The owner-gated registries ship EMPTY, on purpose
-------------------------------------------------

Three decisions in this lane belong to the owner and have not been recorded as
registrations for this engine, so all three registries ship empty and every
resolution fails closed before a single row is scored:

===============================  ==============================================
:data:`REGISTERED_FEATURE_VARIANTS`   ``BLOCKED_NO_REGISTERED_FEATURE_VARIANT``
:data:`REGISTERED_TIE_BREAK_POLICIES` ``BLOCKED_NO_REGISTERED_TIE_BREAK_POLICY``
:data:`REGISTERED_BREADTH_MINIMUMS`   ``BLOCKED_NO_REGISTERED_BREADTH_MINIMUM``
===============================  ==============================================

This mirrors :mod:`qme.data.stores.riskfree_v1` and
:mod:`qme.data.alpha_vantage.plan_v1`: the machinery is complete and tested, and
it refuses to run until a sourced record exists. Callers -- today, only tests --
pass their own records through the ``variants=`` / ``tie_policies=`` /
``breadth_minimums=`` parameters under the ``TEST_CONSTRUCTED`` source kind,
which the ``validate_*_registry`` functions forbid in the shipped registries.
Nothing here supplies a default lookback, a default tie rule, or a default
breadth floor, and no code path falls back to one.

The NEE-119 contract v2 does carry registered decisions for all three (offsets
``-21`` / ``-252``, the ``signal_decimal_descending`` then
``security_id_utf8_bytes_ascending`` total order, and a minimum rank-eligible
breadth of ``150``). Those are the *contract's* registrations, not this engine's,
and this module deliberately does not read them as defaults. What it does
instead is bind the contract bytes (see :data:`BOUND_CONTRACT_AUTHORITY`) so a
registration that claims to carry them can be checked against the exact frozen
artifact, and the tests construct exactly those records and prove the engine
reproduces :func:`qme.quant.contract_v2.selection_size` on them.

Selection
---------

``K_t = min(50, floor(0.20 * N_t))``, implemented in integers as
``min(50, (20 * N_t) // 100)`` over ``N_t``, the rank-eligible breadth. The
constants are the ticket's own words and are bound to the contract's
``selection`` block, not invented here. Below the registered breadth minimum the
run reports ``INVALID_INSUFFICIENT_BREADTH`` with ``selection_size = 0`` and
selects nothing: a thin cross-section produces no exposure rather than a smaller
book. ``K_t = 0`` at or above the minimum reports
``INVALID_ZERO_SELECTION_SIZE`` for the same reason.

Grid variants cannot overwrite the primary
------------------------------------------

Every run carries its variant identity, and every row and digest is derived from
it, so a grid run's rows can never collide with the primary's. On top of that,
:class:`SignalOutputSet` refuses to hold a ``GRID_DIAGNOSTIC`` result in its
primary slot, refuses a ``PRIMARY`` result among the grid results, and refuses a
repeated ``variant_id`` -- ``BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY``.

Non-claims
----------

:data:`NON_CLAIMS` is copied into every manifest. This engine measures nothing:
it computes a registered feature and a registered ranking rule over inputs it is
handed. It claims no empirical performance, no alpha, no capacity, no production
readiness, no prospective consumption, and no live-order authority.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
)
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

from qme.data.corporate_actions.factors_v1 import (
    ARTIFACT_SCALE,
    ROUNDING_MODE,
    CorporateActionFactorError,
    canonical_decimal,
    parse_exact,
    render_artifact,
    render_exact,
)
from qme.data.stores.calendar_v1 import (
    BoundArtifact,
    MarketStoreError,
    TradingCalendar,
    canonical_dataset_digest,
    grouped_sha256_bytes,
    grouped_sha256_file,
    iso_date,
    require_calendar,
    store_binding_digest,
)
from qme.foundation.lineage import canonical_json_bytes

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

ENGINE_ID: Final = "QME-NEE131-SIGNAL-RANK-SELECTION-ENGINE-V1"
SCHEMA_VERSION: Final = "qme.signal_rank_selection.v1"

#: The registered feature name in the NEE-119 contract v2 ``signal`` block.
FEATURE_NAME: Final = "12_1_TOTAL_RETURN_LOG_MOMENTUM"
#: The ticket's feature equation, verbatim, written into every manifest.
FEATURE_EQUATION: Final = "M_(L,S),i,t = ln(TR_i,t-S / TR_i,t-L)"
#: The registered calculation order: form the decimal ratio, then take the log.
CALCULATION_ORDER: Final = "decimal_ratio_then_natural_log"
#: What the ranking comparison actually compares. Stated in the artifact so a
#: reader never has to assume the rank came from the rounded log.
RANKING_COMPARISON: Final = (
    "EXACT_RATIONAL_TOTAL_RETURN_RATIO_DESCENDING_ORDER_IDENTICAL_TO_LOG_ORDER"
)
#: The point-in-time price coordinate this engine consumes, contract-registered.
PRICE_COORDINATE: Final = "POINT_IN_TIME_TOTAL_RETURN_CLOSE_AS_KNOWN_AT_SIGNAL_CUTOFF"
#: The authoritative reported statistic type. Never replaced by the diagnostic.
FEATURE_VALUE_TYPE_LOG: Final = "NATURAL_LOG_TOTAL_RETURN_RATIO"
#: The diagnostic-only simple return, labelled so it cannot pass as authority.
DIAGNOSTIC_VALUE_TYPE_SIMPLE: Final = "DIAGNOSTIC_SIMPLE_RETURN_NOT_AUTHORITY"
#: Feature exactness vocabulary for the reported value.
FEATURE_EXACTNESS_ROUNDED_ARTIFACT: Final = "ROUNDED_DECIMAL_ARTIFACT"
FEATURE_EXACTNESS_NOT_COMPUTED: Final = "NOT_COMPUTED"
FEATURE_EXACTNESS_KINDS: Final = (
    FEATURE_EXACTNESS_NOT_COMPUTED,
    FEATURE_EXACTNESS_ROUNDED_ARTIFACT,
)

#: No nearest-date substitution exists in this module; the flag is emitted so a
#: downstream reader sees the property rather than inferring it.
NEAREST_SESSION_SUBSTITUTION_ALLOWED: Final = False

#: Downstream claims this engine has not earned. Written to every manifest.
NON_CLAIMS: Final[Mapping[str, bool]] = {
    "alpha_demonstrated": False,
    "capacity_value_established": False,
    "empirical_performance_measured": False,
    "freeze_blocker_changed": False,
    "independent_review_recorded": False,
    "live_order_authority": False,
    "owner_registration_recorded": False,
    "production_deployment_authorized": False,
    "production_ready": False,
    "prospective_observations_consumable": False,
}

# ---------------------------------------------------------------------------
# Bound frozen authority (read and hashed, never written)
# ---------------------------------------------------------------------------

#: The frozen artifacts a registration for this engine must be checkable against.
#: Digests are grouped (eight lowercase 8-hex groups joined by ``:``) so no
#: contiguous 64-hex run appears in this file.
BOUND_CONTRACT_AUTHORITY: Final[tuple[BoundArtifact, ...]] = (
    BoundArtifact(
        role="QUANTITATIVE_CONTRACT_V2",
        path="configs/quant/qme-v0.1-contract-v2.json",
        sha256_grouped="d71086f6:9176c1dc:ba82dcc8:dfd018b5:703ff059:f3fd526a:6a92f5c0:3370b285",
    ),
    BoundArtifact(
        role="QUANTITATIVE_CONTRACT_V2_SPEC",
        path="docs/quant/QME_V0_1_QUANTITATIVE_CONTRACT_V2.md",
        sha256_grouped="df918be1:8463ed92:f8846b0a:69b9a25f:9dfd6ded:8598e745:fae713a3:ea4caf4f",
    ),
    BoundArtifact(
        role="TOTAL_RETURN_METHODOLOGY",
        path="configs/quant/qme-v0.1-total-return-methodology.json",
        sha256_grouped="95381821:c1c8ff00:e0e626b3:d7ee3646:6d12c3be:9e6b8cb7:5ee166f0:043454ac",
    ),
    BoundArtifact(
        role="SOURCE_FRESHNESS_POLICY",
        path="configs/quant/source-freshness-policy-v1.json",
        sha256_grouped="3dd94e35:0cc89023:e10efd2a:934e9a67:a502a1c8:4b5478db:82a98958:2ab71edc",
    ),
    BoundArtifact(
        role="ACCOUNTING_EQUATION_CONFIG",
        path="configs/quant/accounting-equations-v1.json",
        sha256_grouped="decb3d52:dea8b402:0f011554:848bb9a7:c6164827:cfe319be:36fc46c7:8b8c2e0c",
    ),
    BoundArtifact(
        role="ACCOUNTING_EQUATION_SPEC",
        path="docs/quant/QME_ACCOUNTING_EXECUTION_METRICS_SPEC.md",
        sha256_grouped="27e906a6:12eb61a2:f12947ff:3696cb90:7d56d883:e45c99a7:503011fe:13bb8840",
    ),
)

# ---------------------------------------------------------------------------
# Numeric policy
# ---------------------------------------------------------------------------

#: Working significant digits for the logarithm, bound to the contract's
#: ``numeric_policy.decimal_precision_digits``.
DECIMAL_CONTEXT_PRECISION: Final = 50
DECIMAL_ROUNDING: Final = ROUND_HALF_EVEN
#: Artifact scale and rounding mode, bound to the NEE-125 kernel constants and to
#: the contract's ``numeric_policy.signal_artifact_scale``.
SIGNAL_ARTIFACT_SCALE: Final = ARTIFACT_SCALE
SIGNAL_ROUNDING_MODE: Final = ROUNDING_MODE

#: The largest ``|ln(R)|`` this engine will report. A ratio further from ``1``
#: than this is refused rather than reported outside the stated error bound.
MAX_ABSOLUTE_LOG_MOMENTUM: Final = Fraction(100)

NATURAL_LOG_ERROR_BOUND: Final = (
    "absolute error of M = ln(TR[t-S]/TR[t-L]) before rendering <= 6e-48: the "
    "exact rational ratio enters a 50-significant-digit ROUND_HALF_EVEN context "
    "through one correctly-rounded division (relative error <= 5e-50), a "
    "relative perturbation e of the argument moves the logarithm by "
    "|ln(1+e)| <= 5.1e-50 absolutely and without cancellation near a ratio of 1, "
    "and Decimal.ln is correctly rounded so it adds at most half an ulp "
    "(<= 5e-50 * |M| <= 5e-48 at the accepted magnitude |M| <= 100); the "
    "scale-18 artifact is therefore the correct rounding of the true value "
    "except within 6e-48 of an exact 1e-18 tie"
)

#: The rank order is decided on the exact ratio, so the bound above never enters
#: a ranking decision. Stated as a constant so the property is machine-readable.
RANK_ORDER_DEPENDS_ON_ROUNDED_LOG: Final = False


def decimal_context() -> Context:
    """The explicit Decimal context for the natural logarithm.

    Fresh per call, so nothing this engine computes depends on ambient context
    state a caller may have changed.
    """
    return Context(
        prec=DECIMAL_CONTEXT_PRECISION,
        rounding=DECIMAL_ROUNDING,
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


# ---------------------------------------------------------------------------
# Typed states
# ---------------------------------------------------------------------------

FEATURE_SCORABLE: Final = "FEATURE_SCORABLE"
NOT_SCORABLE_INVALID_TOTAL_RETURN_CHAIN: Final = "NOT_SCORABLE_INVALID_TOTAL_RETURN_CHAIN"
NOT_SCORABLE_INSUFFICIENT_HISTORY: Final = "NOT_SCORABLE_INSUFFICIENT_HISTORY"
NOT_SCORABLE_MISSING_ANCHOR_RECENT: Final = "NOT_SCORABLE_MISSING_ANCHOR_RECENT"
NOT_SCORABLE_MISSING_ANCHOR_OLD: Final = "NOT_SCORABLE_MISSING_ANCHOR_OLD"
NOT_SCORABLE_STALE_SOURCE: Final = "NOT_SCORABLE_STALE_SOURCE"
NOT_SCORABLE_NONPOSITIVE_ANCHOR_RECENT: Final = "NOT_SCORABLE_NONPOSITIVE_ANCHOR_RECENT"
NOT_SCORABLE_NONPOSITIVE_ANCHOR_OLD: Final = "NOT_SCORABLE_NONPOSITIVE_ANCHOR_OLD"

#: Evaluation order for the feature status. Exactly one status is assigned per
#: row, and it is the first entry whose condition holds. The relative order of
#: the non-scorable states follows the contract's ``reason_code_precedence``;
#: the invalid-chain state leads because a broken total-return chain invalidates
#: every anchor drawn from it.
FEATURE_STATUS_PRECEDENCE: Final = (
    NOT_SCORABLE_INVALID_TOTAL_RETURN_CHAIN,
    NOT_SCORABLE_INSUFFICIENT_HISTORY,
    NOT_SCORABLE_MISSING_ANCHOR_RECENT,
    NOT_SCORABLE_MISSING_ANCHOR_OLD,
    NOT_SCORABLE_STALE_SOURCE,
    NOT_SCORABLE_NONPOSITIVE_ANCHOR_RECENT,
    NOT_SCORABLE_NONPOSITIVE_ANCHOR_OLD,
    FEATURE_SCORABLE,
)

#: Every feature status this engine emits, sorted. Callers may bind it.
FEATURE_STATUSES: Final = tuple(sorted(FEATURE_STATUS_PRECEDENCE))

ELIGIBLE_RANK_ELIGIBLE: Final = "RANK_ELIGIBLE"
EXCLUDED_NOT_IN_REQUIRED_UNIVERSE: Final = "EXCLUDED_NOT_IN_REQUIRED_UNIVERSE"
EXCLUDED_NOT_SCORABLE: Final = "EXCLUDED_NOT_SCORABLE"

#: Every eligibility state this engine emits, sorted.
ELIGIBILITY_STATES: Final = (
    EXCLUDED_NOT_IN_REQUIRED_UNIVERSE,
    EXCLUDED_NOT_SCORABLE,
    ELIGIBLE_RANK_ELIGIBLE,
)

SELECTION_VALID: Final = "SELECTION_VALID"
INVALID_INSUFFICIENT_BREADTH: Final = "INVALID_INSUFFICIENT_BREADTH"
INVALID_ZERO_SELECTION_SIZE: Final = "INVALID_ZERO_SELECTION_SIZE"

#: Every run-level selection state, sorted.
SELECTION_STATES: Final = (
    INVALID_INSUFFICIENT_BREADTH,
    INVALID_ZERO_SELECTION_SIZE,
    SELECTION_VALID,
)

INCLUDED_BY_RANK: Final = "INCLUDED_BY_RANK"
INCLUDED_BOUNDARY_TIE_BREAK: Final = "INCLUDED_BOUNDARY_TIE_BREAK"
EXCLUDED_BOUNDARY_TIE_BREAK: Final = "EXCLUDED_BOUNDARY_TIE_BREAK"
EXCLUDED_BELOW_SELECTION_CUTOFF: Final = "EXCLUDED_BELOW_SELECTION_CUTOFF"
NOT_SELECTED_NOT_RANK_ELIGIBLE: Final = "NOT_SELECTED_NOT_RANK_ELIGIBLE"
NOT_SELECTED_SELECTION_STATE_INVALID: Final = "NOT_SELECTED_SELECTION_STATE_INVALID"

#: Every selection reason this engine emits, sorted.
SELECTION_REASONS: Final = (
    EXCLUDED_BELOW_SELECTION_CUTOFF,
    EXCLUDED_BOUNDARY_TIE_BREAK,
    INCLUDED_BOUNDARY_TIE_BREAK,
    INCLUDED_BY_RANK,
    NOT_SELECTED_NOT_RANK_ELIGIBLE,
    NOT_SELECTED_SELECTION_STATE_INVALID,
)

BLOCKED_AMBIGUOUS_BREADTH_MINIMUM: Final = "BLOCKED_AMBIGUOUS_BREADTH_MINIMUM"
BLOCKED_AMBIGUOUS_FEATURE_VARIANT: Final = "BLOCKED_AMBIGUOUS_FEATURE_VARIANT"
BLOCKED_AMBIGUOUS_TIE_BREAK_POLICY: Final = "BLOCKED_AMBIGUOUS_TIE_BREAK_POLICY"
BLOCKED_CONTRACT_ARTIFACT_MISSING: Final = "BLOCKED_CONTRACT_ARTIFACT_MISSING"
BLOCKED_CONTRACT_AUTHORITY_BYTES_MISMATCH: Final = "BLOCKED_CONTRACT_AUTHORITY_BYTES_MISMATCH"
BLOCKED_DUPLICATE_OBSERVATION_SESSION: Final = "BLOCKED_DUPLICATE_OBSERVATION_SESSION"
BLOCKED_DUPLICATE_SECURITY_ID: Final = "BLOCKED_DUPLICATE_SECURITY_ID"
BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE: Final = "BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE"
BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY: Final = "BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY"
BLOCKED_INVALID_VARIANT_SESSION_OFFSETS: Final = "BLOCKED_INVALID_VARIANT_SESSION_OFFSETS"
BLOCKED_MALFORMED_SIGNAL_INPUT: Final = "BLOCKED_MALFORMED_SIGNAL_INPUT"
BLOCKED_NO_REGISTERED_BREADTH_MINIMUM: Final = "BLOCKED_NO_REGISTERED_BREADTH_MINIMUM"
BLOCKED_NO_REGISTERED_FEATURE_VARIANT: Final = "BLOCKED_NO_REGISTERED_FEATURE_VARIANT"
BLOCKED_NO_REGISTERED_TIE_BREAK_POLICY: Final = "BLOCKED_NO_REGISTERED_TIE_BREAK_POLICY"
BLOCKED_SIGNAL_SESSION_AFTER_ANALYSIS_CUTOFF: Final = (
    "BLOCKED_SIGNAL_SESSION_AFTER_ANALYSIS_CUTOFF"
)
BLOCKED_UNREGISTERED_BREADTH_EVIDENCE_SOURCE: Final = (
    "BLOCKED_UNREGISTERED_BREADTH_EVIDENCE_SOURCE"
)
BLOCKED_UNREGISTERED_BREADTH_MINIMUM: Final = "BLOCKED_UNREGISTERED_BREADTH_MINIMUM"
BLOCKED_UNREGISTERED_FEATURE_VARIANT: Final = "BLOCKED_UNREGISTERED_FEATURE_VARIANT"
BLOCKED_UNREGISTERED_INPUT_VOCABULARY: Final = "BLOCKED_UNREGISTERED_INPUT_VOCABULARY"
BLOCKED_UNREGISTERED_ORDERING_KEY: Final = "BLOCKED_UNREGISTERED_ORDERING_KEY"
BLOCKED_UNREGISTERED_SOURCE_KIND: Final = "BLOCKED_UNREGISTERED_SOURCE_KIND"
BLOCKED_UNREGISTERED_STABLE_KEY: Final = "BLOCKED_UNREGISTERED_STABLE_KEY"
BLOCKED_UNREGISTERED_TIE_BREAK_POLICY: Final = "BLOCKED_UNREGISTERED_TIE_BREAK_POLICY"
BLOCKED_UNREGISTERED_VARIANT_ROLE: Final = "BLOCKED_UNREGISTERED_VARIANT_ROLE"

#: Every fail-closed state this engine raises itself, sorted. Callers may bind it.
#: Refusals that belong to the calendar store -- a malformed date, a date outside
#: accepted coverage, a date that is not a session, an offset that leaves
#: coverage, a missing calendar -- are surfaced unchanged rather than renamed;
#: see :data:`SURFACED_CALENDAR_STATES`.
FAIL_CLOSED_STATES: Final = (
    BLOCKED_AMBIGUOUS_BREADTH_MINIMUM,
    BLOCKED_AMBIGUOUS_FEATURE_VARIANT,
    BLOCKED_AMBIGUOUS_TIE_BREAK_POLICY,
    BLOCKED_CONTRACT_ARTIFACT_MISSING,
    BLOCKED_CONTRACT_AUTHORITY_BYTES_MISMATCH,
    BLOCKED_DUPLICATE_OBSERVATION_SESSION,
    BLOCKED_DUPLICATE_SECURITY_ID,
    BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE,
    BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY,
    BLOCKED_INVALID_VARIANT_SESSION_OFFSETS,
    BLOCKED_MALFORMED_SIGNAL_INPUT,
    BLOCKED_NO_REGISTERED_BREADTH_MINIMUM,
    BLOCKED_NO_REGISTERED_FEATURE_VARIANT,
    BLOCKED_NO_REGISTERED_TIE_BREAK_POLICY,
    BLOCKED_SIGNAL_SESSION_AFTER_ANALYSIS_CUTOFF,
    BLOCKED_UNREGISTERED_BREADTH_EVIDENCE_SOURCE,
    BLOCKED_UNREGISTERED_BREADTH_MINIMUM,
    BLOCKED_UNREGISTERED_FEATURE_VARIANT,
    BLOCKED_UNREGISTERED_INPUT_VOCABULARY,
    BLOCKED_UNREGISTERED_ORDERING_KEY,
    BLOCKED_UNREGISTERED_SOURCE_KIND,
    BLOCKED_UNREGISTERED_STABLE_KEY,
    BLOCKED_UNREGISTERED_TIE_BREAK_POLICY,
    BLOCKED_UNREGISTERED_VARIANT_ROLE,
)

#: Calendar-store refusals this engine deliberately surfaces unchanged.
SURFACED_CALENDAR_STATES: Final = (
    "BLOCKED_DATE_OUT_OF_COVERAGE",
    "BLOCKED_MISSING_CALENDAR",
    "BLOCKED_MISSING_SESSION",
    "BLOCKED_NOT_AN_ISO_DATE",
    "BLOCKED_SESSION_OFFSET_OUT_OF_RANGE",
)

#: How this engine's generic statuses specialize to the NEE-119 contract v2
#: ``reason_code_precedence`` vocabulary at the primary ``(L, S) = (252, 21)``
#: variant. The generic names carry ``L`` and ``S`` as parameters rather than
#: baking ``21`` and ``252`` into a state name, so a grid variant is describable;
#: the alias table keeps the contract binding explicit and checkable.
CONTRACT_V2_REASON_CODE_ALIASES: Final[Mapping[str, str]] = {
    EXCLUDED_BELOW_SELECTION_CUTOFF: "EXCLUDED_BELOW_SELECTION_CUTOFF",
    EXCLUDED_BOUNDARY_TIE_BREAK: "EXCLUDED_BOUNDARY_TIE_BREAK",
    INCLUDED_BOUNDARY_TIE_BREAK: "INCLUDED_BOUNDARY_TIE_BREAK",
    INCLUDED_BY_RANK: "INCLUDED_BY_RANK",
    INVALID_INSUFFICIENT_BREADTH: "INVALID_INSUFFICIENT_BREADTH",
    INVALID_ZERO_SELECTION_SIZE: "INVALID_ZERO_SELECTION_SIZE",
    NOT_SCORABLE_INSUFFICIENT_HISTORY: "NOT_SCORABLE_INSUFFICIENT_HISTORY",
    NOT_SCORABLE_MISSING_ANCHOR_OLD: "NOT_SCORABLE_MISSING_ANCHOR_T_MINUS_252",
    NOT_SCORABLE_MISSING_ANCHOR_RECENT: "NOT_SCORABLE_MISSING_ANCHOR_T_MINUS_21",
    NOT_SCORABLE_NONPOSITIVE_ANCHOR_OLD: "NOT_SCORABLE_NONPOSITIVE_ANCHOR",
    NOT_SCORABLE_NONPOSITIVE_ANCHOR_RECENT: "NOT_SCORABLE_NONPOSITIVE_ANCHOR",
    NOT_SCORABLE_STALE_SOURCE: "NOT_SCORABLE_STALE_SOURCE",
}

#: The explicit sentinel for an engine state the contract deliberately has no
#: row for (deviation #2 in the companion document). Distinct from a refusal:
#: an *unknown* token raises ``BLOCKED_UNREGISTERED_INPUT_VOCABULARY``, never
#: this, so the two cases cannot be conflated by a downstream consumer.
CONTRACT_V2_NO_CONTRACT_EQUIVALENT: Final = "NO_CONTRACT_EQUIVALENT"

#: Every state ``contract_v2_reason_code`` accepts: the closed row-level and
#: run-level vocabularies this engine emits.
_CONTRACT_V2_ALIASABLE_STATES: Final = (
    frozenset(FEATURE_STATUSES) | frozenset(SELECTION_REASONS) | frozenset(SELECTION_STATES)
)


class SignalError(ValueError):
    """A typed fail-closed refusal carrying the state and the affected identity.

    ``state`` is one of :data:`FAIL_CLOSED_STATES`. The identity fields are
    filled in whenever the refusal is attributable to a specific security,
    session, or registry record, so a caller can report *which* input was refused
    rather than only that one was.
    """

    def __init__(
        self,
        state: str,
        message: str,
        *,
        security_id: str | None = None,
        session: str | None = None,
        record_id: str | None = None,
        path: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(f"{state}: {message}")
        self.state = state
        self.security_id = security_id
        self.session = session
        self.record_id = record_id
        self.path = path
        self.detail = detail

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "state": self.state,
            "security_id": self.security_id,
            "session": self.session,
            "record_id": self.record_id,
            "path": self.path,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Shared validators
# ---------------------------------------------------------------------------

_IDENTIFIER_MAX_BYTES: Final = 128


def _identifier(value: object, *, what: str, state: str = BLOCKED_MALFORMED_SIGNAL_INPUT) -> str:
    """A non-empty, control-character-free, bounded UTF-8 identifier."""
    if type(value) is not str:
        raise SignalError(state, f"{what} must be an exact str")
    if not value or value != value.strip():
        raise SignalError(state, f"{what} must be non-empty and unpadded")
    if any(character < " " or character == "\x7f" for character in value):
        raise SignalError(state, f"{what} must not contain control characters")
    if len(value.encode("utf-8")) > _IDENTIFIER_MAX_BYTES:
        raise SignalError(state, f"{what} exceeds {_IDENTIFIER_MAX_BYTES} UTF-8 bytes")
    return value


def _exact_int(value: object, *, what: str, state: str) -> int:
    """An exact ``int``. ``bool`` is refused so ``True`` is never ``1``."""
    if type(value) is not int:
        raise SignalError(state, f"{what} must be an exact int, not {type(value).__name__}")
    return value


def _exact_decimal_string(value: object, *, what: str, security_id: str | None = None) -> Fraction:
    """Lift a canonical base-10 decimal string to an exact Fraction, or refuse."""
    if type(value) is not str:
        raise SignalError(
            BLOCKED_MALFORMED_SIGNAL_INPUT,
            f"{what} must be a canonical base-10 decimal string, not {type(value).__name__}",
            security_id=security_id,
        )
    try:
        return parse_exact(value, what=what)
    except CorporateActionFactorError as exc:
        raise SignalError(
            BLOCKED_MALFORMED_SIGNAL_INPUT,
            f"{what} is not a canonical base-10 decimal string",
            security_id=security_id,
            detail=value,
        ) from exc
    except (ValueError, ArithmeticError) as exc:
        # CPython bounds decimal int()/str() conversions
        # (sys.get_int_max_str_digits); an operand beyond that bound must refuse
        # with the typed state, never escape as a bare ValueError.
        raise SignalError(
            BLOCKED_MALFORMED_SIGNAL_INPUT,
            f"{what} cannot be lifted to an exact rational on this platform: "
            f"{type(exc).__name__}",
            security_id=security_id,
        ) from exc


def _in_vocabulary(value: object, vocabulary: Sequence[str], *, what: str, state: str) -> str:
    if type(value) is not str or value not in vocabulary:
        raise SignalError(state, f"{what} is not a registered value: {value!r}")
    return value


def contract_v2_reason_code(state: str) -> str:
    """The contract-v2 alias for one registered engine state.

    Returns the contract's own token where ``CONTRACT_V2_REASON_CODE_ALIASES``
    registers one, and the explicit :data:`CONTRACT_V2_NO_CONTRACT_EQUIVALENT`
    sentinel where this engine deliberately carries a state the contract has no
    row for. A token outside the registered row and run vocabularies is refused
    with ``BLOCKED_UNREGISTERED_INPUT_VOCABULARY`` -- an unknown state and a
    deliberately-unaliased one are never coerced to one value.
    """
    if type(state) is not str or state not in _CONTRACT_V2_ALIASABLE_STATES:
        raise SignalError(
            BLOCKED_UNREGISTERED_INPUT_VOCABULARY,
            f"{state!r} is not a registered feature status, selection reason, or "
            "selection state of this engine",
        )
    return CONTRACT_V2_REASON_CODE_ALIASES.get(state, CONTRACT_V2_NO_CONTRACT_EQUIVALENT)


def stable_key(security_id: str) -> str:
    """The registered stable key: the security id normalized to Unicode NFC."""
    return unicodedata.normalize(STABLE_KEY_NORMALIZATION_NFC, security_id)


# ---------------------------------------------------------------------------
# Registry vocabularies
# ---------------------------------------------------------------------------

SOURCE_KIND_OWNER_MANDATE: Final = "OWNER_MANDATE"
SOURCE_KIND_PRE_REGISTERED_UNIVERSE_EVIDENCE: Final = "PRE_REGISTERED_UNIVERSE_EVIDENCE"
SOURCE_KIND_REGISTERED_CONTRACT_DECISION: Final = "REGISTERED_CONTRACT_DECISION"
SOURCE_KIND_TEST_CONSTRUCTED: Final = "TEST_CONSTRUCTED"
SOURCE_KINDS: Final = (
    SOURCE_KIND_OWNER_MANDATE,
    SOURCE_KIND_PRE_REGISTERED_UNIVERSE_EVIDENCE,
    SOURCE_KIND_REGISTERED_CONTRACT_DECISION,
    SOURCE_KIND_TEST_CONSTRUCTED,
)
#: Kinds admissible in a shipped registry. ``TEST_CONSTRUCTED`` is not one.
REGISTERED_SOURCE_KINDS: Final = (
    SOURCE_KIND_OWNER_MANDATE,
    SOURCE_KIND_PRE_REGISTERED_UNIVERSE_EVIDENCE,
    SOURCE_KIND_REGISTERED_CONTRACT_DECISION,
)

VARIANT_ROLE_PRIMARY: Final = "PRIMARY"
VARIANT_ROLE_GRID_DIAGNOSTIC: Final = "GRID_DIAGNOSTIC"
VARIANT_ROLES: Final = (VARIANT_ROLE_GRID_DIAGNOSTIC, VARIANT_ROLE_PRIMARY)

ORDERING_KEY_SIGNAL_DECIMAL_DESCENDING: Final = "signal_decimal_descending"
ORDERING_KEY_SECURITY_ID_UTF8_BYTES_ASCENDING: Final = "security_id_utf8_bytes_ascending"
#: The ordering keys a registered tie policy may name, in the contract's own
#: vocabulary. ``signal_decimal_descending`` is *implemented* as an exact
#: rational comparison; see :data:`RANKING_COMPARISON` and the module docstring.
REGISTERED_ORDERING_KEYS: Final = (
    ORDERING_KEY_SIGNAL_DECIMAL_DESCENDING,
    ORDERING_KEY_SECURITY_ID_UTF8_BYTES_ASCENDING,
)
#: The final element of a registered total order must be the stable key.
FINAL_STABLE_ORDERING_KEY: Final = ORDERING_KEY_SECURITY_ID_UTF8_BYTES_ASCENDING

STABLE_KEY_SECURITY_ID: Final = "security_id"
STABLE_KEYS: Final = (STABLE_KEY_SECURITY_ID,)
STABLE_KEY_NORMALIZATION_NFC: Final = "NFC"
STABLE_KEY_NORMALIZATIONS: Final = (STABLE_KEY_NORMALIZATION_NFC,)
STABLE_KEY_ORDER_UTF8_BYTES_ASCENDING: Final = "UTF8_BYTES_ASCENDING"
STABLE_KEY_ORDERS: Final = (STABLE_KEY_ORDER_UTF8_BYTES_ASCENDING,)

RANK_METHOD_UNIQUE_ORDINAL: Final = "UNIQUE_ORDINAL_AFTER_STABLE_TIE_BREAK"
RANK_METHODS: Final = (RANK_METHOD_UNIQUE_ORDINAL,)

BOUNDARY_TIE_POLICY_SPLIT_BY_STABLE_KEY: Final = "SPLIT_BY_STABLE_SECURITY_ID_ORDER"
BOUNDARY_TIE_POLICIES: Final = (BOUNDARY_TIE_POLICY_SPLIT_BY_STABLE_KEY,)

BREADTH_EVIDENCE_OWNER_MANDATE: Final = "OWNER_MANDATE"
BREADTH_EVIDENCE_PRE_REGISTERED_UNIVERSE: Final = "PRE_REGISTERED_UNIVERSE_EVIDENCE"
#: The evidence source types a breadth minimum may declare, from the contract's
#: ``selection.minimum_rank_eligible_breadth.acceptable_source_types``.
ACCEPTABLE_BREADTH_EVIDENCE_SOURCE_TYPES: Final = (
    BREADTH_EVIDENCE_OWNER_MANDATE,
    BREADTH_EVIDENCE_PRE_REGISTERED_UNIVERSE,
)

BREADTH_UNIT_SECURITY_COUNT: Final = "security_count"
BREADTH_UNITS: Final = (BREADTH_UNIT_SECURITY_COUNT,)

# ---------------------------------------------------------------------------
# Selection rule constants (ticket-verbatim, contract-bound, not invented here)
# ---------------------------------------------------------------------------

#: ``K_t = min(50, floor(0.20 * N_t))``. The cap, numerator and denominator are
#: the ticket's own words and are byte-bound to the contract's ``selection``
#: block through :data:`BOUND_CONTRACT_AUTHORITY`.
SELECTION_MAXIMUM_NAMES: Final = 50
SELECTION_FRACTION_NUMERATOR: Final = 20
SELECTION_FRACTION_DENOMINATOR: Final = 100
SELECTION_FORMULA: Final = "K_t = min(50, floor(0.20 * N_t))"
SELECTION_INTEGER_IMPLEMENTATION: Final = "min(50, (20 * N_t) // 100)"


# ---------------------------------------------------------------------------
# Registry records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureVariant:
    """A registered ``(L, S)`` session pair with its provenance.

    ``lookback_sessions`` and ``skip_sessions`` are counts of **exchange
    sessions**, never calendar days. Construction validates the vocabulary and
    the offset relation, so an unusable variant cannot exist.
    """

    variant_id: str
    variant_role: str
    lookback_sessions: int
    skip_sessions: int
    source_kind: str
    source: str
    source_reference: str

    def __post_init__(self) -> None:
        _identifier(self.variant_id, what="variant_id")
        _in_vocabulary(
            self.variant_role,
            VARIANT_ROLES,
            what="variant_role",
            state=BLOCKED_UNREGISTERED_VARIANT_ROLE,
        )
        _in_vocabulary(
            self.source_kind,
            SOURCE_KINDS,
            what="source_kind",
            state=BLOCKED_UNREGISTERED_SOURCE_KIND,
        )
        _identifier(self.source, what=f"{self.variant_id}: source")
        _identifier(self.source_reference, what=f"{self.variant_id}: source_reference")
        lookback = _exact_int(
            self.lookback_sessions,
            what="lookback_sessions",
            state=BLOCKED_INVALID_VARIANT_SESSION_OFFSETS,
        )
        skip = _exact_int(
            self.skip_sessions,
            what="skip_sessions",
            state=BLOCKED_INVALID_VARIANT_SESSION_OFFSETS,
        )
        if skip < 0:
            raise SignalError(
                BLOCKED_INVALID_VARIANT_SESSION_OFFSETS,
                f"{self.variant_id}: skip_sessions must be non-negative",
                record_id=self.variant_id,
            )
        if lookback <= skip:
            raise SignalError(
                BLOCKED_INVALID_VARIANT_SESSION_OFFSETS,
                f"{self.variant_id}: lookback_sessions must exceed skip_sessions",
                record_id=self.variant_id,
            )

    @property
    def minimum_observed_sessions_including_t(self) -> int:
        """Sessions of history a security needs, counting the signal session."""
        return self.lookback_sessions + 1

    @property
    def recent_anchor_exchange_session_offset(self) -> int:
        return -self.skip_sessions

    @property
    def old_anchor_exchange_session_offset(self) -> int:
        return -self.lookback_sessions

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "variant_role": self.variant_role,
            "lookback_sessions": self.lookback_sessions,
            "skip_sessions": self.skip_sessions,
            "recent_anchor_exchange_session_offset": (
                self.recent_anchor_exchange_session_offset
            ),
            "old_anchor_exchange_session_offset": self.old_anchor_exchange_session_offset,
            "minimum_observed_sessions_including_t": (
                self.minimum_observed_sessions_including_t
            ),
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
        }


@dataclass(frozen=True)
class TieBreakPolicy:
    """A registered deterministic total order and its final stable key.

    Construction refuses (``BLOCKED_UNREGISTERED_ORDERING_KEY``) any total
    order that does not place ``signal_decimal_descending`` first and the
    stable key last, so under every admissible registration ``rank 1`` is the
    highest momentum and no admissible policy can rank by name alone.
    """

    policy_id: str
    total_order: tuple[str, ...]
    stable_key: str
    stable_key_normalization: str
    stable_key_order: str
    rank_method: str
    boundary_tie_policy: str
    source_kind: str
    source: str
    source_reference: str

    def __post_init__(self) -> None:
        _identifier(self.policy_id, what="policy_id")
        _in_vocabulary(
            self.source_kind,
            SOURCE_KINDS,
            what="source_kind",
            state=BLOCKED_UNREGISTERED_SOURCE_KIND,
        )
        _identifier(self.source, what=f"{self.policy_id}: source")
        _identifier(self.source_reference, what=f"{self.policy_id}: source_reference")
        if type(self.total_order) is not tuple or not self.total_order:
            raise SignalError(
                BLOCKED_UNREGISTERED_ORDERING_KEY,
                f"{self.policy_id}: total_order must be a non-empty tuple",
                record_id=self.policy_id,
            )
        seen: set[str] = set()
        for key in self.total_order:
            _in_vocabulary(
                key,
                REGISTERED_ORDERING_KEYS,
                what=f"{self.policy_id}: total_order entry",
                state=BLOCKED_UNREGISTERED_ORDERING_KEY,
            )
            if key in seen:
                raise SignalError(
                    BLOCKED_UNREGISTERED_ORDERING_KEY,
                    f"{self.policy_id}: total_order repeats {key!r}",
                    record_id=self.policy_id,
                )
            seen.add(key)
        if self.total_order[0] != ORDERING_KEY_SIGNAL_DECIMAL_DESCENDING:
            raise SignalError(
                BLOCKED_UNREGISTERED_ORDERING_KEY,
                f"{self.policy_id}: total_order must place "
                f"{ORDERING_KEY_SIGNAL_DECIMAL_DESCENDING!r} first: a policy that "
                "omits or demotes the signal key would rank by the stable key "
                "while the manifest still declares descending-momentum order, so "
                "rank 1 would no longer be the highest momentum",
                record_id=self.policy_id,
            )
        if self.total_order[-1] != FINAL_STABLE_ORDERING_KEY:
            raise SignalError(
                BLOCKED_UNREGISTERED_ORDERING_KEY,
                f"{self.policy_id}: total_order must end with {FINAL_STABLE_ORDERING_KEY!r} "
                "so the order is total and no rank depends on input row order",
                record_id=self.policy_id,
            )
        _in_vocabulary(
            self.stable_key,
            STABLE_KEYS,
            what=f"{self.policy_id}: stable_key",
            state=BLOCKED_UNREGISTERED_STABLE_KEY,
        )
        _in_vocabulary(
            self.stable_key_normalization,
            STABLE_KEY_NORMALIZATIONS,
            what=f"{self.policy_id}: stable_key_normalization",
            state=BLOCKED_UNREGISTERED_STABLE_KEY,
        )
        _in_vocabulary(
            self.stable_key_order,
            STABLE_KEY_ORDERS,
            what=f"{self.policy_id}: stable_key_order",
            state=BLOCKED_UNREGISTERED_STABLE_KEY,
        )
        _in_vocabulary(
            self.rank_method,
            RANK_METHODS,
            what=f"{self.policy_id}: rank_method",
            state=BLOCKED_UNREGISTERED_STABLE_KEY,
        )
        _in_vocabulary(
            self.boundary_tie_policy,
            BOUNDARY_TIE_POLICIES,
            what=f"{self.policy_id}: boundary_tie_policy",
            state=BLOCKED_UNREGISTERED_STABLE_KEY,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "total_order": list(self.total_order),
            "stable_key": self.stable_key,
            "stable_key_normalization": self.stable_key_normalization,
            "stable_key_order": self.stable_key_order,
            "rank_method": self.rank_method,
            "boundary_tie_policy": self.boundary_tie_policy,
            "ranking_comparison": RANKING_COMPARISON,
            "input_row_order_authoritative": False,
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
        }


@dataclass(frozen=True)
class BreadthMinimum:
    """A registered minimum rank-eligible breadth with its evidence source."""

    threshold_id: str
    minimum_rank_eligible_breadth: int
    unit: str
    evidence_source_type: str
    evidence_reference: str
    boundary_proof: str
    source_kind: str
    source: str
    source_reference: str

    def __post_init__(self) -> None:
        _identifier(self.threshold_id, what="threshold_id")
        _in_vocabulary(
            self.source_kind,
            SOURCE_KINDS,
            what="source_kind",
            state=BLOCKED_UNREGISTERED_SOURCE_KIND,
        )
        _identifier(self.source, what=f"{self.threshold_id}: source")
        _identifier(self.source_reference, what=f"{self.threshold_id}: source_reference")
        _identifier(self.evidence_reference, what=f"{self.threshold_id}: evidence_reference")
        _identifier(self.boundary_proof, what=f"{self.threshold_id}: boundary_proof")
        _in_vocabulary(
            self.unit,
            BREADTH_UNITS,
            what=f"{self.threshold_id}: unit",
            state=BLOCKED_UNREGISTERED_BREADTH_EVIDENCE_SOURCE,
        )
        _in_vocabulary(
            self.evidence_source_type,
            ACCEPTABLE_BREADTH_EVIDENCE_SOURCE_TYPES,
            what=f"{self.threshold_id}: evidence_source_type",
            state=BLOCKED_UNREGISTERED_BREADTH_EVIDENCE_SOURCE,
        )
        minimum = _exact_int(
            self.minimum_rank_eligible_breadth,
            what="minimum_rank_eligible_breadth",
            state=BLOCKED_UNREGISTERED_BREADTH_EVIDENCE_SOURCE,
        )
        if minimum < 1:
            raise SignalError(
                BLOCKED_UNREGISTERED_BREADTH_EVIDENCE_SOURCE,
                f"{self.threshold_id}: minimum_rank_eligible_breadth must be at least 1",
                record_id=self.threshold_id,
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "threshold_id": self.threshold_id,
            "minimum_rank_eligible_breadth": self.minimum_rank_eligible_breadth,
            "unit": self.unit,
            "evidence_source_type": self.evidence_source_type,
            "evidence_reference": self.evidence_reference,
            "boundary_proof": self.boundary_proof,
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
        }


# ---------------------------------------------------------------------------
# THE REGISTRIES -- all three ship empty and fail closed
# ---------------------------------------------------------------------------

#: Every ``(L, S)`` variant this engine has an owner registration for.
#:
#: EMPTY BY DESIGN. Registering a lookback is an owner decision and no such
#: registration exists for this engine, so :func:`resolve_feature_variant` fails
#: closed with ``BLOCKED_NO_REGISTERED_FEATURE_VARIANT``. The primary v0.1 pair
#: ``(252, 21)`` is registered in the NEE-119 contract, whose bytes this module
#: binds; a registration record that claims it must carry that binding as its
#: ``source_reference``. Nothing here supplies it as a default.
REGISTERED_FEATURE_VARIANTS: Final[tuple[FeatureVariant, ...]] = ()

#: Every tie-break policy this engine has an owner registration for.
#:
#: EMPTY BY DESIGN, for the same reason. The mechanism is complete and tested;
#: it refuses to rank until a sourced record exists.
REGISTERED_TIE_BREAK_POLICIES: Final[tuple[TieBreakPolicy, ...]] = ()

#: Every minimum rank-eligible breadth this engine has a registration for.
#:
#: EMPTY BY DESIGN. A breadth floor decides whether a thin cross-section produces
#: exposure at all; assuming one would be inventing exposure, which is exactly
#: what the fail-closed path exists to prevent.
REGISTERED_BREADTH_MINIMUMS: Final[tuple[BreadthMinimum, ...]] = ()


def validate_feature_variant_registry(
    variants: Sequence[FeatureVariant] = REGISTERED_FEATURE_VARIANTS,
) -> None:
    """Fail closed on an empty, duplicated, or test-contaminated variant registry."""
    if not variants:
        raise SignalError(
            BLOCKED_NO_REGISTERED_FEATURE_VARIANT,
            "no feature-variant registration exists for this engine; the lookback and "
            "skip decision is the owner's, and this engine refuses to assume a session "
            "pair, a primary variant, or a diagnostic grid",
        )
    identifiers: set[str] = set()
    for variant in variants:
        if not isinstance(variant, FeatureVariant):
            raise SignalError(
                BLOCKED_UNREGISTERED_FEATURE_VARIANT,
                "registry entries must be FeatureVariant records",
            )
        if variant.variant_id in identifiers:
            raise SignalError(
                BLOCKED_AMBIGUOUS_FEATURE_VARIANT,
                f"duplicate variant_id in registry: {variant.variant_id}",
                record_id=variant.variant_id,
            )
        identifiers.add(variant.variant_id)
        if (
            variants is REGISTERED_FEATURE_VARIANTS
            and variant.source_kind not in REGISTERED_SOURCE_KINDS
        ):
            raise SignalError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"{variant.variant_id}: {variant.source_kind} may not ship in the registry",
                record_id=variant.variant_id,
            )


def resolve_feature_variant(
    variant_id: str,
    *,
    variants: Sequence[FeatureVariant] = REGISTERED_FEATURE_VARIANTS,
) -> FeatureVariant:
    """Return the registered variant, or fail closed. Never invents ``(L, S)``."""
    validate_feature_variant_registry(variants)
    matches = [variant for variant in variants if variant.variant_id == variant_id]
    if not matches:
        raise SignalError(
            BLOCKED_UNREGISTERED_FEATURE_VARIANT,
            f"feature variant {variant_id!r} is not registered",
            record_id=variant_id,
        )
    if len(matches) > 1:  # pragma: no cover - validate_feature_variant_registry rejects these
        raise SignalError(
            BLOCKED_AMBIGUOUS_FEATURE_VARIANT,
            f"ambiguous feature variant {variant_id!r}",
            record_id=variant_id,
        )
    return matches[0]


def validate_tie_break_policy_registry(
    policies: Sequence[TieBreakPolicy] = REGISTERED_TIE_BREAK_POLICIES,
) -> None:
    """Fail closed on an empty, duplicated, or test-contaminated policy registry."""
    if not policies:
        raise SignalError(
            BLOCKED_NO_REGISTERED_TIE_BREAK_POLICY,
            "no tie-break policy registration exists for this engine; the tie rule and "
            "the final stable key are frozen owner decisions, and this engine refuses "
            "to assume an ordering",
        )
    identifiers: set[str] = set()
    for policy in policies:
        if not isinstance(policy, TieBreakPolicy):
            raise SignalError(
                BLOCKED_UNREGISTERED_TIE_BREAK_POLICY,
                "registry entries must be TieBreakPolicy records",
            )
        if policy.policy_id in identifiers:
            raise SignalError(
                BLOCKED_AMBIGUOUS_TIE_BREAK_POLICY,
                f"duplicate policy_id in registry: {policy.policy_id}",
                record_id=policy.policy_id,
            )
        identifiers.add(policy.policy_id)
        if (
            policies is REGISTERED_TIE_BREAK_POLICIES
            and policy.source_kind not in REGISTERED_SOURCE_KINDS
        ):
            raise SignalError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"{policy.policy_id}: {policy.source_kind} may not ship in the registry",
                record_id=policy.policy_id,
            )


def resolve_tie_break_policy(
    policy_id: str,
    *,
    policies: Sequence[TieBreakPolicy] = REGISTERED_TIE_BREAK_POLICIES,
) -> TieBreakPolicy:
    """Return the registered tie policy, or fail closed. Never invents an order."""
    validate_tie_break_policy_registry(policies)
    matches = [policy for policy in policies if policy.policy_id == policy_id]
    if not matches:
        raise SignalError(
            BLOCKED_UNREGISTERED_TIE_BREAK_POLICY,
            f"tie-break policy {policy_id!r} is not registered",
            record_id=policy_id,
        )
    if len(matches) > 1:  # pragma: no cover - validate_tie_break_policy_registry rejects these
        raise SignalError(
            BLOCKED_AMBIGUOUS_TIE_BREAK_POLICY,
            f"ambiguous tie-break policy {policy_id!r}",
            record_id=policy_id,
        )
    return matches[0]


def validate_breadth_minimum_registry(
    minimums: Sequence[BreadthMinimum] = REGISTERED_BREADTH_MINIMUMS,
) -> None:
    """Fail closed on an empty, duplicated, or test-contaminated breadth registry."""
    if not minimums:
        raise SignalError(
            BLOCKED_NO_REGISTERED_BREADTH_MINIMUM,
            "no minimum rank-eligible breadth is registered for this engine; the floor "
            "is a preregistered owner value with an evidence source, and this engine "
            "refuses to assume one rather than invent exposure on a thin cross-section",
        )
    identifiers: set[str] = set()
    for minimum in minimums:
        if not isinstance(minimum, BreadthMinimum):
            raise SignalError(
                BLOCKED_UNREGISTERED_BREADTH_MINIMUM,
                "registry entries must be BreadthMinimum records",
            )
        if minimum.threshold_id in identifiers:
            raise SignalError(
                BLOCKED_AMBIGUOUS_BREADTH_MINIMUM,
                f"duplicate threshold_id in registry: {minimum.threshold_id}",
                record_id=minimum.threshold_id,
            )
        identifiers.add(minimum.threshold_id)
        if (
            minimums is REGISTERED_BREADTH_MINIMUMS
            and minimum.source_kind not in REGISTERED_SOURCE_KINDS
        ):
            raise SignalError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"{minimum.threshold_id}: {minimum.source_kind} may not ship in the registry",
                record_id=minimum.threshold_id,
            )


def resolve_breadth_minimum(
    threshold_id: str,
    *,
    minimums: Sequence[BreadthMinimum] = REGISTERED_BREADTH_MINIMUMS,
) -> BreadthMinimum:
    """Return the registered breadth minimum, or fail closed. Never invents one."""
    validate_breadth_minimum_registry(minimums)
    matches = [minimum for minimum in minimums if minimum.threshold_id == threshold_id]
    if not matches:
        raise SignalError(
            BLOCKED_UNREGISTERED_BREADTH_MINIMUM,
            f"breadth minimum {threshold_id!r} is not registered",
            record_id=threshold_id,
        )
    if len(matches) > 1:  # pragma: no cover - validate_breadth_minimum_registry rejects these
        raise SignalError(
            BLOCKED_AMBIGUOUS_BREADTH_MINIMUM,
            f"ambiguous breadth minimum {threshold_id!r}",
            record_id=threshold_id,
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Bound-authority verification
# ---------------------------------------------------------------------------


def verify_bound_contract_authority(
    repository_root: Path,
    *,
    artifacts: Sequence[BoundArtifact] = BOUND_CONTRACT_AUTHORITY,
) -> tuple[BoundArtifact, ...]:
    """Re-hash every bound frozen artifact and refuse on any byte drift.

    Read-only: this engine never writes, moves, or rebinds a frozen artifact. A
    mismatch is a failed binding, not permission to update a digest.
    """
    verified: list[BoundArtifact] = []
    for artifact in artifacts:
        path = repository_root / artifact.path
        try:
            observed = grouped_sha256_file(path)
        except MarketStoreError as exc:
            raise SignalError(
                BLOCKED_CONTRACT_ARTIFACT_MISSING,
                f"bound artifact {artifact.role} is not readable",
                path=artifact.path,
            ) from exc
        if observed != artifact.sha256_grouped:
            raise SignalError(
                BLOCKED_CONTRACT_AUTHORITY_BYTES_MISMATCH,
                f"bound artifact {artifact.role} no longer matches its registered digest",
                path=artifact.path,
                detail=observed,
            )
        verified.append(artifact)
    return tuple(verified)


# ---------------------------------------------------------------------------
# Feature arithmetic
# ---------------------------------------------------------------------------


def natural_log_of_ratio(ratio: Fraction) -> Decimal:
    """``ln(R)`` for an exact positive rational ``R``, under the declared context.

    The registered order is ``decimal_ratio_then_natural_log``: the exact
    numerator and denominator enter the context exactly, one correctly-rounded
    division forms the ratio, and one correctly-rounded ``ln`` follows. See
    :data:`NATURAL_LOG_ERROR_BOUND`.
    """
    if not isinstance(ratio, Fraction):
        raise SignalError(
            BLOCKED_MALFORMED_SIGNAL_INPUT,
            "ratio must be an exact Fraction; no binary float is accepted",
        )
    if ratio <= 0:
        raise SignalError(
            BLOCKED_MALFORMED_SIGNAL_INPUT,
            "the natural logarithm of a nonpositive ratio is undefined; a nonpositive "
            "anchor is a typed NOT_SCORABLE state, never a computed value",
        )
    context = decimal_context()
    try:
        quotient = context.divide(Decimal(ratio.numerator), Decimal(ratio.denominator))
        return context.ln(quotient)
    except DecimalException as exc:
        # A trapped context signal (for example Overflow on a ratio whose
        # adjusted exponent leaves the context's range) becomes the typed
        # refusal; it never escapes as an untyped decimal exception.
        raise SignalError(
            BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE,
            "the declared decimal context cannot represent ln(R) for this ratio; "
            f"|ln(R)| would exceed the accepted magnitude {MAX_ABSOLUTE_LOG_MOMENTUM}",
        ) from exc


def feature_value(ratio: Fraction) -> str:
    """The reported feature ``M = ln(R)``, rendered once at the artifact scale."""
    logarithm = natural_log_of_ratio(ratio)
    exact = Fraction(logarithm)
    if abs(exact) > MAX_ABSOLUTE_LOG_MOMENTUM:
        raise SignalError(
            BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE,
            f"|ln(R)| exceeds the accepted magnitude {MAX_ABSOLUTE_LOG_MOMENTUM}; "
            "the documented error bound does not cover it",
            detail=str(exact.numerator) + "/" + str(exact.denominator),
        )
    return render_artifact(exact, scale=SIGNAL_ARTIFACT_SCALE)


def diagnostic_simple_return(ratio: Fraction) -> str:
    """``R - 1``, the registered diagnostic. Never replaces the log statistic."""
    if not isinstance(ratio, Fraction):  # pragma: no cover - callers pass an exact ratio
        raise SignalError(
            BLOCKED_MALFORMED_SIGNAL_INPUT, "ratio must be an exact Fraction"
        )
    return render_artifact(ratio - 1, scale=SIGNAL_ARTIFACT_SCALE)


def selection_size(rank_eligible_breadth: int, minimum: BreadthMinimum) -> tuple[str, int]:
    """``(selection_state, K_t)`` for a rank-eligible breadth and a registered floor.

    Below the registered minimum the state is ``INVALID_INSUFFICIENT_BREADTH``
    and ``K_t`` is ``0``: no exposure is invented for a thin cross-section. At or
    above it, ``K_t = min(50, (20 * N_t) // 100)``; a zero size there is reported
    as ``INVALID_ZERO_SELECTION_SIZE`` rather than as an empty valid book.
    """
    breadth = _exact_int(
        rank_eligible_breadth,
        what="rank_eligible_breadth",
        state=BLOCKED_MALFORMED_SIGNAL_INPUT,
    )
    if breadth < 0:
        raise SignalError(
            BLOCKED_MALFORMED_SIGNAL_INPUT, "rank-eligible breadth cannot be negative"
        )
    if not isinstance(minimum, BreadthMinimum):
        raise SignalError(
            BLOCKED_UNREGISTERED_BREADTH_MINIMUM,
            "the breadth floor must be a registered BreadthMinimum record",
        )
    if breadth < minimum.minimum_rank_eligible_breadth:
        return INVALID_INSUFFICIENT_BREADTH, 0
    size = min(
        SELECTION_MAXIMUM_NAMES,
        (SELECTION_FRACTION_NUMERATOR * breadth) // SELECTION_FRACTION_DENOMINATOR,
    )
    if size == 0:
        return INVALID_ZERO_SELECTION_SIZE, 0
    return SELECTION_VALID, size


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

UNIVERSE_IN_REQUIRED_UNIVERSE: Final = "IN_REQUIRED_UNIVERSE"
UNIVERSE_NOT_IN_REQUIRED_UNIVERSE: Final = "NOT_IN_REQUIRED_UNIVERSE"
UNIVERSE_MEMBERSHIP_STATES: Final = (
    UNIVERSE_IN_REQUIRED_UNIVERSE,
    UNIVERSE_NOT_IN_REQUIRED_UNIVERSE,
)

TOTAL_RETURN_CHAIN_OK: Final = "TOTAL_RETURN_CHAIN_OK"
TOTAL_RETURN_CHAIN_INVALID: Final = "TOTAL_RETURN_CHAIN_INVALID"
TOTAL_RETURN_CHAIN_STATES: Final = (TOTAL_RETURN_CHAIN_INVALID, TOTAL_RETURN_CHAIN_OK)

SOURCE_FRESH_AT_CUTOFF: Final = "SOURCE_FRESH_AT_CUTOFF"
SOURCE_STALE_AT_CUTOFF: Final = "SOURCE_STALE_AT_CUTOFF"
SOURCE_FRESHNESS_STATES: Final = (SOURCE_FRESH_AT_CUTOFF, SOURCE_STALE_AT_CUTOFF)


@dataclass(frozen=True)
class TotalReturnObservation:
    """One point-in-time total-return close at an exact exchange session."""

    session: str
    total_return_close: str

    def __post_init__(self) -> None:
        if type(self.session) is not str:
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                f"observation session must be an exact str, not {type(self.session).__name__}",
            )
        if type(self.total_return_close) is not str:
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                "total_return_close must be a canonical base-10 decimal string, not "
                f"{type(self.total_return_close).__name__}",
            )

    def to_json_dict(self) -> dict[str, str]:
        return {"session": self.session, "total_return_close": self.total_return_close}


@dataclass(frozen=True)
class SecuritySessionInput:
    """Everything this engine needs about one security at one signal session.

    ``total_return_chain_state`` and ``source_freshness_state`` are *declared*
    by the caller from the bound total-return methodology and the bound source
    freshness policy. This engine does not re-derive either -- it consumes the
    typed verdict and refuses an unregistered token. That seam is deliberate:
    the freshness policy is a frozen artifact with its own authority, and a
    second implementation of it here could disagree with it.
    """

    security_id: str
    universe_membership: str
    observed_span_start: str
    total_return_chain_state: str
    source_freshness_state: str
    observations: tuple[TotalReturnObservation, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "security_id",
            "universe_membership",
            "observed_span_start",
            "total_return_chain_state",
            "source_freshness_state",
        ):
            if type(getattr(self, field_name)) is not str:
                raise SignalError(
                    BLOCKED_MALFORMED_SIGNAL_INPUT,
                    f"{field_name} must be an exact str, "
                    f"not {type(getattr(self, field_name)).__name__}",
                )
        if type(self.observations) is not tuple:
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                f"{self.security_id}: observations must be an immutable tuple",
                security_id=self.security_id,
            )
        for observation in self.observations:
            if not isinstance(observation, TotalReturnObservation):
                raise SignalError(
                    BLOCKED_MALFORMED_SIGNAL_INPUT,
                    f"{self.security_id}: observations must be TotalReturnObservation records",
                    security_id=self.security_id,
                )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "universe_membership": self.universe_membership,
            "observed_span_start": self.observed_span_start,
            "total_return_chain_state": self.total_return_chain_state,
            "source_freshness_state": self.source_freshness_state,
            "observations": [item.to_json_dict() for item in self.observations],
        }


@dataclass(frozen=True)
class _NormalizedInput:
    """A validated input with its exact observation index. Internal."""

    security_id: str
    stable_key: str
    universe_membership: str
    observed_span_start: str
    total_return_chain_state: str
    source_freshness_state: str
    observation_by_session: Mapping[str, Fraction]
    canonical_observations: tuple[tuple[str, str], ...]


def _normalize_input(
    candidate: SecuritySessionInput,
    *,
    calendar: TradingCalendar,
    signal_session: str,
    analysis_cutoff: str,
) -> _NormalizedInput:
    if not isinstance(candidate, SecuritySessionInput):
        raise SignalError(
            BLOCKED_MALFORMED_SIGNAL_INPUT, "inputs must be SecuritySessionInput records"
        )
    security_id = _identifier(candidate.security_id, what="security_id")
    _in_vocabulary(
        candidate.universe_membership,
        UNIVERSE_MEMBERSHIP_STATES,
        what=f"{security_id}: universe_membership",
        state=BLOCKED_UNREGISTERED_INPUT_VOCABULARY,
    )
    _in_vocabulary(
        candidate.total_return_chain_state,
        TOTAL_RETURN_CHAIN_STATES,
        what=f"{security_id}: total_return_chain_state",
        state=BLOCKED_UNREGISTERED_INPUT_VOCABULARY,
    )
    _in_vocabulary(
        candidate.source_freshness_state,
        SOURCE_FRESHNESS_STATES,
        what=f"{security_id}: source_freshness_state",
        state=BLOCKED_UNREGISTERED_INPUT_VOCABULARY,
    )
    span_start = iso_date(candidate.observed_span_start, what="observed_span_start")
    calendar.position(span_start)
    if span_start > signal_session:
        raise SignalError(
            BLOCKED_MALFORMED_SIGNAL_INPUT,
            f"{security_id}: observed_span_start {span_start} is after the signal session",
            security_id=security_id,
            session=span_start,
        )
    if type(candidate.observations) is not tuple:
        raise SignalError(
            BLOCKED_MALFORMED_SIGNAL_INPUT,
            f"{security_id}: observations must be an immutable tuple",
            security_id=security_id,
        )
    index: dict[str, Fraction] = {}
    canonical: list[tuple[str, str]] = []
    for observation in candidate.observations:
        if not isinstance(observation, TotalReturnObservation):
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                f"{security_id}: observations must be TotalReturnObservation records",
                security_id=security_id,
            )
        session = iso_date(observation.session, what="observation session")
        # An exact calendar lookup: a nearby date is never substituted.
        calendar.session(session)
        if session > analysis_cutoff:
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                f"{security_id}: observation session {session} is after the analysis "
                f"cutoff {analysis_cutoff}; the declared price coordinate is "
                "point-in-time as known at the signal cutoff, so a later-dated "
                "observation is refused rather than folded into the lineage",
                security_id=security_id,
                session=session,
            )
        if session in index:
            raise SignalError(
                BLOCKED_DUPLICATE_OBSERVATION_SESSION,
                f"{security_id}: session {session} appears more than once",
                security_id=security_id,
                session=session,
            )
        value = _exact_decimal_string(
            observation.total_return_close,
            what=f"{security_id}: total_return_close",
            security_id=security_id,
        )
        index[session] = value
        canonical.append(
            (session, canonical_decimal(observation.total_return_close, what="total_return_close"))
        )
    return _NormalizedInput(
        security_id=security_id,
        stable_key=stable_key(security_id),
        universe_membership=candidate.universe_membership,
        observed_span_start=span_start,
        total_return_chain_state=candidate.total_return_chain_state,
        source_freshness_state=candidate.source_freshness_state,
        observation_by_session=dict(sorted(index.items())),
        canonical_observations=tuple(sorted(canonical)),
    )


# ---------------------------------------------------------------------------
# Output rows
# ---------------------------------------------------------------------------

#: Every field name a row payload carries, sorted. Part of the schema digest.
ROW_FIELD_NAMES: Final = (
    "code_binding_sha256_grouped",
    "config_sha256_grouped",
    "diagnostic_simple_return",
    "diagnostic_value_type",
    "eligibility_state",
    "engine_id",
    "feature_exactness",
    "feature_status",
    "feature_value",
    "feature_value_type",
    "input_sha256_grouped",
    "lookback_sessions",
    "old_anchor_session",
    "old_anchor_total_return",
    "rank",
    "ranking_ratio",
    "recent_anchor_session",
    "recent_anchor_total_return",
    "run_id",
    "schema_sha256_grouped",
    "schema_version",
    "security_id",
    "selected",
    "selection_reason",
    "signal_session",
    "skip_sessions",
    "source_freshness_state",
    "stable_key",
    "tie_break_ordinal",
    "tie_group_key",
    "tie_group_size",
    "total_return_chain_state",
    "universe_membership",
    "variant_id",
    "variant_role",
)

#: Every field name a manifest carries, sorted. Part of the schema digest.
MANIFEST_FIELD_NAMES: Final = (
    "analysis_cutoff",
    "bound_contract_authority",
    "breadth_minimum",
    "calculation_order",
    "claims",
    "code_binding_sha256_grouped",
    "config_sha256_grouped",
    "engine_id",
    "feature_equation",
    "feature_name",
    "feature_value_type",
    "input_sha256_grouped",
    "natural_log_error_bound",
    "nearest_session_substitution_allowed",
    "old_anchor_session",
    "price_coordinate",
    "rank_eligible_breadth",
    "rank_order_depends_on_rounded_log",
    "ranking_comparison",
    "recent_anchor_session",
    "row_count",
    "rows_sha256_grouped",
    "run_id",
    "schema_sha256_grouped",
    "schema_version",
    "selected_count",
    "selection_formula",
    "selection_integer_implementation",
    "selection_size",
    "selection_state",
    "signal_artifact_scale",
    "signal_rounding_mode",
    "signal_session",
    "tie_break_policy",
    "variant",
)


#: Selection reasons that mean the row is in the selected book.
_INCLUDED_SELECTION_REASONS: Final = (INCLUDED_BOUNDARY_TIE_BREAK, INCLUDED_BY_RANK)
#: A positive exact rational rendered as ``numerator/denominator``.
_RANKING_RATIO_RE: Final = re.compile(r"[1-9][0-9]*/[1-9][0-9]*")
#: A grouped digest: eight lowercase 8-hex groups joined by ``:``.
_GROUPED_DIGEST_RE: Final = re.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}")


@dataclass(frozen=True)
class SignalRow:
    """One immutable output row for one security at one signal session.

    Construction validates every closed vocabulary and the cross-field
    invariants below, so a plain constructor call cannot mint a row carrying an
    invented state, a selected-but-unranked contradiction, or a malformed
    ratio, and then acquire a plausible-looking grouped self-hash.
    """

    run_id: str
    signal_session: str
    security_id: str
    stable_key: str
    variant_id: str
    variant_role: str
    lookback_sessions: int
    skip_sessions: int
    universe_membership: str
    total_return_chain_state: str
    source_freshness_state: str
    recent_anchor_session: str | None
    old_anchor_session: str | None
    recent_anchor_total_return: str | None
    old_anchor_total_return: str | None
    ranking_ratio: str | None
    feature_value: str | None
    feature_value_type: str
    feature_exactness: str
    diagnostic_simple_return: str | None
    diagnostic_value_type: str
    feature_status: str
    eligibility_state: str
    rank: int | None
    tie_group_key: str | None
    tie_group_size: int | None
    tie_break_ordinal: int | None
    selected: bool
    selection_reason: str
    input_sha256_grouped: str
    config_sha256_grouped: str
    code_binding_sha256_grouped: str
    schema_sha256_grouped: str

    def __post_init__(self) -> None:
        _identifier(self.security_id, what="security_id")
        _in_vocabulary(
            self.feature_status,
            FEATURE_STATUSES,
            what=f"{self.security_id}: feature_status",
            state=BLOCKED_UNREGISTERED_INPUT_VOCABULARY,
        )
        _in_vocabulary(
            self.universe_membership,
            UNIVERSE_MEMBERSHIP_STATES,
            what=f"{self.security_id}: universe_membership",
            state=BLOCKED_UNREGISTERED_INPUT_VOCABULARY,
        )
        _in_vocabulary(
            self.total_return_chain_state,
            TOTAL_RETURN_CHAIN_STATES,
            what=f"{self.security_id}: total_return_chain_state",
            state=BLOCKED_UNREGISTERED_INPUT_VOCABULARY,
        )
        _in_vocabulary(
            self.source_freshness_state,
            SOURCE_FRESHNESS_STATES,
            what=f"{self.security_id}: source_freshness_state",
            state=BLOCKED_UNREGISTERED_INPUT_VOCABULARY,
        )
        _in_vocabulary(
            self.eligibility_state,
            ELIGIBILITY_STATES,
            what=f"{self.security_id}: eligibility_state",
            state=BLOCKED_UNREGISTERED_INPUT_VOCABULARY,
        )
        _in_vocabulary(
            self.selection_reason,
            SELECTION_REASONS,
            what=f"{self.security_id}: selection_reason",
            state=BLOCKED_UNREGISTERED_INPUT_VOCABULARY,
        )
        _in_vocabulary(
            self.feature_exactness,
            FEATURE_EXACTNESS_KINDS,
            what=f"{self.security_id}: feature_exactness",
            state=BLOCKED_UNREGISTERED_INPUT_VOCABULARY,
        )
        _in_vocabulary(
            self.variant_role,
            VARIANT_ROLES,
            what=f"{self.security_id}: variant_role",
            state=BLOCKED_UNREGISTERED_VARIANT_ROLE,
        )
        if type(self.selected) is not bool:
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                f"{self.security_id}: selected must be an exact bool",
                security_id=self.security_id,
            )
        for field_name in (
            "run_id",
            "input_sha256_grouped",
            "config_sha256_grouped",
            "code_binding_sha256_grouped",
            "schema_sha256_grouped",
        ):
            digest = getattr(self, field_name)
            if type(digest) is not str or _GROUPED_DIGEST_RE.fullmatch(digest) is None:
                raise SignalError(
                    BLOCKED_MALFORMED_SIGNAL_INPUT,
                    f"{self.security_id}: {field_name} must be a grouped sha256 digest",
                    security_id=self.security_id,
                )
        scorable = self.feature_status == FEATURE_SCORABLE
        for field_name in ("feature_value", "ranking_ratio", "diagnostic_simple_return"):
            if (getattr(self, field_name) is not None) != scorable:
                raise SignalError(
                    BLOCKED_MALFORMED_SIGNAL_INPUT,
                    f"{self.security_id}: {field_name} is present exactly when the row "
                    "is FEATURE_SCORABLE; a value can be neither invented for a "
                    "non-scorable row nor withheld from a scorable one",
                    security_id=self.security_id,
                )
        expected_exactness = (
            FEATURE_EXACTNESS_ROUNDED_ARTIFACT if scorable else FEATURE_EXACTNESS_NOT_COMPUTED
        )
        if self.feature_exactness != expected_exactness:
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                f"{self.security_id}: feature_exactness must be "
                f"{expected_exactness!r} for feature_status {self.feature_status!r}",
                security_id=self.security_id,
            )
        if self.ranking_ratio is not None and (
            _RANKING_RATIO_RE.fullmatch(self.ranking_ratio) is None
        ):
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                f"{self.security_id}: ranking_ratio must be a positive exact rational "
                "rendered as numerator/denominator",
                security_id=self.security_id,
            )
        ranked = self.rank is not None
        if ranked != (self.eligibility_state == ELIGIBLE_RANK_ELIGIBLE):
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                f"{self.security_id}: a row carries a rank exactly when it is "
                "RANK_ELIGIBLE",
                security_id=self.security_id,
            )
        for field_name in ("tie_group_key", "tie_group_size", "tie_break_ordinal"):
            if (getattr(self, field_name) is not None) != ranked:
                raise SignalError(
                    BLOCKED_MALFORMED_SIGNAL_INPUT,
                    f"{self.security_id}: rank, tie_group_key, tie_group_size and "
                    "tie_break_ordinal are present together or not at all",
                    security_id=self.security_id,
                )
        if ranked:
            rank = _exact_int(self.rank, what="rank", state=BLOCKED_MALFORMED_SIGNAL_INPUT)
            size = _exact_int(
                self.tie_group_size,
                what="tie_group_size",
                state=BLOCKED_MALFORMED_SIGNAL_INPUT,
            )
            ordinal = _exact_int(
                self.tie_break_ordinal,
                what="tie_break_ordinal",
                state=BLOCKED_MALFORMED_SIGNAL_INPUT,
            )
            if rank < 1 or size < 1 or not 1 <= ordinal <= size:
                raise SignalError(
                    BLOCKED_MALFORMED_SIGNAL_INPUT,
                    f"{self.security_id}: rank, tie_group_size and tie_break_ordinal "
                    "must be positive and mutually consistent",
                    security_id=self.security_id,
                )
            if self.tie_group_key is None or (
                _GROUPED_DIGEST_RE.fullmatch(self.tie_group_key) is None
            ):
                raise SignalError(
                    BLOCKED_MALFORMED_SIGNAL_INPUT,
                    f"{self.security_id}: tie_group_key must be a grouped digest",
                    security_id=self.security_id,
                )
        if ranked == (self.selection_reason == NOT_SELECTED_NOT_RANK_ELIGIBLE):
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                f"{self.security_id}: NOT_SELECTED_NOT_RANK_ELIGIBLE is the selection "
                "reason exactly for unranked rows",
                security_id=self.security_id,
            )
        if self.selected != (self.selection_reason in _INCLUDED_SELECTION_REASONS):
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                f"{self.security_id}: selected must agree with selection_reason; a "
                "selected row carries an INCLUDED_* reason and an unselected row "
                "never does",
                security_id=self.security_id,
            )

    def payload(self) -> dict[str, Any]:
        """The row's canonical payload, without its own self-hash."""
        return {
            "code_binding_sha256_grouped": self.code_binding_sha256_grouped,
            "config_sha256_grouped": self.config_sha256_grouped,
            "diagnostic_simple_return": self.diagnostic_simple_return,
            "diagnostic_value_type": self.diagnostic_value_type,
            "eligibility_state": self.eligibility_state,
            "engine_id": ENGINE_ID,
            "feature_exactness": self.feature_exactness,
            "feature_status": self.feature_status,
            "feature_value": self.feature_value,
            "feature_value_type": self.feature_value_type,
            "input_sha256_grouped": self.input_sha256_grouped,
            "lookback_sessions": self.lookback_sessions,
            "old_anchor_session": self.old_anchor_session,
            "old_anchor_total_return": self.old_anchor_total_return,
            "rank": self.rank,
            "ranking_ratio": self.ranking_ratio,
            "recent_anchor_session": self.recent_anchor_session,
            "recent_anchor_total_return": self.recent_anchor_total_return,
            "run_id": self.run_id,
            "schema_sha256_grouped": self.schema_sha256_grouped,
            "schema_version": SCHEMA_VERSION,
            "security_id": self.security_id,
            "selected": self.selected,
            "selection_reason": self.selection_reason,
            "signal_session": self.signal_session,
            "skip_sessions": self.skip_sessions,
            "source_freshness_state": self.source_freshness_state,
            "stable_key": self.stable_key,
            "tie_break_ordinal": self.tie_break_ordinal,
            "tie_group_key": self.tie_group_key,
            "tie_group_size": self.tie_group_size,
            "total_return_chain_state": self.total_return_chain_state,
            "universe_membership": self.universe_membership,
            "variant_id": self.variant_id,
            "variant_role": self.variant_role,
        }

    @property
    def row_sha256_grouped(self) -> str:
        """The row's grouped self-hash over its canonical payload."""
        return grouped_sha256_bytes(canonical_json_bytes(self.payload()))

    def to_json_dict(self) -> dict[str, Any]:
        document = self.payload()
        document["row_sha256_grouped"] = self.row_sha256_grouped
        return document

    def contract_v2_reason_codes(self) -> dict[str, str]:
        """This row's statuses in the contract's own reason-code vocabulary.

        A state the contract deliberately has no row for maps to the explicit
        :data:`CONTRACT_V2_NO_CONTRACT_EQUIVALENT` sentinel, never to ``None``,
        so it cannot be mistaken for an unknown token (which is refused).
        """
        return {
            "feature_status": contract_v2_reason_code(self.feature_status),
            "selection_reason": contract_v2_reason_code(self.selection_reason),
        }


@dataclass(frozen=True)
class SignalRunResult:
    """One immutable cross-section for one variant at one signal session."""

    run_id: str
    signal_session: str
    analysis_cutoff: str
    variant: FeatureVariant
    tie_policy: TieBreakPolicy
    breadth_minimum: BreadthMinimum
    recent_anchor_session: str
    old_anchor_session: str
    selection_state: str
    rank_eligible_breadth: int
    selection_size: int
    rows: tuple[SignalRow, ...]
    input_sha256_grouped: str
    config_sha256_grouped: str
    code_binding_sha256_grouped: str
    schema_sha256_grouped: str

    def __post_init__(self) -> None:
        """A plain constructor call cannot mint a run with invented outcomes.

        ``selection_state``, ``selection_size`` and every ``selected`` flag must
        be re-derivable from the rows and the registered breadth floor, and each
        row must carry this run's lineage; a fabricated result fails here
        instead of acquiring a plausible-looking manifest digest.
        """
        if not isinstance(self.variant, FeatureVariant):
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                "variant must be a validated FeatureVariant record",
            )
        if not isinstance(self.tie_policy, TieBreakPolicy):
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                "tie_policy must be a validated TieBreakPolicy record",
            )
        if not isinstance(self.breadth_minimum, BreadthMinimum):
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                "breadth_minimum must be a validated BreadthMinimum record",
            )
        _in_vocabulary(
            self.selection_state,
            SELECTION_STATES,
            what="selection_state",
            state=BLOCKED_UNREGISTERED_INPUT_VOCABULARY,
        )
        if type(self.rows) is not tuple:
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT, "rows must be an immutable tuple"
            )
        for row in self.rows:
            if not isinstance(row, SignalRow):
                raise SignalError(
                    BLOCKED_MALFORMED_SIGNAL_INPUT, "rows must be SignalRow records"
                )
            if (
                row.run_id != self.run_id
                or row.signal_session != self.signal_session
                or row.variant_id != self.variant.variant_id
                or row.variant_role != self.variant.variant_role
                or row.input_sha256_grouped != self.input_sha256_grouped
                or row.config_sha256_grouped != self.config_sha256_grouped
                or row.code_binding_sha256_grouped != self.code_binding_sha256_grouped
                or row.schema_sha256_grouped != self.schema_sha256_grouped
            ):
                raise SignalError(
                    BLOCKED_MALFORMED_SIGNAL_INPUT,
                    f"{row.security_id}: row lineage does not match the run it is "
                    "filed under",
                    security_id=row.security_id,
                )
        breadth = _exact_int(
            self.rank_eligible_breadth,
            what="rank_eligible_breadth",
            state=BLOCKED_MALFORMED_SIGNAL_INPUT,
        )
        eligible = [
            row for row in self.rows if row.eligibility_state == ELIGIBLE_RANK_ELIGIBLE
        ]
        if breadth != len(eligible):
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                "rank_eligible_breadth must equal the count of RANK_ELIGIBLE rows",
            )
        if sorted(row.rank for row in eligible if row.rank is not None) != list(
            range(1, breadth + 1)
        ):
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                "ranks must be the unique ordinals 1..N over the rank-eligible rows",
            )
        expected_state, expected_size = selection_size(breadth, self.breadth_minimum)
        if (self.selection_state, self.selection_size) != (expected_state, expected_size):
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT,
                "selection_state and selection_size must be re-derivable from the "
                f"rows under {SELECTION_FORMULA}: declared "
                f"({self.selection_state}, {self.selection_size}), derived "
                f"({expected_state}, {expected_size})",
            )
        for row in self.rows:
            if row.rank is None:
                if row.selected:  # pragma: no cover - SignalRow already refuses this
                    raise SignalError(
                        BLOCKED_MALFORMED_SIGNAL_INPUT,
                        f"{row.security_id}: an unranked row cannot be selected",
                        security_id=row.security_id,
                    )
                continue
            should_select = expected_state == SELECTION_VALID and row.rank <= expected_size
            if row.selected != should_select:
                raise SignalError(
                    BLOCKED_MALFORMED_SIGNAL_INPUT,
                    f"{row.security_id}: selected must hold exactly for "
                    "rank <= selection_size under a valid selection state",
                    security_id=row.security_id,
                )

    @property
    def variant_id(self) -> str:
        return self.variant.variant_id

    @property
    def variant_role(self) -> str:
        return self.variant.variant_role

    @property
    def selected_security_ids(self) -> tuple[str, ...]:
        """Selected securities in rank order. Empty whenever selection is invalid."""
        selected = [row for row in self.rows if row.selected]
        selected.sort(key=lambda row: (row.rank if row.rank is not None else 0))
        return tuple(row.security_id for row in selected)

    def table(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(row.to_json_dict() for row in self.rows)

    def rows_digest(self) -> str:
        return canonical_dataset_digest(
            {"rows": [dict(row) for row in self.table()], "run_id": self.run_id}
        )

    def manifest_payload(self) -> dict[str, Any]:
        """The manifest's canonical payload, without its own self-hash."""
        return {
            "analysis_cutoff": self.analysis_cutoff,
            "bound_contract_authority": [
                artifact.to_json_dict() for artifact in BOUND_CONTRACT_AUTHORITY
            ],
            "breadth_minimum": self.breadth_minimum.to_json_dict(),
            "calculation_order": CALCULATION_ORDER,
            "claims": dict(NON_CLAIMS),
            "code_binding_sha256_grouped": self.code_binding_sha256_grouped,
            "config_sha256_grouped": self.config_sha256_grouped,
            "engine_id": ENGINE_ID,
            "feature_equation": FEATURE_EQUATION,
            "feature_name": FEATURE_NAME,
            "feature_value_type": FEATURE_VALUE_TYPE_LOG,
            "input_sha256_grouped": self.input_sha256_grouped,
            "natural_log_error_bound": NATURAL_LOG_ERROR_BOUND,
            "nearest_session_substitution_allowed": NEAREST_SESSION_SUBSTITUTION_ALLOWED,
            "old_anchor_session": self.old_anchor_session,
            "price_coordinate": PRICE_COORDINATE,
            "rank_eligible_breadth": self.rank_eligible_breadth,
            "rank_order_depends_on_rounded_log": RANK_ORDER_DEPENDS_ON_ROUNDED_LOG,
            "ranking_comparison": RANKING_COMPARISON,
            "recent_anchor_session": self.recent_anchor_session,
            "row_count": len(self.rows),
            "rows_sha256_grouped": self.rows_digest(),
            "run_id": self.run_id,
            "schema_sha256_grouped": self.schema_sha256_grouped,
            "schema_version": SCHEMA_VERSION,
            "selected_count": sum(1 for row in self.rows if row.selected),
            "selection_formula": SELECTION_FORMULA,
            "selection_integer_implementation": SELECTION_INTEGER_IMPLEMENTATION,
            "selection_size": self.selection_size,
            "selection_state": self.selection_state,
            "signal_artifact_scale": SIGNAL_ARTIFACT_SCALE,
            "signal_rounding_mode": SIGNAL_ROUNDING_MODE,
            "signal_session": self.signal_session,
            "tie_break_policy": self.tie_policy.to_json_dict(),
            "variant": self.variant.to_json_dict(),
        }

    @property
    def manifest_sha256_grouped(self) -> str:
        return grouped_sha256_bytes(canonical_json_bytes(self.manifest_payload()))

    def manifest(self) -> dict[str, Any]:
        document = self.manifest_payload()
        document["manifest_sha256_grouped"] = self.manifest_sha256_grouped
        return document

    def to_json_dict(self) -> dict[str, Any]:
        return {"manifest": self.manifest(), "rows": [dict(row) for row in self.table()]}


@dataclass(frozen=True)
class SignalOutputSet:
    """A primary result and its grid diagnostics, structurally kept apart.

    The primary slot admits a ``PRIMARY`` result and nothing else; the grid slot
    admits ``GRID_DIAGNOSTIC`` results and nothing else; no ``variant_id`` may
    appear twice. A grid variant therefore cannot land in, replace, or shadow the
    primary output.
    """

    primary: SignalRunResult
    grid: tuple[SignalRunResult, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.primary, SignalRunResult):
            raise SignalError(
                BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY,
                "the primary slot admits a SignalRunResult and nothing else",
            )
        if self.primary.variant_role != VARIANT_ROLE_PRIMARY:
            raise SignalError(
                BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY,
                f"variant {self.primary.variant_id!r} has role "
                f"{self.primary.variant_role!r} and cannot occupy the primary slot",
                record_id=self.primary.variant_id,
            )
        if type(self.grid) is not tuple:
            raise SignalError(
                BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY,
                "grid results must be an immutable tuple",
            )
        seen = {self.primary.variant_id}
        for result in self.grid:
            if not isinstance(result, SignalRunResult):
                raise SignalError(
                    BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY,
                    "grid entries must be SignalRunResult records",
                )
            if result.variant_role != VARIANT_ROLE_GRID_DIAGNOSTIC:
                raise SignalError(
                    BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY,
                    f"variant {result.variant_id!r} has role {result.variant_role!r} "
                    "and cannot be filed as a grid diagnostic",
                    record_id=result.variant_id,
                )
            if result.variant_id in seen:
                raise SignalError(
                    BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY,
                    f"variant {result.variant_id!r} appears more than once; a grid "
                    "variant may not overwrite another output",
                    record_id=result.variant_id,
                )
            seen.add(result.variant_id)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary.to_json_dict(),
            "grid": [result.to_json_dict() for result in self.grid],
        }


# ---------------------------------------------------------------------------
# Lineage digests
# ---------------------------------------------------------------------------


def schema_digest() -> str:
    """Grouped digest over the declared row and manifest field schema."""
    return canonical_dataset_digest(
        {
            "schema_version": SCHEMA_VERSION,
            "row_field_names": list(ROW_FIELD_NAMES),
            "manifest_field_names": list(MANIFEST_FIELD_NAMES),
            "row_self_hash_field": "row_sha256_grouped",
            "manifest_self_hash_field": "manifest_sha256_grouped",
        }
    )


def code_binding_digest() -> str:
    """Grouped digest over this engine's declared bindings and vocabularies.

    Scope, stated exactly so the field is not read as more than it is: it covers
    the engine identity, the numeric policy, every registered vocabulary and
    typed state, the selection-rule constants, the bound frozen artifacts with
    their grouped digests, and the calendar store's own binding digest. It does
    **not** hash this module's Python source; a source-tree digest is the
    repository lock's job, and self-pinning a module's own bytes is reserved for
    the grandfathered T1 paths in ``configs/governance/change-tier-policy-v1.json``.
    The digest therefore changes whenever a bound artifact, a declared vocabulary,
    or the numeric policy changes, and does not change on a non-semantic edit.
    """
    return canonical_dataset_digest(
        {
            "engine_id": ENGINE_ID,
            "schema_version": SCHEMA_VERSION,
            "feature_name": FEATURE_NAME,
            "feature_equation": FEATURE_EQUATION,
            "calculation_order": CALCULATION_ORDER,
            "ranking_comparison": RANKING_COMPARISON,
            "price_coordinate": PRICE_COORDINATE,
            "numeric_policy": {
                "decimal_context_precision": DECIMAL_CONTEXT_PRECISION,
                "decimal_rounding": DECIMAL_ROUNDING,
                "signal_artifact_scale": SIGNAL_ARTIFACT_SCALE,
                "signal_rounding_mode": SIGNAL_ROUNDING_MODE,
                "max_absolute_log_momentum": str(MAX_ABSOLUTE_LOG_MOMENTUM),
                "natural_log_error_bound": NATURAL_LOG_ERROR_BOUND,
                "binary_float_forbidden": True,
            },
            "selection_rule": {
                "formula": SELECTION_FORMULA,
                "integer_implementation": SELECTION_INTEGER_IMPLEMENTATION,
                "maximum_names": SELECTION_MAXIMUM_NAMES,
                "fraction_numerator": SELECTION_FRACTION_NUMERATOR,
                "fraction_denominator": SELECTION_FRACTION_DENOMINATOR,
            },
            "vocabularies": {
                "boundary_tie_policies": list(BOUNDARY_TIE_POLICIES),
                "breadth_evidence_source_types": list(ACCEPTABLE_BREADTH_EVIDENCE_SOURCE_TYPES),
                "breadth_units": list(BREADTH_UNITS),
                "eligibility_states": list(ELIGIBILITY_STATES),
                "fail_closed_states": list(FAIL_CLOSED_STATES),
                "feature_exactness_kinds": list(FEATURE_EXACTNESS_KINDS),
                "feature_status_precedence": list(FEATURE_STATUS_PRECEDENCE),
                "ordering_keys": list(REGISTERED_ORDERING_KEYS),
                "rank_methods": list(RANK_METHODS),
                "selection_reasons": list(SELECTION_REASONS),
                "selection_states": list(SELECTION_STATES),
                "source_freshness_states": list(SOURCE_FRESHNESS_STATES),
                "source_kinds": list(SOURCE_KINDS),
                "stable_key_normalizations": list(STABLE_KEY_NORMALIZATIONS),
                "stable_key_orders": list(STABLE_KEY_ORDERS),
                "stable_keys": list(STABLE_KEYS),
                "surfaced_calendar_states": list(SURFACED_CALENDAR_STATES),
                "total_return_chain_states": list(TOTAL_RETURN_CHAIN_STATES),
                "universe_membership_states": list(UNIVERSE_MEMBERSHIP_STATES),
                "variant_roles": list(VARIANT_ROLES),
            },
            "bound_contract_authority": [
                artifact.to_json_dict() for artifact in BOUND_CONTRACT_AUTHORITY
            ],
            "calendar_store_binding": store_binding_digest({"signal_engine_id": ENGINE_ID}),
        }
    )


def _input_digest(
    normalized: Sequence[_NormalizedInput], *, signal_session: str, analysis_cutoff: str
) -> str:
    """Grouped digest over the normalized cross-section. Order-invariant."""
    return canonical_dataset_digest(
        {
            "signal_session": signal_session,
            "analysis_cutoff": analysis_cutoff,
            "securities": [
                {
                    "security_id": item.security_id,
                    "stable_key": item.stable_key,
                    "universe_membership": item.universe_membership,
                    "observed_span_start": item.observed_span_start,
                    "total_return_chain_state": item.total_return_chain_state,
                    "source_freshness_state": item.source_freshness_state,
                    "observations": [
                        {"session": session, "total_return_close": value}
                        for session, value in item.canonical_observations
                    ],
                }
                for item in sorted(normalized, key=lambda entry: entry.stable_key.encode("utf-8"))
            ],
        }
    )


def _config_digest(
    variant: FeatureVariant, tie_policy: TieBreakPolicy, minimum: BreadthMinimum
) -> str:
    """Grouped digest over the three resolved registry records and the rule."""
    return canonical_dataset_digest(
        {
            "variant": variant.to_json_dict(),
            "tie_break_policy": tie_policy.to_json_dict(),
            "breadth_minimum": minimum.to_json_dict(),
            "selection_formula": SELECTION_FORMULA,
            "selection_integer_implementation": SELECTION_INTEGER_IMPLEMENTATION,
            "selection_maximum_names": SELECTION_MAXIMUM_NAMES,
            "selection_fraction_numerator": SELECTION_FRACTION_NUMERATOR,
            "selection_fraction_denominator": SELECTION_FRACTION_DENOMINATOR,
        }
    )


def _tie_group_key(ratio: Fraction, *, variant_id: str, signal_session: str) -> str:
    """A content-derived identifier for one exact ranking value in one run."""
    return canonical_dataset_digest(
        {
            "variant_id": variant_id,
            "signal_session": signal_session,
            "ranking_ratio_numerator": str(ratio.numerator),
            "ranking_ratio_denominator": str(ratio.denominator),
        }
    )


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Scored:
    """One normalized input with its computed feature. Internal."""

    normalized: _NormalizedInput
    feature_status: str
    eligibility_state: str
    recent_anchor_total_return: str | None
    old_anchor_total_return: str | None
    ranking_ratio: Fraction | None
    ranking_ratio_text: str | None
    feature_value: str | None
    diagnostic_simple_return: str | None


def _feature_status(
    item: _NormalizedInput,
    *,
    calendar: TradingCalendar,
    signal_session: str,
    variant: FeatureVariant,
    recent_anchor: str,
    old_anchor: str,
) -> tuple[str, Fraction | None, Fraction | None]:
    """The single typed feature status for one row, in registered precedence."""
    if item.total_return_chain_state == TOTAL_RETURN_CHAIN_INVALID:
        return NOT_SCORABLE_INVALID_TOTAL_RETURN_CHAIN, None, None
    observed_sessions = (
        calendar.position(signal_session) - calendar.position(item.observed_span_start) + 1
    )
    if observed_sessions < variant.minimum_observed_sessions_including_t:
        return NOT_SCORABLE_INSUFFICIENT_HISTORY, None, None
    recent = item.observation_by_session.get(recent_anchor)
    if recent is None:
        return NOT_SCORABLE_MISSING_ANCHOR_RECENT, None, None
    old = item.observation_by_session.get(old_anchor)
    if old is None:
        return NOT_SCORABLE_MISSING_ANCHOR_OLD, recent, None
    if item.source_freshness_state == SOURCE_STALE_AT_CUTOFF:
        return NOT_SCORABLE_STALE_SOURCE, recent, old
    if recent <= 0:
        return NOT_SCORABLE_NONPOSITIVE_ANCHOR_RECENT, recent, old
    if old <= 0:
        return NOT_SCORABLE_NONPOSITIVE_ANCHOR_OLD, recent, old
    return FEATURE_SCORABLE, recent, old


def _render_anchor(value: Fraction | None) -> str | None:
    """Echo an anchor in its exact canonical form; anchors are never rounded."""
    if value is None:
        return None
    return render_exact(value, what="anchor total_return_close")


def _ordering_component(key: str, entry: _Scored) -> Any:
    """One component of the registered total order for a rank-eligible row."""
    if key == ORDERING_KEY_SIGNAL_DECIMAL_DESCENDING:
        # The exact rational ratio, negated for descending order. ln is strictly
        # increasing, so this is the log order decided without any rounding.
        ratio = entry.ranking_ratio
        if ratio is None:  # pragma: no cover - only rank-eligible rows are ordered
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT, "a rank-eligible row must carry an exact ratio"
            )
        return -ratio
    if key == ORDERING_KEY_SECURITY_ID_UTF8_BYTES_ASCENDING:
        return entry.normalized.stable_key.encode("utf-8")
    raise SignalError(  # pragma: no cover - TieBreakPolicy validates the vocabulary
        BLOCKED_UNREGISTERED_ORDERING_KEY, f"unregistered ordering key {key!r}"
    )


def evaluate_signal_cross_section(
    inputs: Sequence[SecuritySessionInput],
    *,
    calendar: TradingCalendar | None,
    signal_session: str,
    analysis_cutoff: str,
    variant_id: str,
    tie_policy_id: str,
    breadth_threshold_id: str,
    variants: Sequence[FeatureVariant] = REGISTERED_FEATURE_VARIANTS,
    tie_policies: Sequence[TieBreakPolicy] = REGISTERED_TIE_BREAK_POLICIES,
    breadth_minimums: Sequence[BreadthMinimum] = REGISTERED_BREADTH_MINIMUMS,
    repository_root: Path | None = None,
) -> SignalRunResult:
    """Score, rank, break ties, and select one cross-section. Fail-closed throughout.

    With the shipped registries this raises ``BLOCKED_NO_REGISTERED_FEATURE_VARIANT``
    before touching an input row. Input order never influences an output: rows are
    normalized, digested, ranked, and emitted under content-derived keys only.

    ``repository_root`` is the opt-in run-time byte check of the bound frozen
    authority: when supplied, every artifact in :data:`BOUND_CONTRACT_AUTHORITY`
    is re-hashed from disk before anything else is resolved and drift refuses
    the run. When omitted, the binding is asserted through the declared digests
    only, exactly as the companion document states in section 7.
    """
    if repository_root is not None:
        verify_bound_contract_authority(repository_root)
    variant = resolve_feature_variant(variant_id, variants=variants)
    tie_policy = resolve_tie_break_policy(tie_policy_id, policies=tie_policies)
    minimum = resolve_breadth_minimum(breadth_threshold_id, minimums=breadth_minimums)

    trading_calendar = require_calendar(calendar, what="signal ranking")
    session = iso_date(signal_session, what="signal_session")
    trading_calendar.session(session)
    cutoff = iso_date(analysis_cutoff, what="analysis_cutoff")
    if session > cutoff:
        raise SignalError(
            BLOCKED_SIGNAL_SESSION_AFTER_ANALYSIS_CUTOFF,
            f"signal session {session} is after the analysis cutoff {cutoff}",
            session=session,
        )

    recent_anchor = trading_calendar.offset(session, variant.recent_anchor_exchange_session_offset)
    old_anchor = trading_calendar.offset(session, variant.old_anchor_exchange_session_offset)

    normalized: list[_NormalizedInput] = []
    seen_keys: set[str] = set()
    for candidate in inputs:
        item = _normalize_input(
            candidate,
            calendar=trading_calendar,
            signal_session=session,
            analysis_cutoff=cutoff,
        )
        if item.stable_key in seen_keys:
            raise SignalError(
                BLOCKED_DUPLICATE_SECURITY_ID,
                f"security {item.security_id!r} appears more than once in the cross-section",
                security_id=item.security_id,
                session=session,
            )
        seen_keys.add(item.stable_key)
        normalized.append(item)

    scored: list[_Scored] = []
    for item in normalized:
        status, recent, old = _feature_status(
            item,
            calendar=trading_calendar,
            signal_session=session,
            variant=variant,
            recent_anchor=recent_anchor,
            old_anchor=old_anchor,
        )
        ratio: Fraction | None = None
        ratio_text: str | None = None
        value: str | None = None
        diagnostic: str | None = None
        if status == FEATURE_SCORABLE and recent is not None and old is not None:
            ratio = recent / old
            try:
                ratio_text = f"{ratio.numerator}/{ratio.denominator}"
            except ValueError as exc:
                # CPython bounds int-to-str conversion; a ratio component beyond
                # that bound cannot be rendered into the artifact and refuses
                # with the typed state instead of escaping as a bare ValueError.
                raise SignalError(
                    BLOCKED_MALFORMED_SIGNAL_INPUT,
                    f"{item.security_id}: an exact ranking-ratio component exceeds "
                    "the platform integer-to-string conversion limit and cannot be "
                    "rendered into the artifact",
                    security_id=item.security_id,
                ) from exc
            value = feature_value(ratio)
            diagnostic = diagnostic_simple_return(ratio)
        if item.universe_membership == UNIVERSE_NOT_IN_REQUIRED_UNIVERSE:
            eligibility = EXCLUDED_NOT_IN_REQUIRED_UNIVERSE
        elif status != FEATURE_SCORABLE:
            eligibility = EXCLUDED_NOT_SCORABLE
        else:
            eligibility = ELIGIBLE_RANK_ELIGIBLE
        scored.append(
            _Scored(
                normalized=item,
                feature_status=status,
                eligibility_state=eligibility,
                recent_anchor_total_return=_render_anchor(recent),
                old_anchor_total_return=_render_anchor(old),
                ranking_ratio=ratio,
                ranking_ratio_text=ratio_text,
                feature_value=value,
                diagnostic_simple_return=diagnostic,
            )
        )

    eligible = [entry for entry in scored if entry.eligibility_state == ELIGIBLE_RANK_ELIGIBLE]
    eligible.sort(
        key=lambda entry: tuple(
            _ordering_component(name, entry) for name in tie_policy.total_order
        )
    )

    rank_by_key: dict[str, int] = {}
    tie_group_by_key: dict[str, str] = {}
    tie_size_by_key: dict[str, int] = {}
    tie_ordinal_by_key: dict[str, int] = {}
    group_members: dict[str, list[str]] = {}
    for position, entry in enumerate(eligible, start=1):
        key = entry.normalized.stable_key
        rank_by_key[key] = position
        eligible_ratio = entry.ranking_ratio
        if eligible_ratio is None:  # pragma: no cover - eligibility implies a ratio
            raise SignalError(
                BLOCKED_MALFORMED_SIGNAL_INPUT, "a rank-eligible row must carry an exact ratio"
            )
        group = _tie_group_key(
            eligible_ratio, variant_id=variant.variant_id, signal_session=session
        )
        tie_group_by_key[key] = group
        group_members.setdefault(group, []).append(key)
    for members in group_members.values():
        for ordinal, key in enumerate(members, start=1):
            tie_size_by_key[key] = len(members)
            tie_ordinal_by_key[key] = ordinal

    breadth = len(eligible)
    selection_state, size = selection_size(breadth, minimum)
    spans_cutoff = {
        group
        for group, members in group_members.items()
        if size > 0
        and any(rank_by_key[key] <= size for key in members)
        and any(rank_by_key[key] > size for key in members)
    }

    input_digest = _input_digest(normalized, signal_session=session, analysis_cutoff=cutoff)
    config_digest = _config_digest(variant, tie_policy, minimum)
    code_digest = code_binding_digest()
    schema_hash = schema_digest()
    run_identity = canonical_dataset_digest(
        {
            "engine_id": ENGINE_ID,
            "schema_version": SCHEMA_VERSION,
            "signal_session": session,
            "analysis_cutoff": cutoff,
            "recent_anchor_session": recent_anchor,
            "old_anchor_session": old_anchor,
            "config_sha256_grouped": config_digest,
            "input_sha256_grouped": input_digest,
            "code_binding_sha256_grouped": code_digest,
            "schema_sha256_grouped": schema_hash,
        }
    )

    rows: list[SignalRow] = []
    for entry in sorted(scored, key=lambda item: item.normalized.stable_key.encode("utf-8")):
        key = entry.normalized.stable_key
        rank = rank_by_key.get(key)
        tie_group = tie_group_by_key.get(key)
        if rank is None:
            reason = NOT_SELECTED_NOT_RANK_ELIGIBLE
            selected = False
        elif selection_state != SELECTION_VALID:
            reason = NOT_SELECTED_SELECTION_STATE_INVALID
            selected = False
        elif rank <= size:
            selected = True
            reason = (
                INCLUDED_BOUNDARY_TIE_BREAK if tie_group in spans_cutoff else INCLUDED_BY_RANK
            )
        else:
            selected = False
            reason = (
                EXCLUDED_BOUNDARY_TIE_BREAK
                if tie_group in spans_cutoff
                else EXCLUDED_BELOW_SELECTION_CUTOFF
            )
        rows.append(
            SignalRow(
                run_id=run_identity,
                signal_session=session,
                security_id=entry.normalized.security_id,
                stable_key=key,
                variant_id=variant.variant_id,
                variant_role=variant.variant_role,
                lookback_sessions=variant.lookback_sessions,
                skip_sessions=variant.skip_sessions,
                universe_membership=entry.normalized.universe_membership,
                total_return_chain_state=entry.normalized.total_return_chain_state,
                source_freshness_state=entry.normalized.source_freshness_state,
                recent_anchor_session=recent_anchor,
                old_anchor_session=old_anchor,
                recent_anchor_total_return=entry.recent_anchor_total_return,
                old_anchor_total_return=entry.old_anchor_total_return,
                ranking_ratio=entry.ranking_ratio_text,
                feature_value=entry.feature_value,
                feature_value_type=FEATURE_VALUE_TYPE_LOG,
                feature_exactness=(
                    FEATURE_EXACTNESS_ROUNDED_ARTIFACT
                    if entry.feature_value is not None
                    else FEATURE_EXACTNESS_NOT_COMPUTED
                ),
                diagnostic_simple_return=entry.diagnostic_simple_return,
                diagnostic_value_type=DIAGNOSTIC_VALUE_TYPE_SIMPLE,
                feature_status=entry.feature_status,
                eligibility_state=entry.eligibility_state,
                rank=rank,
                tie_group_key=tie_group,
                tie_group_size=tie_size_by_key.get(key),
                tie_break_ordinal=tie_ordinal_by_key.get(key),
                selected=selected,
                selection_reason=reason,
                input_sha256_grouped=input_digest,
                config_sha256_grouped=config_digest,
                code_binding_sha256_grouped=code_digest,
                schema_sha256_grouped=schema_hash,
            )
        )

    return SignalRunResult(
        run_id=run_identity,
        signal_session=session,
        analysis_cutoff=cutoff,
        variant=variant,
        tie_policy=tie_policy,
        breadth_minimum=minimum,
        recent_anchor_session=recent_anchor,
        old_anchor_session=old_anchor,
        selection_state=selection_state,
        rank_eligible_breadth=breadth,
        selection_size=size,
        rows=tuple(rows),
        input_sha256_grouped=input_digest,
        config_sha256_grouped=config_digest,
        code_binding_sha256_grouped=code_digest,
        schema_sha256_grouped=schema_hash,
    )


__all__ = [
    "ACCEPTABLE_BREADTH_EVIDENCE_SOURCE_TYPES",
    "BLOCKED_AMBIGUOUS_BREADTH_MINIMUM",
    "BLOCKED_AMBIGUOUS_FEATURE_VARIANT",
    "BLOCKED_AMBIGUOUS_TIE_BREAK_POLICY",
    "BLOCKED_CONTRACT_ARTIFACT_MISSING",
    "BLOCKED_CONTRACT_AUTHORITY_BYTES_MISMATCH",
    "BLOCKED_DUPLICATE_OBSERVATION_SESSION",
    "BLOCKED_DUPLICATE_SECURITY_ID",
    "BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE",
    "BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY",
    "BLOCKED_INVALID_VARIANT_SESSION_OFFSETS",
    "BLOCKED_MALFORMED_SIGNAL_INPUT",
    "BLOCKED_NO_REGISTERED_BREADTH_MINIMUM",
    "BLOCKED_NO_REGISTERED_FEATURE_VARIANT",
    "BLOCKED_NO_REGISTERED_TIE_BREAK_POLICY",
    "BLOCKED_SIGNAL_SESSION_AFTER_ANALYSIS_CUTOFF",
    "BLOCKED_UNREGISTERED_BREADTH_EVIDENCE_SOURCE",
    "BLOCKED_UNREGISTERED_BREADTH_MINIMUM",
    "BLOCKED_UNREGISTERED_FEATURE_VARIANT",
    "BLOCKED_UNREGISTERED_INPUT_VOCABULARY",
    "BLOCKED_UNREGISTERED_ORDERING_KEY",
    "BLOCKED_UNREGISTERED_SOURCE_KIND",
    "BLOCKED_UNREGISTERED_STABLE_KEY",
    "BLOCKED_UNREGISTERED_TIE_BREAK_POLICY",
    "BLOCKED_UNREGISTERED_VARIANT_ROLE",
    "BOUNDARY_TIE_POLICIES",
    "BOUNDARY_TIE_POLICY_SPLIT_BY_STABLE_KEY",
    "BOUND_CONTRACT_AUTHORITY",
    "BREADTH_EVIDENCE_OWNER_MANDATE",
    "BREADTH_EVIDENCE_PRE_REGISTERED_UNIVERSE",
    "BREADTH_UNITS",
    "BREADTH_UNIT_SECURITY_COUNT",
    "CALCULATION_ORDER",
    "CONTRACT_V2_NO_CONTRACT_EQUIVALENT",
    "CONTRACT_V2_REASON_CODE_ALIASES",
    "DECIMAL_CONTEXT_PRECISION",
    "DECIMAL_ROUNDING",
    "DIAGNOSTIC_VALUE_TYPE_SIMPLE",
    "ELIGIBILITY_STATES",
    "ELIGIBLE_RANK_ELIGIBLE",
    "ENGINE_ID",
    "EXCLUDED_BELOW_SELECTION_CUTOFF",
    "EXCLUDED_BOUNDARY_TIE_BREAK",
    "EXCLUDED_NOT_IN_REQUIRED_UNIVERSE",
    "EXCLUDED_NOT_SCORABLE",
    "FAIL_CLOSED_STATES",
    "FEATURE_EQUATION",
    "FEATURE_EXACTNESS_KINDS",
    "FEATURE_EXACTNESS_NOT_COMPUTED",
    "FEATURE_EXACTNESS_ROUNDED_ARTIFACT",
    "FEATURE_NAME",
    "FEATURE_SCORABLE",
    "FEATURE_STATUSES",
    "FEATURE_STATUS_PRECEDENCE",
    "FEATURE_VALUE_TYPE_LOG",
    "FINAL_STABLE_ORDERING_KEY",
    "INCLUDED_BOUNDARY_TIE_BREAK",
    "INCLUDED_BY_RANK",
    "INVALID_INSUFFICIENT_BREADTH",
    "INVALID_ZERO_SELECTION_SIZE",
    "MANIFEST_FIELD_NAMES",
    "MAX_ABSOLUTE_LOG_MOMENTUM",
    "NATURAL_LOG_ERROR_BOUND",
    "NEAREST_SESSION_SUBSTITUTION_ALLOWED",
    "NON_CLAIMS",
    "NOT_SCORABLE_INSUFFICIENT_HISTORY",
    "NOT_SCORABLE_INVALID_TOTAL_RETURN_CHAIN",
    "NOT_SCORABLE_MISSING_ANCHOR_OLD",
    "NOT_SCORABLE_MISSING_ANCHOR_RECENT",
    "NOT_SCORABLE_NONPOSITIVE_ANCHOR_OLD",
    "NOT_SCORABLE_NONPOSITIVE_ANCHOR_RECENT",
    "NOT_SCORABLE_STALE_SOURCE",
    "NOT_SELECTED_NOT_RANK_ELIGIBLE",
    "NOT_SELECTED_SELECTION_STATE_INVALID",
    "ORDERING_KEY_SECURITY_ID_UTF8_BYTES_ASCENDING",
    "ORDERING_KEY_SIGNAL_DECIMAL_DESCENDING",
    "PRICE_COORDINATE",
    "RANKING_COMPARISON",
    "RANK_METHODS",
    "RANK_METHOD_UNIQUE_ORDINAL",
    "RANK_ORDER_DEPENDS_ON_ROUNDED_LOG",
    "REGISTERED_BREADTH_MINIMUMS",
    "REGISTERED_FEATURE_VARIANTS",
    "REGISTERED_ORDERING_KEYS",
    "REGISTERED_SOURCE_KINDS",
    "REGISTERED_TIE_BREAK_POLICIES",
    "ROW_FIELD_NAMES",
    "SCHEMA_VERSION",
    "SELECTION_FORMULA",
    "SELECTION_FRACTION_DENOMINATOR",
    "SELECTION_FRACTION_NUMERATOR",
    "SELECTION_INTEGER_IMPLEMENTATION",
    "SELECTION_MAXIMUM_NAMES",
    "SELECTION_REASONS",
    "SELECTION_STATES",
    "SELECTION_VALID",
    "SIGNAL_ARTIFACT_SCALE",
    "SIGNAL_ROUNDING_MODE",
    "SOURCE_FRESHNESS_STATES",
    "SOURCE_FRESH_AT_CUTOFF",
    "SOURCE_KINDS",
    "SOURCE_KIND_OWNER_MANDATE",
    "SOURCE_KIND_PRE_REGISTERED_UNIVERSE_EVIDENCE",
    "SOURCE_KIND_REGISTERED_CONTRACT_DECISION",
    "SOURCE_KIND_TEST_CONSTRUCTED",
    "SOURCE_STALE_AT_CUTOFF",
    "STABLE_KEYS",
    "STABLE_KEY_NORMALIZATIONS",
    "STABLE_KEY_NORMALIZATION_NFC",
    "STABLE_KEY_ORDERS",
    "STABLE_KEY_ORDER_UTF8_BYTES_ASCENDING",
    "STABLE_KEY_SECURITY_ID",
    "SURFACED_CALENDAR_STATES",
    "TOTAL_RETURN_CHAIN_INVALID",
    "TOTAL_RETURN_CHAIN_OK",
    "TOTAL_RETURN_CHAIN_STATES",
    "UNIVERSE_IN_REQUIRED_UNIVERSE",
    "UNIVERSE_MEMBERSHIP_STATES",
    "UNIVERSE_NOT_IN_REQUIRED_UNIVERSE",
    "VARIANT_ROLES",
    "VARIANT_ROLE_GRID_DIAGNOSTIC",
    "VARIANT_ROLE_PRIMARY",
    "BreadthMinimum",
    "FeatureVariant",
    "SecuritySessionInput",
    "SignalError",
    "SignalOutputSet",
    "SignalRow",
    "SignalRunResult",
    "TieBreakPolicy",
    "TotalReturnObservation",
    "code_binding_digest",
    "contract_v2_reason_code",
    "decimal_context",
    "diagnostic_simple_return",
    "evaluate_signal_cross_section",
    "feature_value",
    "natural_log_of_ratio",
    "resolve_breadth_minimum",
    "resolve_feature_variant",
    "resolve_tie_break_policy",
    "schema_digest",
    "selection_size",
    "stable_key",
    "validate_breadth_minimum_registry",
    "validate_feature_variant_registry",
    "validate_tie_break_policy_registry",
    "verify_bound_contract_authority",
]
