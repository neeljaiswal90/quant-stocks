"""Deterministic walk-forward backtest driver with immutable, replayable outputs.

NEE-134. This module ORCHESTRATES the five M2 engines -- signal ranking
(:mod:`qme.quant.signal_v1`), point-in-time universe
(:mod:`qme.quant.universe_v1`), raw-price execution accounting
(:mod:`qme.quant.execution_v1`), cost/turnover/capacity scenarios
(:mod:`qme.quant.scenarios_v1`), and benchmark/ablation controls
(:mod:`qme.quant.benchmarks_v1`). It never re-implements any engine's scoring,
screening, accounting, costing, or benchmarking logic: every engine call site is
explicit and its identity is bound into the run manifest.

Run identity (verbatim NEE-134): ``run_id = SHA256(canonicalized input
manifest)``. The manifest binds the repository commit AND the worktree dirty
flag, the config/schema/data hashes, the sample/fold id, the execution/cost/tax
modes, the bound calendar, and every engine version. Non-deterministic runtime
timestamps DO NOT enter the canonical result identity: a run under a different
clock or timezone yields byte-identical canonical hashes; wall-clock values are
recorded only in a ``provenance`` block that is excluded from every identity and
result digest.

Execution posture:

* Local data only. Network egress is disabled structurally -- ``qme.experiments``
  is a research package that the repository import-boundary suite forbids from
  importing any transport, and :func:`assert_network_egress_denied` re-proves,
  from this module's own first-party import closure, that no transport is
  reachable and REFUSES the run otherwise.
* Partitions (folds) run in a deterministic, content-derived order.
* A failed or degraded partition is RETAINED with typed reason codes but can
  never be coerced into a valid aggregate: :class:`ValidPartition` and
  :class:`DegradedPartition` are disjoint types and :func:`aggregate_valid`
  accepts only the former (the wall is proven statically by an in-test
  ``mypy --strict`` probe).
* Publication is atomic and no-clobber, confined to a caller-supplied runs root;
  a rerun never mutates an existing run directory.

With every owner-gated registry shipped empty, a real run fails closed with the
engines' own typed ``BLOCKED_*`` states, surfaced verbatim as retained degraded
partitions. This module invents no threshold, coefficient, or production value;
the tests exercise valid orchestration only through ``TEST_CONSTRUCTED`` registry
records injected via the engines' explicit override seams.

Non-claims: nothing here demonstrates alpha, establishes capacity value,
measures empirical performance, authorizes a live trading order, or asserts
production readiness. It is a deterministic replay harness over synthetic,
test-only inputs.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from qme.data.stores.calendar_v1 import MarketStoreError
from qme.foundation.lineage import canonical_json_bytes
from qme.quant import (
    benchmarks_v1,
    execution_v1,
    scenarios_v1,
    signal_v1,
    universe_v1,
)
from qme.quant.execution_v1 import ExecutionProgram, ExecutionRun
from qme.quant.scenarios_v1 import LiquidityEvidence, ScenarioReport
from qme.quant.signal_v1 import SecuritySessionInput, SignalRunResult
from qme.quant.universe_v1 import (
    RequiredListing,
    SessionSpine,
    UniverseCandidate,
    UniverseSnapshot,
)

# ---------------------------------------------------------------------------
# Engine identity and driver version
# ---------------------------------------------------------------------------

ENGINE_ID: Final = "QME-NEE134-WALK-FORWARD-BACKTEST-DRIVER-V1"
SCHEMA_VERSION: Final = "qme.walk_forward_backtest_driver.v1"
OUTPUT_SCHEMA_VERSION: Final = "qme.walk_forward_outputs.v1"
MANIFEST_SCHEMA_VERSION: Final = "qme.walk_forward_run_manifest.v1"

#: The five orchestrated engines, in the fixed call order of one fold. Each pair
#: is ``(engine_id, schema_version)`` observed from the engine module itself, so
#: an engine re-identification changes the driver's bound identity.
ORCHESTRATED_ENGINES: Final[tuple[tuple[str, str, str], ...]] = (
    ("universe", universe_v1.KERNEL_ID, universe_v1.SCHEMA_VERSION),
    ("signal", signal_v1.ENGINE_ID, signal_v1.SCHEMA_VERSION),
    ("execution", execution_v1.ENGINE_ID, execution_v1.SCHEMA_VERSION),
    ("scenarios", scenarios_v1.KERNEL_ID, scenarios_v1.SCHEMA_VERSION),
    ("benchmarks", benchmarks_v1.ENGINE_ID, benchmarks_v1.SCHEMA_VERSION),
)

#: Engines whose typed-OK output a partition REQUIRES to be valid. The benchmark
#: controls are comparison artifacts, not part of the strategy's own accounting:
#: a benchmark refusal is retained as a warning and does not degrade the
#: partition, but every REQUIRED stage must succeed or the partition is degraded.
REQUIRED_STAGES: Final[tuple[str, ...]] = ("universe", "signal", "execution", "scenarios")

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
# Grouped SHA-256 (eight lowercase 8-hex groups joined by ':'), on the foundation
# canonical encoder. Never a contiguous 64-hex run in an emitted artifact.
# ---------------------------------------------------------------------------

_GROUP_COUNT: Final = 8
_GROUP_WIDTH: Final = 8


def group_sha256(payload: bytes) -> str:
    """Grouped sha256 over raw bytes."""

    digest = hashlib.sha256(payload).hexdigest()
    return _regroup(digest)


def _regroup(digest: str) -> str:
    return ":".join(
        digest[index : index + _GROUP_WIDTH]
        for index in range(0, _GROUP_COUNT * _GROUP_WIDTH, _GROUP_WIDTH)
    )


def grouped_document_digest(document: Mapping[str, Any]) -> str:
    """Grouped sha256 over the repository canonical JSON encoding of a document."""

    return group_sha256(canonical_json_bytes(document))


def sha256_hex(payload: bytes) -> str:
    """Raw lowercase sha256 hexdigest (used only for filesystem-safe run names)."""

    return hashlib.sha256(payload).hexdigest()


def _content_sorted(documents: Any) -> list[dict[str, Any]]:
    """Order an unordered row collection by content, so a row permutation is invisible.

    Every content hash the driver computes over an unordered input collection
    passes the rows through here first, so shuffling the input rows cannot change
    the digest -- the same order-independence the engines enforce internally.
    """

    return sorted(documents, key=canonical_json_bytes)


# ---------------------------------------------------------------------------
# Typed fail-closed states
# ---------------------------------------------------------------------------

BLOCKED_CALENDAR_BINDING_MISMATCH: Final = "BLOCKED_CALENDAR_BINDING_MISMATCH"
BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE: Final = "BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE"
BLOCKED_DUPLICATE_PARTITION_ID: Final = "BLOCKED_DUPLICATE_PARTITION_ID"
BLOCKED_EMPTY_PARTITION_SET: Final = "BLOCKED_EMPTY_PARTITION_SET"
BLOCKED_ENGINE_IDENTITY_UNBOUND: Final = "BLOCKED_ENGINE_IDENTITY_UNBOUND"
BLOCKED_FOLD_NOT_AUTHORIZED: Final = "BLOCKED_FOLD_NOT_AUTHORIZED"
BLOCKED_MALFORMED_WALK_FORWARD_INPUT: Final = "BLOCKED_MALFORMED_WALK_FORWARD_INPUT"
BLOCKED_MISSING_REQUIRED_DATA: Final = "BLOCKED_MISSING_REQUIRED_DATA"
BLOCKED_MISSING_REQUIRED_HASH: Final = "BLOCKED_MISSING_REQUIRED_HASH"
BLOCKED_NETWORK_EGRESS_REACHABLE: Final = "BLOCKED_NETWORK_EGRESS_REACHABLE"
BLOCKED_NON_LOCAL_INPUT_LOCATOR: Final = "BLOCKED_NON_LOCAL_INPUT_LOCATOR"
BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT: Final = "BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT"
BLOCKED_RUN_DIRECTORY_EXISTS: Final = "BLOCKED_RUN_DIRECTORY_EXISTS"

#: Every fail-closed state this driver raises itself, sorted and unique. Refusals
#: that belong to an orchestrated engine keep the engine's own state verbatim and
#: are surfaced through the retained degraded partitions, never renamed.
WALK_FORWARD_FAIL_CLOSED_STATES: Final[tuple[str, ...]] = (
    BLOCKED_CALENDAR_BINDING_MISMATCH,
    BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE,
    BLOCKED_DUPLICATE_PARTITION_ID,
    BLOCKED_EMPTY_PARTITION_SET,
    BLOCKED_ENGINE_IDENTITY_UNBOUND,
    BLOCKED_FOLD_NOT_AUTHORIZED,
    BLOCKED_MALFORMED_WALK_FORWARD_INPUT,
    BLOCKED_MISSING_REQUIRED_DATA,
    BLOCKED_MISSING_REQUIRED_HASH,
    BLOCKED_NETWORK_EGRESS_REACHABLE,
    BLOCKED_NON_LOCAL_INPUT_LOCATOR,
    BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT,
    BLOCKED_RUN_DIRECTORY_EXISTS,
)

#: Terminal partition states.
PARTITION_VALID: Final = "WALK_FORWARD_PARTITION_VALID"
PARTITION_DEGRADED: Final = "WALK_FORWARD_PARTITION_DEGRADED"

#: Terminal run states.
RUN_COMPLETED_WITH_VALID_PARTITIONS: Final = "WALK_FORWARD_RUN_COMPLETED_WITH_VALID_PARTITIONS"
RUN_COMPLETED_NO_VALID_PARTITIONS: Final = "WALK_FORWARD_RUN_COMPLETED_NO_VALID_PARTITIONS"

#: Per-stage terminal state on success.
STAGE_OK: Final = "WALK_FORWARD_STAGE_OK"


class WalkForwardError(ValueError):
    """A typed fail-closed refusal raised by the driver itself.

    ``state`` is one of :data:`WALK_FORWARD_FAIL_CLOSED_STATES`. The identity
    fields are filled whenever a refusal is attributable to a specific fold,
    stage, or path, so a caller can report which input was refused.
    """

    def __init__(
        self,
        state: str,
        message: str,
        *,
        fold_id: str | None = None,
        stage: str | None = None,
        path: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(f"{state}: {message}")
        self.state = state
        self.message = message
        self.fold_id = fold_id
        self.stage = stage
        self.path = path
        self.detail = detail

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "state": self.state,
            "message": self.message,
            "fold_id": self.fold_id,
            "stage": self.stage,
            "path": self.path,
            "detail": self.detail,
        }


def assert_states_are_complete() -> None:
    """Prove the published fail-closed tuple is sorted, unique, and BLOCKED-only."""

    if list(WALK_FORWARD_FAIL_CLOSED_STATES) != sorted(set(WALK_FORWARD_FAIL_CLOSED_STATES)):
        raise WalkForwardError(
            BLOCKED_MALFORMED_WALK_FORWARD_INPUT,
            "the fail-closed state tuple must be sorted and unique",
        )
    for state in WALK_FORWARD_FAIL_CLOSED_STATES:
        if not state.startswith("BLOCKED_"):
            raise WalkForwardError(
                BLOCKED_MALFORMED_WALK_FORWARD_INPUT,
                f"fail-closed state {state!r} must be a BLOCKED_ state",
            )


# ---------------------------------------------------------------------------
# Structural network-egress denial
# ---------------------------------------------------------------------------

#: Modules whose presence anywhere in the driver's first-party import closure
#: would mean a transport is reachable. Mirrors the repository import-boundary
#: suite (``tests/architecture/test_import_boundaries.py``).
FORBIDDEN_EGRESS_MODULES: Final[frozenset[str]] = frozenset(
    {
        "qme.data.alpha_vantage.acquisition",
        "qme.data.alpha_vantage.client",
        "qme.data.alpha_vantage.m0_fixture_pulls",
        "qme.data.alpha_vantage.transport",
        "qme.data.sec.edgar_receipts",
        "urllib.request",
        "http.client",
        "socket",
        "ssl",
        "ftplib",
        "smtplib",
        "telnetlib",
    }
)

#: This driver module, repository-relative. The egress proof starts here.
_DRIVER_MODULE_RELATIVE: Final = "qme/experiments/walk_forward_v1.py"


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _module_path(repository_root: Path, module_name: str) -> Path | None:
    relative = Path(*module_name.split("."))
    for candidate in (
        repository_root / relative.with_suffix(".py"),
        repository_root / relative / "__init__.py",
    ):
        if candidate.is_file():
            return candidate
    return None


def transport_modules_reachable(
    repository_root: Path,
    *,
    entry_module_source: Path | None = None,
) -> tuple[str, ...]:
    """Return the sorted forbidden egress modules reachable from the driver source.

    The closure follows first-party ``qme.*`` import edges from the entry module
    (this driver, unless ``entry_module_source`` overrides it for a probe) and
    reports any edge -- direct or transitive -- that names a
    :data:`FORBIDDEN_EGRESS_MODULES` member. The real driver reaches none, so the
    result is empty and the run proceeds; a probe module that imports a transport
    makes the result non-empty and the run refuses.
    """

    entry = entry_module_source or (repository_root / _DRIVER_MODULE_RELATIVE)
    seen: set[str] = set()
    stack: list[Path] = [entry]
    hits: set[str] = set()
    while stack:
        current = stack.pop()
        try:
            imported = _module_imports(current)
        except (OSError, SyntaxError):
            continue
        for name in imported:
            if name in FORBIDDEN_EGRESS_MODULES:
                hits.add(name)
            if name.startswith("qme"):
                resolved = _module_path(repository_root, name)
                if resolved is not None:
                    key = str(resolved)
                    if key not in seen:
                        seen.add(key)
                        stack.append(resolved)
    return tuple(sorted(hits))


def assert_network_egress_denied(
    repository_root: Path,
    *,
    entry_module_source: Path | None = None,
) -> None:
    """Refuse the run if any transport is reachable from the driver's source closure."""

    reachable = transport_modules_reachable(
        repository_root, entry_module_source=entry_module_source
    )
    if reachable:
        raise WalkForwardError(
            BLOCKED_NETWORK_EGRESS_REACHABLE,
            "a network transport is reachable from the walk-forward driver; local "
            f"execution requires none: {', '.join(reachable)}",
            detail=",".join(reachable),
        )


_LOCATOR_SCHEMES: Final[tuple[str, ...]] = (
    "http://",
    "https://",
    "ftp://",
    "ftps://",
    "ws://",
    "wss://",
    "//",
)


def assert_local_input_locator(value: str, *, fold_id: str | None = None) -> str:
    """Refuse any input locator that names a network scheme or host."""

    lowered = value.strip().lower()
    for scheme in _LOCATOR_SCHEMES:
        if lowered.startswith(scheme):
            raise WalkForwardError(
                BLOCKED_NON_LOCAL_INPUT_LOCATOR,
                f"input locator {value!r} names a non-local scheme; local data only",
                fold_id=fold_id,
                detail=value,
            )
    return value


# ---------------------------------------------------------------------------
# Frozen input types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineIdentity:
    """One orchestrated engine's bound identity: id, schema, observed self-hash."""

    role: str
    engine_id: str
    schema_version: str
    self_sha256_grouped: str

    def to_json_dict(self) -> dict[str, str]:
        return {
            "role": self.role,
            "engine_id": self.engine_id,
            "schema_version": self.schema_version,
            "self_sha256_grouped": self.self_sha256_grouped,
        }


@dataclass(frozen=True)
class RegistryBundle:
    """The frozen owner-gated config/spec threaded to the engines.

    Every field defaults to the engine's SHIPPED EMPTY registry, so a driver that
    threads the default bundle fails closed exactly as production does. Tests
    inject ``TEST_CONSTRUCTED`` records through these fields via the engines'
    explicit override seams; none of them ships.
    """

    feature_variants: tuple[signal_v1.FeatureVariant, ...] = signal_v1.REGISTERED_FEATURE_VARIANTS
    tie_break_policies: tuple[signal_v1.TieBreakPolicy, ...] = (
        signal_v1.REGISTERED_TIE_BREAK_POLICIES
    )
    breadth_minimums: tuple[signal_v1.BreadthMinimum, ...] = signal_v1.REGISTERED_BREADTH_MINIMUMS
    universe_thresholds: tuple[universe_v1.UniverseThresholdSet, ...] = (
        universe_v1.REGISTERED_UNIVERSE_THRESHOLDS
    )
    liquidity_lookbacks: tuple[scenarios_v1.LiquidityLookbackPolicy, ...] = (
        scenarios_v1.REGISTERED_LIQUIDITY_LOOKBACKS
    )
    participation_scenarios: tuple[scenarios_v1.ParticipationScenario, ...] = (
        scenarios_v1.REGISTERED_PARTICIPATION_SCENARIOS
    )
    commission_schedules: tuple[scenarios_v1.CommissionSchedule, ...] = (
        scenarios_v1.REGISTERED_COMMISSION_SCHEDULES
    )
    spread_models: tuple[scenarios_v1.SpreadModel, ...] = scenarios_v1.REGISTERED_SPREAD_MODELS
    impact_models: tuple[scenarios_v1.ImpactModel, ...] = scenarios_v1.REGISTERED_IMPACT_MODELS
    benchmark_controls: tuple[benchmarks_v1.BenchmarkControlDefinition, ...] = (
        benchmarks_v1.REGISTERED_BENCHMARK_CONTROLS
    )

    def config_document(self) -> dict[str, Any]:
        """The canonical config/spec document, used for the config digest."""

        return {
            "breadth_minimums": [record.to_json_dict() for record in self.breadth_minimums],
            "commission_schedules": [record.to_json_dict() for record in self.commission_schedules],
            "feature_variants": [record.to_json_dict() for record in self.feature_variants],
            "impact_models": [record.to_json_dict() for record in self.impact_models],
            "liquidity_lookbacks": [record.to_json_dict() for record in self.liquidity_lookbacks],
            "participation_scenarios": [
                record.to_json_dict() for record in self.participation_scenarios
            ],
            "spread_models": [record.to_json_dict() for record in self.spread_models],
            "tie_break_policies": [record.to_json_dict() for record in self.tie_break_policies],
            "universe_thresholds": [record.to_json_dict() for record in self.universe_thresholds],
            "benchmark_controls": sorted(
                record.control_id for record in self.benchmark_controls
            ),
        }

    def config_sha256_grouped(self) -> str:
        return grouped_document_digest(self.config_document())


@dataclass(frozen=True)
class FoldInputs:
    """Every typed, locally-pinned input one walk-forward fold threads.

    The driver never re-parses raw data into engine types: it receives the
    already-typed, content-addressed inputs, computes their canonical digests for
    the run identity, and hands them to each engine unchanged.
    """

    fold_id: str
    # signal
    signal_session: str
    analysis_cutoff: str
    signal_inputs: tuple[SecuritySessionInput, ...]
    variant_id: str
    tie_policy_id: str
    breadth_threshold_id: str
    # universe
    universe_sessions: tuple[str, ...]
    universe_candidates: tuple[UniverseCandidate, ...]
    required_listings: tuple[RequiredListing, ...]
    required_coverage_series: tuple[str, ...]
    analysis_as_of: str
    session_spine: SessionSpine
    threshold_set_id: str
    verdict_session: str
    # execution
    execution_program: ExecutionProgram
    # scenarios
    liquidity_evidence: tuple[LiquidityEvidence, ...]
    lookback_id: str
    participation_scenario_id: str
    # benchmarks
    benchmark_control_id: str

    def __post_init__(self) -> None:
        if not self.fold_id or self.fold_id.strip() != self.fold_id:
            raise WalkForwardError(
                BLOCKED_MALFORMED_WALK_FORWARD_INPUT,
                "fold_id must be a non-empty, unpadded identifier",
            )
        if not self.signal_inputs:
            raise WalkForwardError(
                BLOCKED_MISSING_REQUIRED_DATA,
                "a fold must thread at least one signal cross-section input",
                fold_id=self.fold_id,
            )
        if type(self.execution_program) is not ExecutionProgram:
            raise WalkForwardError(
                BLOCKED_MALFORMED_WALK_FORWARD_INPUT,
                "execution_program must be a declared ExecutionProgram",
                fold_id=self.fold_id,
            )

    def signal_input_digest(self) -> str:
        return grouped_document_digest(
            {
                "analysis_cutoff": self.analysis_cutoff,
                "breadth_threshold_id": self.breadth_threshold_id,
                "inputs": _content_sorted(record.to_json_dict() for record in self.signal_inputs),
                "signal_session": self.signal_session,
                "tie_policy_id": self.tie_policy_id,
                "variant_id": self.variant_id,
            }
        )

    def universe_input_digest(self) -> str:
        return grouped_document_digest(
            {
                "analysis_as_of": self.analysis_as_of,
                "candidates": _content_sorted(
                    record.to_json_dict() for record in self.universe_candidates
                ),
                "required_coverage_series": sorted(set(self.required_coverage_series)),
                "required_listings": _content_sorted(
                    record.to_json_dict() for record in self.required_listings
                ),
                "sessions": sorted(set(self.universe_sessions)),
                "session_spine": self.session_spine.to_json_dict(),
                "threshold_set_id": self.threshold_set_id,
                "verdict_session": self.verdict_session,
            }
        )

    def execution_input_digest(self) -> str:
        return self.execution_program.input_digest()

    def scenarios_input_digest(self) -> str:
        return grouped_document_digest(
            {
                "liquidity_evidence": _content_sorted(
                    _liquidity_evidence_document(record) for record in self.liquidity_evidence
                ),
                "lookback_id": self.lookback_id,
                "participation_scenario_id": self.participation_scenario_id,
            }
        )

    def benchmark_input_digest(self) -> str:
        return grouped_document_digest({"control_id": self.benchmark_control_id})

    def data_document(self) -> dict[str, Any]:
        return {
            "benchmark": self.benchmark_input_digest(),
            "execution": self.execution_input_digest(),
            "fold_id": self.fold_id,
            "scenarios": self.scenarios_input_digest(),
            "signal": self.signal_input_digest(),
            "universe": self.universe_input_digest(),
        }

    def initial_state_document(self) -> dict[str, Any]:
        program = self.execution_program
        return {
            "fold_id": self.fold_id,
            "opening_cash": program.opening_cash,
            "opening_positions": dict(sorted(program.opening_positions.items())),
            "opening_receivables": program.opening_receivables,
            "opening_session": program.opening_session.to_json_dict(),
        }


def _liquidity_evidence_document(record: LiquidityEvidence) -> dict[str, Any]:
    return {
        "bars": [
            {
                "raw_close": bar.raw_close,
                "raw_volume": bar.raw_volume,
                "security_id": bar.security_id,
                "session_id": bar.session_id,
            }
            for bar in record.bars
        ],
        "rebalance_id": record.rebalance_id,
        "security_id": record.security_id,
    }


@dataclass(frozen=True)
class WalkForwardPlan:
    """A complete, locally-pinned walk-forward specification."""

    sample_fold_id: str
    folds: tuple[FoldInputs, ...]
    registries: RegistryBundle
    share_mode: str
    regulatory_fee_mode: str
    cost_policy_id: str
    transaction_tax_policy_id: str
    transaction_tax_policy_sha256_grouped: str
    calendar_id: str
    calendar_sha256_grouped: str
    authorized_fold_ids: frozenset[str]

    def __post_init__(self) -> None:
        if not self.folds:
            raise WalkForwardError(
                BLOCKED_EMPTY_PARTITION_SET, "a walk-forward run must name at least one fold"
            )
        seen: set[str] = set()
        for fold in self.folds:
            if fold.fold_id in seen:
                raise WalkForwardError(
                    BLOCKED_DUPLICATE_PARTITION_ID,
                    f"fold {fold.fold_id!r} is declared more than once",
                    fold_id=fold.fold_id,
                )
            seen.add(fold.fold_id)
        for required in (
            ("sample_fold_id", self.sample_fold_id),
            ("share_mode", self.share_mode),
            ("regulatory_fee_mode", self.regulatory_fee_mode),
            ("cost_policy_id", self.cost_policy_id),
            ("transaction_tax_policy_id", self.transaction_tax_policy_id),
            ("transaction_tax_policy_sha256_grouped", self.transaction_tax_policy_sha256_grouped),
            ("calendar_id", self.calendar_id),
            ("calendar_sha256_grouped", self.calendar_sha256_grouped),
        ):
            name, value = required
            if not value or value.strip() != value:
                raise WalkForwardError(
                    BLOCKED_MISSING_REQUIRED_HASH
                    if name.endswith("sha256_grouped")
                    else BLOCKED_MALFORMED_WALK_FORWARD_INPUT,
                    f"{name} must be a non-empty, unpadded value",
                )

    def ordered_folds(self) -> tuple[FoldInputs, ...]:
        """Folds in deterministic, content-derived order (by fold_id UTF-8 bytes)."""

        return tuple(sorted(self.folds, key=lambda fold: fold.fold_id.encode("utf-8")))


def assert_declared_calendar_witnesses_injected(
    trading_calendar: Any, plan: WalkForwardPlan
) -> None:
    """Refuse unless the INJECTED calendar witnesses the plan's DECLARED identity.

    The run identity binds ``calendar_id`` / ``calendar_sha256_grouped`` from the
    plan, while the calendar that actually drives the signal anchors arrives
    separately as ``trading_calendar``. Nothing else forces the two to agree, so
    without this gate the bound calendar identity is only a caller assertion, not
    a witness of the calendar that computed the signals: a caller could declare
    one calendar and inject another, publishing a faithful-looking identity over
    outputs a different calendar produced (or collide two distinct runs on one
    ``run_id``). This asserts the injected calendar's own ``calendar_id`` and
    grouped byte-hash equal the declared values before any fold runs.
    """

    observed_id = getattr(trading_calendar, "calendar_id", None)
    observed_sha = getattr(trading_calendar, "bytes_sha256_grouped", None)
    if observed_id != plan.calendar_id or observed_sha != plan.calendar_sha256_grouped:
        raise WalkForwardError(
            BLOCKED_CALENDAR_BINDING_MISMATCH,
            "the injected trading calendar does not witness the plan's declared "
            f"calendar identity (declared {plan.calendar_id!r}/"
            f"{plan.calendar_sha256_grouped!r}, injected {observed_id!r}/{observed_sha!r})",
            detail=str(observed_id),
        )


# ---------------------------------------------------------------------------
# Bound-input identity material
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundInputs:
    """The reduced canonical input manifest whose sha256 is the run identity.

    Every field is a bound input class. Changing any one changes the run id. No
    field carries a wall-clock timestamp -- runtime clocks never enter identity.
    """

    walk_forward_engine_version: str
    repository_commit: str
    dirty_worktree: bool
    config_sha256_grouped: str
    schema_sha256_grouped: str
    data_manifest_sha256_grouped: str
    initial_state_sha256_grouped: str
    sample_fold_id: str
    authorized_fold_ids: tuple[str, ...]
    share_mode: str
    regulatory_fee_mode: str
    cost_policy_id: str
    transaction_tax_policy_id: str
    transaction_tax_policy_sha256_grouped: str
    benchmark_control_ids: tuple[str, ...]
    calendar_id: str
    calendar_sha256_grouped: str
    engine_bindings: tuple[tuple[str, str], ...]

    def identity_material(self) -> dict[str, Any]:
        """The canonicalized input manifest -- the exact bytes hashed for run_id."""

        return {
            "authorized_fold_ids": list(self.authorized_fold_ids),
            "benchmark_control_ids": list(self.benchmark_control_ids),
            "calendar_id": self.calendar_id,
            "calendar_sha256_grouped": self.calendar_sha256_grouped,
            "config_sha256_grouped": self.config_sha256_grouped,
            "cost_policy_id": self.cost_policy_id,
            "data_manifest_sha256_grouped": self.data_manifest_sha256_grouped,
            "dirty_worktree": self.dirty_worktree,
            "engine_bindings": [list(pair) for pair in self.engine_bindings],
            "engine_id": ENGINE_ID,
            "initial_state_sha256_grouped": self.initial_state_sha256_grouped,
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "regulatory_fee_mode": self.regulatory_fee_mode,
            "repository_commit": self.repository_commit,
            "sample_fold_id": self.sample_fold_id,
            "schema_sha256_grouped": self.schema_sha256_grouped,
            "share_mode": self.share_mode,
            "transaction_tax_policy_id": self.transaction_tax_policy_id,
            "transaction_tax_policy_sha256_grouped": self.transaction_tax_policy_sha256_grouped,
            "walk_forward_engine_version": self.walk_forward_engine_version,
        }

    def run_id_hex(self) -> str:
        """The raw sha256 hexdigest of the canonicalized input manifest."""

        return sha256_hex(canonical_json_bytes(self.identity_material()))

    def run_id(self) -> str:
        """``run_id`` rendered as eight lowercase 8-hex groups joined by ':'."""

        return _regroup(self.run_id_hex())


def bound_input_field_names() -> tuple[str, ...]:
    """Every bound input class, for the change-sensitivity parametrization."""

    return tuple(descriptor.name for descriptor in fields(BoundInputs))


# ---------------------------------------------------------------------------
# Stage results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageOutcome:
    """One engine call site's typed outcome: OK with identity, or a BLOCKED state."""

    role: str
    engine_id: str
    schema_version: str
    state: str
    self_sha256_grouped: str | None
    reason_code: str | None
    detail: Mapping[str, Any] | None

    @property
    def ok(self) -> bool:
        return self.state == STAGE_OK

    def identity(self) -> EngineIdentity:
        if self.self_sha256_grouped is None:
            raise WalkForwardError(
                BLOCKED_ENGINE_IDENTITY_UNBOUND,
                f"stage {self.role!r} produced no engine self-hash to bind",
                stage=self.role,
            )
        return EngineIdentity(
            role=self.role,
            engine_id=self.engine_id,
            schema_version=self.schema_version,
            self_sha256_grouped=self.self_sha256_grouped,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "engine_id": self.engine_id,
            "schema_version": self.schema_version,
            "state": self.state,
            "self_sha256_grouped": self.self_sha256_grouped,
            "reason_code": self.reason_code,
            "detail": dict(self.detail) if self.detail is not None else None,
        }


# ---------------------------------------------------------------------------
# Explicit engine call sites (one per orchestrated engine)
# ---------------------------------------------------------------------------


def _blocked_stage(
    role: str,
    engine_id: str,
    schema_version: str,
    *,
    reason_code: str,
    detail: Mapping[str, Any],
) -> StageOutcome:
    return StageOutcome(
        role=role,
        engine_id=engine_id,
        schema_version=schema_version,
        state=reason_code,
        self_sha256_grouped=None,
        reason_code=reason_code,
        detail=dict(detail),
    )


def run_universe_stage(
    fold: FoldInputs, registries: RegistryBundle
) -> tuple[StageOutcome, UniverseSnapshot | None]:
    """Call site: point-in-time universe. Gates rebalance authorization."""

    role, engine_id, schema = "universe", universe_v1.KERNEL_ID, universe_v1.SCHEMA_VERSION
    try:
        snapshot = universe_v1.build_point_in_time_universe(
            fold.universe_candidates,
            sessions=fold.universe_sessions,
            required_listings=fold.required_listings,
            required_coverage_series=fold.required_coverage_series,
            analysis_as_of=fold.analysis_as_of,
            spine=fold.session_spine,
            threshold_set_id=fold.threshold_set_id,
            threshold_registry=registries.universe_thresholds,
        )
        verdict = snapshot.verdict(fold.verdict_session)
        universe_v1.require_rebalanceable(verdict)
    except universe_v1.PointInTimeUniverseError as error:
        return (
            _blocked_stage(
                role, engine_id, schema, reason_code=error.state, detail=error.to_json_dict()
            ),
            None,
        )
    return (
        StageOutcome(
            role=role,
            engine_id=engine_id,
            schema_version=schema,
            state=STAGE_OK,
            self_sha256_grouped=snapshot.sha256_grouped(),
            reason_code=None,
            detail={"verdict_session": fold.verdict_session, "verdict_state": verdict.state},
        ),
        snapshot,
    )


def run_signal_stage(
    fold: FoldInputs,
    registries: RegistryBundle,
    calendar: Any,
) -> tuple[StageOutcome, SignalRunResult | None]:
    """Call site: 12-1 signal ranking and top-N selection."""

    role, engine_id, schema = "signal", signal_v1.ENGINE_ID, signal_v1.SCHEMA_VERSION
    try:
        result = signal_v1.evaluate_signal_cross_section(
            fold.signal_inputs,
            calendar=calendar,
            signal_session=fold.signal_session,
            analysis_cutoff=fold.analysis_cutoff,
            variant_id=fold.variant_id,
            tie_policy_id=fold.tie_policy_id,
            breadth_threshold_id=fold.breadth_threshold_id,
            variants=registries.feature_variants,
            tie_policies=registries.tie_break_policies,
            breadth_minimums=registries.breadth_minimums,
        )
    except (signal_v1.SignalError, MarketStoreError) as error:
        # signal_v1 DELIBERATELY surfaces calendar-store refusals unchanged --
        # a malformed date, a date outside coverage, a non-session date, an
        # offset that leaves coverage, a missing calendar -- rather than renaming
        # them to SignalError (see signal_v1.SURFACED_CALENDAR_STATES). Such a
        # refusal is a bad fold input, not a driver defect: it must degrade THIS
        # fold with the surfaced typed state and be retained, never escape and
        # abort the whole run (and every otherwise-valid sibling fold). Both
        # error types expose ``.state`` and ``.to_json_dict()``.
        return (
            _blocked_stage(
                role, engine_id, schema, reason_code=error.state, detail=error.to_json_dict()
            ),
            None,
        )
    return (
        StageOutcome(
            role=role,
            engine_id=engine_id,
            schema_version=schema,
            state=STAGE_OK,
            self_sha256_grouped=result.manifest_sha256_grouped,
            reason_code=None,
            detail={"selection_state": result.selection_state, "run_id": result.run_id},
        ),
        result,
    )


def run_execution_stage(
    fold: FoldInputs, *, repository_root: Path
) -> tuple[StageOutcome, ExecutionRun | None]:
    """Call site: raw-price execution and self-financing accounting."""

    role, engine_id, schema = "execution", execution_v1.ENGINE_ID, execution_v1.SCHEMA_VERSION
    try:
        run = execution_v1.run_execution_program(
            fold.execution_program, repository_root=repository_root
        )
    except execution_v1.ExecutionAccountingError as error:
        return (
            _blocked_stage(
                role, engine_id, schema, reason_code=error.state, detail=_execution_error(error)
            ),
            None,
        )
    return (
        StageOutcome(
            role=role,
            engine_id=engine_id,
            schema_version=schema,
            state=STAGE_OK,
            self_sha256_grouped=run.self_sha256_grouped,
            reason_code=None,
            detail={"execution_state": run.state, "manifest": run.manifest.self_sha256_grouped},
        ),
        run,
    )


def _execution_error(error: execution_v1.ExecutionAccountingError) -> dict[str, Any]:
    return {
        "state": error.state,
        "security_id": getattr(error, "security_id", None),
        "session": getattr(error, "session", None),
        "stage_id": getattr(error, "stage_id", None),
        "path": getattr(error, "path", None),
    }


def run_scenarios_stage(
    fold: FoldInputs, registries: RegistryBundle, run: ExecutionRun
) -> tuple[StageOutcome, ScenarioReport | None]:
    """Call site: cost / turnover / liquidity / participation / capacity scenarios."""

    role, engine_id, schema = "scenarios", scenarios_v1.KERNEL_ID, scenarios_v1.SCHEMA_VERSION
    try:
        report = scenarios_v1.evaluate_cost_turnover_capacity_scenarios(
            run,
            liquidity_evidence=fold.liquidity_evidence,
            lookback_id=fold.lookback_id,
            participation_scenario_id=fold.participation_scenario_id,
            lookbacks=registries.liquidity_lookbacks,
            participation_scenarios=registries.participation_scenarios,
            commission_schedules=registries.commission_schedules,
            spread_models=registries.spread_models,
            impact_models=registries.impact_models,
        )
    except scenarios_v1.ScenarioError as error:
        return (
            _blocked_stage(
                role, engine_id, schema, reason_code=error.state, detail=error.to_json_dict()
            ),
            None,
        )
    return (
        StageOutcome(
            role=role,
            engine_id=engine_id,
            schema_version=schema,
            state=STAGE_OK,
            self_sha256_grouped=report.self_sha256_grouped,
            reason_code=None,
            detail={
                "scenario_state": report.state,
                "manifest": report.manifest.self_sha256_grouped,
            },
        ),
        report,
    )


def run_benchmarks_stage(fold: FoldInputs, registries: RegistryBundle) -> StageOutcome:
    """Call site: benchmark/ablation controls. Resolves the control identity.

    With the shipped empty control registry this fails closed with the engine own
    ``BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL`` -- the production posture. A
    resolved control binds the benchmark engine identity for the retained record.
    """

    role, engine_id, schema = "benchmarks", benchmarks_v1.ENGINE_ID, benchmarks_v1.SCHEMA_VERSION
    try:
        definition = benchmarks_v1.resolve_benchmark_control(
            fold.benchmark_control_id, registry=registries.benchmark_controls
        )
    except benchmarks_v1.BenchmarkControlError as error:
        return _blocked_stage(
            role, engine_id, schema, reason_code=error.state, detail=error.to_json_dict()
        )
    # A resolved control binds a content digest of the engine's own definition
    # record; the strategy-vs-basis ledger construction is out of this lane's scope
    # (see the NEE-134 doc), so no ledger is built here.
    return StageOutcome(
        role=role,
        engine_id=engine_id,
        schema_version=schema,
        state=STAGE_OK,
        self_sha256_grouped=grouped_document_digest(definition.to_json_dict()),
        reason_code=None,
        detail={"control_id": definition.control_id, "control_kind": definition.control_kind},
    )


# ---------------------------------------------------------------------------
# Partition type wall
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidPartition:
    """One fold whose every REQUIRED engine stage produced a typed-OK result.

    This type is the ONLY input :func:`aggregate_valid` accepts. It shares no base
    class with :class:`DegradedPartition`, so a degraded partition cannot be
    substituted for a valid one -- the wall is enforced statically by mypy and
    re-checked at runtime by :func:`aggregate_valid`.
    """

    fold_id: str
    universe_snapshot: UniverseSnapshot
    signal_result: SignalRunResult
    execution_run: ExecutionRun
    scenario_report: ScenarioReport
    engine_identities: tuple[EngineIdentity, ...]
    control_warnings: tuple[StageOutcome, ...]

    state: str = PARTITION_VALID

    def identity_document(self) -> dict[str, Any]:
        return {
            "control_warnings": [warning.to_json_dict() for warning in self.control_warnings],
            "engine_identities": [identity.to_json_dict() for identity in self.engine_identities],
            "fold_id": self.fold_id,
            "state": self.state,
        }


@dataclass(frozen=True)
class DegradedPartition:
    """One fold that could not be validated. Retained, never aggregated.

    A degraded partition carries the typed reason codes of every stage that
    refused and the identities of any stage that still bound one. It exposes no
    valid engine outputs; there is no method, and no supertype, by which it can
    stand in for a :class:`ValidPartition`.
    """

    fold_id: str
    reason_codes: tuple[str, ...]
    stage_outcomes: tuple[StageOutcome, ...]
    engine_identities: tuple[EngineIdentity, ...]

    state: str = PARTITION_DEGRADED

    def identity_document(self) -> dict[str, Any]:
        return {
            "engine_identities": [identity.to_json_dict() for identity in self.engine_identities],
            "fold_id": self.fold_id,
            "reason_codes": list(self.reason_codes),
            "stage_outcomes": [outcome.to_json_dict() for outcome in self.stage_outcomes],
            "state": self.state,
        }


Partition = ValidPartition | DegradedPartition


def require_valid(partition: Partition) -> ValidPartition:
    """Narrow a partition to a valid one, or refuse. The only runtime narrowing."""

    if isinstance(partition, ValidPartition):
        return partition
    raise WalkForwardError(
        BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE,
        f"partition {partition.fold_id!r} is degraded and cannot be treated as valid",
        fold_id=partition.fold_id,
    )


@dataclass(frozen=True)
class ValidAggregate:
    """The aggregation of valid partitions -- the only surface that yields valid tables."""

    partitions: tuple[ValidPartition, ...]

    def fold_ids(self) -> tuple[str, ...]:
        return tuple(partition.fold_id for partition in self.partitions)


def aggregate_valid(partitions: Sequence[ValidPartition]) -> ValidAggregate:
    """Aggregate ONLY valid partitions. A degraded partition is a static type error here.

    The parameter type admits only :class:`ValidPartition`; passing a
    :class:`DegradedPartition` is rejected by ``mypy --strict``. The runtime guard
    below is the same wall for callers that bypass the type checker.
    """

    checked: list[ValidPartition] = []
    for partition in partitions:
        if not isinstance(partition, ValidPartition):
            raise WalkForwardError(
                BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE,
                "only valid partitions may enter the valid aggregate",
                fold_id=getattr(partition, "fold_id", None),
            )
        checked.append(partition)
    return ValidAggregate(partitions=tuple(checked))


# ---------------------------------------------------------------------------
# Output tables (verbatim projections of engine outputs, each row with lineage)
# ---------------------------------------------------------------------------

#: Every published output table, in a fixed order. Warnings/errors is driver-owned;
#: the rest are verbatim projections of an orchestrated engine's own output.
OUTPUT_TABLE_NAMES: Final[tuple[str, ...]] = (
    "signal_rank",
    "universe_rows",
    "universe_coverage",
    "nav",
    "cash",
    "receivables",
    "holdings",
    "targets_orders_fills",
    "lots",
    "actions",
    "session_close",
    "costs",
    "turnover",
    "capacity",
    "benchmarks",
    "warnings_errors",
)


def _lineage(run_id: str, fold_id: str, role: str, engine_id: str, source: str) -> dict[str, str]:
    return {
        "fold_id": fold_id,
        "run_id": run_id,
        "source_engine_id": engine_id,
        "source_role": role,
        "source_sha256_grouped": source,
    }


def _row(lineage: Mapping[str, str], payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"lineage": dict(lineage), "payload": dict(payload)}


def _identity_for(partition: ValidPartition, role: str) -> EngineIdentity:
    for identity in partition.engine_identities:
        if identity.role == role:
            return identity
    raise WalkForwardError(
        BLOCKED_ENGINE_IDENTITY_UNBOUND,
        f"valid partition {partition.fold_id!r} is missing the {role!r} engine identity",
        fold_id=partition.fold_id,
        stage=role,
    )


def _valid_partition_rows(
    partition: ValidPartition, run_id: str
) -> dict[str, list[dict[str, Any]]]:
    """Project one valid partition into the named output tables. No value is recomputed."""

    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in OUTPUT_TABLE_NAMES}

    signal_identity = _identity_for(partition, "signal")
    signal_lineage = _lineage(
        run_id, partition.fold_id, "signal", signal_identity.engine_id,
        signal_identity.self_sha256_grouped,
    )
    for row in partition.signal_result.to_json_dict()["rows"]:
        tables["signal_rank"].append(_row(signal_lineage, row))

    universe_identity = _identity_for(partition, "universe")
    universe_lineage = _lineage(
        run_id, partition.fold_id, "universe", universe_identity.engine_id,
        universe_identity.self_sha256_grouped,
    )
    for universe_row in partition.universe_snapshot.rows:
        tables["universe_rows"].append(_row(universe_lineage, universe_row.to_json_dict()))
    for verdict in partition.universe_snapshot.verdicts:
        tables["universe_coverage"].append(_row(universe_lineage, verdict.to_json_dict()))

    execution_identity = _identity_for(partition, "execution")
    execution_lineage = _lineage(
        run_id, partition.fold_id, "execution", execution_identity.engine_id,
        execution_identity.self_sha256_grouped,
    )
    run = partition.execution_run
    tables["nav"].append(
        _row(execution_lineage, {"initial_nav": run.initial_nav, "final_nav": run.final_nav})
    )
    tables["cash"].append(_row(execution_lineage, {"final_cash": run.final_cash}))
    tables["receivables"].append(
        _row(execution_lineage, {"final_receivables": run.final_receivables})
    )
    for security_id, shares in sorted(run.final_positions.items()):
        tables["holdings"].append(
            _row(execution_lineage, {"security_id": security_id, "shares": shares})
        )
    for ledger in run.rebalance_ledgers:
        tables["targets_orders_fills"].append(_row(execution_lineage, ledger.to_json_dict()))
    tables["lots"].append(_row(execution_lineage, run.lots.to_json_dict()))
    for outcome in run.action_outcomes:
        tables["actions"].append(_row(execution_lineage, outcome.to_json_dict()))
    for close in run.session_close_records:
        tables["session_close"].append(_row(execution_lineage, close.to_json_dict()))

    scenario_identity = _identity_for(partition, "scenarios")
    scenario_lineage = _lineage(
        run_id, partition.fold_id, "scenarios", scenario_identity.engine_id,
        scenario_identity.self_sha256_grouped,
    )
    for rebalance in partition.scenario_report.rebalances:
        document = rebalance.to_json_dict()
        tables["costs"].append(
            _row(
                scenario_lineage,
                {
                    "component_costs": document["component_costs"],
                    "rebalance_id": document["rebalance_id"],
                    "regulatory_fee_component": document["regulatory_fee_component"],
                    "tier_costs_ledger": document["tier_costs_ledger"],
                    "tier_costs_rational": document["tier_costs_rational"],
                },
            )
        )
        tables["turnover"].append(
            _row(
                scenario_lineage,
                {
                    "gross_trade_notional": document["gross_trade_notional"],
                    "gtn_ratio": document["gtn_ratio"],
                    "one_way_turnover": document["one_way_turnover"],
                    "rebalance_id": document["rebalance_id"],
                },
            )
        )
        tables["capacity"].append(
            _row(
                scenario_lineage,
                {
                    "binding_security_id": document["binding_security_id"],
                    "portfolio_capacity_ledger": document["portfolio_capacity_ledger"],
                    "portfolio_capacity_rational": document["portfolio_capacity_rational"],
                    "portfolio_capacity_state": document["portfolio_capacity_state"],
                    "rebalance_id": document["rebalance_id"],
                },
            )
        )

    for warning in partition.control_warnings:
        control_lineage = _lineage(
            run_id, partition.fold_id, warning.role, warning.engine_id,
            warning.self_sha256_grouped or "UNBOUND",
        )
        tables["benchmarks"].append(_row(control_lineage, warning.to_json_dict()))

    return tables


def _degraded_rows(partition: DegradedPartition, run_id: str) -> list[dict[str, Any]]:
    lineage = {
        "fold_id": partition.fold_id,
        "run_id": run_id,
        "source_engine_id": ENGINE_ID,
        "source_role": "driver",
        "source_sha256_grouped": grouped_document_digest(partition.identity_document()),
    }
    return [_row(lineage, partition.identity_document())]


# ---------------------------------------------------------------------------
# Schema identity
# ---------------------------------------------------------------------------


def schema_document() -> dict[str, Any]:
    return {
        "driver_schema_version": SCHEMA_VERSION,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "orchestrated_engines": [list(item) for item in ORCHESTRATED_ENGINES],
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "output_tables": list(OUTPUT_TABLE_NAMES),
        "required_stages": list(REQUIRED_STAGES),
    }


def schema_sha256_grouped() -> str:
    return grouped_document_digest(schema_document())


# ---------------------------------------------------------------------------
# Run result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WalkForwardResult:
    """One in-memory walk-forward run: identity, partitions, tables, provenance.

    Publication is a separate, explicit step (:func:`stage_run` / :func:`commit_run`
    / :func:`publish_run`); constructing a result performs no I/O and mutates
    nothing on disk.
    """

    bound_inputs: BoundInputs
    state: str
    ordered_fold_ids: tuple[str, ...]
    valid_partitions: tuple[ValidPartition, ...]
    degraded_partitions: tuple[DegradedPartition, ...]
    aggregate: ValidAggregate
    output_tables: Mapping[str, tuple[Mapping[str, Any], ...]]
    provenance: Mapping[str, Any]

    @property
    def run_id(self) -> str:
        return self.bound_inputs.run_id()

    @property
    def run_id_hex(self) -> str:
        return self.bound_inputs.run_id_hex()

    def table_document(self, name: str) -> dict[str, Any]:
        return {
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "rows": [dict(row) for row in self.output_tables[name]],
            "run_id": self.run_id,
            "table": name,
        }

    def table_sha256_grouped(self, name: str) -> str:
        return grouped_document_digest(self.table_document(name))

    def _engine_bindings_document(self) -> list[dict[str, Any]]:
        bindings: list[dict[str, Any]] = []
        for valid_partition in self.valid_partitions:
            for identity in valid_partition.engine_identities:
                entry = identity.to_json_dict()
                entry["fold_id"] = valid_partition.fold_id
                bindings.append(entry)
        for degraded_partition in self.degraded_partitions:
            for identity in degraded_partition.engine_identities:
                entry = identity.to_json_dict()
                entry["fold_id"] = degraded_partition.fold_id
                bindings.append(entry)
        return sorted(bindings, key=lambda item: (item["fold_id"], item["role"]))

    def _partition_index(self) -> list[dict[str, Any]]:
        index: list[dict[str, Any]] = []
        for valid_partition in self.valid_partitions:
            index.append(
                {
                    "fold_id": valid_partition.fold_id,
                    "reason_codes": [],
                    "state": valid_partition.state,
                }
            )
        for degraded_partition in self.degraded_partitions:
            index.append(
                {
                    "fold_id": degraded_partition.fold_id,
                    "reason_codes": list(degraded_partition.reason_codes),
                    "state": degraded_partition.state,
                }
            )
        return sorted(index, key=lambda item: item["fold_id"])

    def _table_index(self) -> dict[str, Any]:
        return {
            name: {
                "row_count": len(self.output_tables[name]),
                "sha256_grouped": self.table_sha256_grouped(name),
                "source_roles": sorted(
                    {
                        str(row["lineage"]["source_role"])
                        for row in self.output_tables[name]
                    }
                ),
            }
            for name in OUTPUT_TABLE_NAMES
        }

    def result_identity_document(self) -> dict[str, Any]:
        """The clock-independent canonical result identity (no wall-clock values)."""

        return {
            "engine_bindings": self._engine_bindings_document(),
            "output_tables": self._table_index(),
            "partition_index": self._partition_index(),
            "run_id": self.run_id,
            "state": self.state,
        }

    def result_identity_sha256_grouped(self) -> str:
        return grouped_document_digest(self.result_identity_document())

    def manifest_document(self) -> dict[str, Any]:
        return {
            "claims": dict(NON_CLAIMS),
            "engine_bindings": self._engine_bindings_document(),
            "engine_id": ENGINE_ID,
            "fail_closed_states": list(WALK_FORWARD_FAIL_CLOSED_STATES),
            "identity_material": self.bound_inputs.identity_material(),
            "orchestrated_engines": [list(item) for item in ORCHESTRATED_ENGINES],
            "output_tables": self._table_index(),
            "partition_index": self._partition_index(),
            "provenance": dict(self.provenance),
            "result_identity_sha256_grouped": self.result_identity_sha256_grouped(),
            "run_id": self.run_id,
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "state": self.state,
            "valid_fold_ids": list(self.aggregate.fold_ids()),
        }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _run_one_fold(
    fold: FoldInputs,
    *,
    registries: RegistryBundle,
    calendar: Any,
    repository_root: Path,
    authorized_fold_ids: frozenset[str],
) -> Partition:
    if fold.fold_id not in authorized_fold_ids:
        return DegradedPartition(
            fold_id=fold.fold_id,
            reason_codes=(BLOCKED_FOLD_NOT_AUTHORIZED,),
            stage_outcomes=(
                _blocked_stage(
                    "driver",
                    ENGINE_ID,
                    SCHEMA_VERSION,
                    reason_code=BLOCKED_FOLD_NOT_AUTHORIZED,
                    detail={"fold_id": fold.fold_id},
                ),
            ),
            engine_identities=(),
        )

    outcomes: list[StageOutcome] = []
    identities: list[EngineIdentity] = []

    universe_outcome, snapshot = run_universe_stage(fold, registries)
    outcomes.append(universe_outcome)
    signal_outcome, signal_result = run_signal_stage(fold, registries, calendar)
    outcomes.append(signal_outcome)
    execution_outcome, execution_run = run_execution_stage(fold, repository_root=repository_root)
    outcomes.append(execution_outcome)

    if execution_run is not None:
        scenario_outcome, scenario_report = run_scenarios_stage(
            fold, registries, execution_run
        )
    else:
        scenario_report = None
        scenario_outcome = _blocked_stage(
            "scenarios",
            scenarios_v1.KERNEL_ID,
            scenarios_v1.SCHEMA_VERSION,
            reason_code=BLOCKED_MISSING_REQUIRED_DATA,
            detail={"reason": "no valid execution run to derive scenarios from"},
        )
    outcomes.append(scenario_outcome)

    benchmark_outcome = run_benchmarks_stage(fold, registries)

    for outcome in (*outcomes, benchmark_outcome):
        if outcome.ok:
            identities.append(outcome.identity())

    required_ok = all(
        outcome.ok for outcome in outcomes if outcome.role in REQUIRED_STAGES
    )
    if (
        required_ok
        and snapshot is not None
        and signal_result is not None
        and execution_run is not None
        and scenario_report is not None
    ):
        control_warnings = () if benchmark_outcome.ok else (benchmark_outcome,)
        return ValidPartition(
            fold_id=fold.fold_id,
            universe_snapshot=snapshot,
            signal_result=signal_result,
            execution_run=execution_run,
            scenario_report=scenario_report,
            engine_identities=tuple(identities),
            control_warnings=control_warnings,
        )

    reason_codes = tuple(
        outcome.reason_code
        for outcome in (*outcomes, benchmark_outcome)
        if outcome.reason_code is not None
    )
    return DegradedPartition(
        fold_id=fold.fold_id,
        reason_codes=reason_codes,
        stage_outcomes=(*outcomes, benchmark_outcome),
        engine_identities=tuple(identities),
    )


def execute_walk_forward(
    plan: WalkForwardPlan,
    *,
    repository_root: Path,
    trading_calendar: Any,
    repository_commit: str,
    dirty_worktree: bool,
    clock: Any = _utc_now,
    entry_module_source: Path | None = None,
) -> WalkForwardResult:
    """Run every fold in deterministic order and return the immutable run result.

    Performs no publication I/O. Refuses before any fold if a transport is
    reachable. Wall-clock values from ``clock`` land only in ``provenance`` and
    never enter the run identity or any result digest.
    """

    assert_states_are_complete()
    assert_network_egress_denied(repository_root, entry_module_source=entry_module_source)
    assert_declared_calendar_witnesses_injected(trading_calendar, plan)

    ordered = plan.ordered_folds()
    partitions: list[Partition] = [
        _run_one_fold(
            fold,
            registries=plan.registries,
            calendar=trading_calendar,
            repository_root=repository_root,
            authorized_fold_ids=plan.authorized_fold_ids,
        )
        for fold in ordered
    ]

    valid = tuple(p for p in partitions if isinstance(p, ValidPartition))
    degraded = tuple(p for p in partitions if isinstance(p, DegradedPartition))
    aggregate = aggregate_valid(valid)

    bound_inputs = BoundInputs(
        walk_forward_engine_version=SCHEMA_VERSION,
        repository_commit=repository_commit,
        dirty_worktree=dirty_worktree,
        config_sha256_grouped=plan.registries.config_sha256_grouped(),
        schema_sha256_grouped=schema_sha256_grouped(),
        data_manifest_sha256_grouped=grouped_document_digest(
            {"folds": sorted((fold.data_document() for fold in ordered), key=lambda d: d["fold_id"])}
        ),
        initial_state_sha256_grouped=grouped_document_digest(
            {
                "folds": sorted(
                    (fold.initial_state_document() for fold in ordered),
                    key=lambda d: d["fold_id"],
                )
            }
        ),
        sample_fold_id=plan.sample_fold_id,
        authorized_fold_ids=tuple(sorted(plan.authorized_fold_ids)),
        share_mode=plan.share_mode,
        regulatory_fee_mode=plan.regulatory_fee_mode,
        cost_policy_id=plan.cost_policy_id,
        transaction_tax_policy_id=plan.transaction_tax_policy_id,
        transaction_tax_policy_sha256_grouped=plan.transaction_tax_policy_sha256_grouped,
        benchmark_control_ids=tuple(sorted({fold.benchmark_control_id for fold in ordered})),
        calendar_id=plan.calendar_id,
        calendar_sha256_grouped=plan.calendar_sha256_grouped,
        engine_bindings=(
            *((engine_id, schema) for _role, engine_id, schema in ORCHESTRATED_ENGINES),
            (ENGINE_ID, SCHEMA_VERSION),
        ),
    )
    run_id = bound_inputs.run_id()

    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in OUTPUT_TABLE_NAMES}
    for valid_partition in valid:
        for name, rows in _valid_partition_rows(valid_partition, run_id).items():
            tables[name].extend(rows)
    for degraded_partition in degraded:
        tables["warnings_errors"].extend(_degraded_rows(degraded_partition, run_id))

    state = (
        RUN_COMPLETED_WITH_VALID_PARTITIONS
        if valid
        else RUN_COMPLETED_NO_VALID_PARTITIONS
    )
    started = clock()
    provenance = {
        "note": "wall-clock values are recorded outside the canonical result identity",
        "wall_clock_started_utc": _isoformat_utc(started),
    }
    return WalkForwardResult(
        bound_inputs=bound_inputs,
        state=state,
        ordered_fold_ids=tuple(fold.fold_id for fold in ordered),
        valid_partitions=valid,
        degraded_partitions=degraded,
        aggregate=aggregate,
        output_tables={name: tuple(rows) for name, rows in tables.items()},
        provenance=provenance,
    )


def _isoformat_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise WalkForwardError(
            BLOCKED_MALFORMED_WALK_FORWARD_INPUT, "clock must return a timezone-aware datetime"
        )
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Atomic, no-clobber, root-confined publication
# ---------------------------------------------------------------------------


def _lexical_within(path: Path, parent: Path) -> bool:
    candidate = os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))
    boundary = os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(parent))))
    try:
        return os.path.commonpath((candidate, boundary)) == boundary
    except ValueError:
        return False


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_file_durable(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


@dataclass(frozen=True)
class StagedRun:
    """A fully-written staging directory awaiting the atomic publish."""

    staging_directory: Path
    final_directory: Path
    run_id: str
    runs_root: Path


def stage_run(result: WalkForwardResult, *, runs_root: Path) -> StagedRun:
    """Write every table file and the manifest into a private staging directory.

    Nothing appears at the final run directory until :func:`commit_run`. An
    interruption here therefore leaves only the staging directory, never a
    partial published run.
    """

    root = runs_root.resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    final_directory = root / f"run-{result.run_id_hex}"
    if not _lexical_within(final_directory, root):
        raise WalkForwardError(
            BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT,
            "the run directory must resolve inside the configured runs root",
            path=str(final_directory),
        )
    staging_name = tempfile.mkdtemp(prefix=".wf-stage-", dir=root)
    staging_directory = Path(staging_name)
    for name in OUTPUT_TABLE_NAMES:
        _write_file_durable(
            staging_directory / f"table-{name}.json",
            canonical_json_bytes(result.table_document(name)),
        )
    _write_file_durable(
        staging_directory / "manifest.json",
        canonical_json_bytes(result.manifest_document()),
    )
    _fsync_directory(staging_directory)
    return StagedRun(
        staging_directory=staging_directory,
        final_directory=final_directory,
        run_id=result.run_id,
        runs_root=root,
    )


def commit_run(staged: StagedRun) -> Path:
    """Atomically publish a staged run. No-clobber: an existing run is never mutated."""

    if not _lexical_within(staged.final_directory, staged.runs_root):
        raise WalkForwardError(
            BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT,
            "the run directory must resolve inside the configured runs root",
            path=str(staged.final_directory),
        )
    if staged.final_directory.exists():
        with contextlib.suppress(OSError):
            _remove_tree(staged.staging_directory)
        raise WalkForwardError(
            BLOCKED_RUN_DIRECTORY_EXISTS,
            f"run directory {staged.final_directory.name} already exists; reruns never mutate it",
            path=str(staged.final_directory),
        )
    try:
        os.rename(staged.staging_directory, staged.final_directory)
    except (FileExistsError, OSError) as exc:
        with contextlib.suppress(OSError):
            _remove_tree(staged.staging_directory)
        raise WalkForwardError(
            BLOCKED_RUN_DIRECTORY_EXISTS,
            f"run directory {staged.final_directory.name} already exists; reruns never mutate it",
            path=str(staged.final_directory),
        ) from exc
    _fsync_directory(staged.runs_root)
    return staged.final_directory


def _remove_tree(directory: Path) -> None:
    for child in directory.iterdir():
        if child.is_dir():
            _remove_tree(child)
        else:
            child.unlink()
    directory.rmdir()


def publish_run(result: WalkForwardResult, *, runs_root: Path) -> Path:
    """Stage then atomically commit a run directory. Returns the published path."""

    return commit_run(stage_run(result, runs_root=runs_root))


# ---------------------------------------------------------------------------
# Public composition entry point
# ---------------------------------------------------------------------------


def run_and_publish_walk_forward(
    plan: WalkForwardPlan,
    *,
    repository_root: Path,
    trading_calendar: Any,
    runs_root: Path,
    repository_commit: str,
    dirty_worktree: bool,
    clock: Any = _utc_now,
) -> tuple[WalkForwardResult, Path]:
    """Execute a walk-forward run and publish it atomically. The one-call driver."""

    result = execute_walk_forward(
        plan,
        repository_root=repository_root,
        trading_calendar=trading_calendar,
        repository_commit=repository_commit,
        dirty_worktree=dirty_worktree,
        clock=clock,
    )
    published = publish_run(result, runs_root=runs_root)
    return result, published
