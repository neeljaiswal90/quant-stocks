"""Sourced coverage join: the auditor owns the denominator and the verdict.

Callers supply :class:`CoverageRequirement` records derived from a frozen run
plan and :class:`CoverageObservation` records derived from source adapters.
Neither type carries ``ITEM_VALID``. The join decides the state.

This path is not a production coverage proof. Alpha Vantage ``LISTING_STATUS``
may evidence an active/delisted lifecycle interval after 2010-01-01; it does
not evidence delisting reason, merger consideration, or payment timing. Source
PRs #73/#75 and #74/#76 remain open, so no real-data bundle is frozen here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from qme.data.classification.rules_v1 import is_opaque_identifier
from qme.data.coverage.audit_v1 import (
    BLOCKED_ACTION_ABSENCE_IS_NOT_COMPLETENESS,
    BLOCKED_CALLER_DECLARED_COVERAGE_STATE,
    BLOCKED_DENOMINATOR_CHANGED_AFTER_PREREGISTRATION,
    BLOCKED_DUPLICATE_COVERAGE_OBSERVATION,
    BLOCKED_IMPLICIT_BENCHMARK_PROXY,
    BLOCKED_LISTING_STATUS_NOT_EVENT_EVIDENCE,
    BLOCKED_ORPHAN_COVERAGE_OBSERVATION,
    BLOCKED_UNREGISTERED_COVERAGE_CLASS,
    COVERAGE_CLASS_ACTIONS,
    COVERAGE_CLASS_ANCHORS,
    COVERAGE_CLASS_BENCHMARKS,
    COVERAGE_CLASS_CLASSIFICATION,
    COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,
    COVERAGE_CLASS_IDENTITY,
    COVERAGE_CLASS_LISTINGS,
    COVERAGE_CLASS_PRICES,
    COVERAGE_CLASS_SUBJECT_KINDS,
    COVERAGE_CLASSES,
    ITEM_INVALID_FAILED_VALIDATION,
    ITEM_MISSING_AFTER_AVAILABILITY_CUTOFF,
    ITEM_MISSING_NOT_SOURCED,
    ITEM_VALID,
    SUBJECT_KIND_SECURITY,
    CoverageAuditError,
    CoverageAuditReport,
    RequiredItem,
    build_coverage_audit,
)
from qme.data.coverage.delisting_v1 import (
    CUTOFF_KIND_DECISION,
    CUTOFF_KIND_OUTCOME,
    CUTOFF_KINDS,
    SOURCE_KINDS,
    CutoffPolicy,
    iso_day,
    iso_instant,
    opaque_security_id,
    token,
)
from qme.data.identity import grouped_sha256
from qme.data.stores.calendar_v1 import TradingCalendar
from qme.foundation.lineage import canonical_json_bytes

EVIDENCE_LISTING_INTERVAL: Final = "LISTING_INTERVAL"
EVIDENCE_UNIQUE_IDENTITY: Final = "UNIQUE_IDENTITY_RESOLUTION"
EVIDENCE_TERMINAL_CLASSIFICATION: Final = "TERMINAL_CLASSIFICATION"
EVIDENCE_EXACT_PIT_PRICE: Final = "EXACT_PIT_PRICE"
EVIDENCE_ACTION_RECORD: Final = "ACTION_RECORD"
EVIDENCE_ACTION_COMPLETENESS: Final = "ACTION_COMPLETENESS_ASSERTION"
EVIDENCE_ACTION_QUERY_EMPTY: Final = "QUERY_RETURNED_NOTHING"
EVIDENCE_FROZEN_ANCHOR: Final = "FROZEN_REBALANCE_ANCHOR"
EVIDENCE_EXACT_MARK: Final = "EXACT_SESSION_MARK"
EVIDENCE_SETTLED_EXIT: Final = "SETTLED_SOURCED_EXIT"
EVIDENCE_REGISTERED_BENCHMARK: Final = "REGISTERED_BENCHMARK_INPUT"
EVIDENCE_BENCHMARK_PROXY: Final = "PROXY_SUBSTITUTED_LEVEL"

VALID_EVIDENCE_KINDS: Final[Mapping[str, frozenset[str]]] = {
    COVERAGE_CLASS_LISTINGS: frozenset({EVIDENCE_LISTING_INTERVAL}),
    COVERAGE_CLASS_IDENTITY: frozenset({EVIDENCE_UNIQUE_IDENTITY}),
    COVERAGE_CLASS_CLASSIFICATION: frozenset({EVIDENCE_TERMINAL_CLASSIFICATION}),
    COVERAGE_CLASS_PRICES: frozenset({EVIDENCE_EXACT_PIT_PRICE}),
    COVERAGE_CLASS_ACTIONS: frozenset({EVIDENCE_ACTION_RECORD, EVIDENCE_ACTION_COMPLETENESS}),
    COVERAGE_CLASS_ANCHORS: frozenset({EVIDENCE_FROZEN_ANCHOR}),
    COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS: frozenset(
        {EVIDENCE_EXACT_MARK, EVIDENCE_SETTLED_EXIT}
    ),
    COVERAGE_CLASS_BENCHMARKS: frozenset({EVIDENCE_REGISTERED_BENCHMARK}),
}

LISTING_STATUS_FORBIDDEN_PAYLOAD_KEYS: Final = frozenset(
    {
        "delisting_reason",
        "payment_date",
        "announced_payment_date",
        "merger",
        "consideration",
        "form_25",
        "allocation",
        "transaction_effective_at",
    }
)


def _item_key(coverage_class: str, subject_id: str, session: str) -> str:
    return f"{coverage_class}|{subject_id}|{session}"


@dataclass(frozen=True)
class CoverageRequirement:
    """One item the frozen run plan requires. It carries no verdict."""

    coverage_class: str
    subject_id: str
    session: str
    required_by: str

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
        if self.required_by not in CUTOFF_KINDS:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_CLASS,
                f"{self.required_by!r} is not a registered cutoff kind",
            )

    @property
    def item_key(self) -> str:
        return _item_key(self.coverage_class, self.subject_id, self.session)

    def to_json_dict(self) -> dict[str, str]:
        return {
            "coverage_class": self.coverage_class,
            "subject_id": self.subject_id,
            "session": self.session,
            "required_by": self.required_by,
        }


@dataclass(frozen=True)
class CoverageObservation:
    """Source-adapter evidence for one required item. It carries no verdict."""

    coverage_class: str
    subject_id: str
    session: str
    available_at: str
    source_kind: str
    source: str
    source_reference: str
    raw_artifact_sha256_grouped: str
    evidence_kind: str
    payload: Mapping[str, str]

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
        iso_instant(self.available_at, what="available_at")
        if self.source_kind not in SOURCE_KINDS:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_CLASS,
                f"{self.source_kind!r} is not a registered source kind",
            )
        token(self.evidence_kind, what="evidence_kind")
        if not self.source.strip() or self.source.strip() != self.source:
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_CLASS, "observation source must be non-empty"
            )
        if not self.source_reference.strip():
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_CLASS,
                "observation source_reference must be non-empty",
            )
        if not is_opaque_identifier(self.raw_artifact_sha256_grouped):
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_CLASS,
                "raw_artifact_sha256_grouped must be a grouped sha256",
            )
        object.__setattr__(self, "payload", dict(self.payload))

    @property
    def item_key(self) -> str:
        return _item_key(self.coverage_class, self.subject_id, self.session)


@dataclass(frozen=True)
class CoveragePlanView:
    """Bound plan inputs used to derive the coverage denominator.

    This is the composed walk-forward plan's requirement surface, not a smaller
    sample chosen after missingness is observed.
    """

    plan_sha256_grouped: str
    listings: tuple[tuple[str, str], ...]
    identity_keys: tuple[tuple[str, str], ...]
    classifications: tuple[tuple[str, str], ...]
    prices: tuple[tuple[str, str], ...]
    actions: tuple[tuple[str, str], ...]
    anchors: tuple[tuple[str, str], ...]
    held_marks: tuple[tuple[str, str], ...]
    benchmarks: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not is_opaque_identifier(self.plan_sha256_grouped):
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_CLASS,
                "plan_sha256_grouped must be a grouped sha256",
            )


@dataclass(frozen=True)
class CoverageProofPreregistration:
    """Freeze the proof inputs before any coverage fraction is measured."""

    code_commit: str
    tree_sha256_grouped: str
    composed_run_plan_hash: str
    decision_cutoff: str
    outcome_cutoff: str
    benchmark_id: str
    price_coordinate: str
    trade_eligible: bool
    denominator_sha256_grouped: str
    exclusion_policy: str

    def __post_init__(self) -> None:
        token(self.code_commit, what="code_commit")
        if not is_opaque_identifier(self.tree_sha256_grouped):
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_CLASS,
                "tree_sha256_grouped must be a grouped sha256",
            )
        if not is_opaque_identifier(self.composed_run_plan_hash):
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_CLASS,
                "composed_run_plan_hash must be a grouped sha256",
            )
        iso_instant(self.decision_cutoff, what="decision_cutoff")
        iso_instant(self.outcome_cutoff, what="outcome_cutoff")
        token(self.benchmark_id, what="benchmark_id")
        token(self.price_coordinate, what="price_coordinate")
        if self.trade_eligible is not False:
            raise CoverageAuditError(
                BLOCKED_CALLER_DECLARED_COVERAGE_STATE,
                "a coverage proof is not trade eligible",
            )
        if not is_opaque_identifier(self.denominator_sha256_grouped):
            raise CoverageAuditError(
                BLOCKED_UNREGISTERED_COVERAGE_CLASS,
                "denominator_sha256_grouped must be a grouped sha256",
            )
        token(self.exclusion_policy, what="exclusion_policy")


def denominator_sha256_grouped(requirements: Sequence[CoverageRequirement]) -> str:
    """Grouped digest of the frozen requirement set, independent of input order."""
    ordered = sorted((item.to_json_dict() for item in requirements), key=lambda row: str(row))
    return grouped_sha256(canonical_json_bytes({"requirements": ordered}))


def derive_coverage_requirements(view: CoveragePlanView) -> tuple[CoverageRequirement, ...]:
    """Emit one requirement per plan cell. Missingness cannot drop a class."""
    rows: list[CoverageRequirement] = []
    for coverage_class, pairs, required_by in (
        (COVERAGE_CLASS_LISTINGS, view.listings, CUTOFF_KIND_DECISION),
        (COVERAGE_CLASS_IDENTITY, view.identity_keys, CUTOFF_KIND_DECISION),
        (COVERAGE_CLASS_CLASSIFICATION, view.classifications, CUTOFF_KIND_DECISION),
        (COVERAGE_CLASS_PRICES, view.prices, CUTOFF_KIND_DECISION),
        (COVERAGE_CLASS_ACTIONS, view.actions, CUTOFF_KIND_DECISION),
        (COVERAGE_CLASS_ANCHORS, view.anchors, CUTOFF_KIND_DECISION),
        (COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS, view.held_marks, CUTOFF_KIND_OUTCOME),
        (COVERAGE_CLASS_BENCHMARKS, view.benchmarks, CUTOFF_KIND_DECISION),
    ):
        for subject_id, session in pairs:
            rows.append(
                CoverageRequirement(
                    coverage_class=coverage_class,
                    subject_id=subject_id,
                    session=session,
                    required_by=required_by,
                )
            )
    return tuple(sorted(rows, key=lambda item: item.item_key))


def assert_preregistration_matches(
    preregistration: CoverageProofPreregistration,
    requirements: Sequence[CoverageRequirement],
) -> None:
    """Refuse a measured denominator that is not the frozen one."""
    actual = denominator_sha256_grouped(requirements)
    if actual != preregistration.denominator_sha256_grouped:
        raise CoverageAuditError(
            BLOCKED_DENOMINATOR_CHANGED_AFTER_PREREGISTRATION,
            "the measured requirement set does not match the frozen preregistration",
        )


def _listing_covers(payload: Mapping[str, str], session: str) -> bool:
    start = payload.get("interval_start", "")
    end = payload.get("interval_end", "")
    if not start:
        return False
    iso_day(start, what="interval_start")
    if end:
        iso_day(end, what="interval_end")
        return start <= session < end
    return start <= session


def _state_for_observation(
    requirement: CoverageRequirement,
    observation: CoverageObservation,
    cutoff_policy: CutoffPolicy,
) -> str:
    if observation.evidence_kind == EVIDENCE_ACTION_QUERY_EMPTY:
        raise CoverageAuditError(
            BLOCKED_ACTION_ABSENCE_IS_NOT_COMPLETENESS,
            "absence of an action query result is not a completeness assertion",
            coverage_class=requirement.coverage_class,
            session=requirement.session,
        )
    if observation.evidence_kind == EVIDENCE_BENCHMARK_PROXY:
        raise CoverageAuditError(
            BLOCKED_IMPLICIT_BENCHMARK_PROXY,
            "a benchmark observation may not substitute a proxy identifier",
            coverage_class=requirement.coverage_class,
            session=requirement.session,
        )
    if requirement.coverage_class == COVERAGE_CLASS_LISTINGS:
        forbidden = LISTING_STATUS_FORBIDDEN_PAYLOAD_KEYS.intersection(observation.payload)
        if forbidden:
            raise CoverageAuditError(
                BLOCKED_LISTING_STATUS_NOT_EVENT_EVIDENCE,
                "LISTING_STATUS may evidence an active/delisted lifecycle interval, "
                f"not {sorted(forbidden)}",
                coverage_class=requirement.coverage_class,
                session=requirement.session,
            )
    available = iso_instant(observation.available_at, what="available_at")
    limit = cutoff_policy.cutoff_for(requirement.required_by)
    if available > limit:
        return ITEM_MISSING_AFTER_AVAILABILITY_CUTOFF
    allowed = VALID_EVIDENCE_KINDS[requirement.coverage_class]
    if observation.evidence_kind not in allowed:
        return ITEM_INVALID_FAILED_VALIDATION
    if requirement.coverage_class == COVERAGE_CLASS_LISTINGS and not _listing_covers(
        observation.payload, requirement.session
    ):
        return ITEM_INVALID_FAILED_VALIDATION
    if (
        requirement.coverage_class == COVERAGE_CLASS_PRICES
        and observation.payload.get("session") != requirement.session
    ):
        return ITEM_INVALID_FAILED_VALIDATION
    return ITEM_VALID


def join_coverage(
    requirements: Sequence[CoverageRequirement],
    *,
    observations: Sequence[CoverageObservation],
    cutoff_policy: CutoffPolicy,
) -> tuple[RequiredItem, ...]:
    """Join plan requirements to source observations. The auditor assigns state."""
    indexed: dict[str, CoverageRequirement] = {}
    for requirement in requirements:
        if requirement.item_key in indexed:
            raise CoverageAuditError(
                BLOCKED_DUPLICATE_COVERAGE_OBSERVATION,
                f"duplicate requirement {requirement.item_key}",
                coverage_class=requirement.coverage_class,
                session=requirement.session,
            )
        indexed[requirement.item_key] = requirement

    by_key: dict[str, CoverageObservation] = {}
    for candidate in observations:
        if candidate.item_key in by_key:
            raise CoverageAuditError(
                BLOCKED_DUPLICATE_COVERAGE_OBSERVATION,
                f"two observations were supplied for {candidate.item_key}",
                coverage_class=candidate.coverage_class,
                session=candidate.session,
            )
        if candidate.item_key not in indexed:
            raise CoverageAuditError(
                BLOCKED_ORPHAN_COVERAGE_OBSERVATION,
                f"observation {candidate.item_key} has no matching requirement",
                coverage_class=candidate.coverage_class,
                session=candidate.session,
            )
        by_key[candidate.item_key] = candidate

    items: list[RequiredItem] = []
    for key in sorted(indexed):
        requirement = indexed[key]
        matched = by_key.get(key)
        if matched is None:
            items.append(
                RequiredItem(
                    coverage_class=requirement.coverage_class,
                    subject_id=requirement.subject_id,
                    session=requirement.session,
                    state=ITEM_MISSING_NOT_SOURCED,
                )
            )
            continue
        state = _state_for_observation(requirement, matched, cutoff_policy)
        items.append(
            RequiredItem(
                coverage_class=requirement.coverage_class,
                subject_id=requirement.subject_id,
                session=requirement.session,
                state=state,
                source=matched.source,
                availability_time=matched.available_at,
            )
        )
    return tuple(items)


def build_sourced_coverage_audit(
    *,
    audit_id: str,
    analysis_cutoff: str,
    as_of: str,
    requirements: Sequence[CoverageRequirement],
    observations: Sequence[CoverageObservation],
    calendar: TradingCalendar | None = None,
    declared_items: Sequence[RequiredItem] = (),
    decision_cutoff: str | None = None,
    outcome_cutoff: str | None = None,
) -> CoverageAuditReport:
    """Build the coverage audit from plan requirements and source observations.

    ``declared_items`` exists only so a caller-supplied ``ITEM_VALID`` can be
    refused. Production callers omit it.
    """
    if declared_items:
        raise CoverageAuditError(
            BLOCKED_CALLER_DECLARED_COVERAGE_STATE,
            "a sourced coverage audit does not accept caller-declared item states; "
            "the auditor joins requirements to observations",
        )
    cutoff_policy = CutoffPolicy(
        decision_cutoff=analysis_cutoff if decision_cutoff is None else decision_cutoff,
        outcome_cutoff=analysis_cutoff if outcome_cutoff is None else outcome_cutoff,
    )
    items = join_coverage(
        requirements, observations=observations, cutoff_policy=cutoff_policy
    )
    return build_coverage_audit(
        audit_id=audit_id,
        analysis_cutoff=analysis_cutoff,
        as_of=as_of,
        required_items=items,
        calendar=calendar,
        decision_cutoff=decision_cutoff,
        outcome_cutoff=outcome_cutoff,
    )


__all__ = [
    "CoverageObservation",
    "CoveragePlanView",
    "CoverageProofPreregistration",
    "CoverageRequirement",
    "assert_preregistration_matches",
    "build_sourced_coverage_audit",
    "denominator_sha256_grouped",
    "derive_coverage_requirements",
    "join_coverage",
]
