"""Eight-class coverage audit V1 (NEE-128 prebuild, M1 data spine).

Coverage is the honest denominator of a backtest. This module computes it as an
**exact rational** over eight separately-denominated classes, records every
missing or excluded item in a typed ledger, folds in the delisting outcome table
from :mod:`qme.data.coverage.delisting_v1`, and produces a gate status that
cannot be ``VALID`` while a required held position is unaudited.

The formula, and why it is a Fraction
-------------------------------------

``coverage_(c,t) = valid_required_items_(c,t) / required_items_(c,t)``

is evaluated as :class:`fractions.Fraction`. No binary float appears anywhere:
``8/9`` is stored as the exact rational ``8/9``, compared to a threshold as an
exact rational, and only *rendered* -- once, at the artifact boundary, at the
bound scale with ``ROUND_HALF_EVEN`` -- alongside its exact ``numerator/
denominator`` form. A coverage comparison therefore never turns on a
representation error.

Eight denominators, and no ninth number
---------------------------------------

The eight classes are :data:`COVERAGE_CLASSES`, in ticket order. Each keeps its
own denominator; :data:`COVERAGE_CLASS_DENOMINATORS` states, per class, exactly
what one required item is.

A **pooled headline percentage is structurally impossible**, not merely absent:

* a coverage value exists only as :attr:`CoverageClassResult.coverage`, a field
  on a record that also carries its ``coverage_class``, so no rational in this
  module is unlabelled;
* the only callables that return a bare :class:`~fractions.Fraction` --
  :func:`class_coverage` and :meth:`CoverageTable.class_coverage` -- both take a
  ``coverage_class`` argument, so a caller cannot ask this module for "the"
  coverage;
* :class:`CoverageTable` exposes ``by_class`` and nothing that sums, averages,
  or otherwise collapses the eight; there is no ``overall``, ``pooled``,
  ``headline``, ``aggregate`` or ``total`` anywhere in the API or in any emitted
  JSON key;
* :func:`class_coverage` refuses an empty denominator with
  ``BLOCKED_EMPTY_COVERAGE_DENOMINATOR`` rather than reporting ``0/0`` as ``1``,
  so "nothing was required" never reads as "everything is covered".

The one fixed threshold, and the empty registry behind every other
------------------------------------------------------------------

:data:`HELD_POSITION_COVERAGE_REQUIREMENT` is ``Fraction(1)``. The ticket fixes
it, so it is hard-wired here and cannot be registered away:
:func:`validate_threshold_registry` refuses any record naming the held-position
class with ``BLOCKED_HELD_POSITION_THRESHOLD_IS_FIXED``.

Every other minimum coverage or breadth threshold comes from
:data:`REGISTERED_COVERAGE_THRESHOLDS`, which is ``()``. With the shipped
registry :func:`evaluate_gate` therefore reports
``BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD`` and **no run can be gated VALID** --
the machinery is complete and tested and refuses to produce a verdict until an
owner record exists, exactly as
:data:`qme.data.stores.riskfree_v1.REGISTERED_SOURCES` and
:data:`qme.data.alpha_vantage.plan_v1.REGISTERED_PLANS` do for their values.

Run invalidity is checked *before* the registry
-----------------------------------------------

:func:`evaluate_gate` resolves in a fixed order: unaudited held positions first,
incomplete held-position coverage second, unregistered thresholds third, per-class
comparisons last. That ordering matters, because it makes "an unaudited required
held position invalidates the run" provable *today* -- it does not depend on a
threshold nobody has registered.

The unresolved-exit cross-check
-------------------------------

A caller cannot declare a held position valid while its exit is unresolved. For
every delisting row whose outcome state is not in
:data:`~qme.data.coverage.delisting_v1.RESOLVED_OUTCOME_STATES`, the held-position
items for that security are **forced** to ``ITEM_UNAUDITED_HELD_POSITION``,
whatever the caller declared, and the override is recorded in the ledger with its
source. With the empty timing and haircut registries this means any held position
carrying a terminal exit invalidates the run -- which is the intended fail-closed
reading, not an accident.

Non-claims
----------

* Synthetic only. This module acquires nothing, registers nothing, and clears no
  freeze blocker.
* It imports no transport, no vendor client, and no raw-pull store; identifiers
  are opaque and validated for shape only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any, Final

from qme.data.classification.rules_v1 import (
    STATUS_AMBIGUOUS,
    STATUS_CONFIRMED,
    STATUS_UNKNOWN,
    TERMINAL_STATUSES,
)
from qme.data.corporate_actions.factors_v1 import (
    EXCLUDED_UNSUPPORTED_UNHELD_ACTION,
    RUN_INVALID_UNSUPPORTED_HELD_ACTION,
)
from qme.data.coverage.delisting_v1 import (
    BLOCKED_MARK_AFTER_REQUIRED_SESSION,
    BLOCKED_MISSING_MARK_NO_POLICY,
    BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY,
    NON_CLAIMS,
    REGISTERED_BENCHMARK_TREATMENT_DECISIONS,
    REGISTERED_DELISTING_TIMING_RULES,
    REGISTERED_FALLBACK_HAIRCUTS,
    REGISTERED_MISSING_MARK_POLICIES,
    REGISTERED_SENSITIVITY_RANGES,
    REGISTERED_SOURCE_KINDS,
    SOURCE_KINDS,
    BenchmarkTreatmentDecision,
    CoverageError,
    DelistingEvent,
    DelistingPolicyError,
    DelistingTable,
    DelistingTimingRule,
    ExitPricingInput,
    FallbackHaircut,
    FallbackScenarioResult,
    HeldPositionMark,
    Lineage,
    MissingMarkPolicy,
    OutcomeAttributionRow,
    SensitivityRange,
    attribute_pnl_by_outcome_type,
    build_delisting_table,
    code_binding_digest,
    dataset_digest,
    delisting_config_document,
    exact,
    exact_pair,
    iso_day,
    iso_instant,
    opaque_security_id,
    render_ratio,
    require_members,
    resolve_held_mark,
    token,
)
from qme.data.identity import (
    COVERAGE_LIMITATION,
    IDENTITY_RULES_VERSION,
    Ambiguous,
    Resolution,
    ResolvedSecurity,
    Unknown,
)
from qme.data.stores.calendar_v1 import (
    MarketStoreError,
    TradingCalendar,
    canonical_dataset_digest,
    require_calendar,
)
from qme.foundation.lineage import canonical_json_bytes

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

KERNEL_ID: Final = "QME-NEE128-COVERAGE-AUDIT-V1"
SCHEMA_VERSION: Final = "qme.coverage_audit.v1"

# ---------------------------------------------------------------------------
# The eight coverage classes, in ticket order
# ---------------------------------------------------------------------------

COVERAGE_CLASS_LISTINGS: Final = "LISTINGS"
COVERAGE_CLASS_IDENTITY: Final = "IDENTITY"
COVERAGE_CLASS_CLASSIFICATION: Final = "CLASSIFICATION"
COVERAGE_CLASS_PRICES: Final = "PRICES"
COVERAGE_CLASS_ACTIONS: Final = "ACTIONS"
COVERAGE_CLASS_ANCHORS: Final = "ANCHORS"
COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS: Final = "HELD_POSITION_MARKS_EXITS"
COVERAGE_CLASS_BENCHMARKS: Final = "BENCHMARKS"

#: The eight classes, in the ticket's order. Each keeps its own denominator and
#: they are never pooled.
COVERAGE_CLASSES: Final = (
    COVERAGE_CLASS_LISTINGS,
    COVERAGE_CLASS_IDENTITY,
    COVERAGE_CLASS_CLASSIFICATION,
    COVERAGE_CLASS_PRICES,
    COVERAGE_CLASS_ACTIONS,
    COVERAGE_CLASS_ANCHORS,
    COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,
    COVERAGE_CLASS_BENCHMARKS,
)

SUBJECT_KIND_SECURITY: Final = "SECURITY_ID"
SUBJECT_KIND_IDENTITY_KEY: Final = "IDENTITY_KEY"
SUBJECT_KIND_ANCHOR: Final = "ANCHOR_ID"
SUBJECT_KIND_BENCHMARK: Final = "BENCHMARK_ID"
SUBJECT_KINDS: Final = (
    SUBJECT_KIND_SECURITY,
    SUBJECT_KIND_IDENTITY_KEY,
    SUBJECT_KIND_ANCHOR,
    SUBJECT_KIND_BENCHMARK,
)

#: What each class's required item is *about*. A benchmark item therefore cannot
#: carry a security id, and an identity item cannot carry one either -- resolving
#: the key to a security is the thing being measured.
COVERAGE_CLASS_SUBJECT_KINDS: Final[Mapping[str, str]] = {
    COVERAGE_CLASS_LISTINGS: SUBJECT_KIND_SECURITY,
    COVERAGE_CLASS_IDENTITY: SUBJECT_KIND_IDENTITY_KEY,
    COVERAGE_CLASS_CLASSIFICATION: SUBJECT_KIND_SECURITY,
    COVERAGE_CLASS_PRICES: SUBJECT_KIND_SECURITY,
    COVERAGE_CLASS_ACTIONS: SUBJECT_KIND_SECURITY,
    COVERAGE_CLASS_ANCHORS: SUBJECT_KIND_ANCHOR,
    COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS: SUBJECT_KIND_SECURITY,
    COVERAGE_CLASS_BENCHMARKS: SUBJECT_KIND_BENCHMARK,
}

#: Classes whose subject is an opaque security id from the identity layer.
SECURITY_SUBJECT_COVERAGE_CLASSES: Final = tuple(
    name
    for name in COVERAGE_CLASSES
    if COVERAGE_CLASS_SUBJECT_KINDS[name] == SUBJECT_KIND_SECURITY
)

#: Classes whose ``session`` must be an accepted trading session. Checking that
#: requires the accepted calendar, so :func:`build_coverage_audit` demands one --
#: it never assumes a date is tradable.
SESSION_ALIGNED_COVERAGE_CLASSES: Final = (
    COVERAGE_CLASS_PRICES,
    COVERAGE_CLASS_ANCHORS,
    COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,
    COVERAGE_CLASS_BENCHMARKS,
)

#: What exactly *one* required item is, per class. Written into every emitted
#: coverage table so the denominator is never left to interpretation.
COVERAGE_CLASS_DENOMINATORS: Final[Mapping[str, str]] = {
    COVERAGE_CLASS_LISTINGS: (
        "one required item per (security_id, session) whose listing state the run "
        "must know in order to decide whether the security was tradable"
    ),
    COVERAGE_CLASS_IDENTITY: (
        "one required item per (identity_key, as_of session) the run must resolve to "
        "exactly one security; the subject is the listing key, never a security_id, "
        "because resolving it is what is being measured"
    ),
    COVERAGE_CLASS_CLASSIFICATION: (
        "one required item per (security_id, effective_from) interval that needs a "
        "terminal asset-class row; the session component is the interval's start date"
    ),
    COVERAGE_CLASS_PRICES: (
        "one required item per (security_id, session) price observation the run reads"
    ),
    COVERAGE_CLASS_ACTIONS: (
        "one required item per (security_id, effective session) corporate-action "
        "record the run must have before it can adjust that security"
    ),
    COVERAGE_CLASS_ANCHORS: (
        "one required item per (anchor_id, session) formation / rebalance anchor the "
        "run schedules"
    ),
    COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS: (
        "one required item per (security_id, session) at which a held position needs "
        "a valuation mark or a settled exit; this is the class the ticket fixes at "
        "coverage exactly 1"
    ),
    COVERAGE_CLASS_BENCHMARKS: (
        "one required item per (benchmark_id, session) benchmark level or constituent "
        "record the run compares against"
    ),
}

#: The key fields that make each class's denominator its own. The class name is
#: part of every item key, so two classes over the same subject and session still
#: count into two different denominators.
COVERAGE_CLASS_KEY_FIELDS: Final[Mapping[str, tuple[str, str, str]]] = {
    name: ("coverage_class", COVERAGE_CLASS_SUBJECT_KINDS[name].lower(), "session")
    for name in COVERAGE_CLASSES
}

#: The one threshold the ticket fixes: a valid run must have complete
#: held-position valuation / exit coverage. Hard-wired, not registrable.
HELD_POSITION_COVERAGE_REQUIREMENT: Final = Fraction(1)

# ---------------------------------------------------------------------------
# Required-item states
# ---------------------------------------------------------------------------

ITEM_VALID: Final = "ITEM_VALID"
ITEM_MISSING_NOT_SOURCED: Final = "ITEM_MISSING_NOT_SOURCED"
ITEM_MISSING_AFTER_AVAILABILITY_CUTOFF: Final = "ITEM_MISSING_AFTER_AVAILABILITY_CUTOFF"
ITEM_INVALID_FAILED_VALIDATION: Final = "ITEM_INVALID_FAILED_VALIDATION"
ITEM_EXCLUDED_TERMINAL_STATUS: Final = "ITEM_EXCLUDED_TERMINAL_STATUS"
ITEM_EXCLUDED_UNSUPPORTED_ACTION: Final = "ITEM_EXCLUDED_UNSUPPORTED_ACTION"
ITEM_STALE_BEYOND_DECLARED_HORIZON: Final = "ITEM_STALE_BEYOND_DECLARED_HORIZON"
ITEM_UNAUDITED_HELD_POSITION: Final = "ITEM_UNAUDITED_HELD_POSITION"

#: Every state a required item can be in. Exactly one is valid.
ITEM_STATES: Final = (
    ITEM_VALID,
    ITEM_MISSING_NOT_SOURCED,
    ITEM_MISSING_AFTER_AVAILABILITY_CUTOFF,
    ITEM_INVALID_FAILED_VALIDATION,
    ITEM_EXCLUDED_TERMINAL_STATUS,
    ITEM_EXCLUDED_UNSUPPORTED_ACTION,
    ITEM_STALE_BEYOND_DECLARED_HORIZON,
    ITEM_UNAUDITED_HELD_POSITION,
)
#: Every state that is not valid. Each one becomes a ledger record.
NON_VALID_ITEM_STATES: Final = tuple(name for name in ITEM_STATES if name != ITEM_VALID)

#: States that invalidate the affected run outright rather than only reducing a
#: coverage figure.
RUN_INVALIDATING_ITEM_STATES: Final = (ITEM_UNAUDITED_HELD_POSITION,)

#: One deterministic reason per state: a ledger reason is a pure function of the
#: state, so the same input always produces the same text.
ITEM_STATE_REASONS: Final[Mapping[str, str]] = {
    ITEM_VALID: "the required item is present and passed validation",
    ITEM_MISSING_NOT_SOURCED: "no source supplies this required item",
    ITEM_MISSING_AFTER_AVAILABILITY_CUTOFF: (
        "the only candidate became knowable after the run's availability cutoff, so it "
        "is invisible to this run rather than late"
    ),
    ITEM_INVALID_FAILED_VALIDATION: "the item exists but failed its own validation",
    ITEM_EXCLUDED_TERMINAL_STATUS: (
        "the item resolved to a non-confirming terminal status and is excluded rather "
        "than coerced"
    ),
    ITEM_EXCLUDED_UNSUPPORTED_ACTION: (
        "the item carries a corporate action this spine does not model; on an unheld "
        "security that is an exclusion"
    ),
    ITEM_STALE_BEYOND_DECLARED_HORIZON: (
        "the only available mark belongs to an earlier session and no registered policy "
        "authorises carrying it forward"
    ),
    ITEM_UNAUDITED_HELD_POSITION: (
        "a required held position has no audited valuation or settled exit; the affected "
        "run is invalid"
    ),
}

#: Which classes may declare which states. A caller cannot label a price item
#: ``ITEM_UNAUDITED_HELD_POSITION`` to move a run-invalidating condition into a
#: class that does not invalidate runs.
ITEM_STATE_CLASS_RESTRICTIONS: Final[Mapping[str, tuple[str, ...]]] = {
    ITEM_UNAUDITED_HELD_POSITION: (COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,),
    ITEM_STALE_BEYOND_DECLARED_HORIZON: (COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,),
    ITEM_EXCLUDED_UNSUPPORTED_ACTION: (COVERAGE_CLASS_ACTIONS,),
}

OVERRIDE_HELD_MARK_RESOLUTION: Final = "HELD_MARK_RESOLUTION"
OVERRIDE_UNRESOLVED_EXIT_CROSS_CHECK: Final = "UNRESOLVED_EXIT_CROSS_CHECK"
#: The two places this module overrides a declared item state. Both are recorded.
ITEM_STATE_OVERRIDE_SOURCES: Final = (
    OVERRIDE_HELD_MARK_RESOLUTION,
    OVERRIDE_UNRESOLVED_EXIT_CROSS_CHECK,
)

#: How a held-mark refusal maps to a required-item state. Every mapped refusal is
#: a *non-valid* state; none of them yields a number.
MARK_REFUSAL_ITEM_STATES: Final[Mapping[str, str]] = {
    BLOCKED_MISSING_MARK_NO_POLICY: ITEM_MISSING_NOT_SOURCED,
    BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY: ITEM_STALE_BEYOND_DECLARED_HORIZON,
    BLOCKED_MARK_AFTER_REQUIRED_SESSION: ITEM_MISSING_AFTER_AVAILABILITY_CUTOFF,
}

#: The classification terminal status a CLASSIFICATION item must reach to count
#: as valid. Bound to NEE-124's vocabulary rather than restated.
CLASSIFICATION_VALID_STATUS: Final = STATUS_CONFIRMED
#: The non-confirming statuses, kept explicit so the binding is checkable.
CLASSIFICATION_EXCLUDED_STATUSES: Final = (STATUS_AMBIGUOUS, STATUS_UNKNOWN)

# ---------------------------------------------------------------------------
# Threshold kinds
# ---------------------------------------------------------------------------

THRESHOLD_KIND_MINIMUM_COVERAGE: Final = "MINIMUM_COVERAGE"
THRESHOLD_KIND_MINIMUM_BREADTH: Final = "MINIMUM_BREADTH"
THRESHOLD_KINDS: Final = (THRESHOLD_KIND_MINIMUM_COVERAGE, THRESHOLD_KIND_MINIMUM_BREADTH)

# ---------------------------------------------------------------------------
# Typed states
# ---------------------------------------------------------------------------

GATE_VALID: Final = "GATE_VALID"
RUN_INVALID_UNAUDITED_HELD_POSITION: Final = "RUN_INVALID_UNAUDITED_HELD_POSITION"
RUN_INVALID_INCOMPLETE_HELD_POSITION_COVERAGE: Final = (
    "RUN_INVALID_INCOMPLETE_HELD_POSITION_COVERAGE"
)
RUN_INVALID_COVERAGE_BELOW_THRESHOLD: Final = "RUN_INVALID_COVERAGE_BELOW_THRESHOLD"

BLOCKED_DUPLICATE_HELD_MARK: Final = "BLOCKED_DUPLICATE_HELD_MARK"
BLOCKED_DUPLICATE_REQUIRED_ITEM: Final = "BLOCKED_DUPLICATE_REQUIRED_ITEM"
BLOCKED_EMPTY_COVERAGE_DENOMINATOR: Final = "BLOCKED_EMPTY_COVERAGE_DENOMINATOR"
BLOCKED_GATE_NOT_VALID: Final = "BLOCKED_GATE_NOT_VALID"
BLOCKED_HELD_POSITION_THRESHOLD_IS_FIXED: Final = "BLOCKED_HELD_POSITION_THRESHOLD_IS_FIXED"
BLOCKED_INCONSISTENT_COVERAGE_COUNTS: Final = "BLOCKED_INCONSISTENT_COVERAGE_COUNTS"
BLOCKED_ITEM_SESSION_NOT_A_SESSION: Final = "BLOCKED_ITEM_SESSION_NOT_A_SESSION"
BLOCKED_ITEM_STATE_NOT_VALID_FOR_CLASS: Final = "BLOCKED_ITEM_STATE_NOT_VALID_FOR_CLASS"
BLOCKED_MARK_FOR_UNDECLARED_HELD_ITEM: Final = "BLOCKED_MARK_FOR_UNDECLARED_HELD_ITEM"
BLOCKED_UNREGISTERED_COVERAGE_CLASS: Final = "BLOCKED_UNREGISTERED_COVERAGE_CLASS"
BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD: Final = "BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD"
BLOCKED_UNREGISTERED_ITEM_STATE: Final = "BLOCKED_UNREGISTERED_ITEM_STATE"
BLOCKED_UNRESOLVED_IDENTITY: Final = "BLOCKED_UNRESOLVED_IDENTITY"

#: Every fail-closed state this module raises, sorted. Callers may bind it.
COVERAGE_FAIL_CLOSED_STATES: Final = (
    BLOCKED_DUPLICATE_HELD_MARK,
    BLOCKED_DUPLICATE_REQUIRED_ITEM,
    BLOCKED_EMPTY_COVERAGE_DENOMINATOR,
    BLOCKED_GATE_NOT_VALID,
    BLOCKED_HELD_POSITION_THRESHOLD_IS_FIXED,
    BLOCKED_INCONSISTENT_COVERAGE_COUNTS,
    BLOCKED_ITEM_SESSION_NOT_A_SESSION,
    BLOCKED_ITEM_STATE_NOT_VALID_FOR_CLASS,
    BLOCKED_MARK_FOR_UNDECLARED_HELD_ITEM,
    BLOCKED_UNREGISTERED_COVERAGE_CLASS,
    BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
    BLOCKED_UNREGISTERED_ITEM_STATE,
    BLOCKED_UNRESOLVED_IDENTITY,
)

#: Every status :func:`evaluate_gate` can report. ``GATE_VALID`` is the only
#: verdict; everything else is a refusal or a run invalidation.
GATE_STATUSES: Final = (
    GATE_VALID,
    RUN_INVALID_UNAUDITED_HELD_POSITION,
    RUN_INVALID_INCOMPLETE_HELD_POSITION_COVERAGE,
    RUN_INVALID_COVERAGE_BELOW_THRESHOLD,
    BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
)


class CoverageAuditError(CoverageError):
    """A coverage-audit refusal. Distinguishable, still a CoverageError."""


# ---------------------------------------------------------------------------
# Registry: minimum coverage / breadth thresholds (EMPTY)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageThreshold:
    """One registered minimum coverage or breadth threshold.

    Exactly one of ``minimum_fraction`` (an exact base-10 decimal in ``[0, 1]``)
    and ``minimum_count`` (a positive item count) is populated, so a threshold is
    never ambiguous about what it bounds. A ``MINIMUM_COVERAGE`` threshold must
    use the fraction form; a ``MINIMUM_BREADTH`` threshold may use either.
    """

    threshold_id: str
    threshold_kind: str
    coverage_class: str
    minimum_fraction: str | None
    minimum_count: int | None
    source_kind: str
    source: str
    source_reference: str
    effective_date: str
    expires_after: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        token(self.threshold_id, what="threshold_id")
        if self.threshold_kind not in THRESHOLD_KINDS:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
                f"{self.threshold_id}: threshold_kind must be one of {list(THRESHOLD_KINDS)}",
            )
        if self.coverage_class not in COVERAGE_CLASSES:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_CLASS,
                f"{self.threshold_id}: {self.coverage_class!r} is not one of the eight classes",
            )
        if (self.minimum_fraction is None) == (self.minimum_count is None):
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
                f"{self.threshold_id}: state exactly one of minimum_fraction / minimum_count",
            )
        if self.minimum_fraction is not None:
            value = exact(self.minimum_fraction, what=f"{self.threshold_id}: minimum_fraction")
            if not (Fraction(0) <= value <= Fraction(1)):
                raise CoverageAuditError(
                    BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
                    f"{self.threshold_id}: minimum_fraction must lie in [0, 1]",
                )
        if self.minimum_count is not None:
            if self.threshold_kind != THRESHOLD_KIND_MINIMUM_BREADTH:
                raise CoverageAuditError(
                    BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
                    f"{self.threshold_id}: only a breadth threshold may bound a raw count",
                )
            if type(self.minimum_count) is not int or self.minimum_count < 1:
                raise CoverageAuditError(
                    BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
                    f"{self.threshold_id}: minimum_count must be a positive item count",
                )
        if self.source_kind not in SOURCE_KINDS:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
                f"{self.threshold_id}: unregistered source_kind {self.source_kind!r}",
            )
        for field_name, field_value in (
            ("source", self.source),
            ("source_reference", self.source_reference),
        ):
            if not isinstance(field_value, str) or not field_value.strip():
                raise CoverageAuditError(
                    BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
                    f"{self.threshold_id}: {field_name} must state where the number came from",
                )
        iso_day(self.effective_date, what=f"{self.threshold_id}: effective_date")
        if self.expires_after is not None:
            iso_day(self.expires_after, what=f"{self.threshold_id}: expires_after")
            if self.expires_after < self.effective_date:
                raise CoverageAuditError(
                    BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
                    f"{self.threshold_id}: expires_after precedes effective_date",
                )
        if self.schema_version != SCHEMA_VERSION:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
                f"{self.threshold_id}: unsupported schema_version",
            )

    def is_effective_on(self, day: str) -> bool:
        target = iso_day(day, what="day")
        if target < self.effective_date:
            return False
        return self.expires_after is None or target <= self.expires_after

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "threshold_id": self.threshold_id,
            "threshold_kind": self.threshold_kind,
            "coverage_class": self.coverage_class,
            "minimum_fraction": self.minimum_fraction,
            "minimum_count": self.minimum_count,
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
            "effective_date": self.effective_date,
            "expires_after": self.expires_after,
            "schema_version": self.schema_version,
        }


#: Every minimum coverage / breadth threshold this repository has evidence for.
#:
#: EMPTY BY DESIGN. Choosing a minimum coverage or breadth is an owner decision
#: that has not been made, so :func:`resolve_coverage_threshold` fails closed with
#: ``BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD`` and no run can be gated ``VALID``.
#: The held-position class is deliberately absent and may never appear here: the
#: ticket fixes its requirement at 1 and
#: :func:`validate_threshold_registry` refuses a record that tries to restate it.
REGISTERED_COVERAGE_THRESHOLDS: Final[tuple[CoverageThreshold, ...]] = ()


def validate_threshold_registry(
    thresholds: Sequence[CoverageThreshold] = REGISTERED_COVERAGE_THRESHOLDS,
) -> None:
    """Fail closed on an empty, duplicated, test-contaminated, or fixed-class registry."""
    if not thresholds:
        raise CoverageAuditError(
            BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
            "no minimum coverage or breadth threshold is registered; the audit refuses "
            "to invent one, so no coverage verdict can be produced",
        )
    shipped = thresholds is REGISTERED_COVERAGE_THRESHOLDS
    seen: set[str] = set()
    for threshold in thresholds:
        if not isinstance(threshold, CoverageThreshold):
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
                "registry entries must be CoverageThreshold records",
            )
        if threshold.coverage_class == COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS:
            raise CoverageAuditError(
                BLOCKED_HELD_POSITION_THRESHOLD_IS_FIXED,
                f"{threshold.threshold_id}: held-position valuation / exit coverage is fixed "
                f"at {exact_pair(HELD_POSITION_COVERAGE_REQUIREMENT)} by the ticket and may "
                "not be registered, raised, or lowered",
                coverage_class=threshold.coverage_class,
            )
        if threshold.threshold_id in seen:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
                f"duplicate threshold_id in registry: {threshold.threshold_id}",
            )
        seen.add(threshold.threshold_id)
        if shipped and threshold.source_kind not in REGISTERED_SOURCE_KINDS:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
                f"{threshold.threshold_id}: {threshold.source_kind} may not ship in the registry",
            )


def resolve_coverage_threshold(
    coverage_class: str,
    *,
    threshold_kind: str = THRESHOLD_KIND_MINIMUM_COVERAGE,
    as_of: str,
    thresholds: Sequence[CoverageThreshold] = REGISTERED_COVERAGE_THRESHOLDS,
) -> CoverageThreshold:
    """Return the registered threshold for a class, or fail closed.

    The held-position class never reaches here: its requirement is the hard-wired
    :data:`HELD_POSITION_COVERAGE_REQUIREMENT`.
    """
    if coverage_class not in COVERAGE_CLASSES:
        raise CoverageAuditError(
            BLOCKED_UNREGISTERED_COVERAGE_CLASS,
            f"{coverage_class!r} is not one of the eight coverage classes",
        )
    if coverage_class == COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS:
        raise CoverageAuditError(
            BLOCKED_HELD_POSITION_THRESHOLD_IS_FIXED,
            "held-position valuation / exit coverage is fixed by the ticket and is not "
            "resolved from the registry",
            coverage_class=coverage_class,
        )
    validate_threshold_registry(thresholds)
    day = iso_day(as_of, what="as_of")
    matches = [
        threshold
        for threshold in thresholds
        if threshold.coverage_class == coverage_class
        and threshold.threshold_kind == threshold_kind
        and threshold.is_effective_on(day)
    ]
    if not matches:
        raise CoverageAuditError(
            BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
            f"no registered {threshold_kind} threshold covers {coverage_class} on {day}",
            coverage_class=coverage_class,
        )
    if len(matches) > 1:
        names = ", ".join(sorted(item.threshold_id for item in matches))
        raise CoverageAuditError(
            BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
            f"ambiguous {threshold_kind} thresholds for {coverage_class} on {day}: {names}",
            coverage_class=coverage_class,
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Required items and the missingness / exclusion ledger
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequiredItem:
    """One item a run requires, and whether it is there.

    ``session`` means what :data:`COVERAGE_CLASS_DENOMINATORS` says it means for
    the item's class. The class is part of the item key, which is what keeps the
    eight denominators separate.
    """

    coverage_class: str
    subject_id: str
    session: str
    state: str
    source: str | None = None
    availability_time: str | None = None

    def __post_init__(self) -> None:
        if self.coverage_class not in COVERAGE_CLASSES:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_CLASS,
                f"{self.coverage_class!r} is not one of the eight coverage classes",
            )
        if COVERAGE_CLASS_SUBJECT_KINDS[self.coverage_class] == SUBJECT_KIND_SECURITY:
            opaque_security_id(self.subject_id, what=f"{self.coverage_class} subject_id")
        else:
            token(self.subject_id, what=f"{self.coverage_class} subject_id")
        iso_day(self.session, what="session")
        if self.state not in ITEM_STATES:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_ITEM_STATE,
                f"{self.state!r} is not a registered required-item state",
                coverage_class=self.coverage_class,
                session=self.session,
            )
        allowed_classes = ITEM_STATE_CLASS_RESTRICTIONS.get(self.state)
        if allowed_classes is not None and self.coverage_class not in allowed_classes:
            raise CoverageAuditError(
                BLOCKED_ITEM_STATE_NOT_VALID_FOR_CLASS,
                f"{self.state} may only be declared for {list(allowed_classes)}, "
                f"not {self.coverage_class}",
                coverage_class=self.coverage_class,
                session=self.session,
            )
        if self.availability_time is not None:
            iso_instant(self.availability_time, what="availability_time")

    @property
    def item_key(self) -> str:
        """The item's identity within its class. Unique per class by construction."""
        return f"{self.coverage_class}|{self.subject_id}|{self.session}"

    @property
    def subject_kind(self) -> str:
        return COVERAGE_CLASS_SUBJECT_KINDS[self.coverage_class]

    @property
    def is_valid(self) -> bool:
        return self.state == ITEM_VALID

    def with_state(self, state: str) -> RequiredItem:
        """A copy in a different state. Used only by the two recorded overrides."""
        return RequiredItem(
            coverage_class=self.coverage_class,
            subject_id=self.subject_id,
            session=self.session,
            state=state,
            source=self.source,
            availability_time=self.availability_time,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "coverage_class": self.coverage_class,
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "session": self.session,
            "state": self.state,
            "source": self.source,
            "availability_time": self.availability_time,
        }


@dataclass(frozen=True)
class MissingnessRecord:
    """One entry in the missingness / exclusion ledger.

    Every non-valid required item produces exactly one of these, carrying what
    the caller declared, what the audit resolved, and -- when they differ -- which
    of the two recorded overrides changed it.
    """

    coverage_class: str
    item_key: str
    subject_kind: str
    subject_id: str
    session: str
    state: str
    reason: str
    declared_state: str
    override_sources: tuple[str, ...]
    invalidates_run: bool
    source: str | None
    availability_time: str | None

    def __post_init__(self) -> None:
        if self.state not in NON_VALID_ITEM_STATES:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_ITEM_STATE,
                "the ledger records non-valid items only",
                coverage_class=self.coverage_class,
                session=self.session,
            )
        if self.reason != ITEM_STATE_REASONS[self.state]:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_ITEM_STATE,
                "reason is not the registered reason for this state",
                coverage_class=self.coverage_class,
            )
        for override in self.override_sources:
            if override not in ITEM_STATE_OVERRIDE_SOURCES:
                raise CoverageAuditError(
                    BLOCKED_UNREGISTERED_ITEM_STATE,
                    f"{override!r} is not a registered override source",
                    coverage_class=self.coverage_class,
                )
        if self.invalidates_run != (self.state in RUN_INVALIDATING_ITEM_STATES):
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_ITEM_STATE,
                "invalidates_run must follow from the state, not be asserted",
                coverage_class=self.coverage_class,
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "coverage_class": self.coverage_class,
            "item_key": self.item_key,
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "session": self.session,
            "state": self.state,
            "reason": self.reason,
            "declared_state": self.declared_state,
            "override_sources": list(self.override_sources),
            "invalidates_run": self.invalidates_run,
            "source": self.source,
            "availability_time": self.availability_time,
        }


@dataclass(frozen=True)
class MissingnessLedger:
    """The immutable missingness / exclusion ledger for one run."""

    records: tuple[MissingnessRecord, ...]
    lineage: Lineage

    def __post_init__(self) -> None:
        require_members(self.records, kind=MissingnessRecord, what="records")

    def by_class(self) -> Mapping[str, tuple[MissingnessRecord, ...]]:
        return {
            name: tuple(item for item in self.records if item.coverage_class == name)
            for name in COVERAGE_CLASSES
        }

    def run_invalidating(self) -> tuple[MissingnessRecord, ...]:
        return tuple(item for item in self.records if item.invalidates_run)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kernel_id": KERNEL_ID,
            "records": [item.to_json_dict() for item in self.records],
            "item_state_reasons": dict(ITEM_STATE_REASONS),
            "override_sources": list(ITEM_STATE_OVERRIDE_SOURCES),
            "lineage": self.lineage.to_json_dict(),
            "claims": dict(NON_CLAIMS),
        }


# ---------------------------------------------------------------------------
# Coverage results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageClassResult:
    """Exact coverage for exactly one class. There is no unlabelled coverage."""

    coverage_class: str
    required_items: int
    valid_items: int
    coverage: Fraction

    def __post_init__(self) -> None:
        if self.coverage_class not in COVERAGE_CLASSES:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_CLASS,
                f"{self.coverage_class!r} is not one of the eight coverage classes",
            )
        if self.required_items < 1:
            raise CoverageAuditError(
                BLOCKED_EMPTY_COVERAGE_DENOMINATOR,
                f"{self.coverage_class}: a class with no required items has no coverage; "
                "'nothing was required' is never reported as 'everything is covered'",
                coverage_class=self.coverage_class,
            )
        if not (0 <= self.valid_items <= self.required_items):
            raise CoverageAuditError(
                BLOCKED_INCONSISTENT_COVERAGE_COUNTS,
                f"{self.coverage_class}: valid_items must lie in [0, required_items]",
                coverage_class=self.coverage_class,
            )
        if self.coverage != Fraction(self.valid_items, self.required_items):
            raise CoverageAuditError(
                BLOCKED_INCONSISTENT_COVERAGE_COUNTS,
                f"{self.coverage_class}: coverage is not the exact ratio of its counts",
                coverage_class=self.coverage_class,
            )

    @property
    def denominator_definition(self) -> str:
        return COVERAGE_CLASS_DENOMINATORS[self.coverage_class]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "coverage_class": self.coverage_class,
            "subject_kind": COVERAGE_CLASS_SUBJECT_KINDS[self.coverage_class],
            "key_fields": list(COVERAGE_CLASS_KEY_FIELDS[self.coverage_class]),
            "denominator_definition": self.denominator_definition,
            "required_items": self.required_items,
            "valid_items": self.valid_items,
            "coverage_exact": exact_pair(self.coverage),
            "coverage_artifact": render_ratio(self.coverage),
        }


@dataclass(frozen=True)
class CoverageTable:
    """The immutable coverage table: eight class results and nothing pooled.

    ``by_class`` is the only accessor. There is deliberately no method that sums,
    averages or otherwise collapses the eight results into one number.
    """

    results: tuple[CoverageClassResult, ...]
    lineage: Lineage

    def __post_init__(self) -> None:
        require_members(self.results, kind=CoverageClassResult, what="results")
        names = [item.coverage_class for item in self.results]
        if names != list(COVERAGE_CLASSES):
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_CLASS,
                "a coverage table carries exactly the eight classes, in ticket order",
            )

    def by_class(self) -> Mapping[str, CoverageClassResult]:
        return {item.coverage_class: item for item in self.results}

    def class_coverage(self, coverage_class: str) -> Fraction:
        """Exact coverage for one named class. A class must always be named."""
        return class_coverage(self, coverage_class)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kernel_id": KERNEL_ID,
            "coverage_formula": "coverage_(c,t) = valid_required_items_(c,t) / required_items_(c,t)",
            "coverage_classes": list(COVERAGE_CLASSES),
            "class_results": [item.to_json_dict() for item in self.results],
            "held_position_requirement_exact": exact_pair(HELD_POSITION_COVERAGE_REQUIREMENT),
            "lineage": self.lineage.to_json_dict(),
            "claims": dict(NON_CLAIMS),
        }


def class_coverage(table: CoverageTable, coverage_class: str) -> Fraction:
    """Exact coverage for one named class of ``table``.

    The only module-level function that returns a bare rational. It cannot be
    called without naming a class, and :meth:`CoverageTable.class_coverage`
    delegates straight to it -- which together are what make a pooled figure
    unreachable through this API.
    """
    if coverage_class not in COVERAGE_CLASSES:
        raise CoverageAuditError(
            BLOCKED_UNREGISTERED_COVERAGE_CLASS,
            f"{coverage_class!r} is not one of the eight coverage classes",
        )
    result = table.by_class()[coverage_class]
    return result.coverage


# ---------------------------------------------------------------------------
# Identity and mark adapters
# ---------------------------------------------------------------------------


def identity_item_state(resolution: Resolution) -> str:
    """Map an identity resolution to a required-item state. Never coerces a state.

    An :class:`~qme.data.identity.Ambiguous` or
    :class:`~qme.data.identity.Unknown` result becomes a typed exclusion; it is
    never converted into a resolved identity.
    """
    if isinstance(resolution, ResolvedSecurity):
        return ITEM_VALID
    if isinstance(resolution, Ambiguous | Unknown):
        return ITEM_EXCLUDED_TERMINAL_STATUS
    raise CoverageAuditError(
        BLOCKED_UNRESOLVED_IDENTITY,
        "resolution is not one of the three identity states; nothing is assumed about it",
    )


def classification_item_state(classification_status: str) -> str:
    """Map a NEE-124 terminal status to a required-item state."""
    if classification_status not in TERMINAL_STATUSES:
        raise CoverageAuditError(
            BLOCKED_UNREGISTERED_ITEM_STATE,
            f"{classification_status!r} is not a registered terminal classification status",
        )
    if classification_status == CLASSIFICATION_VALID_STATUS:
        return ITEM_VALID
    return ITEM_EXCLUDED_TERMINAL_STATUS


def action_item_state(action_state: str) -> str:
    """Map a NEE-125 unsupported-action state to a required-item state.

    ``RUN_INVALID_UNSUPPORTED_HELD_ACTION`` is deliberately *not* mapped: an
    unsupported action on a held position is the factor kernel's own run
    invalidation and is not downgraded here into a coverage exclusion.
    """
    if action_state == EXCLUDED_UNSUPPORTED_UNHELD_ACTION:
        return ITEM_EXCLUDED_UNSUPPORTED_ACTION
    if action_state == RUN_INVALID_UNSUPPORTED_HELD_ACTION:
        raise CoverageAuditError(
            BLOCKED_ITEM_STATE_NOT_VALID_FOR_CLASS,
            "an unsupported action on a held position invalidates the run in the factor "
            "kernel; it is not recorded here as a coverage exclusion",
            coverage_class=COVERAGE_CLASS_ACTIONS,
        )
    raise CoverageAuditError(
        BLOCKED_UNREGISTERED_ITEM_STATE,
        f"{action_state!r} is not a recognised corporate-action exclusion state",
        coverage_class=COVERAGE_CLASS_ACTIONS,
    )


def held_mark_item_state(
    mark: HeldPositionMark,
    *,
    as_of: str,
    calendar: TradingCalendar | None = None,
    policies: Sequence[MissingMarkPolicy] = REGISTERED_MISSING_MARK_POLICIES,
) -> str:
    """Resolve a held mark to a required-item state, never to a substituted value.

    With the shipped empty policy registry every refusal maps to a *non-valid*
    state: there is no branch in which a missing mark becomes ``ITEM_VALID`` and
    none in which a stale mark is carried forward. A refusal this mapping does
    not recognise degrades to ``ITEM_INVALID_FAILED_VALIDATION`` -- still
    non-valid, never valid -- so an unforeseen refusal cannot become a pass.
    """
    try:
        resolve_held_mark(mark, as_of=as_of, calendar=calendar, policies=policies)
    except DelistingPolicyError as refusal:
        return MARK_REFUSAL_ITEM_STATES.get(refusal.state, ITEM_INVALID_FAILED_VALIDATION)
    return ITEM_VALID


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateStatus:
    """The run's gate status and everything that decided it."""

    status: str
    held_position_requirement_exact: str
    held_position_coverage_exact: str
    unaudited_held_item_keys: tuple[str, ...]
    classes_below_threshold: tuple[str, ...]
    unregistered_threshold_classes: tuple[str, ...]
    resolved_threshold_ids: tuple[str, ...]
    detail: str
    lineage: Lineage

    def __post_init__(self) -> None:
        if self.status not in GATE_STATUSES:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_ITEM_STATE, f"{self.status!r} is not a registered gate status"
            )
        # A verdict must be backed by its own evidence. Without this, a
        # ``GATE_VALID`` could be constructed directly while still carrying
        # unaudited held items or classes below threshold, and every downstream
        # check keys on ``status``.
        if self.status == GATE_VALID:
            if self.unaudited_held_item_keys:
                raise CoverageAuditError(
                    BLOCKED_UNREGISTERED_ITEM_STATE,
                    "a valid gate cannot carry unaudited held positions",
                )
            if self.classes_below_threshold or self.unregistered_threshold_classes:
                raise CoverageAuditError(
                    BLOCKED_UNREGISTERED_ITEM_STATE,
                    "a valid gate cannot carry a class below or without a threshold",
                )
            if self.held_position_coverage_exact != self.held_position_requirement_exact:
                raise CoverageAuditError(
                    BLOCKED_UNREGISTERED_ITEM_STATE,
                    "a valid gate requires complete held-position coverage",
                )
        if self.status == RUN_INVALID_UNAUDITED_HELD_POSITION and not (
            self.unaudited_held_item_keys
        ):
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_ITEM_STATE,
                "an unaudited-held-position invalidation must name the items",
            )
        for name in (
            *self.classes_below_threshold,
            *self.unregistered_threshold_classes,
        ):
            if name not in COVERAGE_CLASSES:
                raise CoverageAuditError(
                    BLOCKED_UNREGISTERED_COVERAGE_CLASS,
                    f"{name!r} is not one of the eight coverage classes",
                )

    @property
    def is_valid(self) -> bool:
        """True only for ``GATE_VALID``. No other status is a verdict."""
        return self.status == GATE_VALID

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kernel_id": KERNEL_ID,
            "status": self.status,
            "is_valid": self.is_valid,
            "held_position_requirement_exact": self.held_position_requirement_exact,
            "held_position_coverage_exact": self.held_position_coverage_exact,
            "unaudited_held_item_keys": list(self.unaudited_held_item_keys),
            "classes_below_threshold": list(self.classes_below_threshold),
            "unregistered_threshold_classes": list(self.unregistered_threshold_classes),
            "resolved_threshold_ids": list(self.resolved_threshold_ids),
            "detail": self.detail,
            "lineage": self.lineage.to_json_dict(),
            "claims": dict(NON_CLAIMS),
        }


def evaluate_gate(
    coverage: CoverageTable,
    ledger: MissingnessLedger,
    *,
    as_of: str,
    thresholds: Sequence[CoverageThreshold] = REGISTERED_COVERAGE_THRESHOLDS,
    lineage: Lineage | None = None,
) -> GateStatus:
    """Resolve the gate in a fixed order; first match wins.

    1. any unaudited required held position -> ``RUN_INVALID_UNAUDITED_HELD_POSITION``;
    2. held-position coverage below the hard-wired ``1`` ->
       ``RUN_INVALID_INCOMPLETE_HELD_POSITION_COVERAGE``;
    3. any of the other seven classes without a registered threshold ->
       ``BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD`` (the shipped case);
    4. any class below its registered threshold -> ``RUN_INVALID_COVERAGE_BELOW_THRESHOLD``;
    5. otherwise ``GATE_VALID``.

    Steps 1 and 2 precede step 3 on purpose, so run invalidity is provable
    without any registration at all.
    """
    resolved_lineage = lineage if lineage is not None else _gate_lineage(coverage, ledger, as_of)
    held = coverage.by_class()[COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS]
    held_exact = exact_pair(held.coverage)
    requirement_exact = exact_pair(HELD_POSITION_COVERAGE_REQUIREMENT)

    unaudited = tuple(
        sorted(
            record.item_key
            for record in ledger.records
            if record.state == ITEM_UNAUDITED_HELD_POSITION
        )
    )
    if unaudited:
        return GateStatus(
            status=RUN_INVALID_UNAUDITED_HELD_POSITION,
            held_position_requirement_exact=requirement_exact,
            held_position_coverage_exact=held_exact,
            unaudited_held_item_keys=unaudited,
            classes_below_threshold=(COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,),
            unregistered_threshold_classes=(),
            resolved_threshold_ids=(),
            detail=(
                f"{len(unaudited)} required held position(s) have no audited valuation or "
                "settled exit; the affected run is invalid and cannot be gated valid"
            ),
            lineage=resolved_lineage,
        )

    if held.coverage != HELD_POSITION_COVERAGE_REQUIREMENT:
        return GateStatus(
            status=RUN_INVALID_INCOMPLETE_HELD_POSITION_COVERAGE,
            held_position_requirement_exact=requirement_exact,
            held_position_coverage_exact=held_exact,
            unaudited_held_item_keys=(),
            classes_below_threshold=(COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,),
            unregistered_threshold_classes=(),
            resolved_threshold_ids=(),
            detail=(
                f"held-position valuation / exit coverage is {held_exact}; the ticket fixes "
                f"the requirement at {requirement_exact} and it is not registrable"
            ),
            lineage=resolved_lineage,
        )

    unregistered: list[str] = []
    resolved_ids: list[str] = []
    below: list[str] = []
    for name in COVERAGE_CLASSES:
        if name == COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS:
            continue
        try:
            threshold = resolve_coverage_threshold(name, as_of=as_of, thresholds=thresholds)
        except CoverageAuditError as refusal:
            if refusal.state != BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD:
                # A poisoned registry -- a duplicate id, a shipped test record, or
                # a record naming the fixed held-position class -- is a hard
                # defect, not an absent registration. It is never softened into
                # "unregistered", because the two need different fixes.
                raise
            unregistered.append(name)
            continue
        resolved_ids.append(threshold.threshold_id)
        result = coverage.by_class()[name]
        if not _meets(result, threshold):
            below.append(name)
            continue
        # A breadth threshold is optional per class, but when one IS registered it
        # is enforced. Without this second lookup a registered breadth record
        # would sit in the registry doing nothing.
        try:
            breadth = resolve_coverage_threshold(
                name,
                threshold_kind=THRESHOLD_KIND_MINIMUM_BREADTH,
                as_of=as_of,
                thresholds=thresholds,
            )
        except CoverageAuditError as refusal:
            if refusal.state != BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD:
                raise
            continue
        resolved_ids.append(breadth.threshold_id)
        if not _meets(result, breadth):
            below.append(name)

    if unregistered:
        return GateStatus(
            status=BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
            held_position_requirement_exact=requirement_exact,
            held_position_coverage_exact=held_exact,
            unaudited_held_item_keys=(),
            classes_below_threshold=(),
            unregistered_threshold_classes=tuple(unregistered),
            resolved_threshold_ids=tuple(sorted(resolved_ids)),
            detail=(
                "no owner-registered minimum coverage or breadth threshold exists for "
                f"{unregistered}; the audit refuses to emit a coverage verdict"
            ),
            lineage=resolved_lineage,
        )
    if below:
        return GateStatus(
            status=RUN_INVALID_COVERAGE_BELOW_THRESHOLD,
            held_position_requirement_exact=requirement_exact,
            held_position_coverage_exact=held_exact,
            unaudited_held_item_keys=(),
            classes_below_threshold=tuple(below),
            unregistered_threshold_classes=(),
            resolved_threshold_ids=tuple(sorted(resolved_ids)),
            detail=f"coverage is below its registered threshold for {below}",
            lineage=resolved_lineage,
        )
    return GateStatus(
        status=GATE_VALID,
        held_position_requirement_exact=requirement_exact,
        held_position_coverage_exact=held_exact,
        unaudited_held_item_keys=(),
        classes_below_threshold=(),
        unregistered_threshold_classes=(),
        resolved_threshold_ids=tuple(sorted(resolved_ids)),
        detail="every class meets its registered threshold and held-position coverage is complete",
        lineage=resolved_lineage,
    )


def _meets(result: CoverageClassResult, threshold: CoverageThreshold) -> bool:
    """Exact comparison of one class result against one registered threshold.

    Both arms are exact: a fraction bound compares rationals, a count bound
    compares integers. Neither converts to a binary float.
    """
    if threshold.minimum_fraction is not None:
        return result.coverage >= exact(threshold.minimum_fraction, what="minimum_fraction")
    if threshold.minimum_count is not None:
        return result.valid_items >= threshold.minimum_count
    raise CoverageAuditError(  # pragma: no cover - construction requires exactly one bound
        BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
        f"{threshold.threshold_id}: no bound to compare against",
        coverage_class=threshold.coverage_class,
    )


def require_valid_gate(gate: GateStatus) -> GateStatus:
    """Return the gate, or fail closed. This never converts a status.

    The sanctioned way for a caller that requires a valid run to get one: any
    non-``GATE_VALID`` status is *rejected* with a typed error, never coerced.
    """
    if gate.is_valid:
        return gate
    raise CoverageAuditError(
        BLOCKED_GATE_NOT_VALID,
        f"the run is not gated valid: {gate.status} -- {gate.detail}",
        detail=gate.status,
    )


# ---------------------------------------------------------------------------
# Building the audit
# ---------------------------------------------------------------------------


def coverage_config_document() -> dict[str, Any]:
    """The declared coverage configuration, including the threshold registry."""
    return {
        "schema_version": SCHEMA_VERSION,
        "kernel_id": KERNEL_ID,
        "coverage_classes": list(COVERAGE_CLASSES),
        "coverage_class_denominators": dict(COVERAGE_CLASS_DENOMINATORS),
        "coverage_class_subject_kinds": dict(COVERAGE_CLASS_SUBJECT_KINDS),
        "coverage_class_key_fields": {
            key: list(value) for key, value in COVERAGE_CLASS_KEY_FIELDS.items()
        },
        "session_aligned_coverage_classes": list(SESSION_ALIGNED_COVERAGE_CLASSES),
        "security_subject_coverage_classes": list(SECURITY_SUBJECT_COVERAGE_CLASSES),
        "held_position_requirement_exact": exact_pair(HELD_POSITION_COVERAGE_REQUIREMENT),
        "item_states": list(ITEM_STATES),
        "item_state_reasons": dict(ITEM_STATE_REASONS),
        "item_state_class_restrictions": {
            key: list(value) for key, value in ITEM_STATE_CLASS_RESTRICTIONS.items()
        },
        "run_invalidating_item_states": list(RUN_INVALIDATING_ITEM_STATES),
        "item_state_override_sources": list(ITEM_STATE_OVERRIDE_SOURCES),
        "mark_refusal_item_states": dict(MARK_REFUSAL_ITEM_STATES),
        "threshold_kinds": list(THRESHOLD_KINDS),
        "gate_statuses": list(GATE_STATUSES),
        "fail_closed_states": list(COVERAGE_FAIL_CLOSED_STATES),
        "classification_valid_status": CLASSIFICATION_VALID_STATUS,
        "classification_excluded_statuses": list(CLASSIFICATION_EXCLUDED_STATUSES),
        "identity_rules_version": IDENTITY_RULES_VERSION,
        "identity_coverage_limitation": COVERAGE_LIMITATION,
        "registered_coverage_thresholds": [
            threshold.to_json_dict() for threshold in REGISTERED_COVERAGE_THRESHOLDS
        ],
        "delisting_config": delisting_config_document(),
        "claims": dict(NON_CLAIMS),
    }


def _config_digest() -> str:
    return dataset_digest(coverage_config_document())


def _gate_lineage(coverage: CoverageTable, ledger: MissingnessLedger, as_of: str) -> Lineage:
    return Lineage(
        dataset_sha256_grouped=dataset_digest(
            {
                "as_of": as_of,
                "coverage": coverage.to_json_dict()["class_results"],
                "ledger": [item.to_json_dict() for item in ledger.records],
            }
        ),
        config_sha256_grouped=_config_digest(),
        code_sha256_grouped=code_binding_digest({"coverage_audit_kernel_id": KERNEL_ID}),
    )


def _validated_items(
    required_items: Sequence[RequiredItem],
    *,
    calendar: TradingCalendar | None,
) -> dict[str, RequiredItem]:
    """Deduplicate, session-align, and index every required item by its key."""
    indexed: dict[str, RequiredItem] = {}
    for item in required_items:
        if not isinstance(item, RequiredItem):
            raise CoverageAuditError(
                BLOCKED_DUPLICATE_REQUIRED_ITEM, "required items must be RequiredItem records"
            )
        if item.item_key in indexed:
            raise CoverageAuditError(
                BLOCKED_DUPLICATE_REQUIRED_ITEM,
                f"duplicate required item {item.item_key}; a denominator counts distinct keys",
                coverage_class=item.coverage_class,
                session=item.session,
            )
        indexed[item.item_key] = item

    aligned = [
        item for item in indexed.values() if item.coverage_class in SESSION_ALIGNED_COVERAGE_CLASSES
    ]
    if aligned:
        resolved = require_calendar(
            calendar, what="session alignment of price, anchor, held and benchmark items"
        )
        for item in aligned:
            try:
                is_session = resolved.is_session(item.session)
            except MarketStoreError as exc:  # pragma: no cover - RequiredItem validates the shape
                raise CoverageAuditError(
                    BLOCKED_ITEM_SESSION_NOT_A_SESSION,
                    f"{item.item_key}: {exc.state}",
                    coverage_class=item.coverage_class,
                    session=item.session,
                ) from exc
            if not is_session:
                raise CoverageAuditError(
                    BLOCKED_ITEM_SESSION_NOT_A_SESSION,
                    f"{item.item_key}: {item.session} is not an accepted trading session, so it "
                    "cannot be a required item for this class",
                    coverage_class=item.coverage_class,
                    session=item.session,
                )
    return indexed


def _apply_mark_overrides(
    indexed: dict[str, RequiredItem],
    held_marks: Sequence[HeldPositionMark],
    *,
    as_of: str,
    calendar: TradingCalendar | None,
    policies: Sequence[MissingMarkPolicy],
) -> dict[str, list[str]]:
    """Resolve every supplied mark and override its item's state, downward only.

    The override is **monotone toward non-valid**: a mark that resolves cleanly
    leaves the caller's declaration alone, so supplying a good mark can never
    erase a non-valid state the caller declared for some other reason. Only a
    refusing mark changes an item, and only ever away from ``ITEM_VALID``.

    Two marks for the same ``(security_id, session)`` are refused rather than
    last-one-wins, so the result cannot depend on input order.
    """
    overrides: dict[str, list[str]] = {}
    seen: set[str] = set()
    for mark in held_marks:
        if not isinstance(mark, HeldPositionMark):
            raise CoverageAuditError(
                BLOCKED_MARK_FOR_UNDECLARED_HELD_ITEM, "marks must be HeldPositionMark records"
            )
        key = f"{COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS}|{mark.security_id}|{mark.session}"
        if key in seen:
            raise CoverageAuditError(
                BLOCKED_DUPLICATE_HELD_MARK,
                f"two marks were supplied for {key}; the audit refuses to pick one, "
                "because which one won would depend on input order",
                coverage_class=COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,
                security_id=mark.security_id,
                session=mark.session,
            )
        seen.add(key)
        if key not in indexed:
            raise CoverageAuditError(
                BLOCKED_MARK_FOR_UNDECLARED_HELD_ITEM,
                f"a mark was supplied for {key}, which is not a declared required item",
                coverage_class=COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,
                security_id=mark.security_id,
                session=mark.session,
            )
        state = held_mark_item_state(
            mark, as_of=as_of, calendar=calendar, policies=policies
        )
        if state == ITEM_VALID or state == indexed[key].state:
            continue
        overrides.setdefault(key, []).append(OVERRIDE_HELD_MARK_RESOLUTION)
        indexed[key] = indexed[key].with_state(state)
    return overrides


def _apply_unresolved_exit_cross_check(
    indexed: dict[str, RequiredItem],
    delisting: DelistingTable,
    overrides: dict[str, list[str]],
) -> None:
    """Force every held item whose security has an unresolved exit to UNAUDITED.

    A caller cannot declare a held position valid while its exit is unresolved:
    this override runs after the caller's declaration and after the mark
    resolution, and it is recorded in the ledger. The two overrides compose
    monotonically toward the stricter state -- the cross-check only ever sets
    ``ITEM_UNAUDITED_HELD_POSITION``, the one run-invalidating state -- and both
    appear in the record's ``override_sources``, in the order applied.
    """
    unresolved = set(delisting.unresolved_security_ids())
    if not unresolved:
        return
    for key, item in list(indexed.items()):
        if item.coverage_class != COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS:
            continue
        if item.subject_id not in unresolved:
            continue
        if item.state == ITEM_UNAUDITED_HELD_POSITION:
            continue
        overrides.setdefault(key, []).append(OVERRIDE_UNRESOLVED_EXIT_CROSS_CHECK)
        indexed[key] = item.with_state(ITEM_UNAUDITED_HELD_POSITION)


@dataclass(frozen=True)
class FallbackSensitivityResults:
    """The fallback-sensitivity section, with its own lineage.

    Empty with the shipped registries: no haircut and no sensitivity range is
    registered, so no scenario number exists to report.
    """

    results: tuple[FallbackScenarioResult, ...]
    lineage: Lineage

    def __post_init__(self) -> None:
        # The container is part of the wall: an ObservedDelistingReturn cannot be
        # smuggled into the fallback section, nor the reverse.
        require_members(self.results, kind=FallbackScenarioResult, what="results")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kernel_id": KERNEL_ID,
            "results": [item.to_json_dict() for item in self.results],
            "lineage": self.lineage.to_json_dict(),
            "claims": dict(NON_CLAIMS),
        }


@dataclass(frozen=True)
class AttributionTable:
    """The P&L-attribution section, with its own lineage."""

    rows: tuple[OutcomeAttributionRow, ...]
    lineage: Lineage

    def __post_init__(self) -> None:
        require_members(self.rows, kind=OutcomeAttributionRow, what="rows")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kernel_id": KERNEL_ID,
            "rows": [item.to_json_dict() for item in self.rows],
            "lineage": self.lineage.to_json_dict(),
            "claims": dict(NON_CLAIMS),
        }


@dataclass(frozen=True)
class CoverageAuditReport:
    """The immutable coverage audit for one run: six outputs, each with lineage.

    Frozen dataclasses throughout, canonical JSON at the boundary, and a grouped
    self-hash over those bytes. Nothing here exposes a pooled coverage figure.
    """

    audit_id: str
    analysis_cutoff: str
    as_of: str
    coverage: CoverageTable
    missingness: MissingnessLedger
    delisting: DelistingTable
    fallbacks: FallbackSensitivityResults
    attribution: AttributionTable
    gate: GateStatus
    lineage: Lineage

    @property
    def is_valid(self) -> bool:
        """True only when the gate is a ``GATE_VALID`` verdict."""
        return self.gate.is_valid

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kernel_id": KERNEL_ID,
            "audit_id": self.audit_id,
            "analysis_cutoff": self.analysis_cutoff,
            "as_of": self.as_of,
            "coverage_table": self.coverage.to_json_dict(),
            "missingness_ledger": self.missingness.to_json_dict(),
            "delisting_table": self.delisting.to_json_dict(),
            "fallback_sensitivity_results": self.fallbacks.to_json_dict(),
            "pnl_attribution": self.attribution.to_json_dict(),
            "gate_status": self.gate.to_json_dict(),
            "lineage": self.lineage.to_json_dict(),
            "claims": dict(NON_CLAIMS),
        }


def canonical_report_bytes(report: CoverageAuditReport) -> bytes:
    """Deterministic UTF-8 / LF JSON bytes for the whole report."""
    return canonical_json_bytes(report.to_json_dict())


def report_sha256_grouped(report: CoverageAuditReport) -> str:
    """The report's grouped self-hash over :func:`canonical_report_bytes`."""
    return canonical_dataset_digest(report.to_json_dict())


def report_identity(report: CoverageAuditReport) -> dict[str, Any]:
    """The emitted report identity: schema, kernel, gate status, self-hash."""
    return {
        "schema_version": SCHEMA_VERSION,
        "kernel_id": KERNEL_ID,
        "audit_id": report.audit_id,
        "as_of": report.as_of,
        "gate_status": report.gate.status,
        "coverage_class_count": len(report.coverage.results),
        "missingness_record_count": len(report.missingness.records),
        "delisting_row_count": len(report.delisting.rows),
        "fallback_result_count": len(report.fallbacks.results),
        "attribution_row_count": len(report.attribution.rows),
        "report_sha256_grouped": report_sha256_grouped(report),
    }


def build_coverage_audit(
    *,
    audit_id: str,
    analysis_cutoff: str,
    as_of: str,
    required_items: Sequence[RequiredItem],
    delisting_events: Sequence[DelistingEvent] = (),
    pricing: Sequence[ExitPricingInput] = (),
    held_marks: Sequence[HeldPositionMark] = (),
    calendar: TradingCalendar | None = None,
    thresholds: Sequence[CoverageThreshold] = REGISTERED_COVERAGE_THRESHOLDS,
    timing_rules: Sequence[DelistingTimingRule] = REGISTERED_DELISTING_TIMING_RULES,
    haircuts: Sequence[FallbackHaircut] = REGISTERED_FALLBACK_HAIRCUTS,
    ranges: Sequence[SensitivityRange] = REGISTERED_SENSITIVITY_RANGES,
    decisions: Sequence[BenchmarkTreatmentDecision] = REGISTERED_BENCHMARK_TREATMENT_DECISIONS,
    mark_policies: Sequence[MissingMarkPolicy] = REGISTERED_MISSING_MARK_POLICIES,
) -> CoverageAuditReport:
    """Build the whole audit: coverage, ledger, delisting, fallbacks, attribution, gate.

    Permutation-invariant by construction: every input is indexed by a content
    key and every output is emitted in content order, so shuffling the inputs
    produces byte-identical canonical JSON.
    """
    token(audit_id, what="audit_id")
    iso_instant(analysis_cutoff, what="analysis_cutoff")
    iso_day(as_of, what="as_of")

    indexed = _validated_items(required_items, calendar=calendar)
    declared_states = {key: item.state for key, item in indexed.items()}

    missing_classes = [
        name
        for name in COVERAGE_CLASSES
        if not any(item.coverage_class == name for item in indexed.values())
    ]
    if missing_classes:
        raise CoverageAuditError(
            BLOCKED_EMPTY_COVERAGE_DENOMINATOR,
            f"no required items were declared for {missing_classes}; a class with an empty "
            "denominator has no coverage, and 'nothing was required' is never reported as "
            "'everything is covered'",
        )

    config = _config_digest()
    code = code_binding_digest({"coverage_audit_kernel_id": KERNEL_ID})

    built = build_delisting_table(
        delisting_events,
        as_of=as_of,
        pricing=pricing,
        rules=timing_rules,
        haircuts=haircuts,
        ranges=ranges,
        decisions=decisions,
        calendar=calendar,
    )
    # One run, one configuration, one code binding: the delisting table's own
    # standalone config/code digests are re-stamped with the audit's, so every
    # one of the six outputs resolves to the same pair. Its dataset digest --
    # the delisting inputs -- is untouched.
    delisting = replace(
        built,
        lineage=Lineage(
            dataset_sha256_grouped=built.lineage.dataset_sha256_grouped,
            config_sha256_grouped=config,
            code_sha256_grouped=code,
        ),
    )

    overrides = _apply_mark_overrides(
        indexed, held_marks, as_of=as_of, calendar=calendar, policies=mark_policies
    )
    _apply_unresolved_exit_cross_check(indexed, delisting, overrides)

    results = []
    for name in COVERAGE_CLASSES:
        items = [item for item in indexed.values() if item.coverage_class == name]
        required = len(items)
        valid = sum(1 for item in items if item.is_valid)
        results.append(
            CoverageClassResult(
                coverage_class=name,
                required_items=required,
                valid_items=valid,
                coverage=Fraction(valid, required),
            )
        )

    ledger_records = tuple(
        MissingnessRecord(
            coverage_class=item.coverage_class,
            item_key=key,
            subject_kind=item.subject_kind,
            subject_id=item.subject_id,
            session=item.session,
            state=item.state,
            reason=ITEM_STATE_REASONS[item.state],
            declared_state=declared_states[key],
            override_sources=tuple(overrides.get(key, ())),
            invalidates_run=item.state in RUN_INVALIDATING_ITEM_STATES,
            source=item.source,
            availability_time=item.availability_time,
        )
        for key, item in sorted(indexed.items())
        if not item.is_valid
    )

    item_inputs = {
        "audit_id": audit_id,
        "analysis_cutoff": analysis_cutoff,
        "as_of": as_of,
        "required_items": [
            indexed[key].to_json_dict() for key in sorted(indexed)
        ],
        "declared_states": dict(sorted(declared_states.items())),
        "held_marks": [
            mark.to_json_dict()
            for mark in sorted(held_marks, key=lambda item: (item.security_id, item.session))
        ],
    }
    items_dataset = dataset_digest(item_inputs)

    coverage_table = CoverageTable(
        results=tuple(results),
        lineage=Lineage(
            dataset_sha256_grouped=items_dataset,
            config_sha256_grouped=config,
            code_sha256_grouped=code,
        ),
    )
    ledger = MissingnessLedger(
        records=ledger_records,
        lineage=Lineage(
            dataset_sha256_grouped=items_dataset,
            config_sha256_grouped=config,
            code_sha256_grouped=code,
        ),
    )
    fallbacks = FallbackSensitivityResults(
        results=delisting.fallbacks,
        lineage=Lineage(
            dataset_sha256_grouped=delisting.lineage.dataset_sha256_grouped,
            config_sha256_grouped=config,
            code_sha256_grouped=code,
        ),
    )
    attribution = AttributionTable(
        rows=attribute_pnl_by_outcome_type(delisting, pricing=pricing),
        lineage=Lineage(
            dataset_sha256_grouped=delisting.lineage.dataset_sha256_grouped,
            config_sha256_grouped=config,
            code_sha256_grouped=code,
        ),
    )
    gate = evaluate_gate(
        coverage_table,
        ledger,
        as_of=as_of,
        thresholds=thresholds,
        lineage=Lineage(
            dataset_sha256_grouped=items_dataset,
            config_sha256_grouped=config,
            code_sha256_grouped=code,
        ),
    )
    return CoverageAuditReport(
        audit_id=audit_id,
        analysis_cutoff=analysis_cutoff,
        as_of=as_of,
        coverage=coverage_table,
        missingness=ledger,
        delisting=delisting,
        fallbacks=fallbacks,
        attribution=attribution,
        gate=gate,
        lineage=Lineage(
            dataset_sha256_grouped=dataset_digest(
                {
                    "items": items_dataset,
                    "delisting": delisting.lineage.dataset_sha256_grouped,
                }
            ),
            config_sha256_grouped=config,
            code_sha256_grouped=code,
        ),
    )


__all__ = [
    "BLOCKED_DUPLICATE_HELD_MARK",
    "BLOCKED_DUPLICATE_REQUIRED_ITEM",
    "BLOCKED_EMPTY_COVERAGE_DENOMINATOR",
    "BLOCKED_GATE_NOT_VALID",
    "BLOCKED_HELD_POSITION_THRESHOLD_IS_FIXED",
    "BLOCKED_INCONSISTENT_COVERAGE_COUNTS",
    "BLOCKED_ITEM_SESSION_NOT_A_SESSION",
    "BLOCKED_ITEM_STATE_NOT_VALID_FOR_CLASS",
    "BLOCKED_MARK_FOR_UNDECLARED_HELD_ITEM",
    "BLOCKED_UNREGISTERED_COVERAGE_CLASS",
    "BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD",
    "BLOCKED_UNREGISTERED_ITEM_STATE",
    "BLOCKED_UNRESOLVED_IDENTITY",
    "CLASSIFICATION_EXCLUDED_STATUSES",
    "CLASSIFICATION_VALID_STATUS",
    "COVERAGE_CLASSES",
    "COVERAGE_CLASS_ACTIONS",
    "COVERAGE_CLASS_ANCHORS",
    "COVERAGE_CLASS_BENCHMARKS",
    "COVERAGE_CLASS_CLASSIFICATION",
    "COVERAGE_CLASS_DENOMINATORS",
    "COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS",
    "COVERAGE_CLASS_IDENTITY",
    "COVERAGE_CLASS_KEY_FIELDS",
    "COVERAGE_CLASS_LISTINGS",
    "COVERAGE_CLASS_PRICES",
    "COVERAGE_CLASS_SUBJECT_KINDS",
    "COVERAGE_FAIL_CLOSED_STATES",
    "GATE_STATUSES",
    "GATE_VALID",
    "HELD_POSITION_COVERAGE_REQUIREMENT",
    "ITEM_EXCLUDED_TERMINAL_STATUS",
    "ITEM_EXCLUDED_UNSUPPORTED_ACTION",
    "ITEM_INVALID_FAILED_VALIDATION",
    "ITEM_MISSING_AFTER_AVAILABILITY_CUTOFF",
    "ITEM_MISSING_NOT_SOURCED",
    "ITEM_STALE_BEYOND_DECLARED_HORIZON",
    "ITEM_STATES",
    "ITEM_STATE_CLASS_RESTRICTIONS",
    "ITEM_STATE_OVERRIDE_SOURCES",
    "ITEM_STATE_REASONS",
    "ITEM_UNAUDITED_HELD_POSITION",
    "ITEM_VALID",
    "KERNEL_ID",
    "MARK_REFUSAL_ITEM_STATES",
    "NON_VALID_ITEM_STATES",
    "OVERRIDE_HELD_MARK_RESOLUTION",
    "OVERRIDE_UNRESOLVED_EXIT_CROSS_CHECK",
    "REGISTERED_COVERAGE_THRESHOLDS",
    "RUN_INVALIDATING_ITEM_STATES",
    "RUN_INVALID_COVERAGE_BELOW_THRESHOLD",
    "RUN_INVALID_INCOMPLETE_HELD_POSITION_COVERAGE",
    "RUN_INVALID_UNAUDITED_HELD_POSITION",
    "SCHEMA_VERSION",
    "SECURITY_SUBJECT_COVERAGE_CLASSES",
    "SESSION_ALIGNED_COVERAGE_CLASSES",
    "SUBJECT_KINDS",
    "SUBJECT_KIND_ANCHOR",
    "SUBJECT_KIND_BENCHMARK",
    "SUBJECT_KIND_IDENTITY_KEY",
    "SUBJECT_KIND_SECURITY",
    "THRESHOLD_KINDS",
    "THRESHOLD_KIND_MINIMUM_BREADTH",
    "THRESHOLD_KIND_MINIMUM_COVERAGE",
    "AttributionTable",
    "CoverageAuditError",
    "CoverageAuditReport",
    "CoverageClassResult",
    "CoverageTable",
    "CoverageThreshold",
    "FallbackSensitivityResults",
    "GateStatus",
    "MissingnessLedger",
    "MissingnessRecord",
    "RequiredItem",
    "action_item_state",
    "build_coverage_audit",
    "canonical_report_bytes",
    "class_coverage",
    "classification_item_state",
    "coverage_config_document",
    "evaluate_gate",
    "held_mark_item_state",
    "identity_item_state",
    "report_identity",
    "report_sha256_grouped",
    "require_valid_gate",
    "resolve_coverage_threshold",
    "validate_threshold_registry",
]
