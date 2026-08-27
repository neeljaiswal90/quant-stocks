"""Composition ticket C: one deterministic composed fold over seven engines.

``KERNEL_ID`` ``QME-COMPOSITION-COMPOSED-FOLD-V1``;
``SCHEMA_VERSION`` ``qme.composed_fold.v1``;
``ticket_id`` ``PENDING_OWNER_ASSIGNMENT`` (composition ticket C under gate
NEE-108, lead plan 2026-08-25).

This module ORCHESTRATES the seven quant engines end to end for ONE fold. It
NEVER reimplements any engine's scoring, screening, weighting, accounting,
costing, benchmarking, or calendar logic: every number that a downstream step
uses is a CONSUMED attribute of the prior engine's typed output, never a
re-derivation. The chain, and the exact consumed attribute at each seam, is:

1. :func:`qme.quant.schedule_v1.derive_rebalance_schedule` -> the fold's
   ``RebalanceEvent`` at the declared ordinal. Consumed: ``event.signal_session``,
   ``event.fill_session``, ``event.signal_session_position``,
   ``event.fill_session_position``, ``event.recent_anchor_session``,
   ``event.old_anchor_session`` and ``event.warmup_state``. Sessions are consumed,
   never computed here; a ``WARMUP_INSUFFICIENT_HISTORY`` event degrades.
2. :func:`qme.quant.universe_v1.build_point_in_time_universe` over the consumed
   ``event.signal_session``. Consumed: ``snapshot.included_rows()`` -> each
   ``IncludedRow.security_id``.
3. :func:`qme.quant.signal_v1.evaluate_signal_cross_section` with the universe
   membership from step 2 as each security's declared ``universe_membership`` and
   the consumed anchor sessions. Consumed: ``result.selected_security_ids`` and
   ``result.selection_size`` (``K_t``). Selection/rank/breadth are NOT recomputed.
4. :func:`qme.quant.targets_v1.construct_targets` over the consumed selected set
   and ``K_t``. Consumed: ``result.signed_deltas()``. Weighting/repair is NOT
   reimplemented.
5. :func:`qme.quant.execution_v1.run_execution_program` over an
   ``ExecutionProgram`` built from the consumed deltas exactly as the targets
   lane's own two-sided oracle builds it, on the SHARED calendar's REAL sessions
   (``event.signal_session`` / ``event.fill_session``). Fills are NOT re-derived,
   and the IMMUTABLE closing portfolio state (``cash_plus``, ``positions_plus``,
   ``receivables_plus``, ``nav_plus``, published ``open_lots``, and any
   corporate-action ``*_after_payment`` fields) is READ straight off the engine
   ledger / tax-lot ledger, never recomputed. ``open_lots`` is bound into the
   carried-state identity ONLY as TAMPER-EVIDENCE; lot cost basis and acquisition
   are NOT carried into a successor fold (there is no incoming-lot engine seam).
6. :func:`qme.quant.scenarios_v1.evaluate_cost_turnover_capacity_scenarios` over
   the step-5 run. Consumed from the ledger: ``gtn_ratio``, ``one_way_turnover``,
   ``gross_trade_notional`` and ``nav_minus`` (recomputing them is a defect).
7. :mod:`qme.quant.benchmarks_v1`: a ``StrategyLedgerBasis`` built on the SAME
   initial capital (the strategy fold's CONSUMED opening NAV,
   ``ExecutionRun.initial_nav``), calendar, eligible sessions, cost-tax config, and
   availability cutoff the strategy used, with the control constructed by CALLING
   the execution engine. The control opens the strategy fold's whole opening NAV as
   cash and buys the reference security (NEE-130 same-initial-capital invariant); a
   control whose consumed initial NAV does not witness the strategy fold's opening
   NAV fails closed (``BLOCKED_BENCHMARK_CAPITAL_NOT_ALIGNED``). Consumed: the
   strategy ``ExecutionRun`` as the parity basis.

ONE UNIFIED SESSION AXIS. Schedule, universe, signal, targets, execution,
scenarios and benchmarks all run on the SAME accepted XNAS calendar
(:data:`qme.data.stores.calendar_v1.CALENDAR_ID` /
:data:`~qme.data.stores.calendar_v1.CALENDAR_SHA256_GROUPED`). A declared
:class:`SessionAxis` binds that calendar identity, timezone, and ordered
session-vector digest; the injected calendar and the universe spine must witness
it EXACTLY, and every consumed schedule/execution session must be a member of the
shared vector, before any valid fold is published. There is no synthetic
ledger calendar: the execution program's opening / fill / mark sessions ARE the
schedule event's own real sessions.

Run/fold identity is a SHA256 over the canonical BOUND-INPUT manifest ONLY.
Derived artifacts (the constructed program, the ledger, the closing portfolio,
scenario and benchmark outputs) bind into a separate ``result_identity`` and
NEVER back into the bound inputs. Timestamps live only in a provenance block
excluded from both identities, so a run under a different clock, timezone, or
``PYTHONHASHSEED`` reproduces the same ``fold_id`` and ``result_identity``.

Establishing that a successor fold can OPEN on this fold's exposed closing
portfolio is a mechanical carry property of TEST_CONSTRUCTED inputs. No
production, prospective-consumption, empirical-performance, alpha,
capacity-value, production-readiness, position-continuity readiness, or
live-order claim is made anywhere.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from qme.data.stores import calendar_v1
from qme.foundation.lineage import canonical_json_bytes
from qme.quant import (
    benchmarks_v1,
    equations,
    execution_v1,
    scenarios_v1,
    schedule_v1,
    signal_v1,
    targets_v1,
    universe_v1,
)

KERNEL_ID: Final = "QME-COMPOSITION-COMPOSED-FOLD-V1"
SCHEMA_VERSION: Final = "qme.composed_fold.v1"
TICKET_ID: Final = "PENDING_OWNER_ASSIGNMENT"

#: No result of this fold is a production, prospective, empirical, alpha,
#: capacity-value, production-readiness, position-continuity-readiness,
#: exact-lot-carry, or live-order claim. Position-level continuity is exposed as a
#: mechanical carry property of TEST_CONSTRUCTED inputs, never a readiness claim.
#: ``exact_lot_carry`` is a NON-claim: the published ``open_lots`` are bound into
#: ``carry_identity`` only as TAMPER-EVIDENCE. Lot cost basis and acquisition are
#: NOT carried into a successor fold (the read-only execution engine exposes no
#: incoming-lot interface), so a position-bearing successor fails closed in the
#: walk-forward lane rather than claim exact lot continuity.
NON_CLAIMS: Final[Mapping[str, str]] = {
    "alpha": "NO_ALPHA_CLAIM",
    "capacity_value": "NO_CAPACITY_VALUE_CLAIM",
    "empirical_performance": "NO_EMPIRICAL_PERFORMANCE_CLAIM",
    "exact_lot_carry": "NO_EXACT_LOT_CARRY_CLAIM",
    "live_order": "NO_LIVE_ORDER_CLAIM",
    "position_continuity_readiness": "NO_POSITION_CONTINUITY_READINESS_CLAIM",
    "production_readiness": "NO_PRODUCTION_READINESS_CLAIM",
    "prospective_consumption": "NO_PROSPECTIVE_CONSUMPTION_CLAIM",
}

# ---------------------------------------------------------------------------
# Typed fold states (VALID / degraded-with-reason) and completeness
# ---------------------------------------------------------------------------

COMPOSED_FOLD_VALID: Final = "COMPOSED_FOLD_VALID"
COMPOSED_FOLD_DEGRADED: Final = "COMPOSED_FOLD_DEGRADED"
COMPOSED_FOLD_STATES: Final[tuple[str, ...]] = (
    COMPOSED_FOLD_DEGRADED,
    COMPOSED_FOLD_VALID,
)

#: Fold-level structural refusals (never an engine's; those are surfaced verbatim).
#: ``BLOCKED_BENCHMARK_CAPITAL_NOT_ALIGNED`` fails closed when the benchmark control
#: cannot be opened on the SAME initial capital (opening NAV) as the strategy fold
#: (NEE-130 same-initial-capital invariant), rather than publishing a mismatched
#: benchmark as valid.
BLOCKED_BENCHMARK_CAPITAL_NOT_ALIGNED: Final = "BLOCKED_BENCHMARK_CAPITAL_NOT_ALIGNED"
BLOCKED_EMPTY_INCLUDED_UNIVERSE: Final = "BLOCKED_EMPTY_INCLUDED_UNIVERSE"
BLOCKED_EVENT_ORDINAL_OUT_OF_RANGE: Final = "BLOCKED_EVENT_ORDINAL_OUT_OF_RANGE"
BLOCKED_INCLUDED_SECURITY_WITHOUT_SIGNAL_INPUT: Final = (
    "BLOCKED_INCLUDED_SECURITY_WITHOUT_SIGNAL_INPUT"
)
#: Unified-session-axis refusals raised BEFORE any engine runs (Part 5).
BLOCKED_SESSION_AXIS_CALENDAR_MISMATCH: Final = "BLOCKED_SESSION_AXIS_CALENDAR_MISMATCH"
BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH: Final = (
    "BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH"
)
BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH: Final = "BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH"
BLOCKED_SESSION_NOT_ON_SHARED_AXIS: Final = "BLOCKED_SESSION_NOT_ON_SHARED_AXIS"
COMPOSED_FOLD_STRUCTURAL_STATES: Final[tuple[str, ...]] = (
    BLOCKED_BENCHMARK_CAPITAL_NOT_ALIGNED,
    BLOCKED_EMPTY_INCLUDED_UNIVERSE,
    BLOCKED_EVENT_ORDINAL_OUT_OF_RANGE,
    BLOCKED_INCLUDED_SECURITY_WITHOUT_SIGNAL_INPUT,
    BLOCKED_SESSION_AXIS_CALENDAR_MISMATCH,
    BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH,
    BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH,
    BLOCKED_SESSION_NOT_ON_SHARED_AXIS,
)

#: Pseudo-engine label for structural session-axis refusals (not one of the seven).
SESSION_AXIS_STAGE: Final[tuple[int, str]] = (0, "session_axis")

#: The ordered engine seams and the class of each engine's typed error, so the
#: fold surfaces every refusal VERBATIM (never renamed) as the degraded reason.
ENGINE_STAGES: Final[tuple[tuple[int, str], ...]] = (
    (1, "schedule"),
    (2, "universe"),
    (3, "signal"),
    (4, "targets"),
    (5, "execution"),
    (6, "scenarios"),
    (7, "benchmarks"),
)

#: The verbatim fail-closed state each engine raises against its shipped-EMPTY
#: registry. Re-checked against the engine modules by the test suite so a rename
#: on either side fails loudly.
ENGINE_EMPTY_REGISTRY_STATES: Final[Mapping[str, str]] = {
    "schedule": schedule_v1.BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY,
    "universe": universe_v1.BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS,
    "signal": signal_v1.BLOCKED_NO_REGISTERED_FEATURE_VARIANT,
    "targets": execution_v1.BLOCKED_NO_REGISTERED_COST_RATE_POLICY,
    "execution": execution_v1.BLOCKED_NO_REGISTERED_PARTICIPATION_LIMIT,
    "scenarios": scenarios_v1.BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK,
    "benchmarks": benchmarks_v1.BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL,
}


def assert_states_complete() -> None:
    """Every fold state is registered; the two axes do not overlap."""

    assert set(COMPOSED_FOLD_STATES) == {COMPOSED_FOLD_DEGRADED, COMPOSED_FOLD_VALID}
    assert COMPOSED_FOLD_VALID not in COMPOSED_FOLD_STRUCTURAL_STATES
    assert COMPOSED_FOLD_DEGRADED not in COMPOSED_FOLD_STRUCTURAL_STATES
    assert list(COMPOSED_FOLD_STRUCTURAL_STATES) == sorted(set(COMPOSED_FOLD_STRUCTURAL_STATES))
    assert all(state.startswith("BLOCKED_") for state in COMPOSED_FOLD_STRUCTURAL_STATES)
    assert set(dict(ENGINE_STAGES).values()) == set(ENGINE_EMPTY_REGISTRY_STATES)


class ComposedFoldError(ValueError):
    """A structural refusal raised while assembling a fold input, never an engine's."""

    def __init__(self, state: str, message: str) -> None:
        super().__init__(f"{state}: {message}")
        self.state = state


# ---------------------------------------------------------------------------
# Grouped digests (eight 8-hex groups); no arithmetic, no float
# ---------------------------------------------------------------------------

_EIGHT_HEX = re.compile(r"[0-9a-f]{8}")
_GROUPED_RE = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")


def _grouped(hexdigest: str) -> str:
    """Render a 64-char hex digest as eight colon-joined 8-hex groups."""

    return ":".join(_EIGHT_HEX.findall(hexdigest))


def _sha256_grouped(payload: bytes) -> str:
    return _grouped(hashlib.sha256(payload).hexdigest())


def _digest(document: Mapping[str, Any]) -> str:
    """Grouped SHA256 over the canonical JSON bytes of ``document``."""

    return _sha256_grouped(canonical_json_bytes(document))


def _ungroup(value: str) -> str:
    """Strip grouping so a grouped digest can seed a contiguous evidence hash."""

    return value.replace(":", "")


# ---------------------------------------------------------------------------
# Engine identities: (declared id, grouped self-hash over the engine source)
# ---------------------------------------------------------------------------

#: engine name -> (module attribute holding its declared id, source filename)
_ENGINE_SOURCES: Final[Mapping[str, tuple[str, str]]] = {
    "schedule": (schedule_v1.KERNEL_ID, "schedule_v1.py"),
    "universe": (universe_v1.KERNEL_ID, "universe_v1.py"),
    "signal": (signal_v1.ENGINE_ID, "signal_v1.py"),
    "targets": (targets_v1.KERNEL_ID, "targets_v1.py"),
    "execution": (execution_v1.ENGINE_ID, "execution_v1.py"),
    "scenarios": (scenarios_v1.KERNEL_ID, "scenarios_v1.py"),
    "benchmarks": (benchmarks_v1.ENGINE_ID, "benchmarks_v1.py"),
}


@dataclass(frozen=True)
class EngineIdentity:
    """One engine's bound identity: its declared id and grouped source self-hash."""

    engine: str
    engine_id: str
    source_sha256_grouped: str

    def to_json_dict(self) -> dict[str, str]:
        return {
            "engine": self.engine,
            "engine_id": self.engine_id,
            "source_sha256_grouped": self.source_sha256_grouped,
        }


def engine_identities(repository_root: Path) -> dict[str, EngineIdentity]:
    """Bind each engine's declared id and grouped self-hash over its source bytes."""

    quant = repository_root.joinpath("qme", "quant")
    identities: dict[str, EngineIdentity] = {}
    for engine, (engine_id, filename) in _ENGINE_SOURCES.items():
        source = quant.joinpath(filename)
        identities[engine] = EngineIdentity(
            engine=engine,
            engine_id=engine_id,
            source_sha256_grouped=_sha256_grouped(source.read_bytes()),
        )
    return identities


def _engine_identity_document(identities: Mapping[str, EngineIdentity]) -> dict[str, Any]:
    return {name: identities[name].to_json_dict() for name in sorted(identities)}


# ---------------------------------------------------------------------------
# Lineage: input / config / code / schema grouped digests on every result
# ---------------------------------------------------------------------------

_MODULE_FILENAME: Final = "composed_fold_v1.py"


def _schema_descriptor() -> dict[str, Any]:
    return {
        "bound_input_manifest_fields": sorted(BOUND_INPUT_MANIFEST_FIELDS),
        "engine_stages": [list(stage) for stage in ENGINE_STAGES],
        "kernel_id": KERNEL_ID,
        "schema_version": SCHEMA_VERSION,
        "states": list(COMPOSED_FOLD_STATES),
        "structural_states": list(COMPOSED_FOLD_STRUCTURAL_STATES),
    }


def _schema_sha256_grouped() -> str:
    return _digest(_schema_descriptor())


def _fold_lineage(
    *,
    fold_id: str,
    identities: Mapping[str, EngineIdentity],
    repository_root: Path,
) -> dict[str, str]:
    source = repository_root.joinpath("qme", "experiments", _MODULE_FILENAME)
    return {
        "input_sha256_grouped": fold_id,
        "config_sha256_grouped": _digest(_engine_identity_document(identities)),
        "code_sha256_grouped": _sha256_grouped(source.read_bytes()),
        "schema_sha256_grouped": _schema_sha256_grouped(),
    }


# ---------------------------------------------------------------------------
# The ONE unified session axis (Part 5): a declared calendar/session manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionAxis:
    """The ONE immutable calendar/session manifest every engine input must witness.

    Binds the accepted calendar's identity, timezone, and the grouped digest over
    its ordered session vector. The injected trading calendar and the universe
    spine must AGREE with all three exactly, and every consumed schedule /
    execution session must be a member of the shared vector, before a valid fold
    is published.
    """

    calendar_id: str
    calendar_sha256_grouped: str
    timezone: str
    session_ids_sha256_grouped: str

    @classmethod
    def from_calendar(cls, calendar: calendar_v1.TradingCalendar) -> SessionAxis:
        return cls(
            calendar_id=calendar.calendar_id,
            calendar_sha256_grouped=calendar.bytes_sha256_grouped,
            timezone=calendar.timezone,
            session_ids_sha256_grouped=calendar.session_ids_sha256_grouped,
        )

    def to_json_dict(self) -> dict[str, str]:
        return {
            "calendar_id": self.calendar_id,
            "calendar_sha256_grouped": self.calendar_sha256_grouped,
            "session_ids_sha256_grouped": self.session_ids_sha256_grouped,
            "timezone": self.timezone,
        }


def check_session_axis(
    axis: SessionAxis,
    *,
    calendar: calendar_v1.TradingCalendar,
    spine: universe_v1.SessionSpine,
) -> str | None:
    """Refuse unless the injected calendar AND the universe spine witness the axis.

    Returns a typed ``BLOCKED_SESSION_AXIS_*`` state on the FIRST disagreement, or
    ``None`` when the calendar identity, timezone, and ordered session vector all
    agree across the schedule calendar and the universe spine. No engine has run
    yet, so a mismatch fails closed before any valid result exists.
    """

    if (
        calendar.calendar_id != axis.calendar_id
        or calendar.bytes_sha256_grouped != axis.calendar_sha256_grouped
    ):
        return BLOCKED_SESSION_AXIS_CALENDAR_MISMATCH
    if calendar.timezone != axis.timezone:
        return BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH
    if calendar.session_ids_sha256_grouped != axis.session_ids_sha256_grouped:
        return BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH
    if (
        spine.calendar_id != axis.calendar_id
        or spine.calendar_sha256_grouped != axis.calendar_sha256_grouped
    ):
        return BLOCKED_SESSION_AXIS_CALENDAR_MISMATCH
    if spine.session_ids_sha256_grouped != axis.session_ids_sha256_grouped:
        return BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH
    return None


# ---------------------------------------------------------------------------
# Locally pinned, typed fold inputs (all BOUND inputs; no derived artifact here)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleBinding:
    """Schedule inputs: the accepted calendar, the policy id, range, offsets, event."""

    calendar: calendar_v1.TradingCalendar
    schedule_policy_id: str
    range_start: str
    range_end: str
    lookback_sessions: int
    skip_sessions: int
    event_ordinal: int
    schedule_policies: tuple[schedule_v1.SchedulePolicy, ...]


@dataclass(frozen=True)
class UniverseBinding:
    """Universe inputs; the point-in-time session is CONSUMED from the schedule event."""

    candidates: tuple[universe_v1.UniverseCandidate, ...]
    required_listings: tuple[universe_v1.RequiredListing, ...]
    required_coverage_series: tuple[str, ...]
    analysis_as_of: str
    spine: universe_v1.SessionSpine
    threshold_set_id: str
    threshold_registry: tuple[universe_v1.UniverseThresholdSet, ...]
    universe_rules_version: str


@dataclass(frozen=True)
class SignalObservationPair:
    """One security's pinned total-return closes at the fold's anchor sessions."""

    recent_total_return_close: str
    old_total_return_close: str
    observed_span_start: str
    total_return_chain_state: str
    source_freshness_state: str


@dataclass(frozen=True)
class SignalBinding:
    """Signal inputs; anchor sessions are CONSUMED from the schedule event."""

    per_security: Mapping[str, SignalObservationPair]
    variant_id: str
    tie_policy_id: str
    breadth_threshold_id: str
    analysis_cutoff: str
    variants: tuple[signal_v1.FeatureVariant, ...]
    tie_policies: tuple[signal_v1.TieBreakPolicy, ...]
    breadth_minimums: tuple[signal_v1.BreadthMinimum, ...]


@dataclass(frozen=True)
class ExecutionBinding:
    """Targets/execution inputs: prior state, raw prices, the pinned ledger world.

    Carries NO session and NO calendar identity of its own: the execution
    program's opening / fill / mark sessions are the schedule event's OWN real
    sessions on the shared :class:`SessionAxis`, so there is exactly one calendar.
    ``opening_cash`` / ``prior_positions`` / ``opening_receivables`` are the
    portfolio a successor fold OPENS on -- in a walk-forward they are the
    predecessor's exposed CLOSING state, consumed, not caller-declared.
    """

    program_id: str
    share_mode: str
    regulatory_fee_mode: str
    cost_policy_id: str
    transaction_tax_policy: equations.TransactionTaxPolicy
    registries: execution_v1.RegistryOverrides
    participation_limit_id: str
    prior_positions: Mapping[str, str]
    price_by_security: Mapping[str, str]
    opening_cash: str
    opening_receivables: str
    declared_pre_trade_nav: str
    ledger_source_id: str
    ledger_snapshot_id: str
    ledger_snapshot_sha256_grouped: str
    fill_reason_code: str
    rebalance_id: str


@dataclass(frozen=True)
class LiquidityBarSpec:
    """One prior-session raw bar for the ADV liquidity evidence."""

    session_id: str
    raw_close: str
    raw_volume: str


@dataclass(frozen=True)
class ScenariosBinding:
    """Scenario inputs: ADV evidence and the TEST_CONSTRUCTED lookback/participation."""

    adv_bars_by_security: Mapping[str, tuple[LiquidityBarSpec, ...]]
    lookback_id: str
    participation_scenario_id: str
    lookbacks: tuple[scenarios_v1.LiquidityLookbackPolicy, ...]
    participation_scenarios: tuple[scenarios_v1.ParticipationScenario, ...]


@dataclass(frozen=True)
class BenchmarksBinding:
    """Benchmark inputs: the strategy basis parameters and one reference control.

    The eligible-session window and the reference control's fill / eligible
    sessions are CONSUMED from the schedule event (the shared axis), never
    declared here, so a control cannot run on a second calendar.
    """

    strategy_id: str
    strategy_config_dimensions: Mapping[str, str]
    control_id: str
    trading_frequency: str
    control_registry: tuple[benchmarks_v1.BenchmarkControlDefinition, ...]
    reference_price: str
    reference_delta_raw_shares: str
    reference_rebalance_id: str


@dataclass(frozen=True)
class ComposedFoldInputs:
    """One fold's complete, typed, locally-pinned input bundle."""

    session_axis: SessionAxis
    schedule: ScheduleBinding
    universe: UniverseBinding
    signal: SignalBinding
    execution: ExecutionBinding
    scenarios: ScenariosBinding
    benchmarks: BenchmarksBinding


# ---------------------------------------------------------------------------
# The BOUND-INPUT manifest (inputs only) and the derived result identity
# ---------------------------------------------------------------------------

#: The exact top-level field set of the bound-input manifest. Every entry is an
#: INPUT; no derived artifact (program, ledger, closing portfolio, scenario or
#: benchmark output, the selected set, or the signed deltas) may appear here.
BOUND_INPUT_MANIFEST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "kernel_id",
        "ticket_id",
        "session_axis",
        "schedule",
        "universe",
        "signal",
        "portfolio_prior",
        "raw_prices",
        "tax_policy",
        "registries",
        "execution_binding",
        "scenarios_binding",
        "benchmarks_binding",
        "engine_identities",
    }
)


def _schedule_manifest(binding: ScheduleBinding) -> dict[str, Any]:
    calendar = binding.calendar
    return {
        "schedule_policy_id": binding.schedule_policy_id,
        "event_ordinal": binding.event_ordinal,
        "range_start": binding.range_start,
        "range_end": binding.range_end,
        "lookback_sessions": binding.lookback_sessions,
        "skip_sessions": binding.skip_sessions,
        "calendar_id": calendar.calendar_id,
        "calendar_sha256_grouped": calendar.bytes_sha256_grouped,
        "registered_policy_ids": sorted(policy.policy_id for policy in binding.schedule_policies),
    }


def _universe_manifest(binding: UniverseBinding) -> dict[str, Any]:
    candidates_document = [
        candidate.to_json_dict()
        for candidate in sorted(binding.candidates, key=lambda item: item.key)
    ]
    listings_document = sorted(listing.key for listing in binding.required_listings)
    return {
        "threshold_set_id": binding.threshold_set_id,
        "universe_rules_version": binding.universe_rules_version,
        "analysis_as_of": binding.analysis_as_of,
        "required_coverage_series": sorted(binding.required_coverage_series),
        "candidates_sha256_grouped": _digest({"candidates": candidates_document}),
        "required_listings": [list(item) for item in listings_document],
        "threshold_registry_ids": sorted(
            item.threshold_set_id for item in binding.threshold_registry
        ),
        "spine_sha256_grouped": binding.spine.session_ids_sha256_grouped,
    }


def _signal_manifest(binding: SignalBinding) -> dict[str, Any]:
    per_security_document = {
        security_id: {
            "recent_total_return_close": pair.recent_total_return_close,
            "old_total_return_close": pair.old_total_return_close,
            "observed_span_start": pair.observed_span_start,
            "total_return_chain_state": pair.total_return_chain_state,
            "source_freshness_state": pair.source_freshness_state,
        }
        for security_id, pair in binding.per_security.items()
    }
    return {
        "variant_id": binding.variant_id,
        "tie_policy_id": binding.tie_policy_id,
        "breadth_threshold_id": binding.breadth_threshold_id,
        "analysis_cutoff": binding.analysis_cutoff,
        "per_security_sha256_grouped": _digest({"per_security": per_security_document}),
        "registered_variant_ids": sorted(item.variant_id for item in binding.variants),
        "registered_tie_policy_ids": sorted(item.policy_id for item in binding.tie_policies),
        "registered_breadth_ids": sorted(item.threshold_id for item in binding.breadth_minimums),
    }


def _tax_policy_document(policy: equations.TransactionTaxPolicy) -> dict[str, str]:
    return {
        "policy_id": policy.policy_id,
        "policy_sha256": policy.policy_sha256,
        "source_id": policy.source_id,
        "assessment_base": policy.assessment_base,
        "assessment_side": policy.assessment_side.value,
        "rate_bps": str(policy.rate_bps),
    }


def _execution_manifest(binding: ExecutionBinding) -> dict[str, Any]:
    return {
        "program_id": binding.program_id,
        "share_mode": binding.share_mode,
        "regulatory_fee_mode": binding.regulatory_fee_mode,
        "cost_policy_id": binding.cost_policy_id,
        "participation_limit_id": binding.participation_limit_id,
        "opening_cash": binding.opening_cash,
        "opening_receivables": binding.opening_receivables,
        "declared_pre_trade_nav": binding.declared_pre_trade_nav,
        "ledger_source_id": binding.ledger_source_id,
        "ledger_snapshot_id": binding.ledger_snapshot_id,
        "ledger_snapshot_sha256_grouped": binding.ledger_snapshot_sha256_grouped,
        "fill_reason_code": binding.fill_reason_code,
        "rebalance_id": binding.rebalance_id,
    }


def _scenarios_manifest(binding: ScenariosBinding) -> dict[str, Any]:
    bars_document = {
        security_id: [
            {
                "session_id": bar.session_id,
                "raw_close": bar.raw_close,
                "raw_volume": bar.raw_volume,
            }
            for bar in bars
        ]
        for security_id, bars in binding.adv_bars_by_security.items()
    }
    return {
        "lookback_id": binding.lookback_id,
        "participation_scenario_id": binding.participation_scenario_id,
        "adv_bars_sha256_grouped": _digest({"adv_bars": bars_document}),
        "registered_lookback_ids": sorted(item.lookback_id for item in binding.lookbacks),
        "registered_participation_ids": sorted(
            item.scenario_id for item in binding.participation_scenarios
        ),
    }


def _benchmarks_manifest(binding: BenchmarksBinding) -> dict[str, Any]:
    return {
        "strategy_id": binding.strategy_id,
        "control_id": binding.control_id,
        "trading_frequency": binding.trading_frequency,
        "strategy_config_sha256_grouped": _digest(
            {"dimensions": dict(binding.strategy_config_dimensions)}
        ),
        "reference_price": binding.reference_price,
        "reference_delta_raw_shares": binding.reference_delta_raw_shares,
        "reference_rebalance_id": binding.reference_rebalance_id,
        "registered_control_ids": sorted(
            item.control_id for item in binding.control_registry
        ),
    }


def bound_input_manifest(
    inputs: ComposedFoldInputs,
    *,
    identities: Mapping[str, EngineIdentity],
) -> dict[str, Any]:
    """Assemble the canonical BOUND-INPUT manifest from inputs and engine identities.

    Contains INPUTS only. No constructed program, ledger, closing portfolio,
    scenario or benchmark output, selected set, or signed delta is ever bound here.
    """

    return {
        "schema_version": SCHEMA_VERSION,
        "kernel_id": KERNEL_ID,
        "ticket_id": TICKET_ID,
        "session_axis": inputs.session_axis.to_json_dict(),
        "schedule": _schedule_manifest(inputs.schedule),
        "universe": _universe_manifest(inputs.universe),
        "signal": _signal_manifest(inputs.signal),
        "portfolio_prior": {
            security_id: inputs.execution.prior_positions[security_id]
            for security_id in sorted(inputs.execution.prior_positions)
        },
        "raw_prices": {
            security_id: inputs.execution.price_by_security[security_id]
            for security_id in sorted(inputs.execution.price_by_security)
        },
        "tax_policy": _tax_policy_document(inputs.execution.transaction_tax_policy),
        "registries": inputs.execution.registries.to_json_dict(),
        "execution_binding": _execution_manifest(inputs.execution),
        "scenarios_binding": _scenarios_manifest(inputs.scenarios),
        "benchmarks_binding": _benchmarks_manifest(inputs.benchmarks),
        "engine_identities": _engine_identity_document(identities),
    }


def fold_id_of(manifest: Mapping[str, Any]) -> str:
    """The fold identity: grouped SHA256 over the canonical bound-input manifest."""

    return _digest(manifest)


# ---------------------------------------------------------------------------
# The immutable closing / opening portfolio (Part 4): read straight off the
# engine ledger and tax-lot ledger; never recomputed or re-derived here.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClosingPortfolioState:
    """The execution engine's IMMUTABLE closing portfolio state for this fold.

    Every field is READ verbatim from the engine's own outputs: ``cash`` from
    ``RebalanceLedger.cash_plus``, ``positions`` from ``positions_plus``,
    ``receivables`` from ``receivables_plus``, ``nav`` from ``nav_plus``,
    ``open_lots`` from the published ``LotPublication.open_lots``, and
    ``corporate_action_state`` from any fired ``CorporateActionOutcome``'s
    ``cash_after_payment`` / ``receivables_after_payment`` / ``nav_after_payment``.
    Nothing here is recomputed by the composition layer.

    ``open_lots`` is exposed and bound into ``carry_identity`` ONLY as
    TAMPER-EVIDENCE over the fold's own closing lots. It is NOT a carried/consumed
    incoming-lot surface: lot cost basis and acquisition are not threaded into a
    successor fold (the read-only execution engine has no incoming-lot interface),
    so a position-bearing successor fails closed in the walk-forward lane
    (``BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED``) rather than claim exact lot carry.
    """

    cash: str
    positions: Mapping[str, str]
    receivables: str
    nav: str
    open_lots: tuple[Mapping[str, Any], ...]
    corporate_action_state: tuple[Mapping[str, str], ...]

    def __post_init__(self) -> None:
        # Deep-freeze every carried mapping so no caller can mutate a completed
        # fold's closing state (and thereby make its computed ``carry_identity``
        # disagree with an already-created successor chain link). ``dict(...)``
        # copies for serialization still work over the immutable views.
        object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))
        object.__setattr__(
            self,
            "open_lots",
            tuple(MappingProxyType(dict(lot)) for lot in self.open_lots),
        )
        object.__setattr__(
            self,
            "corporate_action_state",
            tuple(MappingProxyType(dict(item)) for item in self.corporate_action_state),
        )

    def held_positions(self) -> dict[str, str]:
        """The non-zero holdings (a zeroed row is not a holding), sorted by id."""

        return {
            security_id: self.positions[security_id]
            for security_id in _sorted_ids(set(self.positions))
            if Decimal(self.positions[security_id]) != _DECIMAL_ZERO
        }

    def carry_document(self) -> dict[str, Any]:
        """The canonical carried-state document over cash+positions+lots+receivables+action."""

        return {
            "cash": self.cash,
            "corporate_action_state": [dict(item) for item in self.corporate_action_state],
            "held_positions": self.held_positions(),
            "open_lots": [dict(lot) for lot in self.open_lots],
            "receivables": self.receivables,
        }

    @property
    def carry_identity(self) -> str:
        """Grouped digest over the exact carried state (Part 4.3 chain material)."""

        return _digest(self.carry_document())

    def to_json_dict(self) -> dict[str, Any]:
        document = self.carry_document()
        document["carry_identity"] = self.carry_identity
        document["nav"] = self.nav
        document["positions"] = {
            security_id: self.positions[security_id]
            for security_id in _sorted_ids(set(self.positions))
        }
        return document


@dataclass(frozen=True)
class OpeningPortfolioState:
    """The engine-consumed OPENING portfolio state for this fold.

    ``cash`` / ``positions`` / ``receivables`` are the values the execution engine
    opened on (consumed verbatim from the bound opening state); ``nav`` is the
    engine-computed ``initial_nav`` (``RebalanceLedger``'s pre-trade ``nav_minus``
    surface). A successor's opening state is compared to the predecessor's closing
    state over these consumed figures, never a declared proxy.
    """

    cash: str
    positions: Mapping[str, str]
    receivables: str
    nav: str

    def __post_init__(self) -> None:
        # Deep-freeze the consumed opening holdings for the same reason the closing
        # state is frozen: a completed fold's opening composition is immutable.
        object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))

    def held_positions(self) -> dict[str, str]:
        return {
            security_id: self.positions[security_id]
            for security_id in _sorted_ids(set(self.positions))
            if Decimal(self.positions[security_id]) != _DECIMAL_ZERO
        }

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "cash": self.cash,
            "nav": self.nav,
            "positions": {
                security_id: self.positions[security_id]
                for security_id in _sorted_ids(set(self.positions))
            },
            "receivables": self.receivables,
        }


_DECIMAL_ZERO: Final = Decimal(0)

#: The canonical Q8 ledger zero. The benchmark control opens the strategy fold's
#: whole opening NAV as CASH (holding the reference security, not the strategy's
#: positions), so its opening receivables are exactly zero -- a literal constant,
#: never an arithmetic split of the opening NAV.
_LEDGER_ZERO: Final = "0.00000000"


# ---------------------------------------------------------------------------
# Frozen results: a degraded fold and a valid fold are DISTINCT types, so a
# degraded fold can never be coerced to a valid one (a mypy --strict wall).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DegradedComposedFold:
    """A fold that could not reach a valid composed result: typed, with a reason."""

    state: str
    bound_input_manifest: Mapping[str, Any]
    fold_id: str
    degraded_stage: int
    degraded_engine: str
    degraded_reason: str
    engine_identities: Mapping[str, EngineIdentity]
    result_identity: str
    lineage: Mapping[str, str]
    provenance: Mapping[str, str]

    def _identity_document(self) -> dict[str, Any]:
        return {
            "bound_input_manifest": dict(self.bound_input_manifest),
            "claims": dict(NON_CLAIMS),
            "degraded": {
                "degraded_engine": self.degraded_engine,
                "degraded_reason": self.degraded_reason,
                "degraded_stage": self.degraded_stage,
            },
            "engine_identities": _engine_identity_document(self.engine_identities),
            "fold_id": self.fold_id,
            "kernel_id": KERNEL_ID,
            "lineage": dict(self.lineage),
            "result_identity": self.result_identity,
            "schema_version": SCHEMA_VERSION,
            "state": self.state,
            "ticket_id": TICKET_ID,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self._identity_document())

    @property
    def self_sha256_grouped(self) -> str:
        return _sha256_grouped(self.canonical_bytes())

    def to_json_dict(self) -> dict[str, Any]:
        document = self._identity_document()
        document["provenance"] = dict(self.provenance)
        document["self_sha256_grouped"] = self.self_sha256_grouped
        return document


@dataclass(frozen=True)
class ValidComposedFold:
    """A fold that threaded all seven engines to one immutable composed result."""

    state: str
    bound_input_manifest: Mapping[str, Any]
    fold_id: str
    event_consumed: Mapping[str, Any]
    selected_security_ids: tuple[str, ...]
    selection_k_t: int
    program_identity: Mapping[str, str]
    ledger_identity: str
    ledger_figures: Mapping[str, Any]
    opening_portfolio: OpeningPortfolioState
    closing_portfolio: ClosingPortfolioState
    scenario_identity: str
    scenario_figures: Mapping[str, str]
    benchmark_identity: Mapping[str, str]
    engine_identities: Mapping[str, EngineIdentity]
    result_identity: str
    lineage: Mapping[str, str]
    provenance: Mapping[str, str]

    @property
    def final_nav(self) -> str:
        """The engine's consumed closing NAV (``nav_plus`` / run ``final_nav``)."""

        return self.closing_portfolio.nav

    @property
    def carry_identity(self) -> str:
        """The grouped digest over this fold's exact carried closing state."""

        return self.closing_portfolio.carry_identity

    def _derived_document(self) -> dict[str, Any]:
        return {
            "benchmark_identity": dict(self.benchmark_identity),
            "closing_portfolio": self.closing_portfolio.to_json_dict(),
            "event_consumed": dict(self.event_consumed),
            "ledger_figures": dict(self.ledger_figures),
            "ledger_identity": self.ledger_identity,
            "opening_portfolio": self.opening_portfolio.to_json_dict(),
            "program_identity": dict(self.program_identity),
            "scenario_figures": dict(self.scenario_figures),
            "scenario_identity": self.scenario_identity,
            "selected_security_ids": list(self.selected_security_ids),
            "selection_k_t": self.selection_k_t,
        }

    def _identity_document(self) -> dict[str, Any]:
        return {
            "bound_input_manifest": dict(self.bound_input_manifest),
            "claims": dict(NON_CLAIMS),
            "derived": self._derived_document(),
            "engine_identities": _engine_identity_document(self.engine_identities),
            "fold_id": self.fold_id,
            "kernel_id": KERNEL_ID,
            "lineage": dict(self.lineage),
            "result_identity": self.result_identity,
            "schema_version": SCHEMA_VERSION,
            "state": self.state,
            "ticket_id": TICKET_ID,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self._identity_document())

    @property
    def self_sha256_grouped(self) -> str:
        return _sha256_grouped(self.canonical_bytes())

    def to_json_dict(self) -> dict[str, Any]:
        document = self._identity_document()
        document["provenance"] = dict(self.provenance)
        document["self_sha256_grouped"] = self.self_sha256_grouped
        return document


#: The union a caller must narrow before touching any valid-only field.
ComposedFoldResult = ValidComposedFold | DegradedComposedFold


def _result_identity(fold_id: str, derived: Mapping[str, Any], state: str) -> str:
    """Grouped SHA256 over the fold id and the DERIVED outputs (never the inputs alone)."""

    return _digest({"derived": dict(derived), "fold_id": fold_id, "state": state})


def _provenance() -> dict[str, str]:
    """Wall-clock provenance. Excluded from ``fold_id`` and ``result_identity``."""

    return {
        "composed_at": datetime.now(UTC).isoformat(),
        "kernel_id": KERNEL_ID,
        "schema_version": SCHEMA_VERSION,
        "ticket_id": TICKET_ID,
    }


# ---------------------------------------------------------------------------
# The derived ledger world: REAL sessions of the shared axis (Part 5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LedgerWorld:
    """The execution/benchmark session world DERIVED from the consumed event.

    Every session is a REAL session of the shared :class:`SessionAxis` calendar:
    ``opening`` and ``signal`` are the event's signal session; ``eligible`` and
    ``fill`` are the event's fill session (the exchange session immediately after
    the signal). There is no synthetic ledger calendar anywhere.
    """

    calendar_id: str
    calendar_sha256_grouped: str
    opening: execution_v1.SessionRef
    signal: execution_v1.SessionRef
    eligible: execution_v1.SessionRef
    fill: execution_v1.SessionRef
    observation_session: str
    available_at: str
    analysis_as_of: str
    trade_date: str
    charge_date: str
    availability_cutoff: str
    eligible_sessions: tuple[str, ...]


def _session_ref(axis: SessionAxis, session_date: str, ordinal: int) -> execution_v1.SessionRef:
    return execution_v1.SessionRef(
        calendar_id=axis.calendar_id,
        calendar_sha256_grouped=axis.calendar_sha256_grouped,
        session_date=date.fromisoformat(session_date),
        ordinal=ordinal,
    )


def _derive_ledger_world(
    axis: SessionAxis, event: schedule_v1.RebalanceEvent
) -> _LedgerWorld:
    """Build the ledger world from the consumed event on the shared axis."""

    signal_ref = _session_ref(axis, event.signal_session, event.signal_session_position)
    fill_ref = _session_ref(axis, event.fill_session, event.fill_session_position)
    return _LedgerWorld(
        calendar_id=axis.calendar_id,
        calendar_sha256_grouped=axis.calendar_sha256_grouped,
        opening=signal_ref,
        signal=signal_ref,
        eligible=fill_ref,
        fill=fill_ref,
        observation_session=event.signal_session,
        available_at=f"{event.signal_session}T00:00:00+00:00",
        analysis_as_of=f"{event.fill_session}T00:00:00+00:00",
        trade_date=event.fill_session,
        charge_date=event.fill_session,
        availability_cutoff=f"{event.fill_session}T21:00:00+00:00",
        eligible_sessions=(event.signal_session, event.fill_session),
    )


def derive_ledger_world(
    inputs: ComposedFoldInputs, *, repository_root: Path
) -> tuple[schedule_v1.RebalanceEvent, _LedgerWorld]:
    """Derive the consumed event and its REAL-session ledger world (test seam).

    Refuses with :class:`ComposedFoldError` if the session axis is not witnessed,
    the event ordinal is out of range, warmup is unsatisfied, or a boundary
    session is not a member of the shared vector. Used by ``compose_fold`` and by
    the acceptance tests that independently rebuild the execution program.
    """

    axis = inputs.session_axis
    axis_state = check_session_axis(
        axis, calendar=inputs.schedule.calendar, spine=inputs.universe.spine
    )
    if axis_state is not None:
        raise ComposedFoldError(axis_state, "the injected calendar does not witness the axis")
    binding = inputs.schedule
    schedule = schedule_v1.derive_rebalance_schedule(
        binding.calendar,
        schedule_policy_id=binding.schedule_policy_id,
        range_start=binding.range_start,
        range_end=binding.range_end,
        lookback_sessions=binding.lookback_sessions,
        skip_sessions=binding.skip_sessions,
        policies=binding.schedule_policies,
    )
    if not (0 <= binding.event_ordinal < len(schedule.events)):
        raise ComposedFoldError(
            BLOCKED_EVENT_ORDINAL_OUT_OF_RANGE, "event ordinal out of range"
        )
    event = schedule.events[binding.event_ordinal]
    membership = _session_membership_state(binding.calendar, event)
    if membership is not None:
        raise ComposedFoldError(membership, "a boundary session is not on the shared axis")
    return event, _derive_ledger_world(axis, event)


def _session_membership_state(
    calendar: calendar_v1.TradingCalendar, event: schedule_v1.RebalanceEvent
) -> str | None:
    """Refuse if a consumed boundary session is not a member of the shared vector."""

    for session_id in (event.signal_session, event.fill_session):
        if not calendar.is_session(session_id):
            return BLOCKED_SESSION_NOT_ON_SHARED_AXIS
    return None


# ---------------------------------------------------------------------------
# Seam builders (pure assembly of engine INPUTS; no scoring/weight/GTN/month math)
# ---------------------------------------------------------------------------


def _evidence(
    world: _LedgerWorld, binding: ExecutionBinding, security_id: str
) -> equations.MarketEvidenceBinding:
    return equations.MarketEvidenceBinding(
        security_id=security_id,
        source_id=binding.ledger_source_id,
        snapshot_id=binding.ledger_snapshot_id,
        snapshot_sha256=_ungroup(binding.ledger_snapshot_sha256_grouped),
        calendar_id=world.calendar_id,
        calendar_sha256=_ungroup(world.calendar_sha256_grouped),
        observation_start_session=date.fromisoformat(world.observation_session),
        observation_end_session=date.fromisoformat(world.observation_session),
        available_at=datetime.fromisoformat(world.available_at),
        analysis_as_of=datetime.fromisoformat(world.analysis_as_of),
    )


def _price(
    world: _LedgerWorld, binding: ExecutionBinding, security_id: str
) -> equations.RawExecutionPrice:
    return equations.RawExecutionPrice(
        value=Decimal(binding.price_by_security[security_id]),
        evidence=_evidence(world, binding, security_id),
    )


def _marks(
    world: _LedgerWorld, binding: ExecutionBinding, security_ids: Sequence[str]
) -> execution_v1.LedgerMarkSet:
    return execution_v1.LedgerMarkSet(
        marks={
            security_id: equations.RawMark(
                value=Decimal(binding.price_by_security[security_id]),
                evidence=_evidence(world, binding, security_id),
            )
            for security_id in security_ids
        }
    )


def _sorted_ids(security_ids: object) -> tuple[str, ...]:
    """Content-derived ordering by UTF-8 bytes; a permutation cannot reorder it."""

    assert isinstance(security_ids, (set, frozenset, list, tuple))
    return tuple(sorted({str(item) for item in security_ids}, key=lambda text: text.encode("utf-8")))


def _build_signal_inputs(
    binding: SignalBinding,
    *,
    included_ids: Sequence[str],
    recent_anchor_session: str,
    old_anchor_session: str,
) -> list[signal_v1.SecuritySessionInput]:
    """One declared cross-section row per included security; membership is CONSUMED."""

    rows: list[signal_v1.SecuritySessionInput] = []
    for security_id in included_ids:
        pair = binding.per_security.get(security_id)
        if pair is None:
            raise ComposedFoldError(
                BLOCKED_INCLUDED_SECURITY_WITHOUT_SIGNAL_INPUT,
                f"universe included {security_id!r} but no pinned signal input covers it",
            )
        rows.append(
            signal_v1.SecuritySessionInput(
                security_id=security_id,
                universe_membership=signal_v1.UNIVERSE_IN_REQUIRED_UNIVERSE,
                observed_span_start=pair.observed_span_start,
                total_return_chain_state=pair.total_return_chain_state,
                source_freshness_state=pair.source_freshness_state,
                observations=(
                    signal_v1.TotalReturnObservation(
                        old_anchor_session, pair.old_total_return_close
                    ),
                    signal_v1.TotalReturnObservation(
                        recent_anchor_session, pair.recent_total_return_close
                    ),
                ),
            )
        )
    return rows


def _build_target_request(
    binding: ExecutionBinding,
    world: _LedgerWorld,
    *,
    selected: tuple[str, ...],
    k_t: int,
) -> targets_v1.TargetConstructionRequest:
    trade_universe = _sorted_ids(set(selected) | set(binding.prior_positions))
    return targets_v1.TargetConstructionRequest(
        request_id=binding.program_id,
        selected=selected,
        declared_selection_count=k_t,
        prior_positions=dict(binding.prior_positions),
        raw_execution_prices={
            security_id: _price(world, binding, security_id) for security_id in trade_universe
        },
        cash_pre=binding.opening_cash,
        receivables_pre=binding.opening_receivables,
        declared_pre_trade_nav=binding.declared_pre_trade_nav,
        cost_policy_id=binding.cost_policy_id,
        transaction_tax_policy=binding.transaction_tax_policy,
        regulatory_fee_mode=binding.regulatory_fee_mode,
        registries=binding.registries,
    )


def _build_execution_program(
    binding: ExecutionBinding, world: _LedgerWorld, *, deltas: Mapping[str, str]
) -> execution_v1.ExecutionProgram:
    """Wrap the CONSUMED deltas exactly as the targets lane's two-sided oracle does."""

    trade_universe = _sorted_ids(set(deltas) | set(binding.prior_positions))
    eligible = execution_v1.derive_eligible_fill_session(world.signal, world.eligible)
    fill_session = execution_v1.FillSession(
        eligible=eligible,
        session=world.fill,
        reason_code=binding.fill_reason_code,
    )
    stage = execution_v1.RebalanceStage(
        rebalance_id=binding.rebalance_id,
        fill_session=fill_session,
        raw_marks=_marks(world, binding, trade_universe),
        target=execution_v1.DeclaredSignedDeltas(
            deltas=tuple(
                execution_v1.SignedTargetDelta(
                    security_id=security_id,
                    delta_raw_shares=deltas[security_id],
                    raw_execution_price=_price(world, binding, security_id),
                )
                for security_id in sorted(deltas)
            )
        ),
        trade_date=date.fromisoformat(world.trade_date),
        charge_date=date.fromisoformat(world.charge_date),
        availability={
            security_id: execution_v1.FillPriceAvailability(
                security_id=security_id,
                official_next_session_raw_open_available=True,
                declared_first_regular_session_print_available=False,
                halted=False,
                delisted_between_signal_and_fill=False,
            )
            for security_id in trade_universe
        },
        regulatory_trade_metadata={},
        participation_limit_id=binding.participation_limit_id,
    )
    return execution_v1.ExecutionProgram(
        program_id=binding.program_id,
        share_mode=binding.share_mode,
        regulatory_fee_mode=binding.regulatory_fee_mode,
        cost_policy_id=binding.cost_policy_id,
        transaction_tax_policy=binding.transaction_tax_policy,
        opening_session=world.opening,
        opening_cash=binding.opening_cash,
        opening_positions=dict(binding.prior_positions),
        opening_receivables=binding.opening_receivables,
        opening_marks=_marks(world, binding, _sorted_ids(set(binding.prior_positions))),
        stages=(stage,),
        registries=binding.registries,
    )


def _build_liquidity_evidence(
    binding: ScenariosBinding, *, rebalance_id: str, traded_ids: Sequence[str]
) -> list[scenarios_v1.LiquidityEvidence]:
    evidence: list[scenarios_v1.LiquidityEvidence] = []
    for security_id in traded_ids:
        bars = binding.adv_bars_by_security.get(security_id)
        if bars is None:
            continue
        evidence.append(
            scenarios_v1.LiquidityEvidence(
                rebalance_id=rebalance_id,
                security_id=security_id,
                bars=tuple(
                    scenarios_v1.RawSessionBar(
                        security_id=security_id,
                        session_id=bar.session_id,
                        raw_close=bar.raw_close,
                        raw_volume=bar.raw_volume,
                    )
                    for bar in bars
                ),
            )
        )
    return evidence


def _build_strategy_basis(
    inputs: ComposedFoldInputs, world: _LedgerWorld, *, opening_capital: str
) -> benchmarks_v1.StrategyLedgerBasis:
    """The strategy basis whose SAME-initial-capital surface is the strategy opening NAV.

    ``opening_capital`` is the strategy fold's CONSUMED, engine-computed opening NAV
    (``ExecutionRun.initial_nav`` = opening cash + opening positions valued at the
    opening marks). The benchmark control opens that whole capital as CASH and holds
    the reference security (not the strategy's positions), so the basis carries the
    opening NAV as ``opening_cash`` and zero receivables. This is the NEE-130
    "same initial capital" surface the benchmarks engine binds and verifies; the
    composition performs no arithmetic (it consumes ``initial_nav`` verbatim).
    """

    execution = inputs.execution
    benchmarks = inputs.benchmarks
    return benchmarks_v1.StrategyLedgerBasis(
        strategy_id=benchmarks.strategy_id,
        opening_session=world.opening,
        opening_cash=opening_capital,
        opening_receivables=_LEDGER_ZERO,
        eligible_sessions=world.eligible_sessions,
        availability_cutoff=world.availability_cutoff,
        cost_policy_id=execution.cost_policy_id,
        transaction_tax_policy=execution.transaction_tax_policy,
        share_mode=execution.share_mode,
        regulatory_fee_mode=execution.regulatory_fee_mode,
        registries=execution.registries,
        strategy_config=benchmarks_v1.ConfigFingerprint(
            dimensions=dict(benchmarks.strategy_config_dimensions)
        ),
    )


def _build_control_program(
    inputs: ComposedFoldInputs,
    world: _LedgerWorld,
    definition: benchmarks_v1.BenchmarkControlDefinition,
    *,
    opening_capital: str,
) -> execution_v1.ExecutionProgram:
    """A single-reference buy-and-hold that opens on the strategy fold's opening NAV.

    ``opening_capital`` (the strategy fold's consumed ``initial_nav``) is opened as
    CASH -- the control holds the reference security, not the strategy's positions --
    so the control is CAPITAL-ALIGNED to the strategy fold: same initial NAV, no
    compounded in-market window, no residual-cash under-capitalization.
    """

    execution = inputs.execution
    benchmarks = inputs.benchmarks
    reference = definition.reference_security_id
    assert reference is not None
    reference_binding = _reference_execution_binding(inputs)
    fill_session = execution_v1.FillSession(
        eligible=execution_v1.derive_eligible_fill_session(world.signal, world.eligible),
        session=world.fill,
        reason_code=execution.fill_reason_code,
    )
    stage = execution_v1.RebalanceStage(
        rebalance_id=benchmarks.reference_rebalance_id,
        fill_session=fill_session,
        raw_marks=_marks(world, reference_binding, (reference,)),
        target=execution_v1.DeclaredSignedDeltas(
            deltas=(
                execution_v1.SignedTargetDelta(
                    security_id=reference,
                    delta_raw_shares=benchmarks.reference_delta_raw_shares,
                    raw_execution_price=_price(world, reference_binding, reference),
                ),
            )
        ),
        trade_date=date.fromisoformat(world.fill.session_date.isoformat()),
        charge_date=date.fromisoformat(world.fill.session_date.isoformat()),
        availability={
            reference: execution_v1.FillPriceAvailability(
                security_id=reference,
                official_next_session_raw_open_available=True,
                declared_first_regular_session_print_available=False,
                halted=False,
                delisted_between_signal_and_fill=False,
            )
        },
        regulatory_trade_metadata={},
        participation_limit_id=execution.participation_limit_id,
    )
    return execution_v1.ExecutionProgram(
        program_id=f"{execution.program_id}-CONTROL",
        share_mode=execution.share_mode,
        regulatory_fee_mode=execution.regulatory_fee_mode,
        cost_policy_id=execution.cost_policy_id,
        transaction_tax_policy=execution.transaction_tax_policy,
        opening_session=world.opening,
        opening_cash=opening_capital,
        opening_positions={},
        opening_receivables=_LEDGER_ZERO,
        opening_marks=execution_v1.LedgerMarkSet(marks={}),
        stages=(stage,),
        registries=execution.registries,
    )


def _reference_execution_binding(inputs: ComposedFoldInputs) -> ExecutionBinding:
    """The strategy's ledger world, carrying the reference security's pinned price."""

    execution = inputs.execution
    prices = dict(execution.price_by_security)
    for definition in inputs.benchmarks.control_registry:
        if definition.reference_security_id is not None:
            prices.setdefault(
                definition.reference_security_id, inputs.benchmarks.reference_price
            )
    return _replace_prices(execution, prices)


def _replace_prices(
    binding: ExecutionBinding, prices: Mapping[str, str]
) -> ExecutionBinding:
    return ExecutionBinding(
        program_id=binding.program_id,
        share_mode=binding.share_mode,
        regulatory_fee_mode=binding.regulatory_fee_mode,
        cost_policy_id=binding.cost_policy_id,
        transaction_tax_policy=binding.transaction_tax_policy,
        registries=binding.registries,
        participation_limit_id=binding.participation_limit_id,
        prior_positions=binding.prior_positions,
        price_by_security=dict(prices),
        opening_cash=binding.opening_cash,
        opening_receivables=binding.opening_receivables,
        declared_pre_trade_nav=binding.declared_pre_trade_nav,
        ledger_source_id=binding.ledger_source_id,
        ledger_snapshot_id=binding.ledger_snapshot_id,
        ledger_snapshot_sha256_grouped=binding.ledger_snapshot_sha256_grouped,
        fill_reason_code=binding.fill_reason_code,
        rebalance_id=binding.rebalance_id,
    )


# ---------------------------------------------------------------------------
# The composed fold
# ---------------------------------------------------------------------------


def _event_consumed(event: schedule_v1.RebalanceEvent) -> dict[str, Any]:
    return {
        "event_ordinal": event.event_ordinal,
        "fill_session": event.fill_session,
        "fill_session_position": event.fill_session_position,
        "old_anchor_session": event.old_anchor_session,
        "recent_anchor_session": event.recent_anchor_session,
        "signal_session": event.signal_session,
        "signal_session_position": event.signal_session_position,
        "warmup_state": event.warmup_state,
    }


def _closing_portfolio(
    run: execution_v1.ExecutionRun, ledger: execution_v1.RebalanceLedger
) -> ClosingPortfolioState:
    """Read the engine's IMMUTABLE closing state off the ledger / tax-lot ledger."""

    corporate_action_state = tuple(
        {
            "stage_id": outcome.stage_id,
            "cash_after_payment": outcome.cash_after_payment,
            "receivables_after_payment": outcome.receivables_after_payment,
            "nav_after_payment": outcome.nav_after_payment,
        }
        for outcome in run.action_outcomes
    )
    return ClosingPortfolioState(
        cash=ledger.cash_plus,
        positions=dict(ledger.positions_plus),
        receivables=ledger.receivables_plus,
        nav=ledger.nav_plus,
        open_lots=tuple(dict(lot) for lot in run.lots.open_lots),
        corporate_action_state=corporate_action_state,
    )


def _opening_portfolio(
    binding: ExecutionBinding, run: execution_v1.ExecutionRun
) -> OpeningPortfolioState:
    """The engine-consumed opening state; ``nav`` is the engine's ``initial_nav``."""

    return OpeningPortfolioState(
        cash=binding.opening_cash,
        positions=dict(binding.prior_positions),
        receivables=binding.opening_receivables,
        nav=run.initial_nav,
    )


def _degraded(
    *,
    fold_id: str,
    manifest: Mapping[str, Any],
    identities: Mapping[str, EngineIdentity],
    stage: int,
    engine: str,
    reason: str,
    lineage: Mapping[str, str],
    provenance: Mapping[str, str],
) -> DegradedComposedFold:
    result_identity = _result_identity(
        fold_id,
        {"degraded_engine": engine, "degraded_reason": reason, "degraded_stage": stage},
        COMPOSED_FOLD_DEGRADED,
    )
    return DegradedComposedFold(
        state=COMPOSED_FOLD_DEGRADED,
        bound_input_manifest=manifest,
        fold_id=fold_id,
        degraded_stage=stage,
        degraded_engine=engine,
        degraded_reason=reason,
        engine_identities=identities,
        result_identity=result_identity,
        lineage=lineage,
        provenance=provenance,
    )


def compose_fold(
    inputs: ComposedFoldInputs,
    *,
    repository_root: Path,
    identities: Mapping[str, EngineIdentity] | None = None,
) -> ComposedFoldResult:
    """Thread one fold through the seven engines, or fail closed with a typed reason.

    The seven engine identities may be injected (``identities``) for identity
    tests; by default they are bound from the engine sources under
    ``repository_root``.
    """

    bound_identities = (
        dict(identities) if identities is not None else engine_identities(repository_root)
    )
    manifest = bound_input_manifest(inputs, identities=bound_identities)
    fold_id = fold_id_of(manifest)
    lineage = _fold_lineage(
        fold_id=fold_id, identities=bound_identities, repository_root=repository_root
    )
    provenance = _provenance()

    def degrade(stage: int, engine: str, reason: str) -> DegradedComposedFold:
        return _degraded(
            fold_id=fold_id,
            manifest=manifest,
            identities=bound_identities,
            stage=stage,
            engine=engine,
            reason=reason,
            lineage=lineage,
            provenance=provenance,
        )

    # 0. Unified session axis: the injected calendar AND the universe spine must
    #    witness the declared axis (id + hash + timezone + ordered vector) BEFORE
    #    any engine runs.
    axis = inputs.session_axis
    axis_state = check_session_axis(
        axis, calendar=inputs.schedule.calendar, spine=inputs.universe.spine
    )
    if axis_state is not None:
        return degrade(SESSION_AXIS_STAGE[0], SESSION_AXIS_STAGE[1], axis_state)

    # 1. Schedule -> the fold's rebalance event.
    schedule_binding = inputs.schedule
    try:
        schedule = schedule_v1.derive_rebalance_schedule(
            schedule_binding.calendar,
            schedule_policy_id=schedule_binding.schedule_policy_id,
            range_start=schedule_binding.range_start,
            range_end=schedule_binding.range_end,
            lookback_sessions=schedule_binding.lookback_sessions,
            skip_sessions=schedule_binding.skip_sessions,
            policies=schedule_binding.schedule_policies,
        )
    except schedule_v1.RebalanceScheduleError as error:
        return degrade(1, "schedule", error.state)
    if not (0 <= schedule_binding.event_ordinal < len(schedule.events)):
        return degrade(1, "schedule", BLOCKED_EVENT_ORDINAL_OUT_OF_RANGE)
    event = schedule.events[schedule_binding.event_ordinal]
    if event.warmup_state != schedule_v1.WARMUP_SATISFIED:
        return degrade(1, "schedule", event.warmup_state)
    if event.recent_anchor_session is None or event.old_anchor_session is None:
        return degrade(1, "schedule", event.warmup_state)

    # 0b. Every consumed boundary session must be a member of the shared vector.
    membership = _session_membership_state(schedule_binding.calendar, event)
    if membership is not None:
        return degrade(SESSION_AXIS_STAGE[0], SESSION_AXIS_STAGE[1], membership)
    world = _derive_ledger_world(axis, event)

    # 2. Universe -> the point-in-time eligible set at the consumed signal session.
    universe_binding = inputs.universe
    try:
        snapshot = universe_v1.build_point_in_time_universe(
            universe_binding.candidates,
            sessions=(event.signal_session,),
            required_listings=universe_binding.required_listings,
            required_coverage_series=universe_binding.required_coverage_series,
            analysis_as_of=universe_binding.analysis_as_of,
            spine=universe_binding.spine,
            threshold_set_id=universe_binding.threshold_set_id,
            threshold_registry=universe_binding.threshold_registry,
            universe_rules_version=universe_binding.universe_rules_version,
        )
    except universe_v1.PointInTimeUniverseError as error:
        return degrade(2, "universe", error.state)
    included_ids = _sorted_ids({row.security_id for row in snapshot.included_rows()})
    if not included_ids:
        return degrade(2, "universe", BLOCKED_EMPTY_INCLUDED_UNIVERSE)

    # 3. Signal -> the selected set and K_t (rank/breadth are the engine's, consumed).
    signal_binding = inputs.signal
    try:
        signal_inputs = _build_signal_inputs(
            signal_binding,
            included_ids=included_ids,
            recent_anchor_session=event.recent_anchor_session,
            old_anchor_session=event.old_anchor_session,
        )
    except ComposedFoldError as error:
        return degrade(3, "signal", error.state)
    try:
        signal_result = signal_v1.evaluate_signal_cross_section(
            signal_inputs,
            calendar=schedule_binding.calendar,
            signal_session=event.signal_session,
            analysis_cutoff=signal_binding.analysis_cutoff,
            variant_id=signal_binding.variant_id,
            tie_policy_id=signal_binding.tie_policy_id,
            breadth_threshold_id=signal_binding.breadth_threshold_id,
            variants=signal_binding.variants,
            tie_policies=signal_binding.tie_policies,
            breadth_minimums=signal_binding.breadth_minimums,
        )
    except signal_v1.SignalError as error:
        return degrade(3, "signal", error.state)
    if signal_result.selection_state != signal_v1.SELECTION_VALID:
        return degrade(3, "signal", signal_result.selection_state)
    selected = signal_result.selected_security_ids
    k_t = signal_result.selection_size

    # 4. Targets -> signed integer share deltas (weighting/repair is the engine's).
    execution_binding = inputs.execution
    try:
        target_result = targets_v1.construct_targets(
            _build_target_request(execution_binding, world, selected=selected, k_t=k_t),
            repository_root=repository_root,
        )
    except targets_v1.TargetConstructionError as error:
        return degrade(4, "targets", error.state)
    deltas = dict(target_result.signed_deltas())

    # 5. Execution -> the ledger (fills are the engine's; never re-derived here).
    program = _build_execution_program(execution_binding, world, deltas=deltas)
    try:
        run = execution_v1.run_execution_program(program, repository_root=repository_root)
    except execution_v1.ExecutionAccountingError as error:
        return degrade(5, "execution", error.state)
    ledger = run.rebalance_ledgers[0]
    closing_portfolio = _closing_portfolio(run, ledger)
    opening_portfolio = _opening_portfolio(execution_binding, run)

    # 6. Scenarios -> consumed GTN / NAV_minus / turnover from the ledger.
    scenarios_binding = inputs.scenarios
    try:
        report = scenarios_v1.evaluate_cost_turnover_capacity_scenarios(
            run,
            liquidity_evidence=_build_liquidity_evidence(
                scenarios_binding,
                rebalance_id=ledger.rebalance_id,
                traded_ids=_sorted_ids(set(deltas)),
            ),
            lookback_id=scenarios_binding.lookback_id,
            participation_scenario_id=scenarios_binding.participation_scenario_id,
            lookbacks=scenarios_binding.lookbacks,
            participation_scenarios=scenarios_binding.participation_scenarios,
        )
    except scenarios_v1.ScenarioError as error:
        return degrade(6, "scenarios", error.state)
    rebalance_scenario = report.rebalance(ledger.rebalance_id)

    # 7. Benchmarks -> a control built by CALLING execution, on the strategy basis.
    #    CAPITAL ALIGNMENT (NEE-130 "same initial capital"): the control opens on the
    #    strategy fold's CONSUMED opening NAV (run.initial_nav = opening cash + opening
    #    positions valued at the opening marks), supplied as the control's opening cash.
    #    The benchmarks engine's StrategyLedgerBasis same-initial-capital surface binds
    #    control.opening_cash == basis.opening_cash; we additionally verify the control's
    #    CONSUMED initial NAV witnesses the strategy fold's initial NAV, failing closed
    #    rather than publishing a mis-capitalized benchmark as valid.
    benchmarks_binding = inputs.benchmarks
    strategy_opening_capital = run.initial_nav
    basis = _build_strategy_basis(inputs, world, opening_capital=strategy_opening_capital)
    try:
        definition = benchmarks_v1.resolve_benchmark_control(
            benchmarks_binding.control_id, registry=benchmarks_binding.control_registry
        )
        control_program = _build_control_program(
            inputs, world, definition, opening_capital=strategy_opening_capital
        )
        benchmark_ledger = benchmarks_v1.construct_external_benchmark(
            definition=definition,
            basis=basis,
            program=control_program,
            trading_frequency=benchmarks_binding.trading_frequency,
            repository_root=repository_root,
        )
    except benchmarks_v1.BenchmarkControlError as error:
        return degrade(7, "benchmarks", error.state)
    if benchmark_ledger.run.initial_nav != strategy_opening_capital:
        return degrade(7, "benchmarks", BLOCKED_BENCHMARK_CAPITAL_NOT_ALIGNED)

    # Every seam threaded: bind the derived outputs into a separate result identity.
    ledger_figures: dict[str, Any] = {
        "rebalance_id": ledger.rebalance_id,
        "nav_minus": ledger.nav_minus,
        "nav_plus": ledger.nav_plus,
        "gross_trade_notional": ledger.gross_trade_notional,
        "gtn_ratio": ledger.gtn_ratio,
        "one_way_turnover": ledger.one_way_turnover,
        "initial_nav": run.initial_nav,
        "final_nav": run.final_nav,
        "execution_state": run.state,
    }
    scenario_figures = {
        "gtn_ratio": rebalance_scenario.gtn_ratio,
        "one_way_turnover": rebalance_scenario.one_way_turnover,
    }
    program_identity = {
        "program_id": program.program_id,
        "input_sha256_grouped": program.input_digest(),
    }
    benchmark_identity = {
        "control_id": benchmark_ledger.control_id,
        "control_initial_nav": benchmark_ledger.run.initial_nav,
        "strategy_basis_sha256_grouped": basis.sha256_grouped(),
        "run_sha256_grouped": benchmark_ledger.run_sha256_grouped,
    }
    derived = {
        "benchmark_identity": benchmark_identity,
        "closing_portfolio": closing_portfolio.to_json_dict(),
        "event_consumed": _event_consumed(event),
        "ledger_figures": ledger_figures,
        "ledger_identity": run.self_sha256_grouped,
        "opening_portfolio": opening_portfolio.to_json_dict(),
        "program_identity": program_identity,
        "scenario_figures": scenario_figures,
        "scenario_identity": report.self_sha256_grouped,
        "selected_security_ids": list(selected),
        "selection_k_t": k_t,
    }
    result_identity = _result_identity(fold_id, derived, COMPOSED_FOLD_VALID)
    return ValidComposedFold(
        state=COMPOSED_FOLD_VALID,
        bound_input_manifest=manifest,
        fold_id=fold_id,
        event_consumed=_event_consumed(event),
        selected_security_ids=selected,
        selection_k_t=k_t,
        program_identity=program_identity,
        ledger_identity=run.self_sha256_grouped,
        ledger_figures=ledger_figures,
        opening_portfolio=opening_portfolio,
        closing_portfolio=closing_portfolio,
        scenario_identity=report.self_sha256_grouped,
        scenario_figures=scenario_figures,
        benchmark_identity=benchmark_identity,
        engine_identities=bound_identities,
        result_identity=result_identity,
        lineage=lineage,
        provenance=provenance,
    )


__all__ = [
    "BOUND_INPUT_MANIFEST_FIELDS",
    "BLOCKED_BENCHMARK_CAPITAL_NOT_ALIGNED",
    "BLOCKED_EMPTY_INCLUDED_UNIVERSE",
    "BLOCKED_EVENT_ORDINAL_OUT_OF_RANGE",
    "BLOCKED_INCLUDED_SECURITY_WITHOUT_SIGNAL_INPUT",
    "BLOCKED_SESSION_AXIS_CALENDAR_MISMATCH",
    "BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH",
    "BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH",
    "BLOCKED_SESSION_NOT_ON_SHARED_AXIS",
    "COMPOSED_FOLD_DEGRADED",
    "COMPOSED_FOLD_STATES",
    "COMPOSED_FOLD_STRUCTURAL_STATES",
    "COMPOSED_FOLD_VALID",
    "ENGINE_EMPTY_REGISTRY_STATES",
    "ENGINE_STAGES",
    "KERNEL_ID",
    "NON_CLAIMS",
    "SCHEMA_VERSION",
    "SESSION_AXIS_STAGE",
    "TICKET_ID",
    "BenchmarksBinding",
    "ClosingPortfolioState",
    "ComposedFoldError",
    "ComposedFoldInputs",
    "ComposedFoldResult",
    "DegradedComposedFold",
    "EngineIdentity",
    "ExecutionBinding",
    "LiquidityBarSpec",
    "OpeningPortfolioState",
    "ScenariosBinding",
    "ScheduleBinding",
    "SessionAxis",
    "SignalBinding",
    "SignalObservationPair",
    "UniverseBinding",
    "ValidComposedFold",
    "assert_states_complete",
    "bound_input_manifest",
    "check_session_axis",
    "compose_fold",
    "derive_ledger_world",
    "engine_identities",
    "fold_id_of",
]
