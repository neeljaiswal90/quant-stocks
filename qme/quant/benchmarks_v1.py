"""NEE-130 aligned benchmark and ablation controls.

This engine builds benchmark and ablation *controls* that cannot benefit from an
easier universe, action, execution, or cost assumption than the strategy. It
constructs **no ledger of its own**: every benchmark ledger is produced by
CALLING the frozen NEE-129 execution engine
(:func:`qme.quant.execution_v1.run_execution_program`) through an
:class:`~qme.quant.execution_v1.ExecutionProgram`, so a benchmark is literally
the same accounting path as the strategy -- the same t+1 timing, the same
registered cost policy, the same ``1e-8`` rounding, the same self-financing cash
rule, the same corporate-action handling. Reusing that code is the requirement,
not an optimization.

Controls this engine recognizes
-------------------------------

* an **SPY** buy-and-hold total-return external benchmark;
* a **QQQ** buy-and-hold total-return external benchmark;
* a **monthly equal-weight** point-in-time eligible-universe external benchmark,
  targeting ``1/N_t`` across the SAME point-in-time eligible set the strategy
  uses;
* a **no-filter strategy** control and per-filter variants, carried as LABELED
  ABLATIONS -- never as independent external benchmarks.

Two sibling result types, never interconvertible
------------------------------------------------

:class:`ExternalBenchmark` and :class:`Ablation` are siblings, not subtypes. An
``ExternalBenchmark`` can be serialized as an external benchmark record;
:func:`serialize_as_external_benchmark` accepts an ``ExternalBenchmark`` and
nothing else. An :class:`Ablation` carries no such method and the serializer
refuses it as :data:`BLOCKED_ABLATION_NOT_AN_EXTERNAL_BENCHMARK`, so a filter
variant can never be published as an independent external benchmark. The static
half of that wall is proved by a ``mypy --strict`` probe in the test module.

An ablation changes ONLY its declared dimension
-----------------------------------------------

Every :class:`ConfigFingerprint` carries one value per registered configuration
dimension. An ablation declares exactly one ablated dimension, which must be a
registered *universe filter* dimension (never an action, execution, or cost
dimension), and :func:`assert_ablation_changes_only_declared_dimension` refuses
(:data:`BLOCKED_ABLATION_TOUCHED_UNDECLARED_DIMENSION`) an ablation whose
fingerprint differs from the strategy's at any other dimension. A benchmark
therefore cannot smuggle an easier action, execution, or cost assumption in
behind a filter change.

Same inputs as the strategy
---------------------------

:class:`StrategyLedgerBasis` threads the SAME initial capital, exchange calendar,
opening session, eligible date range, availability cutoff, cost/tax configuration,
share mode, regulatory-fee mode, and owner-gated registries the strategy runs
under. :func:`construct_external_benchmark` and :func:`construct_ablation` refuse
(:data:`BLOCKED_INITIAL_CAPITAL_MISMATCH`, :data:`BLOCKED_CALENDAR_MISMATCH`,
:data:`BLOCKED_DATE_RANGE_MISMATCH`, :data:`BLOCKED_COST_TAX_CONFIG_MISMATCH`,
:data:`BLOCKED_EXECUTION_CONFIG_MISMATCH`) any program that departs from the
basis, so a control cannot quietly award itself more cash, a friendlier calendar,
an earlier or later opening session, a session outside the strategy's date range,
a cheaper cost policy, or a different fee mode. The opening-session wall compares
the FULL session identity (``session_date``, ``ordinal``, and
``calendar_identity``), and every executed rebalance/close/action session must lie
within ``[min(eligible_sessions), max(eligible_sessions)]``, so a control cannot
open on a different session and compound an easier in-market window before the
first aligned axis date.

Adjusted-close shortcut is refused
----------------------------------

A control's construction basis must be the frozen raw price/action ledger
(:data:`CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER`). The adjusted-close total-return
shortcut is a distinct, unbuildable basis token
(:data:`ADJUSTED_CLOSE_TOTAL_RETURN_SHORTCUT`) that
:class:`BenchmarkControlDefinition` refuses at construction as
:data:`BLOCKED_ADJUSTED_CLOSE_SHORTCUT_FORBIDDEN`. Because every ledger runs
through the execution engine, whose ledger fields admit only the frozen raw
observations, an adjusted-close series cannot be mixed into implementable
accounting even if a caller tried.

Current constituents cannot replace historical membership
---------------------------------------------------------

The equal-weight universe is fed a :class:`PointInTimeEligibleUniverse` whose
``membership_basis`` must be :data:`POINT_IN_TIME_ELIGIBLE_SET`; a
current-constituents basis is refused as
:data:`BLOCKED_CURRENT_CONSTITUENTS_FORBIDDEN`.
:func:`eligible_universe_from_snapshot` derives that membership from a
NEE-133 :class:`~qme.quant.universe_v1.UniverseSnapshot`'s point-in-time
``included_rows``, binding the snapshot's own content hash.

Alignment is downstream of construction
---------------------------------------

:func:`align_benchmark_returns` takes only fully constructed
:class:`BenchmarkLedger` values -- each of which wraps a completed
``EXECUTION_OK`` run -- so benchmark returns are aligned only AFTER each
independent ledger is constructed. It asserts every ledger uses IDENTICAL
eligible dates and availability cutoffs as the strategy (equality, not
similarity) and refuses (:data:`BLOCKED_MISSING_BENCHMARK_OBSERVATION`) any hole
that would silently shorten one series but not the others.

Owner-gated registry ships EMPTY
--------------------------------

The set of sanctioned benchmark controls -- which security is the SPY control,
which is QQQ, and each control's reinvestment policy -- is an owner decision that
has not been made. :data:`REGISTERED_BENCHMARK_CONTROLS` is therefore ``()`` and
:func:`resolve_benchmark_control` fails closed with
:data:`BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL`, mirroring the empty-registry
pattern in :mod:`qme.data.stores.riskfree_v1` and :mod:`qme.quant.execution_v1`.
Tests inject records through the ``registry=`` parameter under the
``TEST_CONSTRUCTED`` kind, which :func:`validate_benchmark_control_registry`
forbids in the shipped registry.

Non-claims
----------

No performance threshold is asserted; the economic comparison is out of scope.
This engine claims no alpha, no empirical performance, no capacity value, no
production readiness, no live-order authority, and no prospective consumption.
:data:`NON_CLAIMS` is copied into every manifest and report.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Final

from qme.foundation.lineage import canonical_json_bytes
from qme.quant.equations import (
    MarketEvidenceBinding,
    RawExecutionPrice,
    TransactionTaxPolicy,
)
from qme.quant.execution_v1 import (
    ENGINE_ID as EXECUTION_ENGINE_ID,
)
from qme.quant.execution_v1 import (
    EXECUTION_OK,
    CorporateActionStage,
    DeclaredSignedDeltas,
    EqualWeightTargetProgram,
    ExecutionProgram,
    ExecutionRun,
    RebalanceStage,
    RegistryOverrides,
    SessionCloseStage,
    SessionRef,
    format_ledger,
    run_execution_program,
)
from qme.quant.universe_v1 import UniverseSnapshot

# ---------------------------------------------------------------------------
# Engine identity
# ---------------------------------------------------------------------------

ENGINE_ID: Final = "QME-NEE130-BENCHMARK-ABLATION-CONTROLS-ENGINE-V1"
SCHEMA_VERSION: Final = "qme.benchmark_controls.v1"

#: The identity of the execution engine every benchmark ledger must be built by.
#: A run whose manifest does not carry this engine id was not produced by the
#: shared accounting path and is refused.
REQUIRED_EXECUTION_ENGINE_ID: Final = EXECUTION_ENGINE_ID

# ---------------------------------------------------------------------------
# Construction basis and membership vocabulary
# ---------------------------------------------------------------------------

#: The only admissible construction basis: the frozen raw price/action ledger,
#: run through the execution engine.
CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER: Final = (
    "FROZEN_RAW_PRICE_ACTION_LEDGER_VIA_EXECUTION_ENGINE"
)
#: The forbidden shortcut. Named so it can be refused explicitly; it can never be
#: built, because the execution engine's ledger fields admit only raw observations.
ADJUSTED_CLOSE_TOTAL_RETURN_SHORTCUT: Final = "ADJUSTED_CLOSE_TOTAL_RETURN_SHORTCUT"
CONSTRUCTION_BASES: Final[tuple[str, ...]] = (
    ADJUSTED_CLOSE_TOTAL_RETURN_SHORTCUT,
    CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER,
)

#: The only admissible universe membership basis for the equal-weight control.
POINT_IN_TIME_ELIGIBLE_SET: Final = "POINT_IN_TIME_ELIGIBLE_SET"
#: A current-constituents membership basis, named so it can be refused explicitly.
CURRENT_CONSTITUENTS_SNAPSHOT: Final = "CURRENT_CONSTITUENTS_SNAPSHOT"
MEMBERSHIP_BASES: Final[tuple[str, ...]] = (
    CURRENT_CONSTITUENTS_SNAPSHOT,
    POINT_IN_TIME_ELIGIBLE_SET,
)

#: Registered external-benchmark control kinds.
CONTROL_KIND_SPY_BUY_AND_HOLD: Final = "SPY_BUY_AND_HOLD_TOTAL_RETURN"
CONTROL_KIND_QQQ_BUY_AND_HOLD: Final = "QQQ_BUY_AND_HOLD_TOTAL_RETURN"
CONTROL_KIND_MONTHLY_EQUAL_WEIGHT: Final = "MONTHLY_EQUAL_WEIGHT_ELIGIBLE_UNIVERSE"
REGISTERED_CONTROL_KINDS: Final[tuple[str, ...]] = (
    CONTROL_KIND_MONTHLY_EQUAL_WEIGHT,
    CONTROL_KIND_QQQ_BUY_AND_HOLD,
    CONTROL_KIND_SPY_BUY_AND_HOLD,
)
#: Control kinds that name a single reference security (the buy-and-hold ETFs).
REFERENCE_SECURITY_CONTROL_KINDS: Final[tuple[str, ...]] = (
    CONTROL_KIND_QQQ_BUY_AND_HOLD,
    CONTROL_KIND_SPY_BUY_AND_HOLD,
)

#: Registered reinvestment policies. Both route dividends through the frozen
#: ledger; neither is an adjusted-close shortcut.
REINVESTMENT_HELD_AS_CASH: Final = "DIVIDEND_HELD_AS_CASH"
REINVESTMENT_REINVESTED_NEXT_SESSION_RAW_OPEN: Final = (
    "DIVIDEND_REINVESTED_NEXT_SESSION_RAW_OPEN"
)
REGISTERED_REINVESTMENT_POLICIES: Final[tuple[str, ...]] = (
    REINVESTMENT_HELD_AS_CASH,
    REINVESTMENT_REINVESTED_NEXT_SESSION_RAW_OPEN,
)

#: Registered trading frequencies a control may declare.
TRADING_FREQUENCY_BUY_AND_HOLD: Final = "BUY_AND_HOLD"
TRADING_FREQUENCY_MONTHLY: Final = "MONTHLY"
TRADING_FREQUENCY_PER_STRATEGY_REBALANCE: Final = "PER_STRATEGY_REBALANCE"
REGISTERED_TRADING_FREQUENCIES: Final[tuple[str, ...]] = (
    TRADING_FREQUENCY_BUY_AND_HOLD,
    TRADING_FREQUENCY_MONTHLY,
    TRADING_FREQUENCY_PER_STRATEGY_REBALANCE,
)

#: The two sibling benchmark classes.
BENCHMARK_CLASS_EXTERNAL: Final = "EXTERNAL_BENCHMARK"
BENCHMARK_CLASS_ABLATION: Final = "ABLATION"

#: The registered universe-FILTER dimensions an ablation may ablate. Every entry
#: is a ``universe.*`` key; action, execution, and cost dimensions are absent by
#: design, so an ablation can never declare one.
REGISTERED_ABLATION_DIMENSIONS: Final[tuple[str, ...]] = (
    "universe.classification_filter",
    "universe.coverage_minimum",
    "universe.eligibility_filter",
    "universe.history_minimum",
    "universe.liquidity_floor",
    "universe.raw_price_floor",
    "universe.staleness_bound",
)

#: The M1 coverage limitation every report carries, verbatim from NEE-133. The
#: benchmark universe is the same survivorship-reduced proxy the strategy screens.
COVERAGE_LIMITATION: Final = "AV_SURVIVORSHIP_REDUCED_PROXY"

#: A fixed, citable statement of what these controls do and do not establish.
LIMITATIONS: Final[tuple[str, ...]] = (
    "The eligible universe is the NEE-133 survivorship-reduced Alpha Vantage "
    "proxy; a benchmark screened over it inherits that limitation.",
    "No performance threshold is asserted and no economic comparison is drawn; "
    "aligned NAV series are published without a superiority claim.",
    "Every ledger is a research reconstruction of a raw price/action ledger; it "
    "is not a live or prospective order path.",
    "Reinvestment, dividends, costs, and taxes are the frozen execution engine's, "
    "so a benchmark cannot be cheaper or better-timed than the strategy.",
)

#: Downstream claims this engine has not earned. Written to every manifest and
#: report; every value is False.
NON_CLAIMS: Final[Mapping[str, bool]] = MappingProxyType(
    {
        "alpha_demonstrated": False,
        "benchmark_outperformance_measured": False,
        "capacity_value_registered": False,
        "economic_comparison_drawn": False,
        "empirical_performance_measured": False,
        "freeze_blocker_changed": False,
        "independent_review_recorded": False,
        "live_order_authority": False,
        "owner_gated_values_registered": False,
        "production_deployment_authorized": False,
        "production_ready": False,
        "prospective_observations_consumable": False,
    }
)

# ---------------------------------------------------------------------------
# Provenance vocabulary for the owner-gated registry
# ---------------------------------------------------------------------------

SOURCE_KIND_OWNER_DECISION_RECORD: Final = "OWNER_DECISION_RECORD"
SOURCE_KIND_PUBLISHER_REFERENCE: Final = "PUBLISHER_REFERENCE"
SOURCE_KIND_TEST_CONSTRUCTED: Final = "TEST_CONSTRUCTED"
SOURCE_KINDS: Final[tuple[str, ...]] = (
    SOURCE_KIND_OWNER_DECISION_RECORD,
    SOURCE_KIND_PUBLISHER_REFERENCE,
    SOURCE_KIND_TEST_CONSTRUCTED,
)
#: Kinds admissible in the shipped registry. ``TEST_CONSTRUCTED`` is not one.
REGISTERED_SOURCE_KINDS: Final[tuple[str, ...]] = (
    SOURCE_KIND_OWNER_DECISION_RECORD,
    SOURCE_KIND_PUBLISHER_REFERENCE,
)

# ---------------------------------------------------------------------------
# Typed fail-closed states
# ---------------------------------------------------------------------------

BENCHMARK_OK: Final = "BENCHMARK_OK"

BLOCKED_ABLATION_NOT_AN_EXTERNAL_BENCHMARK: Final = (
    "BLOCKED_ABLATION_NOT_AN_EXTERNAL_BENCHMARK"
)
BLOCKED_ABLATION_TOUCHED_UNDECLARED_DIMENSION: Final = (
    "BLOCKED_ABLATION_TOUCHED_UNDECLARED_DIMENSION"
)
BLOCKED_ADJUSTED_CLOSE_SHORTCUT_FORBIDDEN: Final = (
    "BLOCKED_ADJUSTED_CLOSE_SHORTCUT_FORBIDDEN"
)
BLOCKED_ALIGNMENT_BEFORE_LEDGER_CONSTRUCTED: Final = (
    "BLOCKED_ALIGNMENT_BEFORE_LEDGER_CONSTRUCTED"
)
BLOCKED_AMBIGUOUS_BENCHMARK_CONTROL: Final = "BLOCKED_AMBIGUOUS_BENCHMARK_CONTROL"
BLOCKED_AVAILABILITY_CUTOFF_MISMATCH: Final = "BLOCKED_AVAILABILITY_CUTOFF_MISMATCH"
BLOCKED_CALENDAR_MISMATCH: Final = "BLOCKED_CALENDAR_MISMATCH"
BLOCKED_CONTROL_PROGRAM_MISMATCH: Final = "BLOCKED_CONTROL_PROGRAM_MISMATCH"
BLOCKED_COST_TAX_CONFIG_MISMATCH: Final = "BLOCKED_COST_TAX_CONFIG_MISMATCH"
BLOCKED_CURRENT_CONSTITUENTS_FORBIDDEN: Final = "BLOCKED_CURRENT_CONSTITUENTS_FORBIDDEN"
BLOCKED_DATE_RANGE_MISMATCH: Final = "BLOCKED_DATE_RANGE_MISMATCH"
BLOCKED_ELIGIBLE_DATES_MISMATCH: Final = "BLOCKED_ELIGIBLE_DATES_MISMATCH"
BLOCKED_EMPTY_ELIGIBLE_UNIVERSE: Final = "BLOCKED_EMPTY_ELIGIBLE_UNIVERSE"
BLOCKED_EXECUTION_CONFIG_MISMATCH: Final = "BLOCKED_EXECUTION_CONFIG_MISMATCH"
BLOCKED_INITIAL_CAPITAL_MISMATCH: Final = "BLOCKED_INITIAL_CAPITAL_MISMATCH"
BLOCKED_MALFORMED_BENCHMARK_INPUT: Final = "BLOCKED_MALFORMED_BENCHMARK_INPUT"
BLOCKED_MISSING_BENCHMARK_OBSERVATION: Final = "BLOCKED_MISSING_BENCHMARK_OBSERVATION"
BLOCKED_NON_EXECUTION_LEDGER: Final = "BLOCKED_NON_EXECUTION_LEDGER"
BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL: Final = (
    "BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL"
)
BLOCKED_UNDECLARED_ABLATION_DIMENSION: Final = "BLOCKED_UNDECLARED_ABLATION_DIMENSION"
BLOCKED_UNREGISTERED_BENCHMARK_CONTROL: Final = (
    "BLOCKED_UNREGISTERED_BENCHMARK_CONTROL"
)
BLOCKED_UNREGISTERED_CONTROL_KIND: Final = "BLOCKED_UNREGISTERED_CONTROL_KIND"
BLOCKED_UNREGISTERED_REINVESTMENT_POLICY: Final = (
    "BLOCKED_UNREGISTERED_REINVESTMENT_POLICY"
)
BLOCKED_UNREGISTERED_SOURCE_KIND: Final = "BLOCKED_UNREGISTERED_SOURCE_KIND"
BLOCKED_UNREGISTERED_TRADING_FREQUENCY: Final = (
    "BLOCKED_UNREGISTERED_TRADING_FREQUENCY"
)

#: Every fail-closed state this module raises, sorted. The test module asserts the
#: observed union of raised states equals this tuple exactly, so neither an
#: unraisable state nor an unregistered one may exist.
BENCHMARK_FAIL_CLOSED_STATES: Final[tuple[str, ...]] = (
    BLOCKED_ABLATION_NOT_AN_EXTERNAL_BENCHMARK,
    BLOCKED_ABLATION_TOUCHED_UNDECLARED_DIMENSION,
    BLOCKED_ADJUSTED_CLOSE_SHORTCUT_FORBIDDEN,
    BLOCKED_ALIGNMENT_BEFORE_LEDGER_CONSTRUCTED,
    BLOCKED_AMBIGUOUS_BENCHMARK_CONTROL,
    BLOCKED_AVAILABILITY_CUTOFF_MISMATCH,
    BLOCKED_CALENDAR_MISMATCH,
    BLOCKED_CONTROL_PROGRAM_MISMATCH,
    BLOCKED_COST_TAX_CONFIG_MISMATCH,
    BLOCKED_CURRENT_CONSTITUENTS_FORBIDDEN,
    BLOCKED_DATE_RANGE_MISMATCH,
    BLOCKED_ELIGIBLE_DATES_MISMATCH,
    BLOCKED_EMPTY_ELIGIBLE_UNIVERSE,
    BLOCKED_EXECUTION_CONFIG_MISMATCH,
    BLOCKED_INITIAL_CAPITAL_MISMATCH,
    BLOCKED_MALFORMED_BENCHMARK_INPUT,
    BLOCKED_MISSING_BENCHMARK_OBSERVATION,
    BLOCKED_NON_EXECUTION_LEDGER,
    BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL,
    BLOCKED_UNDECLARED_ABLATION_DIMENSION,
    BLOCKED_UNREGISTERED_BENCHMARK_CONTROL,
    BLOCKED_UNREGISTERED_CONTROL_KIND,
    BLOCKED_UNREGISTERED_REINVESTMENT_POLICY,
    BLOCKED_UNREGISTERED_SOURCE_KIND,
    BLOCKED_UNREGISTERED_TRADING_FREQUENCY,
)


def assert_fail_closed_states_complete() -> None:
    """Refuse a states tuple that is unsorted or contains a duplicate.

    The completeness assertion the ticket requires: the published tuple is the
    sorted, duplicate-free union of every ``BLOCKED_*`` constant in this module.
    """

    if list(BENCHMARK_FAIL_CLOSED_STATES) != sorted(set(BENCHMARK_FAIL_CLOSED_STATES)):
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT,
            "the fail-closed states tuple must be sorted and free of duplicates",
        )


class BenchmarkControlError(ValueError):
    """A typed fail-closed refusal carrying the state and the affected identity.

    ``state`` is one of :data:`BENCHMARK_FAIL_CLOSED_STATES`. The identity fields
    are filled in whenever a refusal is attributable to a specific control,
    session, dimension, or field.
    """

    def __init__(
        self,
        state: str,
        message: str,
        *,
        control_id: str | None = None,
        session: str | None = None,
        dimension: str | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(f"{state}: {message}")
        self.state = state
        self.control_id = control_id
        self.session = session
        self.dimension = dimension
        self.path = path

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "control_id": self.control_id,
            "dimension": self.dimension,
            "path": self.path,
            "session": self.session,
            "state": self.state,
        }


# ---------------------------------------------------------------------------
# Digest and identifier primitives (this module's own local helpers)
# ---------------------------------------------------------------------------

_HEX_GROUP_START: Final = 0
_HEX_DIGEST_LENGTH: Final = 64
_HEX_GROUP_STEP: Final = 8


def group_sha256(payload: bytes) -> str:
    """Grouped SHA-256: eight lowercase 8-hex groups joined by ``:``.

    Local to this module, matching the grouped form the wave-1 engines publish,
    so a contiguous 64-hex run never appears in an artifact.
    """

    digest = hashlib.sha256(payload).hexdigest()
    return ":".join(
        digest[index : index + _HEX_GROUP_STEP]
        for index in range(_HEX_GROUP_START, _HEX_DIGEST_LENGTH, _HEX_GROUP_STEP)
    )


def grouped_document_digest(document: Mapping[str, Any]) -> str:
    """Grouped SHA-256 over the canonical JSON bytes of a document."""

    return group_sha256(canonical_json_bytes(document))


def _identifier(value: object, *, what: str) -> str:
    """Refuse a blank or non-string identifier."""

    if type(value) is not str or not value.strip():
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT, f"{what} must be non-empty text"
        )
    return value


def _sorted_ids(values: Sequence[str]) -> tuple[str, ...]:
    """Content-derived order: UTF-8 bytes ascending, matching the engines."""

    return tuple(sorted(values, key=lambda item: item.encode("utf-8")))


def _parse_cutoff_instant(value: str, *, what: str) -> datetime:
    """Parse an ISO-8601 availability cutoff into a timezone-aware instant.

    The strategy's availability cutoff is not a free-text label: it is the as-of
    instant every benchmark observation must respect. Parsing it here (accepting a
    trailing ``Z``) lets the construction wall compare each observation's evidence
    vintage against it. A blank, non-ISO, or naive value fails closed.
    """

    text = _identifier(value, what=what)
    normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT,
            f"{what} must be an ISO-8601 instant; got {value!r}",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT,
            f"{what} must be a timezone-aware ISO-8601 instant; got {value!r}",
        )
    return parsed


# ---------------------------------------------------------------------------
# Configuration fingerprints and the ablation dimension diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigFingerprint:
    """One declared value per configuration dimension, across all four groups.

    Keys are namespaced ``universe.*`` / ``action.*`` / ``execution.*`` /
    ``cost.*``. The ablation diff compares two fingerprints dimension by
    dimension, so a benchmark cannot change an action, execution, or cost
    dimension while claiming to ablate a filter.
    """

    dimensions: Mapping[str, str]

    def __post_init__(self) -> None:
        raw = self.dimensions
        if not isinstance(raw, Mapping) or not raw:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                "a config fingerprint needs at least one declared dimension",
            )
        validated: dict[str, str] = {}
        for key, value in raw.items():
            dimension = _identifier(key, what="config dimension")
            validated[dimension] = _identifier(value, what=f"{dimension} value")
        object.__setattr__(self, "dimensions", MappingProxyType(dict(validated)))

    def to_json_dict(self) -> dict[str, str]:
        return {key: self.dimensions[key] for key in sorted(self.dimensions)}

    def sha256_grouped(self) -> str:
        return grouped_document_digest(
            {"dimensions": self.to_json_dict(), "schema_version": SCHEMA_VERSION}
        )


def assert_ablation_changes_only_declared_dimension(
    *,
    strategy_config: ConfigFingerprint,
    ablation_config: ConfigFingerprint,
    ablated_dimension: str,
) -> None:
    """Refuse an ablation that touches any dimension other than the declared one.

    The declared dimension must be a registered universe-filter dimension. Both
    fingerprints must carry the same dimension key set, and every dimension other
    than the declared one must be byte-for-byte identical.
    """

    if ablated_dimension not in REGISTERED_ABLATION_DIMENSIONS:
        raise BenchmarkControlError(
            BLOCKED_UNDECLARED_ABLATION_DIMENSION,
            f"{ablated_dimension!r} is not a registered universe-filter dimension; "
            f"only {REGISTERED_ABLATION_DIMENSIONS} may be ablated",
            dimension=ablated_dimension,
        )
    strategy_keys = set(strategy_config.dimensions)
    ablation_keys = set(ablation_config.dimensions)
    if strategy_keys != ablation_keys:
        raise BenchmarkControlError(
            BLOCKED_ABLATION_TOUCHED_UNDECLARED_DIMENSION,
            "an ablation may not add or remove a configuration dimension; the "
            f"dimension key sets differ by {sorted(strategy_keys ^ ablation_keys)}",
            dimension=ablated_dimension,
        )
    if ablated_dimension not in strategy_keys:
        raise BenchmarkControlError(
            BLOCKED_UNDECLARED_ABLATION_DIMENSION,
            f"the declared dimension {ablated_dimension!r} is absent from the "
            "strategy configuration fingerprint",
            dimension=ablated_dimension,
        )
    touched = sorted(
        key
        for key in strategy_keys
        if key != ablated_dimension
        and strategy_config.dimensions[key] != ablation_config.dimensions[key]
    )
    if touched:
        raise BenchmarkControlError(
            BLOCKED_ABLATION_TOUCHED_UNDECLARED_DIMENSION,
            f"the ablation of {ablated_dimension!r} also changed {touched}; an "
            "ablation changes only its declared filter dimension",
            dimension=ablated_dimension,
        )


# ---------------------------------------------------------------------------
# Owner-gated benchmark control registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkControlDefinition:
    """A sanctioned benchmark control, with its provenance and construction basis.

    Construction refuses the adjusted-close shortcut basis, an unregistered
    control kind or reinvestment policy, and a reference security declared for a
    control kind that has none (or absent for one that requires it).
    """

    control_id: str
    source_kind: str
    source: str
    source_reference: str
    effective_date: date
    control_kind: str
    construction_basis: str
    reinvestment_policy: str
    reference_security_id: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.control_id, what="control_id")
        if self.source_kind not in SOURCE_KINDS:
            raise BenchmarkControlError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"unregistered source_kind {self.source_kind!r}",
                control_id=self.control_id,
            )
        for label, value in (
            ("source", self.source),
            ("source_reference", self.source_reference),
        ):
            if type(value) is not str or not value.strip():
                raise BenchmarkControlError(
                    BLOCKED_UNREGISTERED_SOURCE_KIND,
                    f"{label} must state where the control came from",
                    control_id=self.control_id,
                )
        if type(self.effective_date) is not date:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                "effective_date must be an exact date",
                control_id=self.control_id,
            )
        if self.construction_basis == ADJUSTED_CLOSE_TOTAL_RETURN_SHORTCUT:
            raise BenchmarkControlError(
                BLOCKED_ADJUSTED_CLOSE_SHORTCUT_FORBIDDEN,
                "an adjusted-close total-return shortcut may not be mixed with "
                "implementable strategy accounting; the only admissible basis is "
                f"{CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER!r}",
                control_id=self.control_id,
            )
        if self.construction_basis != CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                f"unregistered construction basis {self.construction_basis!r}",
                control_id=self.control_id,
            )
        if self.control_kind not in REGISTERED_CONTROL_KINDS:
            raise BenchmarkControlError(
                BLOCKED_UNREGISTERED_CONTROL_KIND,
                f"unregistered control kind {self.control_kind!r}",
                control_id=self.control_id,
            )
        if self.reinvestment_policy not in REGISTERED_REINVESTMENT_POLICIES:
            raise BenchmarkControlError(
                BLOCKED_UNREGISTERED_REINVESTMENT_POLICY,
                f"unregistered reinvestment policy {self.reinvestment_policy!r}",
                control_id=self.control_id,
            )
        needs_reference = self.control_kind in REFERENCE_SECURITY_CONTROL_KINDS
        if needs_reference and self.reference_security_id is None:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                f"{self.control_kind} names a single reference security; "
                "reference_security_id must be declared",
                control_id=self.control_id,
            )
        if not needs_reference and self.reference_security_id is not None:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                f"{self.control_kind} screens a universe and names no single "
                "reference security; reference_security_id must be absent",
                control_id=self.control_id,
            )
        if self.reference_security_id is not None:
            _identifier(self.reference_security_id, what="reference_security_id")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "construction_basis": self.construction_basis,
            "control_id": self.control_id,
            "control_kind": self.control_kind,
            "effective_date": self.effective_date.isoformat(),
            "reference_security_id": self.reference_security_id,
            "reinvestment_policy": self.reinvestment_policy,
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
        }


#: EMPTY BY DESIGN. The sanctioned benchmark-control set -- which security is the
#: SPY control, which is QQQ, and each control's reinvestment policy -- is an
#: owner decision that has not been made, so there is nothing to resolve and
#: :func:`resolve_benchmark_control` fails closed.
REGISTERED_BENCHMARK_CONTROLS: Final[tuple[BenchmarkControlDefinition, ...]] = ()


def validate_benchmark_control_registry(
    registry: Sequence[BenchmarkControlDefinition] = REGISTERED_BENCHMARK_CONTROLS,
) -> None:
    """Fail closed on an empty, duplicated, or test-contaminated registry."""

    if not registry:
        raise BenchmarkControlError(
            BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL,
            "no benchmark control is registered; the sanctioned benchmark set is an "
            "owner decision that is pending, and this engine refuses to assume one",
        )
    seen: set[str] = set()
    for definition in registry:
        if not isinstance(definition, BenchmarkControlDefinition):
            raise BenchmarkControlError(
                BLOCKED_UNREGISTERED_BENCHMARK_CONTROL,
                "registry entries must be BenchmarkControlDefinition records",
            )
        if definition.control_id in seen:
            raise BenchmarkControlError(
                BLOCKED_AMBIGUOUS_BENCHMARK_CONTROL,
                f"duplicate control_id in registry: {definition.control_id}",
                control_id=definition.control_id,
            )
        seen.add(definition.control_id)
        if (
            registry is REGISTERED_BENCHMARK_CONTROLS
            and definition.source_kind not in REGISTERED_SOURCE_KINDS
        ):
            raise BenchmarkControlError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"{definition.control_id}: {definition.source_kind} may not ship in "
                "the registry",
                control_id=definition.control_id,
            )


def resolve_benchmark_control(
    control_id: str,
    *,
    registry: Sequence[BenchmarkControlDefinition] = REGISTERED_BENCHMARK_CONTROLS,
) -> BenchmarkControlDefinition:
    """Return the registered control, or fail closed. Never invents one."""

    validate_benchmark_control_registry(registry)
    matches = [item for item in registry if item.control_id == control_id]
    if not matches:
        raise BenchmarkControlError(
            BLOCKED_UNREGISTERED_BENCHMARK_CONTROL,
            f"benchmark control {control_id!r} is not registered",
            control_id=control_id,
        )
    if len(matches) > 1:  # pragma: no cover - validate rejects duplicates first
        raise BenchmarkControlError(
            BLOCKED_AMBIGUOUS_BENCHMARK_CONTROL,
            f"ambiguous benchmark control {control_id!r}",
            control_id=control_id,
        )
    return matches[0]


# ---------------------------------------------------------------------------
# The strategy basis every control must be built from
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyLedgerBasis:
    """The shared inputs threaded from the strategy.

    Pins the SAME initial capital, exchange calendar, availability cutoff,
    cost/tax configuration, share mode, regulatory-fee mode, and owner-gated
    registries the strategy runs under. A benchmark or ablation program that
    departs from these is refused, so a control cannot benefit from an easier
    assumption than the strategy.
    """

    strategy_id: str
    opening_session: SessionRef
    opening_cash: str
    opening_receivables: str
    eligible_sessions: tuple[str, ...]
    availability_cutoff: str
    cost_policy_id: str
    transaction_tax_policy: TransactionTaxPolicy
    share_mode: str
    regulatory_fee_mode: str
    registries: RegistryOverrides
    strategy_config: ConfigFingerprint

    def __post_init__(self) -> None:
        _identifier(self.strategy_id, what="strategy_id")
        if type(self.opening_session) is not SessionRef:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                "opening_session must be a typed execution SessionRef",
            )
        if type(self.transaction_tax_policy) is not TransactionTaxPolicy:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                "transaction_tax_policy must be a frozen NEE-118 TransactionTaxPolicy",
            )
        if type(self.registries) is not RegistryOverrides:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                "registries must be a frozen execution RegistryOverrides",
            )
        if type(self.strategy_config) is not ConfigFingerprint:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                "strategy_config must be a ConfigFingerprint",
            )
        _identifier(self.cost_policy_id, what="cost_policy_id")
        # The availability cutoff is the strategy's pinned as-of instant, not a free
        # label: it must parse as a timezone-aware ISO-8601 instant so the
        # construction wall can bind every benchmark observation's evidence to it.
        _parse_cutoff_instant(self.availability_cutoff, what="availability_cutoff")
        if not self.eligible_sessions:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                "the strategy basis needs at least one eligible session",
            )
        for session in self.eligible_sessions:
            _identifier(session, what="eligible session")
        object.__setattr__(self, "eligible_sessions", tuple(self.eligible_sessions))

    @property
    def calendar_identity(self) -> tuple[str, str]:
        return self.opening_session.calendar_identity

    def sha256_grouped(self) -> str:
        return grouped_document_digest(
            {
                "availability_cutoff": self.availability_cutoff,
                "calendar_identity": list(self.calendar_identity),
                "cost_policy_id": self.cost_policy_id,
                "eligible_sessions": list(self.eligible_sessions),
                "opening_cash": format_ledger(self.opening_cash, what="opening_cash"),
                "opening_receivables": format_ledger(
                    self.opening_receivables, what="opening_receivables"
                ),
                "regulatory_fee_mode": self.regulatory_fee_mode,
                "schema_version": SCHEMA_VERSION,
                "share_mode": self.share_mode,
                "strategy_config_sha256_grouped": self.strategy_config.sha256_grouped(),
                "strategy_id": self.strategy_id,
            }
        )


def _assert_program_matches_basis(
    program: ExecutionProgram, basis: StrategyLedgerBasis
) -> None:
    """Refuse a program that departs from the strategy basis in any shared input.

    Besides the same capital, calendar, cost/tax config, share mode, and fee mode,
    the ticket requires a control to run over the SAME opening session and DATE
    RANGE as the strategy. Two walls enforce that:

    * the program must open on the strategy's EXACT opening session -- identical
      ``session_date``, ``ordinal``, and ``calendar_identity``, not merely the same
      calendar -- so it cannot open earlier or later and compound an in-market
      window before the first aligned axis date;
    * no executed session (a rebalance fill, a session close, or a corporate-action
      session) may fall outside the strategy's eligible date range
      ``[min(eligible_sessions), max(eligible_sessions)]`` -- so a control cannot
      trade or mark on a session outside the strategy's date range.

    Both fail closed with :data:`BLOCKED_DATE_RANGE_MISMATCH`.
    """

    if format_ledger(program.opening_cash, what="opening_cash") != format_ledger(
        basis.opening_cash, what="opening_cash"
    ):
        raise BenchmarkControlError(
            BLOCKED_INITIAL_CAPITAL_MISMATCH,
            "a control must open with the same initial capital as the strategy",
        )
    if format_ledger(
        program.opening_receivables, what="opening_receivables"
    ) != format_ledger(basis.opening_receivables, what="opening_receivables"):
        raise BenchmarkControlError(
            BLOCKED_INITIAL_CAPITAL_MISMATCH,
            "a control must open with the same receivables as the strategy",
        )
    if program.opening_session.calendar_identity != basis.calendar_identity:
        raise BenchmarkControlError(
            BLOCKED_CALENDAR_MISMATCH,
            "a control must run on the same exchange calendar as the strategy",
        )
    program_open = program.opening_session
    basis_open = basis.opening_session
    if (
        program_open.calendar_identity != basis_open.calendar_identity
        or program_open.session_date != basis_open.session_date
        or program_open.ordinal != basis_open.ordinal
    ):
        raise BenchmarkControlError(
            BLOCKED_DATE_RANGE_MISMATCH,
            "a control must open on the strategy's exact opening session "
            f"(session_date={basis_open.session_date.isoformat()}, "
            f"ordinal={basis_open.ordinal}), not merely the same calendar; opening "
            f"on session_date={program_open.session_date.isoformat()}, "
            f"ordinal={program_open.ordinal} compounds an in-market window the "
            "strategy never had before the first aligned axis date",
            session=program_open.session_date.isoformat(),
        )
    first_session = min(basis.eligible_sessions)
    last_session = max(basis.eligible_sessions)
    for path, session_id in _iter_program_stage_sessions(program):
        if session_id < first_session or session_id > last_session:
            raise BenchmarkControlError(
                BLOCKED_DATE_RANGE_MISMATCH,
                f"{path} executes on session {session_id}, outside the strategy's "
                f"eligible date range [{first_session}, {last_session}]; a control "
                "may not trade or mark on a session outside the strategy's date range",
                session=session_id,
                path=path,
            )
    if program.cost_policy_id != basis.cost_policy_id:
        raise BenchmarkControlError(
            BLOCKED_COST_TAX_CONFIG_MISMATCH,
            "a control must resolve the same registered cost policy as the strategy",
        )
    if program.transaction_tax_policy != basis.transaction_tax_policy:
        raise BenchmarkControlError(
            BLOCKED_COST_TAX_CONFIG_MISMATCH,
            "a control must use the same transaction-tax policy as the strategy",
        )
    if program.registries != basis.registries:
        raise BenchmarkControlError(
            BLOCKED_COST_TAX_CONFIG_MISMATCH,
            "a control must use the same owner-gated registries as the strategy",
        )
    if program.share_mode != basis.share_mode:
        raise BenchmarkControlError(
            BLOCKED_EXECUTION_CONFIG_MISMATCH,
            "a control must use the same share mode as the strategy",
        )
    if program.regulatory_fee_mode != basis.regulatory_fee_mode:
        raise BenchmarkControlError(
            BLOCKED_EXECUTION_CONFIG_MISMATCH,
            "a control must use the same regulatory-fee mode as the strategy",
        )


def _iter_program_evidence(
    program: ExecutionProgram,
) -> Iterator[tuple[str, MarketEvidenceBinding]]:
    """Yield ``(path, evidence)`` for every raw observation the program feeds.

    The walk covers the opening marks, and for each stage its rebalance marks, the
    execution price on every declared-delta or equal-weight target, the session
    close marks, and both post-split and post-entitlement action marks -- i.e.
    every :class:`MarketEvidenceBinding` the execution engine will read. Nothing a
    control consumes escapes it.
    """

    for security_id, mark in program.opening_marks.marks.items():
        yield (f"opening_marks[{security_id}]", mark.evidence)
    for index, stage in enumerate(program.stages):
        prefix = f"stages[{index}]"
        if type(stage) is RebalanceStage:
            for security_id, mark in stage.raw_marks.marks.items():
                yield (f"{prefix}.raw_marks[{security_id}]", mark.evidence)
            target = stage.target
            if type(target) is DeclaredSignedDeltas:
                for delta in target.deltas:
                    yield (
                        f"{prefix}.target.deltas[{delta.security_id}].raw_execution_price",
                        delta.raw_execution_price.evidence,
                    )
            elif type(target) is EqualWeightTargetProgram:
                for security_id, exec_price in target.raw_execution_prices.items():
                    yield (
                        f"{prefix}.target.raw_execution_prices[{security_id}]",
                        exec_price.evidence,
                    )
        elif type(stage) is SessionCloseStage:
            for security_id, mark in stage.raw_close_marks.marks.items():
                yield (f"{prefix}.raw_close_marks[{security_id}]", mark.evidence)
        elif type(stage) is CorporateActionStage:
            for label, mark_set in (
                ("raw_marks_after_split", stage.raw_marks_after_split),
                ("raw_marks_after_entitlement", stage.raw_marks_after_entitlement),
            ):
                for security_id, mark in mark_set.marks.items():
                    yield (f"{prefix}.{label}[{security_id}]", mark.evidence)


def _iter_program_stage_sessions(
    program: ExecutionProgram,
) -> Iterator[tuple[str, str]]:
    """Yield ``(path, session_id)`` for every session the program EXECUTES on.

    Mirrors the stage walk in :func:`_iter_program_evidence`, but yields the
    session a stage acts on rather than its evidence: each rebalance's FILL
    session, each session-close session, and each corporate-action session. The
    session id is the ISO ``session_date``, the same key ``eligible_sessions`` and
    the ``nav_by_session`` axis are expressed in. The program's opening session is
    excluded on purpose: it is pinned to the strategy's exact opening session by
    :func:`_assert_program_matches_basis`, and (as with the strategy) may fall on
    the session before the first eligible axis date.
    """

    for index, stage in enumerate(program.stages):
        prefix = f"stages[{index}]"
        if type(stage) is RebalanceStage:
            yield (
                f"{prefix}.fill_session",
                stage.fill_session.session.session_date.isoformat(),
            )
        elif isinstance(stage, (SessionCloseStage, CorporateActionStage)):
            # A session close and a corporate-action stage each act on a single
            # ``session``; a rebalance acts on its ``fill_session``.
            yield (f"{prefix}.session", stage.session.session_date.isoformat())


def _assert_program_evidence_within_cutoff(
    program: ExecutionProgram, basis: StrategyLedgerBasis
) -> None:
    """Refuse any observation whose evidence postdates the availability cutoff.

    The label-equality check in :func:`align_benchmark_returns` proves the DECLARED
    cutoffs match; this binds the DATA to that cutoff. A control may not consume a
    mark, execution price, or corporate-action observation whose first-availability
    (``available_at``) or analysis vintage (``analysis_as_of``) is later than the
    strategy's availability cutoff, so it cannot benefit from look-ahead evidence
    the strategy could not itself have seen. Fails closed with
    :data:`BLOCKED_AVAILABILITY_CUTOFF_MISMATCH`.
    """

    cutoff = _parse_cutoff_instant(basis.availability_cutoff, what="availability_cutoff")
    for path, binding in _iter_program_evidence(program):
        for field_name, instant in (
            ("available_at", binding.available_at),
            ("analysis_as_of", binding.analysis_as_of),
        ):
            if instant > cutoff:
                raise BenchmarkControlError(
                    BLOCKED_AVAILABILITY_CUTOFF_MISMATCH,
                    f"{path} carries {field_name}={instant.isoformat()}, later than the "
                    f"strategy availability cutoff {basis.availability_cutoff}; a control "
                    "may not consume evidence the strategy could not have seen",
                    path=path,
                )


# ---------------------------------------------------------------------------
# Point-in-time eligible universe (the equal-weight control's membership)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PointInTimeEligibleUniverse:
    """The point-in-time eligible set per session, bound to its universe lineage.

    ``membership_basis`` must be :data:`POINT_IN_TIME_ELIGIBLE_SET`; a
    current-constituents basis is refused, so a benchmark cannot back-fill today's
    index membership over history.
    """

    membership_basis: str
    sessions: tuple[str, ...]
    included_by_session: Mapping[str, tuple[str, ...]]
    universe_lineage_sha256_grouped: str

    def __post_init__(self) -> None:
        if self.membership_basis == CURRENT_CONSTITUENTS_SNAPSHOT:
            raise BenchmarkControlError(
                BLOCKED_CURRENT_CONSTITUENTS_FORBIDDEN,
                "current index constituents may not replace historical point-in-time "
                "membership; the eligible set must be resolved as known at each session",
            )
        if self.membership_basis != POINT_IN_TIME_ELIGIBLE_SET:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                f"unregistered membership basis {self.membership_basis!r}",
            )
        if not self.sessions:
            raise BenchmarkControlError(
                BLOCKED_EMPTY_ELIGIBLE_UNIVERSE,
                "a point-in-time eligible universe needs at least one session",
            )
        sessions = tuple(self.sessions)
        for session in sessions:
            _identifier(session, what="session")
        if len(set(sessions)) != len(sessions):
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT, "duplicate session in the eligible universe"
            )
        raw = self.included_by_session
        if not isinstance(raw, Mapping) or set(raw) != set(sessions):
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                "included_by_session must carry exactly the declared sessions",
            )
        normalized: dict[str, tuple[str, ...]] = {}
        for session in sessions:
            included = raw[session]
            for security_id in included:
                _identifier(security_id, what="included security_id")
            if len(set(included)) != len(tuple(included)):
                raise BenchmarkControlError(
                    BLOCKED_MALFORMED_BENCHMARK_INPUT,
                    "a security may appear once in a session's eligible set",
                    session=session,
                )
            normalized[session] = _sorted_ids(tuple(included))
        _identifier(
            self.universe_lineage_sha256_grouped, what="universe_lineage_sha256_grouped"
        )
        object.__setattr__(self, "sessions", sessions)
        object.__setattr__(self, "included_by_session", MappingProxyType(normalized))

    def eligible_on(self, session: str) -> tuple[str, ...]:
        """The point-in-time eligible security ids for one session; unknown fails closed."""

        if session not in self.included_by_session:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                f"{session!r} was not part of this eligible universe",
                session=session,
            )
        return self.included_by_session[session]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "included_by_session": {
                session: list(self.included_by_session[session])
                for session in self.sessions
            },
            "membership_basis": self.membership_basis,
            "sessions": list(self.sessions),
            "universe_lineage_sha256_grouped": self.universe_lineage_sha256_grouped,
        }


def eligible_universe_from_snapshot(
    snapshot: UniverseSnapshot, *, sessions: Sequence[str]
) -> PointInTimeEligibleUniverse:
    """Derive point-in-time membership from a NEE-133 universe snapshot.

    Reads the snapshot's point-in-time ``included_rows`` -- the eligible set as
    known at each session -- and binds the snapshot's own content hash as the
    universe lineage. This is the genuine reuse of the universe engine: the
    eligible set is never re-derived here.
    """

    if type(snapshot) is not UniverseSnapshot:
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT,
            "eligible_universe_from_snapshot requires a NEE-133 UniverseSnapshot",
        )
    requested = tuple(sessions)
    if not requested:
        raise BenchmarkControlError(
            BLOCKED_EMPTY_ELIGIBLE_UNIVERSE,
            "at least one session must be requested from the snapshot",
        )
    included_by_session: dict[str, list[str]] = {session: [] for session in requested}
    for row in snapshot.included_rows():
        session = row.session_id
        if session not in included_by_session:
            continue
        security_id = row.security_id
        if security_id is None:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                "an included universe row is missing its resolved security id",
                session=session,
            )
        included_by_session[session].append(security_id)
    return PointInTimeEligibleUniverse(
        membership_basis=POINT_IN_TIME_ELIGIBLE_SET,
        sessions=requested,
        included_by_session={
            session: tuple(included_by_session[session]) for session in requested
        },
        universe_lineage_sha256_grouped=snapshot.sha256_grouped(),
    )


def equal_weight_targets_for_session(
    *,
    eligible_universe: PointInTimeEligibleUniverse,
    session: str,
    raw_execution_prices: Mapping[str, RawExecutionPrice],
) -> EqualWeightTargetProgram:
    """Build the execution engine's equal-weight target over the SAME eligible set.

    ``selected`` is exactly the point-in-time eligible set for the session, so the
    ``1/N_t`` weighting the execution engine solves is taken across the identical
    universe the strategy screens -- never a broader or current-constituents set.
    """

    selected = eligible_universe.eligible_on(session)
    if not selected:
        raise BenchmarkControlError(
            BLOCKED_EMPTY_ELIGIBLE_UNIVERSE,
            "the point-in-time eligible set for this session is empty; an "
            "equal-weight target over an empty universe is undefined",
            session=session,
        )
    missing = [security_id for security_id in selected if security_id not in raw_execution_prices]
    if missing:
        raise BenchmarkControlError(
            BLOCKED_MISSING_BENCHMARK_OBSERVATION,
            f"no raw execution price for eligible securities {sorted(missing)}; the "
            "equal-weight ledger may not silently drop a name",
            session=session,
        )
    return EqualWeightTargetProgram(
        selected=selected,
        raw_execution_prices={
            security_id: raw_execution_prices[security_id] for security_id in selected
        },
    )


# ---------------------------------------------------------------------------
# The two sibling control types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExternalBenchmark:
    """An independent external benchmark control (SPY / QQQ / equal-weight).

    Sibling of :class:`Ablation`. Carries the serialization method that publishes
    it as an external benchmark record.
    """

    benchmark_class: ClassVar[str] = BENCHMARK_CLASS_EXTERNAL

    control_id: str
    control_kind: str
    trading_frequency: str

    def __post_init__(self) -> None:
        _identifier(self.control_id, what="control_id")
        if self.control_kind not in REGISTERED_CONTROL_KINDS:
            raise BenchmarkControlError(
                BLOCKED_UNREGISTERED_CONTROL_KIND,
                f"unregistered control kind {self.control_kind!r}",
                control_id=self.control_id,
            )
        if self.trading_frequency not in REGISTERED_TRADING_FREQUENCIES:
            raise BenchmarkControlError(
                BLOCKED_UNREGISTERED_TRADING_FREQUENCY,
                f"unregistered trading frequency {self.trading_frequency!r}",
                control_id=self.control_id,
            )

    def external_benchmark_record(self) -> dict[str, Any]:
        """The external-benchmark record this control publishes."""

        return {
            "benchmark_class": self.benchmark_class,
            "control_id": self.control_id,
            "control_kind": self.control_kind,
            "trading_frequency": self.trading_frequency,
        }


@dataclass(frozen=True)
class Ablation:
    """A labeled ablation control (no-filter, per-filter variants).

    Sibling of :class:`ExternalBenchmark`. Deliberately carries NO method that
    serializes it as an external benchmark; :func:`serialize_as_external_benchmark`
    refuses it. A filter variant is therefore structurally unable to masquerade as
    an independent external benchmark.
    """

    benchmark_class: ClassVar[str] = BENCHMARK_CLASS_ABLATION

    ablation_id: str
    ablated_dimension: str
    strategy_config: ConfigFingerprint
    ablation_config: ConfigFingerprint

    def __post_init__(self) -> None:
        _identifier(self.ablation_id, what="ablation_id")
        assert_ablation_changes_only_declared_dimension(
            strategy_config=self.strategy_config,
            ablation_config=self.ablation_config,
            ablated_dimension=self.ablated_dimension,
        )

    def ablation_record(self) -> dict[str, Any]:
        """The ablation record. Note: NOT an external-benchmark record."""

        return {
            "ablated_dimension": self.ablated_dimension,
            "ablation_config_sha256_grouped": self.ablation_config.sha256_grouped(),
            "ablation_id": self.ablation_id,
            "benchmark_class": self.benchmark_class,
            "strategy_config_sha256_grouped": self.strategy_config.sha256_grouped(),
        }


def serialize_as_external_benchmark(control: ExternalBenchmark) -> dict[str, Any]:
    """Serialize an external benchmark; refuse anything that is not one.

    The static type wall admits only :class:`ExternalBenchmark`. The runtime guard
    refuses any other value -- an :class:`Ablation` in particular -- as
    :data:`BLOCKED_ABLATION_NOT_AN_EXTERNAL_BENCHMARK`, so a filter ablation can
    never be published as an independent external benchmark.
    """

    if type(control) is not ExternalBenchmark:
        control_id = getattr(control, "ablation_id", None) or getattr(
            control, "control_id", None
        )
        raise BenchmarkControlError(
            BLOCKED_ABLATION_NOT_AN_EXTERNAL_BENCHMARK,
            "only an ExternalBenchmark may be serialized as an external benchmark; a "
            "labeled ablation is a filter variant of the strategy, not an "
            "independent external control",
            control_id=control_id if isinstance(control_id, str) else None,
        )
    return control.external_benchmark_record()


# ---------------------------------------------------------------------------
# Constructed benchmark ledgers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkLedger:
    """One benchmark control's ledger, produced by the execution engine.

    Construction asserts the wrapped run is a completed ``EXECUTION_OK``
    :class:`~qme.quant.execution_v1.ExecutionRun` built by the shared engine, so a
    ledger that did not pass through :func:`run_execution_program` cannot exist.
    """

    control_id: str
    benchmark_class: str
    control_kind: str
    trading_frequency: str
    run: ExecutionRun
    eligible_sessions: tuple[str, ...]
    availability_cutoff: str
    strategy_basis_sha256_grouped: str
    config_sha256_grouped: str

    def __post_init__(self) -> None:
        _identifier(self.control_id, what="control_id")
        if self.benchmark_class not in (BENCHMARK_CLASS_EXTERNAL, BENCHMARK_CLASS_ABLATION):
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                f"unregistered benchmark class {self.benchmark_class!r}",
                control_id=self.control_id,
            )
        if type(self.run) is not ExecutionRun:
            raise BenchmarkControlError(
                BLOCKED_NON_EXECUTION_LEDGER,
                "a benchmark ledger must wrap a NEE-129 ExecutionRun built by "
                "run_execution_program",
                control_id=self.control_id,
            )
        if self.run.state != EXECUTION_OK:
            raise BenchmarkControlError(
                BLOCKED_NON_EXECUTION_LEDGER,
                f"the wrapped execution run is not {EXECUTION_OK}: {self.run.state}",
                control_id=self.control_id,
            )
        if not self.eligible_sessions:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                "a benchmark ledger needs at least one eligible session",
                control_id=self.control_id,
            )
        object.__setattr__(self, "eligible_sessions", tuple(self.eligible_sessions))

    @property
    def execution_input_sha256_grouped(self) -> str:
        """The execution run's own input digest -- consumed, never recomputed."""

        return self.run.manifest.lineage.input_sha256_grouped

    @property
    def run_sha256_grouped(self) -> str:
        """The benchmark-level run hash, over the config hash and the execution run."""

        return grouped_document_digest(
            {
                "config_sha256_grouped": self.config_sha256_grouped,
                "control_id": self.control_id,
                "execution_input_sha256_grouped": self.execution_input_sha256_grouped,
                "execution_run_sha256_grouped": self.run.self_sha256_grouped,
                "schema_version": SCHEMA_VERSION,
            }
        )

    def nav_by_session(self) -> dict[str, str]:
        """NAV at each session close, from the execution run's own records.

        The values are taken verbatim from ``run.session_close_records`` -- the
        execution ledger's NAV -- and never recomputed here.
        """

        series: dict[str, str] = {}
        for record in self.run.session_close_records:
            session = record.session.session_date.isoformat()
            series[session] = record.nav_after
        return series

    def total_transaction_cost(self) -> str:
        """Total transaction cost across rebalances, summed from the run's ledgers."""

        return _sum_ledger(
            [ledger.transaction_cost for ledger in self.run.rebalance_ledgers]
        )

    def total_transaction_tax(self) -> str:
        return _sum_ledger(
            [ledger.transaction_tax for ledger in self.run.rebalance_ledgers]
        )

    def total_regulatory_fees(self) -> str:
        return _sum_ledger(
            [ledger.regulatory_fees_total for ledger in self.run.rebalance_ledgers]
        )

    def total_dividend_receivable(self) -> str:
        return _sum_ledger(
            [outcome.dividend_receivable for outcome in self.run.action_outcomes]
        )


def _sum_ledger(values: Sequence[str]) -> str:
    """Sum ledger Q8 strings, re-quantized once through the frozen formatter."""

    total = Decimal(0)
    for value in values:
        total += Decimal(value)
    return format_ledger(total, what="ledger_total")


def _run_against_basis(
    *,
    program: ExecutionProgram,
    basis: StrategyLedgerBasis,
    repository_root: Path,
) -> ExecutionRun:
    """Refuse any easier assumption, then CALL the execution engine."""

    if type(program) is not ExecutionProgram:
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT,
            "a control requires a declared ExecutionProgram",
        )
    if type(basis) is not StrategyLedgerBasis:
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT, "a control requires a StrategyLedgerBasis"
        )
    _assert_program_matches_basis(program, basis)
    _assert_program_evidence_within_cutoff(program, basis)
    run = run_execution_program(program, repository_root=repository_root)
    if run.manifest.to_json_dict()["engine_id"] != REQUIRED_EXECUTION_ENGINE_ID:
        raise BenchmarkControlError(
            BLOCKED_NON_EXECUTION_LEDGER,
            "the ledger was not produced by the registered execution engine",
        )
    return run


def _assert_program_matches_control_kind(
    *,
    definition: BenchmarkControlDefinition,
    program: ExecutionProgram,
    eligible_universe: PointInTimeEligibleUniverse | None,
) -> None:
    """Bind the declared control kind to the program the engine actually runs.

    Without this, a control kind is a free-text label a caller can paste onto any
    program: a ``MONTHLY_EQUAL_WEIGHT`` control could carry a single-name declared
    delta, or a ``SPY`` control could trade a different security. A single-security
    buy-and-hold must trade ONLY its reference security through declared deltas and
    screen no universe; a monthly equal-weight control must carry the execution
    engine's equal-weight target whose selection EQUALS the point-in-time eligible
    set for each rebalance's session. A mismatch fails closed with
    :data:`BLOCKED_CONTROL_PROGRAM_MISMATCH`.
    """

    kind = definition.control_kind
    if kind in REFERENCE_SECURITY_CONTROL_KINDS:
        if eligible_universe is not None:
            raise BenchmarkControlError(
                BLOCKED_CONTROL_PROGRAM_MISMATCH,
                f"{kind} names a single reference security and screens no eligible "
                "universe; an eligible universe must not be supplied",
                control_id=definition.control_id,
            )
        reference = definition.reference_security_id
        for stage in program.stages:
            if type(stage) is RebalanceStage:
                target = stage.target
                if type(target) is not DeclaredSignedDeltas:
                    raise BenchmarkControlError(
                        BLOCKED_CONTROL_PROGRAM_MISMATCH,
                        f"{kind} is a single-security buy-and-hold; rebalance "
                        f"{stage.rebalance_id!r} must declare signed deltas in "
                        f"{reference!r} alone, not an equal-weight target",
                        control_id=definition.control_id,
                    )
                foreign = sorted(
                    {
                        delta.security_id
                        for delta in target.deltas
                        if delta.security_id != reference
                    }
                )
                if foreign:
                    raise BenchmarkControlError(
                        BLOCKED_CONTROL_PROGRAM_MISMATCH,
                        f"{kind} names reference security {reference!r}; rebalance "
                        f"{stage.rebalance_id!r} also trades {foreign}",
                        control_id=definition.control_id,
                    )
        return
    if kind == CONTROL_KIND_MONTHLY_EQUAL_WEIGHT:
        if type(eligible_universe) is not PointInTimeEligibleUniverse:
            raise BenchmarkControlError(
                BLOCKED_CONTROL_PROGRAM_MISMATCH,
                f"{kind} targets 1/N over the point-in-time eligible set; the eligible "
                "universe must be supplied so the selection can be bound to it",
                control_id=definition.control_id,
            )
        for stage in program.stages:
            if type(stage) is RebalanceStage:
                target = stage.target
                if type(target) is not EqualWeightTargetProgram:
                    raise BenchmarkControlError(
                        BLOCKED_CONTROL_PROGRAM_MISMATCH,
                        f"{kind} requires the execution engine's equal-weight target; "
                        f"rebalance {stage.rebalance_id!r} declared arbitrary signed "
                        "deltas instead",
                        control_id=definition.control_id,
                    )
                session = stage.fill_session.eligible.session.session_date.isoformat()
                expected = eligible_universe.eligible_on(session)
                if tuple(target.selected) != tuple(expected):
                    raise BenchmarkControlError(
                        BLOCKED_CONTROL_PROGRAM_MISMATCH,
                        f"the equal-weight selection {tuple(target.selected)} for "
                        f"rebalance {stage.rebalance_id!r} does not equal the "
                        f"point-in-time eligible set {tuple(expected)} for session "
                        f"{session}",
                        control_id=definition.control_id,
                        session=session,
                    )
        return


def construct_external_benchmark(
    *,
    definition: BenchmarkControlDefinition,
    basis: StrategyLedgerBasis,
    program: ExecutionProgram,
    trading_frequency: str,
    repository_root: Path,
    eligible_universe: PointInTimeEligibleUniverse | None = None,
) -> BenchmarkLedger:
    """Construct an external benchmark ledger by CALLING the execution engine.

    ``eligible_universe`` is REQUIRED for the monthly equal-weight control kind and
    forbidden for the single-security buy-and-hold kinds: it lets construction bind
    the executed program to the declared control kind (see
    :func:`_assert_program_matches_control_kind`), so the kind cannot be a label
    pasted onto an unrelated program.
    """

    if type(definition) is not BenchmarkControlDefinition:
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT,
            "construct_external_benchmark requires a resolved BenchmarkControlDefinition",
        )
    if type(program) is not ExecutionProgram:
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT,
            "construct_external_benchmark requires a declared ExecutionProgram",
        )
    _assert_program_matches_control_kind(
        definition=definition, program=program, eligible_universe=eligible_universe
    )
    benchmark = ExternalBenchmark(
        control_id=definition.control_id,
        control_kind=definition.control_kind,
        trading_frequency=trading_frequency,
    )
    run = _run_against_basis(program=program, basis=basis, repository_root=repository_root)
    config_sha256_grouped = grouped_document_digest(
        {
            "benchmark_class": BENCHMARK_CLASS_EXTERNAL,
            "definition": definition.to_json_dict(),
            "schema_version": SCHEMA_VERSION,
            "strategy_basis_sha256_grouped": basis.sha256_grouped(),
            "trading_frequency": benchmark.trading_frequency,
        }
    )
    return BenchmarkLedger(
        control_id=definition.control_id,
        benchmark_class=BENCHMARK_CLASS_EXTERNAL,
        control_kind=definition.control_kind,
        trading_frequency=benchmark.trading_frequency,
        run=run,
        eligible_sessions=basis.eligible_sessions,
        availability_cutoff=basis.availability_cutoff,
        strategy_basis_sha256_grouped=basis.sha256_grouped(),
        config_sha256_grouped=config_sha256_grouped,
    )


def construct_ablation(
    *,
    ablation: Ablation,
    basis: StrategyLedgerBasis,
    program: ExecutionProgram,
    trading_frequency: str,
    repository_root: Path,
) -> BenchmarkLedger:
    """Construct an ablation ledger by CALLING the execution engine.

    The declared-dimension diff has already run in :class:`Ablation`'s
    constructor, so an ablation that touched an undeclared dimension cannot reach
    here. The ledger runs through the identical execution path as the strategy.
    """

    if type(ablation) is not Ablation:
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT, "construct_ablation requires an Ablation"
        )
    if type(basis) is not StrategyLedgerBasis:
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT, "construct_ablation requires a StrategyLedgerBasis"
        )
    if trading_frequency not in REGISTERED_TRADING_FREQUENCIES:
        raise BenchmarkControlError(
            BLOCKED_UNREGISTERED_TRADING_FREQUENCY,
            f"unregistered trading frequency {trading_frequency!r}",
            control_id=ablation.ablation_id,
        )
    # The "changes only its declared dimension" diff is relative to the ablation's
    # OWN declared strategy_config. Bind that baseline to the basis the run uses, so
    # the diff is anchored to the actual strategy configuration, not a free-floating
    # baseline the caller invented alongside the run.
    if ablation.strategy_config.sha256_grouped() != basis.strategy_config.sha256_grouped():
        raise BenchmarkControlError(
            BLOCKED_CONTROL_PROGRAM_MISMATCH,
            "an ablation must be defined against the strategy configuration the basis "
            "pins; the ablation's declared strategy_config does not match the basis "
            "strategy_config",
            control_id=ablation.ablation_id,
        )
    run = _run_against_basis(program=program, basis=basis, repository_root=repository_root)
    config_sha256_grouped = grouped_document_digest(
        {
            "ablated_dimension": ablation.ablated_dimension,
            "ablation_config_sha256_grouped": ablation.ablation_config.sha256_grouped(),
            "benchmark_class": BENCHMARK_CLASS_ABLATION,
            "control_id": ablation.ablation_id,
            "schema_version": SCHEMA_VERSION,
            "strategy_basis_sha256_grouped": basis.sha256_grouped(),
            "trading_frequency": trading_frequency,
        }
    )
    return BenchmarkLedger(
        control_id=ablation.ablation_id,
        benchmark_class=BENCHMARK_CLASS_ABLATION,
        control_kind=f"ABLATION:{ablation.ablated_dimension}",
        trading_frequency=trading_frequency,
        run=run,
        eligible_sessions=basis.eligible_sessions,
        availability_cutoff=basis.availability_cutoff,
        strategy_basis_sha256_grouped=basis.sha256_grouped(),
        config_sha256_grouped=config_sha256_grouped,
    )


# ---------------------------------------------------------------------------
# Alignment (only AFTER each independent ledger is constructed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyReturnSeries:
    """The strategy's own aligned axis: eligible dates, cutoff, and NAV per date."""

    strategy_id: str
    eligible_sessions: tuple[str, ...]
    availability_cutoff: str
    nav_by_session: Mapping[str, str]

    def __post_init__(self) -> None:
        _identifier(self.strategy_id, what="strategy_id")
        _identifier(self.availability_cutoff, what="availability_cutoff")
        if not self.eligible_sessions:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                "the strategy series needs at least one eligible session",
            )
        sessions = tuple(self.eligible_sessions)
        for session in sessions:
            _identifier(session, what="eligible session")
            if session not in self.nav_by_session:
                raise BenchmarkControlError(
                    BLOCKED_MISSING_BENCHMARK_OBSERVATION,
                    "the strategy series is missing a NAV for one of its own eligible "
                    "sessions; a series may not be silently shortened",
                    session=session,
                )
        object.__setattr__(self, "eligible_sessions", sessions)
        object.__setattr__(
            self, "nav_by_session", MappingProxyType(dict(self.nav_by_session))
        )


@dataclass(frozen=True)
class AlignedComparison:
    """Aligned NAV series over the strategy's eligible-date axis.

    Carries no performance metric: the economic comparison is out of scope. It is
    a side-by-side of independently constructed NAV paths on one date axis.
    """

    strategy_id: str
    axis_sessions: tuple[str, ...]
    availability_cutoff: str
    strategy_series: tuple[str, ...]
    series_by_control: Mapping[str, tuple[str, ...]]
    lineage_sha256_grouped: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "availability_cutoff": self.availability_cutoff,
            "axis_sessions": list(self.axis_sessions),
            "claims": dict(NON_CLAIMS),
            "lineage_sha256_grouped": self.lineage_sha256_grouped,
            "series_by_control": {
                control_id: list(self.series_by_control[control_id])
                for control_id in sorted(self.series_by_control)
            },
            "strategy_id": self.strategy_id,
            "strategy_series": list(self.strategy_series),
        }


def align_benchmark_returns(
    *,
    strategy: StrategyReturnSeries,
    benchmark_ledgers: Sequence[BenchmarkLedger],
) -> AlignedComparison:
    """Align constructed benchmark ledgers to the strategy's eligible-date axis.

    Every input is a fully constructed :class:`BenchmarkLedger` (each wraps a
    completed ``EXECUTION_OK`` run), so alignment is provably downstream of ledger
    construction. Every ledger must use IDENTICAL eligible dates and availability
    cutoffs as the strategy (equality, not similarity), and every axis session
    must carry a NAV in every series, so a hole cannot silently shorten one series.
    """

    if type(strategy) is not StrategyReturnSeries:
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT, "a StrategyReturnSeries is required"
        )
    ledgers = tuple(benchmark_ledgers)
    if not ledgers:
        raise BenchmarkControlError(
            BLOCKED_ALIGNMENT_BEFORE_LEDGER_CONSTRUCTED,
            "no constructed benchmark ledger was supplied to align",
        )
    axis = strategy.eligible_sessions
    strategy_series = tuple(strategy.nav_by_session[session] for session in axis)
    series_by_control: dict[str, tuple[str, ...]] = {}
    for ledger in ledgers:
        if type(ledger) is not BenchmarkLedger:
            raise BenchmarkControlError(
                BLOCKED_ALIGNMENT_BEFORE_LEDGER_CONSTRUCTED,
                "alignment admits only constructed BenchmarkLedger values",
            )
        if ledger.run.state != EXECUTION_OK:  # pragma: no cover - guarded at construction
            raise BenchmarkControlError(
                BLOCKED_ALIGNMENT_BEFORE_LEDGER_CONSTRUCTED,
                "a benchmark ledger with an incomplete run cannot be aligned",
                control_id=ledger.control_id,
            )
        if tuple(ledger.eligible_sessions) != tuple(axis):
            raise BenchmarkControlError(
                BLOCKED_ELIGIBLE_DATES_MISMATCH,
                "a benchmark and the strategy must use IDENTICAL eligible dates",
                control_id=ledger.control_id,
            )
        if ledger.availability_cutoff != strategy.availability_cutoff:
            raise BenchmarkControlError(
                BLOCKED_AVAILABILITY_CUTOFF_MISMATCH,
                "a benchmark and the strategy must use IDENTICAL availability cutoffs",
                control_id=ledger.control_id,
            )
        nav = ledger.nav_by_session()
        aligned: list[str] = []
        for session in axis:
            if session not in nav:
                raise BenchmarkControlError(
                    BLOCKED_MISSING_BENCHMARK_OBSERVATION,
                    "a benchmark series is missing a NAV for an aligned session; the "
                    "series may not be silently shortened while the others run on",
                    control_id=ledger.control_id,
                    session=session,
                )
            aligned.append(nav[session])
        if ledger.control_id in series_by_control:
            raise BenchmarkControlError(
                BLOCKED_MALFORMED_BENCHMARK_INPUT,
                "two benchmark ledgers share one control id",
                control_id=ledger.control_id,
            )
        series_by_control[ledger.control_id] = tuple(aligned)
    lineage = grouped_document_digest(
        {
            "availability_cutoff": strategy.availability_cutoff,
            "axis_sessions": list(axis),
            "control_run_hashes": {
                ledger.control_id: ledger.run_sha256_grouped
                for ledger in sorted(ledgers, key=lambda item: item.control_id)
            },
            "schema_version": SCHEMA_VERSION,
            "strategy_id": strategy.strategy_id,
        }
    )
    return AlignedComparison(
        strategy_id=strategy.strategy_id,
        axis_sessions=tuple(axis),
        availability_cutoff=strategy.availability_cutoff,
        strategy_series=strategy_series,
        series_by_control=MappingProxyType(series_by_control),
        lineage_sha256_grouped=lineage,
    )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def benchmark_manifest(ledger: BenchmarkLedger) -> dict[str, Any]:
    """The per-control manifest: method, frequency, costs, taxes, lineage, limits."""

    if type(ledger) is not BenchmarkLedger:
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT, "benchmark_manifest requires a BenchmarkLedger"
        )
    run = ledger.run
    return {
        "benchmark_class": ledger.benchmark_class,
        "claims": dict(NON_CLAIMS),
        "construction_method": {
            "basis": CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER,
            "built_by_execution_engine_id": REQUIRED_EXECUTION_ENGINE_ID,
            "control_kind": ledger.control_kind,
        },
        "control_id": ledger.control_id,
        "costs": {
            "regulatory_fees_total": ledger.total_regulatory_fees(),
            "transaction_cost": ledger.total_transaction_cost(),
        },
        "coverage_limitation": COVERAGE_LIMITATION,
        "engine_id": ENGINE_ID,
        "lineage": {
            "availability_cutoff": ledger.availability_cutoff,
            "config_sha256_grouped": ledger.config_sha256_grouped,
            "eligible_sessions": list(ledger.eligible_sessions),
            "execution_code_sha256_grouped": run.manifest.lineage.code_sha256_grouped,
            "execution_config_sha256_grouped": run.manifest.lineage.config_sha256_grouped,
            "execution_input_sha256_grouped": ledger.execution_input_sha256_grouped,
            "execution_schema_sha256_grouped": run.manifest.lineage.schema_sha256_grouped,
            "run_sha256_grouped": ledger.run_sha256_grouped,
            "strategy_basis_sha256_grouped": ledger.strategy_basis_sha256_grouped,
        },
        "limitations": list(LIMITATIONS),
        "schema_version": SCHEMA_VERSION,
        "taxes": {
            "dividend_receivable_recognized": ledger.total_dividend_receivable(),
            "transaction_tax": ledger.total_transaction_tax(),
        },
        "trading_frequency": ledger.trading_frequency,
    }


def comparison_report(
    *,
    aligned: AlignedComparison,
    benchmark_ledgers: Sequence[BenchmarkLedger],
) -> dict[str, Any]:
    """The comparison report: one manifest per control plus the aligned axis.

    Contains construction method, trading frequency, costs, taxes, lineage, and
    limitations for every control; carries no performance or superiority claim.
    """

    if type(aligned) is not AlignedComparison:
        raise BenchmarkControlError(
            BLOCKED_MALFORMED_BENCHMARK_INPUT, "comparison_report requires an AlignedComparison"
        )
    manifests = [
        benchmark_manifest(ledger)
        for ledger in sorted(benchmark_ledgers, key=lambda item: item.control_id)
    ]
    return {
        "aligned_comparison": aligned.to_json_dict(),
        "claims": dict(NON_CLAIMS),
        "control_manifests": manifests,
        "coverage_limitation": COVERAGE_LIMITATION,
        "engine_id": ENGINE_ID,
        "limitations": list(LIMITATIONS),
        "schema_version": SCHEMA_VERSION,
    }


assert_fail_closed_states_complete()


__all__ = [
    "ADJUSTED_CLOSE_TOTAL_RETURN_SHORTCUT",
    "BENCHMARK_CLASS_ABLATION",
    "BENCHMARK_CLASS_EXTERNAL",
    "BENCHMARK_FAIL_CLOSED_STATES",
    "BENCHMARK_OK",
    "BLOCKED_ABLATION_NOT_AN_EXTERNAL_BENCHMARK",
    "BLOCKED_ABLATION_TOUCHED_UNDECLARED_DIMENSION",
    "BLOCKED_ADJUSTED_CLOSE_SHORTCUT_FORBIDDEN",
    "BLOCKED_ALIGNMENT_BEFORE_LEDGER_CONSTRUCTED",
    "BLOCKED_AMBIGUOUS_BENCHMARK_CONTROL",
    "BLOCKED_AVAILABILITY_CUTOFF_MISMATCH",
    "BLOCKED_CALENDAR_MISMATCH",
    "BLOCKED_CONTROL_PROGRAM_MISMATCH",
    "BLOCKED_COST_TAX_CONFIG_MISMATCH",
    "BLOCKED_CURRENT_CONSTITUENTS_FORBIDDEN",
    "BLOCKED_DATE_RANGE_MISMATCH",
    "BLOCKED_ELIGIBLE_DATES_MISMATCH",
    "BLOCKED_EMPTY_ELIGIBLE_UNIVERSE",
    "BLOCKED_EXECUTION_CONFIG_MISMATCH",
    "BLOCKED_INITIAL_CAPITAL_MISMATCH",
    "BLOCKED_MALFORMED_BENCHMARK_INPUT",
    "BLOCKED_MISSING_BENCHMARK_OBSERVATION",
    "BLOCKED_NON_EXECUTION_LEDGER",
    "BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL",
    "BLOCKED_UNDECLARED_ABLATION_DIMENSION",
    "BLOCKED_UNREGISTERED_BENCHMARK_CONTROL",
    "BLOCKED_UNREGISTERED_CONTROL_KIND",
    "BLOCKED_UNREGISTERED_REINVESTMENT_POLICY",
    "BLOCKED_UNREGISTERED_SOURCE_KIND",
    "BLOCKED_UNREGISTERED_TRADING_FREQUENCY",
    "CONSTRUCTION_BASES",
    "CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER",
    "CONTROL_KIND_MONTHLY_EQUAL_WEIGHT",
    "CONTROL_KIND_QQQ_BUY_AND_HOLD",
    "CONTROL_KIND_SPY_BUY_AND_HOLD",
    "COVERAGE_LIMITATION",
    "CURRENT_CONSTITUENTS_SNAPSHOT",
    "ENGINE_ID",
    "LIMITATIONS",
    "MEMBERSHIP_BASES",
    "NON_CLAIMS",
    "POINT_IN_TIME_ELIGIBLE_SET",
    "REGISTERED_ABLATION_DIMENSIONS",
    "REGISTERED_BENCHMARK_CONTROLS",
    "REGISTERED_CONTROL_KINDS",
    "REGISTERED_REINVESTMENT_POLICIES",
    "REGISTERED_SOURCE_KINDS",
    "REGISTERED_TRADING_FREQUENCIES",
    "REINVESTMENT_HELD_AS_CASH",
    "REINVESTMENT_REINVESTED_NEXT_SESSION_RAW_OPEN",
    "REQUIRED_EXECUTION_ENGINE_ID",
    "SCHEMA_VERSION",
    "SOURCE_KINDS",
    "SOURCE_KIND_OWNER_DECISION_RECORD",
    "SOURCE_KIND_PUBLISHER_REFERENCE",
    "SOURCE_KIND_TEST_CONSTRUCTED",
    "TRADING_FREQUENCY_BUY_AND_HOLD",
    "TRADING_FREQUENCY_MONTHLY",
    "TRADING_FREQUENCY_PER_STRATEGY_REBALANCE",
    "Ablation",
    "AlignedComparison",
    "BenchmarkControlDefinition",
    "BenchmarkControlError",
    "BenchmarkLedger",
    "ConfigFingerprint",
    "ExternalBenchmark",
    "PointInTimeEligibleUniverse",
    "StrategyLedgerBasis",
    "StrategyReturnSeries",
    "align_benchmark_returns",
    "assert_ablation_changes_only_declared_dimension",
    "assert_fail_closed_states_complete",
    "benchmark_manifest",
    "comparison_report",
    "construct_ablation",
    "construct_external_benchmark",
    "eligible_universe_from_snapshot",
    "equal_weight_targets_for_session",
    "group_sha256",
    "grouped_document_digest",
    "resolve_benchmark_control",
    "serialize_as_external_benchmark",
    "validate_benchmark_control_registry",
]
