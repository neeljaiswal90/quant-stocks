"""Composition ticket D: a deterministic composed walk-forward over the seven engines.

``KERNEL_ID`` ``QME-COMPOSITION-COMPOSED-WALK-FORWARD-V1``;
``SCHEMA_VERSION`` ``qme.composed_walk_forward.v1``;
``ticket_id`` ``PENDING_OWNER_ASSIGNMENT`` (composition ticket D under gate
NEE-108, lead plan 2026-08-25).

This module ORCHESTRATES composition ticket C
(:func:`qme.experiments.composed_fold_v1.compose_fold`) across a schedule-ordered
sequence of folds and reuses NEE-134
(:mod:`qme.experiments.walk_forward_v1`) for its publication and type-wall
primitives. It NEVER reimplements any engine's scoring, screening, weighting,
accounting, costing, benchmarking, or calendar logic, and it never re-derives a
composed fold: every fold outcome is a CONSUMED
:class:`~qme.experiments.composed_fold_v1.ComposedFoldResult`, and the
publication mechanics (path confinement, durable exclusive-create writes, directory
fsync, atomic no-clobber rename) are :mod:`walk_forward_v1`'s own primitives,
called -- not copied.

What this lane adds over one fold:

1. **Schedule order.** Folds carry a strictly-increasing ``event_ordinal`` and are
   run in that order. A caller may present them shuffled; the driver content-sorts
   by ordinal before anything else, so the run identity is permutation-invariant.
2. **Cross-fold ledger-state carry.** ``compose_fold`` exposes the execution
   engine's IMMUTABLE closing portfolio -- cash, holdings (``positions_plus``),
   receivables, published tax lots (``open_lots``), and any fired corporate-action
   ``*_after_payment`` state. The SUCCESSOR fold OPENS on the predecessor's exposed
   CLOSING cash, positions, and receivables -- consumed, not caller-declared. The
   carry is enforced ledger-to-ledger over CONSUMED figures -- the successor's
   engine-computed ``initial_nav`` versus the predecessor's ``final_nav`` for the
   book value, and the successor's consumed opening cash / held positions /
   receivables versus the predecessor's ``cash_plus`` / ``positions_plus`` /
   ``receivables_plus`` for the composition of that value. A successor whose consumed
   opening NAV does not witness its predecessor's close degrades
   (``BLOCKED_LEDGER_STATE_CARRY_BROKEN``); one whose consumed opening composition
   (cash/positions/receivables) is missing, altered, reordered, duplicated, or
   otherwise incompatible with the predecessor's close degrades
   (``BLOCKED_POSITION_STATE_CARRY_BROKEN``) -- catching a NAV-preserving position
   tamper the book-value check alone cannot; a successor of a fold that produced NO
   close cannot fabricate a carry and degrades
   (``BLOCKED_PREDECESSOR_FOLD_DEGRADED_NO_CARRY``). **Exact tax-lot carry is NOT
   supported.** The predecessor's ``open_lots`` are bound into the carried-state
   identity only as TAMPER-EVIDENCE; lot cost basis and acquisition are not threaded
   into a successor (the read-only execution engine has no incoming-lot interface),
   so a successor consuming a predecessor that CLOSED holding non-empty lots fails
   closed (``BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED``) before it is admitted valid,
   pending a future execution-engine incoming-lot interface. A pure cash carry
   (empty predecessor lots) is unaffected. This carry is a mechanical property of
   TEST_CONSTRUCTED inputs, never a readiness claim.
   The published result is DEEP-FROZEN. Execution
   (:func:`execute_composed_walk_forward`) returns ONLY that read-only
   :class:`ComposedWalkForwardResult`; it carries no caller-usable publication receipt.
   The single supported PUBLIC publication entry is
   :func:`run_and_publish_composed_walk_forward`: it takes a PLAN and a runs root, runs
   the engines internally, and returns the published path plus the read-only result. It
   NEVER accepts a caller-supplied result, run, or receipt. Internally it mints a PRIVATE
   ``_PublicationReceipt`` from the GENUINE engine content -- the run identity, the DERIVED
   ``run_id_hex`` (the sole authority for the published directory name), and the expected
   grouped sha256 of the exact canonical bytes of every table file and ``manifest.json`` --
   and verifies both the in-memory result and the STAGED BYTES ON DISK against it
   immediately before the atomic rename, so a staged-file edit after staging, a
   ``run_id_hex`` swap, or a table-content replace fails closed
   (``BLOCKED_STAGED_ARTIFACT_TAMPERED`` / ``BLOCKED_RESULT_IDENTITY_TAMPERED``) before
   anything is published.

   **Trust boundary (the achievable public boundary, honestly stated).** This closes the
   SUPPORTED-PUBLIC-API trust boundary; it does NOT claim in-process cryptographic trust.
   Because :func:`run_and_publish_composed_walk_forward` is the ONLY SUPPORTED PUBLIC
   PUBLICATION ENTRY and never accepts caller-supplied content, a public-API caller
   CANNOT PUBLISH CALLER-SUPPLIED content: the output is strictly engine-derived, with no
   window to substitute a result between execution and publication. The private
   publication machinery (the underscored ``_PublicationReceipt`` / ``_ComposedWalkForwardRun``
   bundle and the ``_stage_run`` / ``_commit_run`` / ``_publish_run`` helpers) is a
   DETERMINISTIC function of the result -- tamper-EVIDENCE for a genuine pair, NOT an
   independent execution-captured authority. Arbitrary access to those underscored
   internals is OUT OF CONTRACT: in one deterministic in-process library there is no
   secret with which to bind an authority a result-holder cannot reproduce, so protection
   against MALICIOUS SAME-PROCESS CODE that reaches the internals requires a SEPARATE
   TRUSTED PROCESS OR EXTERNAL SIGNING AUTHORITY, not an in-process seal.
3. **Hash-chained fold identities.** Each fold's chain link binds its
   predecessor's chain hash and this fold's identity material (a valid fold's
   ``result_identity``; a degraded fold's ``fold_id`` and reason). Reordering or
   tampering any fold changes that link and every link after it -- a tamper-evident
   chain whose head is bound into the derived result identity, never the manifest.
4. **Run identity over bound inputs only.** ``run_id = SHA256(bound-input
   manifest)`` where the manifest binds each fold's ``fold_id`` (itself a
   bound-input digest from ``compose_fold``), the shared schedule/calendar/mode
   identities, the authorized-fold set, and the seven engine identities -- and NO
   derived artifact. The carry links, the chain head, and every ``result_identity``
   are DERIVED and bind only into ``result_identity_document`` -- never back into
   the manifest, so ``run_id`` is not circular. A wall clock lands only in a
   provenance block excluded from every identity.

No production, prospective-consumption, empirical-performance, alpha,
capacity-value, production-readiness, or live-order claim is made anywhere.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from qme.experiments import composed_fold_v1 as cf
from qme.experiments import walk_forward_v1 as wf
from qme.foundation.lineage import canonical_json_bytes

KERNEL_ID: Final = "QME-COMPOSITION-COMPOSED-WALK-FORWARD-V1"
SCHEMA_VERSION: Final = "qme.composed_walk_forward.v1"
MANIFEST_SCHEMA_VERSION: Final = "qme.composed_walk_forward_run_manifest.v1"
OUTPUT_SCHEMA_VERSION: Final = "qme.composed_walk_forward_outputs.v1"
TICKET_ID: Final = cf.TICKET_ID

_MODULE_FILENAME: Final = "composed_walk_forward_v1.py"
_MODULE_RELATIVE: Final = "qme/experiments/composed_walk_forward_v1.py"

#: No result of this run is a production, prospective, empirical, alpha,
#: capacity-value, production-readiness, review, registration, or live-order claim.
NON_CLAIMS: Final[Mapping[str, bool]] = {
    "alpha_demonstrated": False,
    "capacity_value_established": False,
    "empirical_performance_measured": False,
    "exact_lot_carry_supported": False,
    "freeze_blocker_changed": False,
    "independent_review_recorded": False,
    "live_order_authority": False,
    "owner_registration_recorded": False,
    "position_level_continuity_established": False,
    "production_deployment_authorized": False,
    "production_ready": False,
    "prospective_observations_consumable": False,
}

# ---------------------------------------------------------------------------
# Typed terminal states
# ---------------------------------------------------------------------------

PARTITION_VALID: Final = "COMPOSED_WALK_FORWARD_PARTITION_VALID"
PARTITION_DEGRADED: Final = "COMPOSED_WALK_FORWARD_PARTITION_DEGRADED"
PARTITION_STATES: Final[tuple[str, ...]] = (PARTITION_DEGRADED, PARTITION_VALID)

RUN_COMPLETED_WITH_VALID_PARTITIONS: Final = (
    "COMPOSED_WALK_FORWARD_RUN_COMPLETED_WITH_VALID_PARTITIONS"
)
RUN_COMPLETED_NO_VALID_PARTITIONS: Final = (
    "COMPOSED_WALK_FORWARD_RUN_COMPLETED_NO_VALID_PARTITIONS"
)

#: The carry disposition recorded on every chain link.
CARRY_GENESIS: Final = "CARRY_GENESIS"
CARRY_CONTINUOUS: Final = "CARRY_CONTINUOUS"
CARRY_BROKEN: Final = "CARRY_BROKEN"
CARRY_POSITION_BROKEN: Final = "CARRY_POSITION_BROKEN"
#: A successor consuming a predecessor that closed holding NON-EMPTY tax lots: exact
#: lot carry (shares + basis + acquisition) cannot be achieved without an incoming-
#: lot execution-engine interface, so the successor fails closed rather than claim
#: CARRY_CONTINUOUS (see ``BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED``).
CARRY_LOT_CARRY_UNSUPPORTED: Final = "CARRY_LOT_CARRY_UNSUPPORTED"
CARRY_PREDECESSOR_DEGRADED: Final = "CARRY_PREDECESSOR_DEGRADED"
CARRY_NOT_ATTEMPTED_UNAUTHORIZED: Final = "CARRY_NOT_ATTEMPTED_UNAUTHORIZED"
CARRY_NOT_ATTEMPTED_FOLD_DEGRADED: Final = "CARRY_NOT_ATTEMPTED_FOLD_DEGRADED"
CARRY_STATES: Final[tuple[str, ...]] = (
    CARRY_BROKEN,
    CARRY_CONTINUOUS,
    CARRY_GENESIS,
    CARRY_LOT_CARRY_UNSUPPORTED,
    CARRY_NOT_ATTEMPTED_FOLD_DEGRADED,
    CARRY_NOT_ATTEMPTED_UNAUTHORIZED,
    CARRY_POSITION_BROKEN,
    CARRY_PREDECESSOR_DEGRADED,
)

#: Fail-closed states raised by THIS driver. States that belong to an orchestrated
#: engine (or to composition ticket C) are surfaced VERBATIM through the retained
#: degraded partitions, never renamed. Three run-directory states, the aggregation
#: wall state, the fold-authorization state, the calendar-binding state, and the
#: egress state are REUSED from :mod:`walk_forward_v1` verbatim (identical string
#: objects), so the two lanes cannot drift.
BLOCKED_CALENDAR_BINDING_MISMATCH: Final = wf.BLOCKED_CALENDAR_BINDING_MISMATCH
BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE: Final = wf.BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE
BLOCKED_DUPLICATE_FOLD_ORDINAL: Final = "BLOCKED_DUPLICATE_FOLD_ORDINAL"
BLOCKED_EMPTY_FOLD_SCHEDULE: Final = "BLOCKED_EMPTY_FOLD_SCHEDULE"
BLOCKED_FOLD_BOUNDARY_SESSIONS_DISAGREE: Final = "BLOCKED_FOLD_BOUNDARY_SESSIONS_DISAGREE"
BLOCKED_FOLD_NOT_AUTHORIZED: Final = wf.BLOCKED_FOLD_NOT_AUTHORIZED
BLOCKED_FOLD_ORDINAL_MISMATCH: Final = "BLOCKED_FOLD_ORDINAL_MISMATCH"
#: A position-bearing successor cannot inherit exact tax lots (shares+basis+
#: acquisition) because the read-only execution engine exposes no incoming-lot
#: interface. Fails closed BEFORE the fold is admitted valid (owner-authorized
#: remediation): predecessor open_lots are bound only as tamper-evidence, not carried.
BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED: Final = "BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED"
BLOCKED_INCONSISTENT_SHARED_MODES: Final = "BLOCKED_INCONSISTENT_SHARED_MODES"
BLOCKED_INCONSISTENT_SHARED_SCHEDULE: Final = "BLOCKED_INCONSISTENT_SHARED_SCHEDULE"
BLOCKED_LEDGER_STATE_CARRY_BROKEN: Final = "BLOCKED_LEDGER_STATE_CARRY_BROKEN"
BLOCKED_MALFORMED_COMPOSED_WALK_FORWARD_INPUT: Final = (
    "BLOCKED_MALFORMED_COMPOSED_WALK_FORWARD_INPUT"
)
BLOCKED_NETWORK_EGRESS_REACHABLE: Final = wf.BLOCKED_NETWORK_EGRESS_REACHABLE
BLOCKED_POSITION_STATE_CARRY_BROKEN: Final = "BLOCKED_POSITION_STATE_CARRY_BROKEN"
BLOCKED_PREDECESSOR_FOLD_DEGRADED_NO_CARRY: Final = (
    "BLOCKED_PREDECESSOR_FOLD_DEGRADED_NO_CARRY"
)
#: The in-memory result presented to publication does not witness the private
#: ``_PublicationReceipt``: its ``run_id``, its ``run_id_hex`` field (DERIVED at publish
#: and rejected if it disagrees), its bound-input manifest, a per-table byte-hash, or the
#: manifest byte-hash diverged from the receipt. Publication recomputes each from the
#: result and refuses before writing anything. The published directory name is ALWAYS the
#: DERIVED ``run-<hex>``.
BLOCKED_RESULT_IDENTITY_TAMPERED: Final = "BLOCKED_RESULT_IDENTITY_TAMPERED"
BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT: Final = wf.BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT
BLOCKED_RUN_DIRECTORY_EXISTS: Final = wf.BLOCKED_RUN_DIRECTORY_EXISTS
#: Unified-session-axis refusals raised BEFORE any fold runs (Part 5).
BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH: Final = (
    cf.BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH
)
BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH: Final = cf.BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH
#: The EXACT staged file set or the staged BYTES ON DISK do not witness the private
#: ``_PublicationReceipt`` immediately before the atomic rename: a
#: staged table/manifest whose re-read bytes hash differently, a missing or extra file,
#: or an on-disk manifest whose own bound per-table hashes disagree. Fails closed BEFORE
#: any rename/publish write, so an edit to a staged file after staging publishes nothing.
BLOCKED_STAGED_ARTIFACT_TAMPERED: Final = "BLOCKED_STAGED_ARTIFACT_TAMPERED"

COMPOSED_WALK_FORWARD_FAIL_CLOSED_STATES: Final[tuple[str, ...]] = (
    BLOCKED_CALENDAR_BINDING_MISMATCH,
    BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE,
    BLOCKED_DUPLICATE_FOLD_ORDINAL,
    BLOCKED_EMPTY_FOLD_SCHEDULE,
    BLOCKED_FOLD_BOUNDARY_SESSIONS_DISAGREE,
    BLOCKED_FOLD_NOT_AUTHORIZED,
    BLOCKED_FOLD_ORDINAL_MISMATCH,
    BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED,
    BLOCKED_INCONSISTENT_SHARED_MODES,
    BLOCKED_INCONSISTENT_SHARED_SCHEDULE,
    BLOCKED_LEDGER_STATE_CARRY_BROKEN,
    BLOCKED_MALFORMED_COMPOSED_WALK_FORWARD_INPUT,
    BLOCKED_NETWORK_EGRESS_REACHABLE,
    BLOCKED_POSITION_STATE_CARRY_BROKEN,
    BLOCKED_PREDECESSOR_FOLD_DEGRADED_NO_CARRY,
    BLOCKED_RESULT_IDENTITY_TAMPERED,
    BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT,
    BLOCKED_RUN_DIRECTORY_EXISTS,
    BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH,
    BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH,
    BLOCKED_STAGED_ARTIFACT_TAMPERED,
)


class ComposedWalkForwardError(ValueError):
    """A typed fail-closed refusal raised by this driver itself.

    ``state`` is one of :data:`COMPOSED_WALK_FORWARD_FAIL_CLOSED_STATES`.
    """

    def __init__(
        self,
        state: str,
        message: str,
        *,
        event_ordinal: int | None = None,
        fold_id: str | None = None,
        path: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(f"{state}: {message}")
        self.state = state
        self.message = message
        self.event_ordinal = event_ordinal
        self.fold_id = fold_id
        self.path = path
        self.detail = detail

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "message": self.message,
            "event_ordinal": self.event_ordinal,
            "fold_id": self.fold_id,
            "path": self.path,
            "detail": self.detail,
        }


def assert_states_are_complete() -> None:
    """Prove the fail-closed tuple is sorted, unique, and BLOCKED-only."""

    if list(COMPOSED_WALK_FORWARD_FAIL_CLOSED_STATES) != sorted(
        set(COMPOSED_WALK_FORWARD_FAIL_CLOSED_STATES)
    ):
        raise ComposedWalkForwardError(
            BLOCKED_MALFORMED_COMPOSED_WALK_FORWARD_INPUT,
            "the fail-closed state tuple must be sorted and unique",
        )
    for state in COMPOSED_WALK_FORWARD_FAIL_CLOSED_STATES:
        if not state.startswith("BLOCKED_"):
            raise ComposedWalkForwardError(
                BLOCKED_MALFORMED_COMPOSED_WALK_FORWARD_INPUT,
                f"fail-closed state {state!r} must be a BLOCKED_ state",
            )
    if set(PARTITION_STATES) != {PARTITION_DEGRADED, PARTITION_VALID}:
        raise ComposedWalkForwardError(
            BLOCKED_MALFORMED_COMPOSED_WALK_FORWARD_INPUT,
            "the partition-state set is incomplete",
        )


# ---------------------------------------------------------------------------
# Grouped digests -- REUSED verbatim from walk_forward_v1 (public helpers)
# ---------------------------------------------------------------------------


def _document_digest(document: Mapping[str, Any]) -> str:
    return wf.grouped_document_digest(document)


def _bytes_digest(payload: bytes) -> str:
    return wf.group_sha256(payload)


#: The fixed genesis link for the fold hash-chain (no predecessor).
GENESIS_CHAIN_HASH: Final = _document_digest(
    {"genesis": KERNEL_ID, "schema_version": SCHEMA_VERSION}
)


# ---------------------------------------------------------------------------
# Deep-freeze / thaw for published result content (P1-1 fix (1))
# ---------------------------------------------------------------------------


def _freeze(value: Any) -> Any:
    """Recursively render a JSON-ish value IMMUTABLE.

    Every mapping becomes a :class:`types.MappingProxyType` over a private dict and
    every sequence a tuple, so no caller can mutate a completed result's published
    table rows or any nested structure (carry records, lineage, closing/opening
    portfolio documents, chain links). Scalars pass through unchanged. ``_plain``
    thaws the structure back to mutable ``dict``/``list`` for serialization.
    """

    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    """Recursively render a (possibly frozen) value as mutable ``dict``/``list``.

    The inverse of :func:`_freeze`: a deep copy whose mappings are ordinary dicts
    and whose sequences are lists, so ``json.dumps`` can serialize it and callers
    receive a mutable copy that does not alias the frozen original.
    """

    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Bound-input plan (schedule of composed folds)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldSlot:
    """One scheduled fold: its ``event_ordinal`` and its complete composed-fold inputs."""

    event_ordinal: int
    inputs: cf.ComposedFoldInputs


@dataclass(frozen=True)
class ComposedWalkForwardPlan:
    """A complete, locally-pinned composed walk-forward specification.

    Every field is a bound input. The folds share one schedule policy, calendar,
    range, and set of execution modes -- they differ only in ``event_ordinal`` and
    the per-fold ledger world (opening state, sessions, ids). ``authorized_fold_ids``
    names the ``compose_fold`` fold ids permitted to become valid partitions; it
    shapes the produced output, so it is a bound input class the run identity binds
    (two runs that authorize different folds must not collide).
    """

    folds: tuple[FoldSlot, ...]
    authorized_fold_ids: frozenset[str]
    session_axis: cf.SessionAxis
    sample_fold_ordinal: int

    @property
    def calendar_id(self) -> str:
        return self.session_axis.calendar_id

    @property
    def calendar_sha256_grouped(self) -> str:
        return self.session_axis.calendar_sha256_grouped

    def __post_init__(self) -> None:
        if not self.folds:
            raise ComposedWalkForwardError(
                BLOCKED_EMPTY_FOLD_SCHEDULE,
                "a composed walk-forward run must name at least one fold",
            )
        ordinals = [slot.event_ordinal for slot in self.folds]
        if len(set(ordinals)) != len(ordinals):
            raise ComposedWalkForwardError(
                BLOCKED_DUPLICATE_FOLD_ORDINAL,
                f"fold event ordinals must be unique; got {sorted(ordinals)}",
            )
        for name, value in (
            ("calendar_id", self.session_axis.calendar_id),
            ("calendar_sha256_grouped", self.session_axis.calendar_sha256_grouped),
            ("timezone", self.session_axis.timezone),
            ("session_ids_sha256_grouped", self.session_axis.session_ids_sha256_grouped),
        ):
            if not value or value.strip() != value:
                raise ComposedWalkForwardError(
                    BLOCKED_MALFORMED_COMPOSED_WALK_FORWARD_INPUT,
                    f"session_axis.{name} must be a non-empty, unpadded value",
                )
        first = self.folds[0].inputs
        for slot in self.folds:
            if slot.inputs.schedule.event_ordinal != slot.event_ordinal:
                raise ComposedWalkForwardError(
                    BLOCKED_FOLD_ORDINAL_MISMATCH,
                    "a fold slot's event_ordinal must equal its schedule binding's "
                    f"event_ordinal (slot {slot.event_ordinal}, "
                    f"schedule {slot.inputs.schedule.event_ordinal})",
                    event_ordinal=slot.event_ordinal,
                )
            _assert_shared_schedule(first.schedule, slot.inputs.schedule)
            _assert_shared_modes(first.execution, slot.inputs.execution)
            _assert_shared_session_axis(self.session_axis, slot.inputs.session_axis)

    def ordered_slots(self) -> tuple[FoldSlot, ...]:
        """Folds in deterministic schedule order (by strictly-increasing ordinal)."""

        return tuple(sorted(self.folds, key=lambda slot: slot.event_ordinal))

    def schedule_identity(self) -> dict[str, Any]:
        binding = self.folds[0].inputs.schedule
        calendar = binding.calendar
        return {
            "schedule_policy_id": binding.schedule_policy_id,
            "range_start": binding.range_start,
            "range_end": binding.range_end,
            "lookback_sessions": binding.lookback_sessions,
            "skip_sessions": binding.skip_sessions,
            "calendar_id": calendar.calendar_id,
            "calendar_sha256_grouped": calendar.bytes_sha256_grouped,
            "registered_policy_ids": sorted(
                policy.policy_id for policy in binding.schedule_policies
            ),
        }

    def modes_document(self) -> dict[str, str]:
        execution = self.folds[0].inputs.execution
        policy = execution.transaction_tax_policy
        return {
            "share_mode": execution.share_mode,
            "regulatory_fee_mode": execution.regulatory_fee_mode,
            "cost_policy_id": execution.cost_policy_id,
            "transaction_tax_policy_id": policy.policy_id,
            "transaction_tax_policy_sha256": policy.policy_sha256,
        }


def _assert_shared_schedule(
    first: cf.ScheduleBinding, other: cf.ScheduleBinding
) -> None:
    if (
        first.schedule_policy_id != other.schedule_policy_id
        or first.range_start != other.range_start
        or first.range_end != other.range_end
        or first.lookback_sessions != other.lookback_sessions
        or first.skip_sessions != other.skip_sessions
        or first.calendar.calendar_id != other.calendar.calendar_id
        or first.calendar.bytes_sha256_grouped != other.calendar.bytes_sha256_grouped
    ):
        raise ComposedWalkForwardError(
            BLOCKED_INCONSISTENT_SHARED_SCHEDULE,
            "every fold must run under one shared schedule policy, range, offsets, "
            "and calendar; they may differ only in event_ordinal",
        )


def _assert_shared_modes(
    first: cf.ExecutionBinding, other: cf.ExecutionBinding
) -> None:
    if (
        first.share_mode != other.share_mode
        or first.regulatory_fee_mode != other.regulatory_fee_mode
        or first.cost_policy_id != other.cost_policy_id
        or first.transaction_tax_policy.policy_id != other.transaction_tax_policy.policy_id
        or first.transaction_tax_policy.policy_sha256
        != other.transaction_tax_policy.policy_sha256
    ):
        raise ComposedWalkForwardError(
            BLOCKED_INCONSISTENT_SHARED_MODES,
            "every fold must share one set of execution modes (share/fee/cost/tax)",
        )


def _assert_shared_session_axis(axis: cf.SessionAxis, other: cf.SessionAxis) -> None:
    """Refuse a fold whose declared session axis disagrees with the shared one.

    Every fold must run on the ONE shared calendar/session axis (id + grouped
    hash + timezone + ordered session vector). A fold whose boundary sessions live
    on a different axis fails closed BEFORE any fold runs.
    """

    if axis != other:
        raise ComposedWalkForwardError(
            BLOCKED_FOLD_BOUNDARY_SESSIONS_DISAGREE,
            "every fold must declare the ONE shared session axis (calendar id, "
            "grouped hash, timezone, and ordered session vector); a fold whose "
            "boundary sessions live on a different axis is refused",
        )


def assert_declared_calendar_witnesses_injected(
    trading_calendar: Any, plan: ComposedWalkForwardPlan
) -> None:
    """Refuse unless the INJECTED calendar witnesses the plan's DECLARED session axis.

    Mirrors ``walk_forward_v1.assert_declared_calendar_witnesses_injected`` and
    extends it to the WHOLE session axis: the run identity binds the plan's
    ``session_axis`` (calendar id + grouped hash + timezone + ordered session
    vector), while the calendar that actually orders the folds arrives separately.
    This asserts the injected calendar witnesses every axis field exactly, with a
    STABLE TYPED reason per class of disagreement, before any fold runs.
    """

    axis = plan.session_axis
    observed_id = getattr(trading_calendar, "calendar_id", None)
    observed_sha = getattr(trading_calendar, "bytes_sha256_grouped", None)
    if observed_id != axis.calendar_id or observed_sha != axis.calendar_sha256_grouped:
        raise ComposedWalkForwardError(
            BLOCKED_CALENDAR_BINDING_MISMATCH,
            "the injected trading calendar does not witness the plan's declared "
            f"calendar identity (declared {axis.calendar_id!r}/"
            f"{axis.calendar_sha256_grouped!r}, injected {observed_id!r}/{observed_sha!r})",
            detail=str(observed_id),
        )
    observed_tz = getattr(trading_calendar, "timezone", None)
    if observed_tz != axis.timezone:
        raise ComposedWalkForwardError(
            BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH,
            "the injected trading calendar's timezone does not witness the plan's "
            f"declared axis timezone (declared {axis.timezone!r}, injected {observed_tz!r})",
            detail=str(observed_tz),
        )
    observed_vector = getattr(trading_calendar, "session_ids_sha256_grouped", None)
    if observed_vector != axis.session_ids_sha256_grouped:
        raise ComposedWalkForwardError(
            BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH,
            "the injected trading calendar's ordered session vector does not witness "
            "the plan's declared axis session vector",
            detail=str(observed_vector),
        )


def assert_network_egress_denied(
    repository_root: Path, *, entry_module_source: Path | None = None
) -> None:
    """Refuse the run if any transport is reachable from this driver's source closure.

    REUSES ``walk_forward_v1.transport_modules_reachable`` -- the same first-party
    import-closure walk NEE-134 uses -- starting from THIS module.
    """

    entry = entry_module_source or repository_root.joinpath(_MODULE_RELATIVE)
    reachable = wf.transport_modules_reachable(repository_root, entry_module_source=entry)
    if reachable:
        raise ComposedWalkForwardError(
            BLOCKED_NETWORK_EGRESS_REACHABLE,
            "a network transport is reachable from the composed walk-forward driver; "
            f"local execution requires none: {', '.join(reachable)}",
            detail=",".join(reachable),
        )


# ---------------------------------------------------------------------------
# Engine identities and lineage
# ---------------------------------------------------------------------------


def _engine_identity_document(
    identities: Mapping[str, cf.EngineIdentity],
) -> dict[str, Any]:
    return {name: identities[name].to_json_dict() for name in sorted(identities)}


def _schema_descriptor() -> dict[str, Any]:
    return {
        "bound_input_manifest_fields": sorted(BOUND_INPUT_MANIFEST_FIELDS),
        "carry_states": list(CARRY_STATES),
        "fail_closed_states": list(COMPOSED_WALK_FORWARD_FAIL_CLOSED_STATES),
        "kernel_id": KERNEL_ID,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "output_tables": list(OUTPUT_TABLE_NAMES),
        "partition_states": list(PARTITION_STATES),
        "schema_version": SCHEMA_VERSION,
    }


def _run_lineage(
    *,
    run_id: str,
    identities: Mapping[str, cf.EngineIdentity],
    repository_root: Path,
) -> dict[str, str]:
    source = repository_root.joinpath("qme", "experiments", _MODULE_FILENAME)
    return {
        "input_sha256_grouped": run_id,
        "config_sha256_grouped": _document_digest(_engine_identity_document(identities)),
        "code_sha256_grouped": _bytes_digest(source.read_bytes()),
        "schema_sha256_grouped": _document_digest(_schema_descriptor()),
    }


# ---------------------------------------------------------------------------
# Bound-input manifest (inputs only) and the run identity
# ---------------------------------------------------------------------------

#: The exact top-level field set of the bound-input manifest. Every entry is an
#: INPUT: no derived artifact (a result_identity, a ledger identity, a final NAV,
#: a chain hash, a carry link, or a partition index) may ever appear here.
BOUND_INPUT_MANIFEST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "kernel_id",
        "ticket_id",
        "manifest_schema_version",
        "schedule_identity",
        "modes",
        "session_axis",
        "sample_fold_ordinal",
        "authorized_fold_ids",
        "ordered_folds",
        "engine_identities",
    }
)


def fold_id_of_slot(
    slot: FoldSlot, *, identities: Mapping[str, cf.EngineIdentity]
) -> str:
    """The ``compose_fold`` bound-input digest of a slot's inputs (a bound input).

    Pure over the slot's inputs and the engine identities -- it never runs the
    fold, so the run identity is computable from bound inputs alone.
    """

    manifest = cf.bound_input_manifest(slot.inputs, identities=identities)
    return cf.fold_id_of(manifest)


def bound_input_manifest(
    plan: ComposedWalkForwardPlan,
    *,
    identities: Mapping[str, cf.EngineIdentity],
) -> dict[str, Any]:
    """Assemble the canonical BOUND-INPUT manifest. Contains INPUTS only.

    Each fold contributes ``(event_ordinal, fold_id)`` in schedule order, where
    ``fold_id`` is ``compose_fold``'s own bound-input digest of that fold -- a
    bound input, never a derived artifact.
    """

    ordered = plan.ordered_slots()
    ordered_folds = [
        {"event_ordinal": slot.event_ordinal, "fold_id": fold_id_of_slot(slot, identities=identities)}
        for slot in ordered
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "kernel_id": KERNEL_ID,
        "ticket_id": TICKET_ID,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "schedule_identity": plan.schedule_identity(),
        "modes": plan.modes_document(),
        "session_axis": plan.session_axis.to_json_dict(),
        "sample_fold_ordinal": plan.sample_fold_ordinal,
        "authorized_fold_ids": sorted(plan.authorized_fold_ids),
        "ordered_folds": ordered_folds,
        "engine_identities": _engine_identity_document(identities),
    }


def run_id_of(manifest: Mapping[str, Any]) -> str:
    """The run identity: grouped SHA256 over the canonical bound-input manifest."""

    return _bytes_digest(canonical_json_bytes(manifest))


def run_id_hex_of(manifest: Mapping[str, Any]) -> str:
    """The raw sha256 hexdigest of the manifest (a filesystem-safe run name)."""

    return wf.sha256_hex(canonical_json_bytes(manifest))


# ---------------------------------------------------------------------------
# The fold hash-chain
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainLink:
    """One tamper-evident link binding its predecessor and this fold's identity.

    ``carried_state_identity`` is the predecessor fold's exposed CLOSING-state
    carry identity (a grouped digest over its exact cash + held positions + lots +
    receivables + corporate-action state), bound into the chain hash ALONGSIDE
    ``predecessor_closing_nav``, so tampering any carried field changes this link
    and every successor plus the derived run identity.
    """

    event_ordinal: int
    fold_id: str
    partition_state: str
    predecessor_chain_hash: str
    chain_hash: str
    carried_in_nav: str
    predecessor_closing_nav: str | None
    carried_state_identity: str | None
    carry_state: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "carried_in_nav": self.carried_in_nav,
            "carried_state_identity": self.carried_state_identity,
            "carry_state": self.carry_state,
            "chain_hash": self.chain_hash,
            "event_ordinal": self.event_ordinal,
            "fold_id": self.fold_id,
            "partition_state": self.partition_state,
            "predecessor_chain_hash": self.predecessor_chain_hash,
            "predecessor_closing_nav": self.predecessor_closing_nav,
        }


def _chain_hash(
    *,
    predecessor_chain_hash: str,
    event_ordinal: int,
    fold_id: str,
    partition_state: str,
    fold_identity_material: Mapping[str, Any],
) -> str:
    """Bind the predecessor's chain hash and this fold's identity into a new link hash."""

    return _document_digest(
        {
            "fold_identity_material": dict(fold_identity_material),
            "link": {
                "event_ordinal": event_ordinal,
                "fold_id": fold_id,
                "partition_state": partition_state,
            },
            "predecessor_chain_hash": predecessor_chain_hash,
        }
    )


# ---------------------------------------------------------------------------
# The partition type wall (disjoint frozen types; a degraded partition can never
# be coerced into the valid aggregate -- a mypy --strict wall, the same discipline
# walk_forward_v1 enforces, applied to composed folds).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidComposedPartition:
    """One fold that reached a VALID composed result AND continued the ledger carry.

    The ONLY input :func:`aggregate_valid_partitions` accepts. It shares no base
    class with :class:`DegradedComposedPartition`.
    """

    event_ordinal: int
    fold: cf.ValidComposedFold
    link: ChainLink

    state: str = PARTITION_VALID

    @property
    def fold_id(self) -> str:
        return self.fold.fold_id

    def closing_nav(self) -> str:
        return str(self.fold.ledger_figures["final_nav"])

    def identity_document(self) -> dict[str, Any]:
        return {
            "chain_hash": self.link.chain_hash,
            "event_ordinal": self.event_ordinal,
            "fold_id": self.fold_id,
            "result_identity": self.fold.result_identity,
            "state": self.state,
        }


@dataclass(frozen=True)
class DegradedComposedPartition:
    """One fold retained degraded: its typed reason codes, and any fold it produced.

    Exposes no valid composed fold; there is no method, and no supertype, by which
    it can stand in for a :class:`ValidComposedPartition`.
    """

    event_ordinal: int
    fold_id: str
    reason_codes: tuple[str, ...]
    fold_result: cf.ComposedFoldResult | None
    link: ChainLink

    state: str = PARTITION_DEGRADED

    def identity_document(self) -> dict[str, Any]:
        return {
            "chain_hash": self.link.chain_hash,
            "event_ordinal": self.event_ordinal,
            "fold_id": self.fold_id,
            "reason_codes": list(self.reason_codes),
            "state": self.state,
        }


ComposedPartition = ValidComposedPartition | DegradedComposedPartition


def require_valid_partition(partition: ComposedPartition) -> ValidComposedPartition:
    """Narrow a partition to a valid one, or refuse. The only runtime narrowing."""

    if isinstance(partition, ValidComposedPartition):
        return partition
    raise ComposedWalkForwardError(
        BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE,
        f"partition {partition.fold_id!r} is degraded and cannot be treated as valid",
        event_ordinal=partition.event_ordinal,
        fold_id=partition.fold_id,
    )


@dataclass(frozen=True)
class ValidComposedAggregate:
    """The aggregation of valid partitions -- the only surface that yields valid tables."""

    partitions: tuple[ValidComposedPartition, ...]

    def fold_ids(self) -> tuple[str, ...]:
        return tuple(partition.fold_id for partition in self.partitions)

    def event_ordinals(self) -> tuple[int, ...]:
        return tuple(partition.event_ordinal for partition in self.partitions)


def aggregate_valid_partitions(
    partitions: Sequence[ValidComposedPartition],
) -> ValidComposedAggregate:
    """Aggregate ONLY valid partitions. A degraded partition is a static type error here.

    The parameter type admits only :class:`ValidComposedPartition`; passing a
    :class:`DegradedComposedPartition` is rejected by ``mypy --strict``. The runtime
    guard below is the same wall for callers that bypass the type checker.
    """

    checked: list[ValidComposedPartition] = []
    for partition in partitions:
        if not isinstance(partition, ValidComposedPartition):
            raise ComposedWalkForwardError(
                BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE,
                "only valid partitions may enter the valid aggregate",
                fold_id=getattr(partition, "fold_id", None),
            )
        checked.append(partition)
    return ValidComposedAggregate(partitions=tuple(checked))


# ---------------------------------------------------------------------------
# Output tables (verbatim projections of consumed composed-fold outputs)
# ---------------------------------------------------------------------------

#: Every published output table, in a fixed order. ``carry_chain`` and
#: ``warnings_errors`` are driver-owned; ``folds`` is a verbatim projection of each
#: valid composed fold's own consumed result.
OUTPUT_TABLE_NAMES: Final[tuple[str, ...]] = (
    "folds",
    "carry_chain",
    "warnings_errors",
)


def _fold_lineage(run_id: str, fold_id: str, source_sha256_grouped: str) -> dict[str, str]:
    return {
        "fold_id": fold_id,
        "run_id": run_id,
        "source_engine_id": cf.KERNEL_ID,
        "source_role": "composed_fold",
        "source_sha256_grouped": source_sha256_grouped,
    }


def _driver_lineage(run_id: str, fold_id: str, source_sha256_grouped: str) -> dict[str, str]:
    return {
        "fold_id": fold_id,
        "run_id": run_id,
        "source_engine_id": KERNEL_ID,
        "source_role": "composed_walk_forward",
        "source_sha256_grouped": source_sha256_grouped,
    }


def _row(lineage: Mapping[str, str], payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"lineage": dict(lineage), "payload": dict(payload)}


def _valid_fold_row(partition: ValidComposedPartition, run_id: str) -> dict[str, Any]:
    fold = partition.fold
    lineage = _fold_lineage(run_id, partition.fold_id, fold.self_sha256_grouped)
    payload = {
        "benchmark_identity": dict(fold.benchmark_identity),
        "carried_state_identity": partition.link.carried_state_identity,
        "carry_state": partition.link.carry_state,
        "closing_portfolio": fold.closing_portfolio.to_json_dict(),
        "event_consumed": dict(fold.event_consumed),
        "event_ordinal": partition.event_ordinal,
        "fold_id": partition.fold_id,
        "fold_self_sha256_grouped": fold.self_sha256_grouped,
        "ledger_figures": dict(fold.ledger_figures),
        "ledger_identity": fold.ledger_identity,
        "opening_portfolio": fold.opening_portfolio.to_json_dict(),
        "result_identity": fold.result_identity,
        "scenario_figures": dict(fold.scenario_figures),
        "scenario_identity": fold.scenario_identity,
        "selection_k_t": fold.selection_k_t,
        "state": partition.state,
    }
    return _row(lineage, payload)


def _carry_chain_row(link: ChainLink, run_id: str) -> dict[str, Any]:
    lineage = _driver_lineage(run_id, link.fold_id, link.chain_hash)
    return _row(lineage, link.to_json_dict())


def _degraded_row(partition: DegradedComposedPartition, run_id: str) -> dict[str, Any]:
    lineage = _driver_lineage(
        run_id, partition.fold_id, _document_digest(partition.identity_document())
    )
    payload: dict[str, Any] = {
        "event_ordinal": partition.event_ordinal,
        "fold_id": partition.fold_id,
        "reason_codes": list(partition.reason_codes),
        "state": partition.state,
    }
    result = partition.fold_result
    if isinstance(result, cf.DegradedComposedFold):
        payload["composed_fold_degraded"] = {
            "degraded_engine": result.degraded_engine,
            "degraded_reason": result.degraded_reason,
            "degraded_stage": result.degraded_stage,
        }
    return _row(lineage, payload)


# ---------------------------------------------------------------------------
# The run result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComposedWalkForwardResult:
    """One in-memory composed walk-forward run: identity, partitions, chain, tables.

    This is the read-only value :func:`execute_composed_walk_forward` returns;
    constructing it performs no I/O. Publication is the separate, internal-only step
    driven by :func:`run_and_publish_composed_walk_forward` (the single supported public
    publication entry); it consumes a PRIVATE ``_ComposedWalkForwardRun`` bundle that no
    public surface hands the caller. Every published table row (and every nested carry
    record, lineage block, portfolio document, and chain link) is DEEP-FROZEN at
    construction, so a completed result's published content cannot be mutated in place.
    The result carries NO caller-usable seal field: publication verifies against a
    SEPARATE private ``_PublicationReceipt`` minted inside the publish driver from this
    genuine content (tamper-EVIDENCE for a genuine pair, not an independent authority --
    see that class). ``run_id_hex`` is retained only as an informational echo --
    publication DERIVES the directory name from the bound-input manifest and refuses if
    this field disagrees (``BLOCKED_RESULT_IDENTITY_TAMPERED``).
    """

    bound_input_manifest: Mapping[str, Any]
    run_id: str
    run_id_hex: str
    state: str
    ordered_event_ordinals: tuple[int, ...]
    valid_partitions: tuple[ValidComposedPartition, ...]
    degraded_partitions: tuple[DegradedComposedPartition, ...]
    aggregate: ValidComposedAggregate
    chain_head: str
    engine_identities: Mapping[str, cf.EngineIdentity]
    output_tables: Mapping[str, tuple[Mapping[str, Any], ...]]
    lineage: Mapping[str, str]
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        # Deep-freeze EVERY published surface so no caller can mutate a completed
        # result's content in place. The published tables and their nested structures,
        # the engine-identity mapping (which drives the manifest's top-level
        # ``engine_bindings``), the bound-input manifest (whose digest is ``run_id``),
        # and the ``lineage`` / ``provenance`` blocks all become recursively immutable.
        # A ``dataclasses.replace`` that swaps a tampered surface produces a new result
        # whose recomputed content NO LONGER witnesses the execution-captured receipt,
        # and is caught at publication before any write (P1-1).
        object.__setattr__(self, "output_tables", _freeze(self.output_tables))
        object.__setattr__(
            self, "engine_identities", MappingProxyType(dict(self.engine_identities))
        )
        object.__setattr__(self, "bound_input_manifest", _freeze(self.bound_input_manifest))
        object.__setattr__(self, "lineage", _freeze(self.lineage))
        object.__setattr__(self, "provenance", _freeze(self.provenance))

    def table_document(self, name: str) -> dict[str, Any]:
        return {
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "rows": [_plain(row) for row in self.output_tables[name]],
            "run_id": self.run_id,
            "table": name,
        }

    def table_sha256_grouped(self, name: str) -> str:
        return _document_digest(self.table_document(name))

    def _ordered_partitions(self) -> list[ComposedPartition]:
        partitions: list[ComposedPartition] = [*self.valid_partitions, *self.degraded_partitions]
        return sorted(partitions, key=lambda partition: partition.event_ordinal)

    def _partition_index(self) -> list[dict[str, Any]]:
        index: list[dict[str, Any]] = []
        for partition in self._ordered_partitions():
            if isinstance(partition, ValidComposedPartition):
                index.append(
                    {
                        "event_ordinal": partition.event_ordinal,
                        "fold_id": partition.fold_id,
                        "reason_codes": [],
                        "state": partition.state,
                    }
                )
            else:
                index.append(
                    {
                        "event_ordinal": partition.event_ordinal,
                        "fold_id": partition.fold_id,
                        "reason_codes": list(partition.reason_codes),
                        "state": partition.state,
                    }
                )
        return index

    def _fold_identities(self) -> list[dict[str, Any]]:
        identities: list[dict[str, Any]] = []
        for partition in self._ordered_partitions():
            if isinstance(partition, ValidComposedPartition):
                identities.append(
                    {
                        "event_ordinal": partition.event_ordinal,
                        "fold_id": partition.fold_id,
                        "result_identity": partition.fold.result_identity,
                    }
                )
            else:
                identities.append(
                    {
                        "event_ordinal": partition.event_ordinal,
                        "fold_id": partition.fold_id,
                        "reason_codes": list(partition.reason_codes),
                    }
                )
        return identities

    def _carry_chain(self) -> list[dict[str, Any]]:
        return [
            partition.link.to_json_dict() for partition in self._ordered_partitions()
        ]

    def _table_index(self) -> dict[str, Any]:
        return {
            name: {
                "row_count": len(self.output_tables[name]),
                "sha256_grouped": self.table_sha256_grouped(name),
                "source_roles": sorted(
                    {str(row["lineage"]["source_role"]) for row in self.output_tables[name]}
                ),
            }
            for name in OUTPUT_TABLE_NAMES
        }

    def _engine_bindings_document(self) -> list[dict[str, Any]]:
        return [
            self.engine_identities[name].to_json_dict()
            for name in sorted(self.engine_identities)
        ]

    def lineage_document(self) -> dict[str, str]:
        """The published integrity-provenance block (input/config/code/schema hashes).

        ``lineage`` is a flat ``str -> str`` mapping, so a shallow copy fully thaws it.
        """

        return dict(self.lineage)

    def result_identity_document(self) -> dict[str, Any]:
        """The clock-independent canonical result identity (no wall-clock values)."""

        return {
            "carry_chain": self._carry_chain(),
            "chain_head": self.chain_head,
            "fold_identities": self._fold_identities(),
            "output_tables": self._table_index(),
            "partition_index": self._partition_index(),
            "run_id": self.run_id,
            "state": self.state,
            "valid_fold_ids": list(self.aggregate.fold_ids()),
        }

    def result_identity_sha256_grouped(self) -> str:
        return _document_digest(self.result_identity_document())

    def manifest_document(self) -> dict[str, Any]:
        return {
            "chain_head": self.chain_head,
            "claims": dict(NON_CLAIMS),
            "engine_bindings": self._engine_bindings_document(),
            "fail_closed_states": list(COMPOSED_WALK_FORWARD_FAIL_CLOSED_STATES),
            "identity_material": _plain(self.bound_input_manifest),
            "kernel_id": KERNEL_ID,
            "lineage": self.lineage_document(),
            "orchestrated_composed_fold": {
                "kernel_id": cf.KERNEL_ID,
                "schema_version": cf.SCHEMA_VERSION,
            },
            "output_tables": self._table_index(),
            "partition_index": self._partition_index(),
            "provenance": _plain(self.provenance),
            "result_identity_sha256_grouped": self.result_identity_sha256_grouped(),
            "run_id": self.run_id,
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "state": self.state,
            "valid_fold_ids": list(self.aggregate.fold_ids()),
        }


# ---------------------------------------------------------------------------
# The PRIVATE publication receipt (P1-1): minted INSIDE the publish driver from genuine
# content and verified against the STAGED BYTES ON DISK immediately before the atomic
# rename. It is tamper-EVIDENCE for a genuine {result, receipt} pair, NOT an independent
# authority. It is deliberately kept off the public surface (underscored, absent from
# __all__): the single supported public publication entry is
# run_and_publish_composed_walk_forward, which never accepts caller-supplied content, so
# a public-API caller cannot publish caller-supplied content. Reaching this private type
# is OUT OF CONTRACT. See _PublicationReceipt for the exact boundary.
# ---------------------------------------------------------------------------

#: The manifest artifact's published basename (a plain, separator-free basename).
_MANIFEST_FILENAME: Final = "manifest.json"


def _artifact_filename(table_name: str) -> str:
    """The published basename for one output table (a plain, separator-free basename)."""

    return f"table-{table_name}.json"


def expected_artifact_filenames() -> tuple[str, ...]:
    """The EXACT published-artifact basename set: every table file plus the manifest."""

    return (*(_artifact_filename(name) for name in OUTPUT_TABLE_NAMES), _MANIFEST_FILENAME)


@dataclass(frozen=True)
class _PublicationReceipt:
    """The PRIVATE publication receipt, minted INSIDE the publish driver. NOT public.

    Minted in :func:`run_and_publish_composed_walk_forward` (via
    :func:`_mint_publication_receipt`) from the just-computed GENUINE result, it holds:

    * ``run_id`` -- the run identity (grouped sha256 over the bound-input manifest);
    * ``run_id_hex`` -- the run identity DERIVED from the SAME bound-input manifest
      (:func:`run_id_hex_of`); the SOLE authority for the published directory name,
      never a caller-supplied ``result.run_id_hex`` field;
    * ``artifact_grouped_sha256`` -- the exact expected ``{artifact filename -> grouped
      sha256 of the canonical bytes that _stage_run will write}`` for EVERY output table
      AND ``manifest.json``.

    It is held only in the PRIVATE ``_ComposedWalkForwardRun`` bundle and is NEVER a
    field of the result nor exported, so publication verifies the STAGED BYTES against
    THIS receipt -- catching any tamper that RETAINS the receipt: a staged-file edit
    after staging, a ``run_id_hex`` field swap (the directory name is DERIVED, not that
    field), and a table-content replace carried with the genuine receipt all fail closed.

    **This receipt is tamper-EVIDENCE, not an independent authority.** It is a
    DETERMINISTIC function of the result -- the grouped sha256 of the result's own
    ``table_document`` / ``manifest_document`` bytes and the derived run identity. It is
    therefore deliberately kept OFF the public surface: the ONLY SUPPORTED PUBLIC
    PUBLICATION ENTRY is :func:`run_and_publish_composed_walk_forward`, which runs the
    engines internally and never accepts caller-supplied content, so a public-API caller
    CANNOT PUBLISH CALLER-SUPPLIED content. Arbitrary access to this private type (or to
    ``_stage_run`` / ``_commit_run`` / ``_publish_run``) is OUT OF CONTRACT: in one
    deterministic in-process library no secret can bind an authority a result-holder
    cannot reproduce, so protection against MALICIOUS SAME-PROCESS CODE that reaches these
    internals requires a SEPARATE TRUSTED PROCESS OR EXTERNAL SIGNING AUTHORITY, not an
    in-process seal.
    """

    run_id: str
    run_id_hex: str
    artifact_grouped_sha256: Mapping[str, str]

    def artifact_names(self) -> frozenset[str]:
        """The exact set of artifact basenames the receipt pins."""

        return frozenset(self.artifact_grouped_sha256)


@dataclass(frozen=True)
class _ComposedWalkForwardRun:
    """The PRIVATE publish bundle: the in-memory result AND its publication receipt.

    Produced and consumed only inside :func:`run_and_publish_composed_walk_forward` (the
    single supported public publication entry); it is never returned to a caller and is
    absent from ``__all__``. The private publish helpers
    (:func:`_stage_run` / :func:`_commit_run` / :func:`_publish_run`) verify the staged
    artifacts against ``receipt``, so a tampered ``result`` carried with the GENUINE
    receipt cannot publish. Because no public surface hands this bundle (or a receipt
    constructor) to a caller, a public-API caller cannot publish caller-supplied content;
    reaching this private type is OUT OF CONTRACT (see :class:`_PublicationReceipt`).
    """

    result: ComposedWalkForwardResult
    receipt: _PublicationReceipt


def _mint_publication_receipt(result: ComposedWalkForwardResult) -> _PublicationReceipt:
    """Capture the private publication receipt from a GENUINE result. Internal only.

    Called ONLY inside :func:`run_and_publish_composed_walk_forward`, from the
    just-computed genuine result (never from caller-provided content). Each expected
    artifact hash is the grouped sha256 of the EXACT canonical bytes ``_stage_run`` will
    write for that file, so the receipt witnesses the on-disk bytes directly, not a
    mutable result field. The DERIVED ``run_id_hex`` is computed from the bound-input
    manifest, not read from ``result.run_id_hex``.
    """

    artifact_hashes: dict[str, str] = {
        _artifact_filename(name): _bytes_digest(
            canonical_json_bytes(result.table_document(name))
        )
        for name in OUTPUT_TABLE_NAMES
    }
    artifact_hashes[_MANIFEST_FILENAME] = _bytes_digest(
        canonical_json_bytes(result.manifest_document())
    )
    plain_manifest = _plain(result.bound_input_manifest)
    return _PublicationReceipt(
        run_id=run_id_of(plain_manifest),
        run_id_hex=run_id_hex_of(plain_manifest),
        artifact_grouped_sha256=MappingProxyType(artifact_hashes),
    )


def _assert_result_matches_receipt(
    result: ComposedWalkForwardResult, receipt: _PublicationReceipt
) -> None:
    """Refuse unless the in-memory result witnesses the execution receipt.

    Recomputes, from the result's CURRENT content, the run identity and the DERIVED
    ``run_id_hex`` (both from the bound-input manifest) and the grouped sha256 of the
    exact canonical bytes each artifact file will contain, and asserts each equals the
    execution-captured value in ``receipt``. Catches a swapped ``run_id_hex`` field
    (the directory name is DERIVED, never that field), a tampered table/manifest
    surface, and a tampered bound-input manifest -- fail-closed
    ``BLOCKED_RESULT_IDENTITY_TAMPERED`` -- BEFORE any staging or publish write.
    """

    plain_manifest = _plain(result.bound_input_manifest)
    derived_run_id = run_id_of(plain_manifest)
    derived_run_id_hex = run_id_hex_of(plain_manifest)
    tampered: list[str] = []
    if derived_run_id != receipt.run_id:
        tampered.append("bound_input_manifest")
    if result.run_id != receipt.run_id:
        tampered.append("run_id")
    if result.run_id_hex != receipt.run_id_hex or derived_run_id_hex != receipt.run_id_hex:
        tampered.append("run_id_hex")
    for name in OUTPUT_TABLE_NAMES:
        filename = _artifact_filename(name)
        current = _bytes_digest(canonical_json_bytes(result.table_document(name)))
        if current != receipt.artifact_grouped_sha256.get(filename):
            tampered.append(f"table:{name}")
    current_manifest = _bytes_digest(canonical_json_bytes(result.manifest_document()))
    if current_manifest != receipt.artifact_grouped_sha256.get(_MANIFEST_FILENAME):
        tampered.append(_MANIFEST_FILENAME)
    if tampered:
        raise ComposedWalkForwardError(
            BLOCKED_RESULT_IDENTITY_TAMPERED,
            "the result's current content no longer witnesses the execution receipt "
            f"({', '.join(sorted(tampered))}); publication is refused before any write",
            detail=",".join(sorted(tampered)),
        )


def _assert_confined_basename(filename: str, staging_directory: Path) -> None:
    """Refuse an artifact filename that is not a plain basename confined to the staging dir.

    An expected artifact filename must be a bare basename (no path separator, no ``..``
    traversal) whose join with the staging directory resolves strictly inside it, so
    nothing is ever read from or renamed to a path outside ``staging_directory``.
    """

    separator_present = (
        os.sep in filename
        or (os.altsep is not None and os.altsep in filename)
        or "/" in filename
        or "\\" in filename
    )
    if (
        not filename
        or filename in {os.curdir, os.pardir}
        or os.path.basename(filename) != filename
        or separator_present
        or not wf._lexical_within(staging_directory.joinpath(filename), staging_directory)
    ):
        raise ComposedWalkForwardError(
            BLOCKED_STAGED_ARTIFACT_TAMPERED,
            "an artifact filename must be a plain basename confined to the staging "
            f"directory; got {filename!r}",
            path=str(staging_directory),
        )


def _assert_staged_artifacts_match_receipt(
    staging_directory: Path, receipt: _PublicationReceipt
) -> None:
    """Verify the EXACT staged file set and BYTES against the receipt (before the rename).

    Immediately before the atomic rename: enumerate the files actually present in the
    staging directory and assert the set EXACTLY equals the expected artifact set (every
    ``table-<name>.json`` plus ``manifest.json`` -- no missing, no extra); then RE-READ
    each file's bytes from disk, recompute its grouped sha256, and assert it equals the
    execution-captured expected byte-hash for that filename; finally, parse the on-disk
    ``manifest.json`` and assert its OWN bound per-table ``sha256_grouped`` values equal
    those same expected hashes. Any mismatch -- a tampered staged table, a tampered
    manifest, a missing or extra file -- fails closed with
    ``BLOCKED_STAGED_ARTIFACT_TAMPERED`` BEFORE any rename/publish write. This witnesses
    the on-disk bytes directly, so a caller who edits a staged file AFTER staging cannot
    publish the tampered file.
    """

    expected = receipt.artifact_grouped_sha256
    expected_names = frozenset(expected)
    for filename in expected_names:
        _assert_confined_basename(filename, staging_directory)
    # EVERY entry counts (a stray subdirectory or symlink is an "extra"), so nothing
    # outside the expected set can ride along on the rename.
    present = frozenset(child.name for child in staging_directory.iterdir())
    if present != expected_names:
        missing = sorted(expected_names - present)
        extra = sorted(present - expected_names)
        raise ComposedWalkForwardError(
            BLOCKED_STAGED_ARTIFACT_TAMPERED,
            "the staged artifact set does not witness the execution receipt "
            f"(missing={missing}, extra={extra}); publication is refused before the rename",
            path=str(staging_directory),
            detail=",".join(
                sorted({*(f"missing:{n}" for n in missing), *(f"extra:{n}" for n in extra)})
            ),
        )
    mismatched: list[str] = []
    for filename in sorted(expected_names):
        path = staging_directory.joinpath(filename)
        if not path.is_file():
            # An expected name replaced by a directory/symlink is a tamper, not bytes.
            mismatched.append(filename)
            continue
        if _bytes_digest(path.read_bytes()) != expected[filename]:
            mismatched.append(filename)
    # The on-disk manifest's OWN bound per-table hashes must also witness the receipt.
    bound_tables: Any = None
    with contextlib.suppress(OSError, ValueError, TypeError):
        manifest_document = json.loads(staging_directory.joinpath(_MANIFEST_FILENAME).read_bytes())
        if isinstance(manifest_document, Mapping):
            bound_tables = manifest_document.get("output_tables")
    if not isinstance(bound_tables, Mapping):
        mismatched.append("manifest:output_tables")
    else:
        for name in OUTPUT_TABLE_NAMES:
            entry = bound_tables.get(name)
            bound_hash = entry.get("sha256_grouped") if isinstance(entry, Mapping) else None
            if bound_hash != expected[_artifact_filename(name)]:
                mismatched.append(f"manifest_table:{name}")
    if mismatched:
        raise ComposedWalkForwardError(
            BLOCKED_STAGED_ARTIFACT_TAMPERED,
            "a staged artifact's bytes no longer witness the execution receipt "
            f"({', '.join(sorted(mismatched))}); publication is refused before the rename",
            path=str(staging_directory),
            detail=",".join(sorted(mismatched)),
        )


# ---------------------------------------------------------------------------
# The orchestrator
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _isoformat_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ComposedWalkForwardError(
            BLOCKED_MALFORMED_COMPOSED_WALK_FORWARD_INPUT,
            "clock must return a timezone-aware datetime",
        )
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class _CarryDecision:
    """The carry disposition for one authorized, internally-valid fold."""

    valid: bool
    carry_state: str
    predecessor_closing_nav: str | None
    carried_state_identity: str | None
    reason_code: str | None


def _decimal_equal(left: str, right: str) -> bool:
    """Numeric equality over two canonical ledger strings (no arithmetic operator)."""

    return Decimal(left) == Decimal(right)


def _carried_state_matches(
    predecessor: cf.ClosingPortfolioState, successor: cf.OpeningPortfolioState
) -> bool:
    """Whether the successor's CONSUMED opening composition equals the predecessor close.

    Compares the engine-consumed opening cash, receivables, and held (non-zero)
    positions against the predecessor's ``cash_plus`` / ``receivables_plus`` /
    ``positions_plus`` closing state. A MISSING, ALTERED, REORDERED, or DUPLICATED
    carried holding, or a mismatched cash / receivable, makes this False -- even
    when the total book value (NAV) is preserved, which the book-value check alone
    would miss. This reconciles the successor's opening SHARE COUNT against the
    predecessor's close; lot cost basis and acquisition are NOT carried. Exact lot
    carry is unsupported, so the lot gate in :func:`_evaluate_carry` fails closed on
    ANY non-empty predecessor ``open_lots`` regardless of a matching held-position
    vector -- a matching position vector does NOT carry the lot shares.
    """

    if not _decimal_equal(predecessor.cash, successor.cash):
        return False
    if not _decimal_equal(predecessor.receivables, successor.receivables):
        return False
    predecessor_held = predecessor.held_positions()
    successor_held = successor.held_positions()
    if set(predecessor_held) != set(successor_held):
        return False
    for security_id, shares in predecessor_held.items():
        if not _decimal_equal(shares, successor_held[security_id]):
            return False
    return True


def _evaluate_carry(
    *,
    is_first: bool,
    consumed_in_nav: str,
    successor_opening: cf.OpeningPortfolioState,
    predecessor_produced_close: bool,
    predecessor_closing_nav: str | None,
    predecessor_closing_portfolio: cf.ClosingPortfolioState | None,
) -> _CarryDecision:
    """Decide the cross-fold carry ledger-to-ledger over CONSUMED figures.

    Two consumed-figure checks compose the carry:

    * BOOK VALUE. ``consumed_in_nav`` is the successor's engine-computed opening
      NAV -- its ``compose_fold`` ``ledger_figures["initial_nav"]`` -- on the same
      canonical ``format_ledger`` surface as the predecessor's consumed
      ``final_nav`` carried in ``predecessor_closing_nav``. The carry is therefore
      consumed initial_nav[k+1] vs consumed final_nav[k]; it never reads the
      successor's ``declared_pre_trade_nav`` bound input, whose tie to the real
      opening state holds only within
      ``targets_v1.PRE_TRADE_NAV_IDENTITY_TOLERANCE`` (1e-6). The declared value's
      own anti-spoof check stays at the targets stage
      (``INVALID_PRE_TRADE_NAV_IDENTITY``); it is not weakened here.
    * COMPOSITION. The successor's CONSUMED opening cash / held positions /
      receivables must equal the predecessor's ``cash_plus`` / ``positions_plus`` /
      ``receivables_plus`` closing state exactly (``_carried_state_matches``),
      catching a NAV-preserving position tamper the book-value check cannot.

    The book-value check runs first so the 5e-7 declared-vs-consumed NAV
    regression keeps producing ``BLOCKED_LEDGER_STATE_CARRY_BROKEN``.

    * LOT GATE. After the cash/position/receivable/NAV checks pass, a successor
      whose consumed predecessor CLOSING state carries NON-EMPTY ``open_lots`` fails
      closed (``BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED``) rather than report
      CARRY_CONTINUOUS: exact lot carry (shares + basis + acquisition) is not
      achievable without an incoming-lot execution-engine interface. A pure cash
      carry (empty predecessor lots) is unaffected.
    """

    if is_first:
        return _CarryDecision(True, CARRY_GENESIS, None, None, None)
    if not predecessor_produced_close or predecessor_closing_portfolio is None:
        return _CarryDecision(
            False,
            CARRY_PREDECESSOR_DEGRADED,
            None,
            None,
            BLOCKED_PREDECESSOR_FOLD_DEGRADED_NO_CARRY,
        )
    carried_identity = predecessor_closing_portfolio.carry_identity
    if consumed_in_nav != predecessor_closing_nav:
        return _CarryDecision(
            False,
            CARRY_BROKEN,
            predecessor_closing_nav,
            carried_identity,
            BLOCKED_LEDGER_STATE_CARRY_BROKEN,
        )
    if not _carried_state_matches(predecessor_closing_portfolio, successor_opening):
        return _CarryDecision(
            False,
            CARRY_POSITION_BROKEN,
            predecessor_closing_nav,
            carried_identity,
            BLOCKED_POSITION_STATE_CARRY_BROKEN,
        )
    # LOT GATE (owner-authorized fail-closed remediation). The cash / positions /
    # receivables / NAV carry checks above are the mechanics that DO hold up. But the
    # predecessor's published open_lots reconcile with positions_plus only INSIDE the
    # execution engine, which exposes NO incoming-lot interface: exact lot carry
    # (shares + cost basis + acquisition) cannot be threaded into the successor this
    # cycle. So a successor whose consumed predecessor CLOSING state carries NON-EMPTY
    # open_lots fails closed here -- BEFORE the fold is admitted valid -- rather than
    # report CARRY_CONTINUOUS. A predecessor that closes with EMPTY lots (a pure cash
    # carry) is unaffected and may carry validly. carried_identity binds the
    # predecessor open_lots only as TAMPER-EVIDENCE, never as a carried/consumed lot.
    if predecessor_closing_portfolio.open_lots:
        return _CarryDecision(
            False,
            CARRY_LOT_CARRY_UNSUPPORTED,
            predecessor_closing_nav,
            carried_identity,
            BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED,
        )
    return _CarryDecision(
        True, CARRY_CONTINUOUS, predecessor_closing_nav, carried_identity, None
    )


def execute_composed_walk_forward(
    plan: ComposedWalkForwardPlan,
    *,
    repository_root: Path,
    trading_calendar: Any,
    clock: Any = _utc_now,
    identities: Mapping[str, cf.EngineIdentity] | None = None,
    entry_module_source: Path | None = None,
) -> ComposedWalkForwardResult:
    """Run every fold in schedule order, threading the ledger carry and the chain.

    Performs no publication I/O. Refuses before any fold if a transport is reachable or
    the injected calendar does not witness the declared identity. A wall clock from
    ``clock`` lands only in ``provenance`` and never enters the run identity or any
    result digest. Returns ONLY the read-only :class:`ComposedWalkForwardResult` -- no
    publication receipt or bundle is exposed, so no public value carries a caller-usable
    receipt. Publication is driven separately and internally by
    :func:`run_and_publish_composed_walk_forward` (the single supported public
    publication entry), which mints the private receipt itself.
    """

    assert_states_are_complete()
    cf.assert_states_complete()
    assert_network_egress_denied(repository_root, entry_module_source=entry_module_source)
    assert_declared_calendar_witnesses_injected(trading_calendar, plan)

    bound_identities = (
        dict(identities) if identities is not None else cf.engine_identities(repository_root)
    )
    manifest = bound_input_manifest(plan, identities=bound_identities)
    run_id = run_id_of(manifest)
    run_id_hex = run_id_hex_of(manifest)
    lineage = _run_lineage(
        run_id=run_id, identities=bound_identities, repository_root=repository_root
    )

    ordered = plan.ordered_slots()
    partitions: list[ComposedPartition] = []
    chain_hash = GENESIS_CHAIN_HASH
    predecessor_produced_close = False
    predecessor_closing_nav: str | None = None
    predecessor_closing_portfolio: cf.ClosingPortfolioState | None = None
    is_first = True

    for slot in ordered:
        fold_id = fold_id_of_slot(slot, identities=bound_identities)
        declared_in_nav = str(slot.inputs.execution.declared_pre_trade_nav)

        partition = _run_one_slot(
            slot=slot,
            fold_id=fold_id,
            declared_in_nav=declared_in_nav,
            authorized=fold_id in plan.authorized_fold_ids,
            predecessor_chain_hash=chain_hash,
            is_first=is_first,
            predecessor_produced_close=predecessor_produced_close,
            predecessor_closing_nav=predecessor_closing_nav,
            predecessor_closing_portfolio=predecessor_closing_portfolio,
            repository_root=repository_root,
            identities=bound_identities,
        )
        partitions.append(partition)

        chain_hash = partition.link.chain_hash
        if isinstance(partition, ValidComposedPartition):
            predecessor_produced_close = True
            predecessor_closing_nav = partition.closing_nav()
            predecessor_closing_portfolio = partition.fold.closing_portfolio
        else:
            predecessor_produced_close = False
            predecessor_closing_nav = None
            predecessor_closing_portfolio = None
        is_first = False

    valid = tuple(p for p in partitions if isinstance(p, ValidComposedPartition))
    degraded = tuple(p for p in partitions if isinstance(p, DegradedComposedPartition))
    aggregate = aggregate_valid_partitions(valid)

    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in OUTPUT_TABLE_NAMES}
    for partition in sorted(partitions, key=lambda item: item.event_ordinal):
        tables["carry_chain"].append(_carry_chain_row(partition.link, run_id))
        if isinstance(partition, ValidComposedPartition):
            tables["folds"].append(_valid_fold_row(partition, run_id))
        else:
            tables["warnings_errors"].append(_degraded_row(partition, run_id))

    state = (
        RUN_COMPLETED_WITH_VALID_PARTITIONS
        if valid
        else RUN_COMPLETED_NO_VALID_PARTITIONS
    )
    provenance = {
        "note": "wall-clock values are recorded outside the canonical result identity",
        "wall_clock_started_utc": _isoformat_utc(clock()),
    }
    result = ComposedWalkForwardResult(
        bound_input_manifest=manifest,
        run_id=run_id,
        run_id_hex=run_id_hex,
        state=state,
        ordered_event_ordinals=tuple(slot.event_ordinal for slot in ordered),
        valid_partitions=valid,
        degraded_partitions=degraded,
        aggregate=aggregate,
        chain_head=chain_hash,
        engine_identities=bound_identities,
        output_tables={name: tuple(rows) for name, rows in tables.items()},
        lineage=lineage,
        provenance=provenance,
    )
    # Return ONLY the read-only, deep-frozen result. No publication receipt or bundle is
    # exposed here; the private receipt is minted inside the publish driver
    # (run_and_publish_composed_walk_forward), which is the single supported public
    # publication entry.
    return result


def _run_one_slot(
    *,
    slot: FoldSlot,
    fold_id: str,
    declared_in_nav: str,
    authorized: bool,
    predecessor_chain_hash: str,
    is_first: bool,
    predecessor_produced_close: bool,
    predecessor_closing_nav: str | None,
    predecessor_closing_portfolio: cf.ClosingPortfolioState | None,
    repository_root: Path,
    identities: Mapping[str, cf.EngineIdentity],
) -> ComposedPartition:
    """Run one scheduled fold and classify it into a valid or degraded partition."""

    # The predecessor's exact carried closing-state identity (None at genesis, or
    # when the predecessor produced no close). Bound into this fold's chain link.
    carried_state_identity = (
        predecessor_closing_portfolio.carry_identity
        if predecessor_produced_close and predecessor_closing_portfolio is not None
        else None
    )

    def degraded(
        *,
        reason_codes: tuple[str, ...],
        fold_result: cf.ComposedFoldResult | None,
        carry_state: str,
        carried_pred_nav: str | None,
        carried_state: str | None,
        carried_in_nav: str | None = None,
    ) -> DegradedComposedPartition:
        # A fold that produced a CONSUMED opening (internally valid but carry-broken)
        # records that engine-computed initial_nav; a fold that never ran (unauthorized
        # or engine-degraded) has no consumed figure and falls back to its declared one.
        link = ChainLink(
            event_ordinal=slot.event_ordinal,
            fold_id=fold_id,
            partition_state=PARTITION_DEGRADED,
            predecessor_chain_hash=predecessor_chain_hash,
            chain_hash=_chain_hash(
                predecessor_chain_hash=predecessor_chain_hash,
                event_ordinal=slot.event_ordinal,
                fold_id=fold_id,
                partition_state=PARTITION_DEGRADED,
                fold_identity_material={
                    "carried_state_identity": carried_state,
                    "reason_codes": list(reason_codes),
                },
            ),
            carried_in_nav=declared_in_nav if carried_in_nav is None else carried_in_nav,
            predecessor_closing_nav=carried_pred_nav,
            carried_state_identity=carried_state,
            carry_state=carry_state,
        )
        return DegradedComposedPartition(
            event_ordinal=slot.event_ordinal,
            fold_id=fold_id,
            reason_codes=reason_codes,
            fold_result=fold_result,
            link=link,
        )

    # An unauthorized fold is retained degraded and never run (fail-closed).
    if not authorized:
        return degraded(
            reason_codes=(BLOCKED_FOLD_NOT_AUTHORIZED,),
            fold_result=None,
            carry_state=CARRY_NOT_ATTEMPTED_UNAUTHORIZED,
            carried_pred_nav=predecessor_closing_nav if predecessor_produced_close else None,
            carried_state=carried_state_identity,
        )

    result = cf.compose_fold(
        slot.inputs, repository_root=repository_root, identities=identities
    )

    # The composed fold refused with its own (or an engine's) verbatim state.
    if isinstance(result, cf.DegradedComposedFold):
        return degraded(
            reason_codes=(result.degraded_reason,),
            fold_result=result,
            carry_state=CARRY_NOT_ATTEMPTED_FOLD_DEGRADED,
            carried_pred_nav=predecessor_closing_nav if predecessor_produced_close else None,
            carried_state=carried_state_identity,
        )

    # The fold is internally valid: its CONSUMED engine-computed opening NAV
    # (ledger_figures["initial_nav"], the same canonical format_ledger surface as the
    # predecessor's consumed final_nav) and its CONSUMED opening composition
    # (opening_portfolio) are the state it actually opened on. Enforce the cross-fold
    # carry ledger-to-ledger over consumed figures -- book value AND composition --
    # never the tolerant declared_pre_trade_nav proxy, and record the consumed figure
    # plus the carried-state identity on the link.
    consumed_in_nav = str(result.ledger_figures["initial_nav"])
    decision = _evaluate_carry(
        is_first=is_first,
        consumed_in_nav=consumed_in_nav,
        successor_opening=result.opening_portfolio,
        predecessor_produced_close=predecessor_produced_close,
        predecessor_closing_nav=predecessor_closing_nav,
        predecessor_closing_portfolio=predecessor_closing_portfolio,
    )
    if not decision.valid:
        assert decision.reason_code is not None
        return degraded(
            reason_codes=(decision.reason_code,),
            fold_result=result,
            carry_state=decision.carry_state,
            carried_pred_nav=decision.predecessor_closing_nav,
            carried_state=decision.carried_state_identity,
            carried_in_nav=consumed_in_nav,
        )

    link = ChainLink(
        event_ordinal=slot.event_ordinal,
        fold_id=fold_id,
        partition_state=PARTITION_VALID,
        predecessor_chain_hash=predecessor_chain_hash,
        chain_hash=_chain_hash(
            predecessor_chain_hash=predecessor_chain_hash,
            event_ordinal=slot.event_ordinal,
            fold_id=fold_id,
            partition_state=PARTITION_VALID,
            fold_identity_material={
                "carried_state_identity": decision.carried_state_identity,
                "result_identity": result.result_identity,
            },
        ),
        carried_in_nav=consumed_in_nav,
        predecessor_closing_nav=decision.predecessor_closing_nav,
        carried_state_identity=decision.carried_state_identity,
        carry_state=decision.carry_state,
    )
    return ValidComposedPartition(
        event_ordinal=slot.event_ordinal, fold=result, link=link
    )


# ---------------------------------------------------------------------------
# Atomic, no-clobber, root-confined publication -- REUSES walk_forward_v1's own
# durability/confinement primitives (path confinement, durable exclusive-create writes,
# directory fsync, recursive cleanup). This lane never reimplements those
# mechanics; it composes them for its own output-table set.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StagedRun:
    """A fully-written staging directory awaiting the atomic publish. PRIVATE.

    Produced and consumed only inside the private publish machinery. Carries the
    publication ``receipt`` alongside the result, so :func:`_commit_run` verifies the
    STAGED BYTES against the receipt before the atomic rename. This catches any tamper
    that RETAINS the receipt; it is not resistance against same-process code reaching
    these internals (see :class:`_PublicationReceipt` for the trust boundary).
    """

    staging_directory: Path
    final_directory: Path
    run_id: str
    runs_root: Path
    result: ComposedWalkForwardResult
    receipt: _PublicationReceipt


def _stage_run(run: _ComposedWalkForwardRun, *, runs_root: Path) -> _StagedRun:
    """Write every table file and the manifest into a private, root-confined staging dir.

    Nothing appears at the final run directory until :func:`_commit_run`. Delegates the
    durable exclusive-create writes, directory fsync, and confinement primitive to
    :mod:`walk_forward_v1`. The run directory name is DERIVED from the execution
    receipt's ``run_id_hex`` (itself derived from the bound-input manifest), never the
    caller-supplied ``result.run_id_hex`` field. BOTH the staging directory and the
    final directory must resolve strictly inside ``runs_root``; each artifact filename
    must be a plain basename confined to the staging directory. Before writing anything
    it asserts the in-memory result witnesses the receipt (``BLOCKED_RESULT_IDENTITY_
    TAMPERED`` otherwise).
    """

    result = run.result
    receipt = run.receipt
    _assert_result_matches_receipt(result, receipt)
    root = runs_root.resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    # DERIVE the directory name from the receipt (never from result.run_id_hex).
    final_directory = root.joinpath(f"run-{receipt.run_id_hex}")
    if not wf._lexical_within(final_directory, root):
        raise ComposedWalkForwardError(
            BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT,
            "the run directory must resolve inside the configured runs root",
            path=str(final_directory),
        )
    staging_directory = Path(tempfile.mkdtemp(prefix=".cwf-stage-", dir=root))
    if not wf._lexical_within(staging_directory, root):
        with contextlib.suppress(OSError):
            wf._remove_tree(staging_directory)
        raise ComposedWalkForwardError(
            BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT,
            "the staging directory must resolve inside the configured runs root",
            path=str(staging_directory),
        )
    for name in OUTPUT_TABLE_NAMES:
        filename = _artifact_filename(name)
        _assert_confined_basename(filename, staging_directory)
        wf._write_file_durable(
            staging_directory.joinpath(filename),
            canonical_json_bytes(result.table_document(name)),
        )
    _assert_confined_basename(_MANIFEST_FILENAME, staging_directory)
    wf._write_file_durable(
        staging_directory.joinpath(_MANIFEST_FILENAME),
        canonical_json_bytes(result.manifest_document()),
    )
    wf._fsync_directory(staging_directory)
    return _StagedRun(
        staging_directory=staging_directory,
        final_directory=final_directory,
        run_id=result.run_id,
        runs_root=root,
        result=result,
        receipt=receipt,
    )


def _commit_run(staged: _StagedRun) -> Path:
    """Atomically publish a staged run. No-clobber: an existing run is never mutated.

    Immediately before the atomic rename it (1) re-asserts the in-memory result witnesses
    the execution receipt (``BLOCKED_RESULT_IDENTITY_TAMPERED``); (2) confirms BOTH the
    staging and the final directory still resolve strictly inside ``runs_root``
    (``BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT``); and (3) verifies the EXACT staged file set
    and RE-READ staged bytes against the receipt (``BLOCKED_STAGED_ARTIFACT_TAMPERED``),
    so a staged file edited after staging publishes NOTHING. Only then does it rename.
    """

    _assert_result_matches_receipt(staged.result, staged.receipt)
    for directory in (staged.staging_directory, staged.final_directory):
        if not wf._lexical_within(directory, staged.runs_root):
            raise ComposedWalkForwardError(
                BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT,
                "the run directory must resolve inside the configured runs root",
                path=str(directory),
            )
    _assert_staged_artifacts_match_receipt(staged.staging_directory, staged.receipt)
    if staged.final_directory.exists():
        with contextlib.suppress(OSError):
            wf._remove_tree(staged.staging_directory)
        raise ComposedWalkForwardError(
            BLOCKED_RUN_DIRECTORY_EXISTS,
            f"run directory {staged.final_directory.name} already exists; reruns never mutate it",
            path=str(staged.final_directory),
        )
    try:
        os.rename(staged.staging_directory, staged.final_directory)
    except (FileExistsError, OSError) as exc:
        with contextlib.suppress(OSError):
            wf._remove_tree(staged.staging_directory)
        raise ComposedWalkForwardError(
            BLOCKED_RUN_DIRECTORY_EXISTS,
            f"run directory {staged.final_directory.name} already exists; reruns never mutate it",
            path=str(staged.final_directory),
        ) from exc
    wf._fsync_directory(staged.runs_root)
    return staged.final_directory


def _publish_run(run: _ComposedWalkForwardRun, *, runs_root: Path) -> Path:
    """Stage then atomically commit a run directory. Returns the published path. PRIVATE.

    Internal helper driven only by :func:`run_and_publish_composed_walk_forward`; it
    verifies the staged bytes against the receipt before the rename. It is NOT a public
    publication path -- the single supported public entry is
    :func:`run_and_publish_composed_walk_forward`, which never accepts caller-supplied
    content. Reaching this helper directly is OUT OF CONTRACT (see
    :class:`_PublicationReceipt`).
    """

    return _commit_run(_stage_run(run, runs_root=runs_root))


def run_and_publish_composed_walk_forward(
    plan: ComposedWalkForwardPlan,
    *,
    repository_root: Path,
    trading_calendar: Any,
    runs_root: Path,
    clock: Any = _utc_now,
    identities: Mapping[str, cf.EngineIdentity] | None = None,
) -> tuple[ComposedWalkForwardResult, Path]:
    """Execute a composed walk-forward run and publish it atomically. The one-call driver.

    This is the SINGLE SUPPORTED PUBLIC PUBLICATION ENTRY. The caller supplies only a
    PLAN and the execution environment (including ``runs_root``); it NEVER accepts a
    caller-supplied result, run, or receipt. It executes the engines internally, mints
    the PRIVATE ``_PublicationReceipt`` from the genuine result
    (:func:`_mint_publication_receipt`), bundles it privately, and publishes via the
    private ``_stage_run`` -> ``_commit_run`` machinery (through :func:`_publish_run`).
    The published output is therefore strictly engine-derived, with no window to
    interpose caller-supplied content between execution and publication -- so a public-API
    caller CANNOT PUBLISH CALLER-SUPPLIED content.

    This closes the SUPPORTED-PUBLIC-API trust boundary; it does NOT claim in-process
    cryptographic trust. Protection against malicious SAME-PROCESS code that reaches the
    underscored internals is OUT OF CONTRACT and requires a separate trusted process or
    external signing authority, not an in-process seal.

    Returns the read-only :class:`ComposedWalkForwardResult` and the published path.
    """

    result = execute_composed_walk_forward(
        plan,
        repository_root=repository_root,
        trading_calendar=trading_calendar,
        clock=clock,
        identities=identities,
    )
    run = _ComposedWalkForwardRun(result=result, receipt=_mint_publication_receipt(result))
    published = _publish_run(run, runs_root=runs_root)
    return result, published


__all__ = [
    "BOUND_INPUT_MANIFEST_FIELDS",
    "CARRY_BROKEN",
    "CARRY_CONTINUOUS",
    "CARRY_GENESIS",
    "CARRY_LOT_CARRY_UNSUPPORTED",
    "CARRY_NOT_ATTEMPTED_FOLD_DEGRADED",
    "CARRY_NOT_ATTEMPTED_UNAUTHORIZED",
    "CARRY_POSITION_BROKEN",
    "CARRY_PREDECESSOR_DEGRADED",
    "CARRY_STATES",
    "COMPOSED_WALK_FORWARD_FAIL_CLOSED_STATES",
    "GENESIS_CHAIN_HASH",
    "KERNEL_ID",
    "MANIFEST_SCHEMA_VERSION",
    "NON_CLAIMS",
    "OUTPUT_SCHEMA_VERSION",
    "OUTPUT_TABLE_NAMES",
    "PARTITION_DEGRADED",
    "PARTITION_STATES",
    "PARTITION_VALID",
    "RUN_COMPLETED_NO_VALID_PARTITIONS",
    "RUN_COMPLETED_WITH_VALID_PARTITIONS",
    "SCHEMA_VERSION",
    "TICKET_ID",
    "BLOCKED_CALENDAR_BINDING_MISMATCH",
    "BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE",
    "BLOCKED_DUPLICATE_FOLD_ORDINAL",
    "BLOCKED_EMPTY_FOLD_SCHEDULE",
    "BLOCKED_FOLD_BOUNDARY_SESSIONS_DISAGREE",
    "BLOCKED_FOLD_NOT_AUTHORIZED",
    "BLOCKED_FOLD_ORDINAL_MISMATCH",
    "BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED",
    "BLOCKED_INCONSISTENT_SHARED_MODES",
    "BLOCKED_INCONSISTENT_SHARED_SCHEDULE",
    "BLOCKED_LEDGER_STATE_CARRY_BROKEN",
    "BLOCKED_MALFORMED_COMPOSED_WALK_FORWARD_INPUT",
    "BLOCKED_NETWORK_EGRESS_REACHABLE",
    "BLOCKED_POSITION_STATE_CARRY_BROKEN",
    "BLOCKED_PREDECESSOR_FOLD_DEGRADED_NO_CARRY",
    "BLOCKED_RESULT_IDENTITY_TAMPERED",
    "BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT",
    "BLOCKED_RUN_DIRECTORY_EXISTS",
    "BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH",
    "BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH",
    "BLOCKED_STAGED_ARTIFACT_TAMPERED",
    "ChainLink",
    "ComposedPartition",
    "ComposedWalkForwardError",
    "ComposedWalkForwardPlan",
    "ComposedWalkForwardResult",
    "DegradedComposedPartition",
    "FoldSlot",
    "ValidComposedAggregate",
    "ValidComposedPartition",
    "aggregate_valid_partitions",
    "assert_declared_calendar_witnesses_injected",
    "assert_network_egress_denied",
    "assert_states_are_complete",
    "bound_input_manifest",
    "execute_composed_walk_forward",
    "expected_artifact_filenames",
    "fold_id_of_slot",
    "require_valid_partition",
    "run_and_publish_composed_walk_forward",
    "run_id_hex_of",
    "run_id_of",
]
