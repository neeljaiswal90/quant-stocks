"""NEE-132 cost, turnover, liquidity, participation, and capacity scenario engine.

This engine turns one **already-published execution ledger** into transparent
cost, turnover, liquidity, participation, and capacity *scenarios*. Two rules
shape every line of it:

* **No double-counting.** The gross traded notional, the pre-trade NAV, the
  signed deltas, and the raw execution prices are *consumed* from the execution
  ledger, never recomputed. Recomputing them would silently diverge from the
  frozen accounting quantum and is treated as a defect. The exact attribute
  paths consumed are listed in :data:`CONSUMED_LEDGER_ATTRIBUTE_PATHS`.
* **No uncalibrated assumption is ever a fact.** A spread or impact coefficient
  that the owner has not registered does not become a number: the component is
  returned as an :class:`UncalibratedScenario`, a type that carries *no* amount
  field and therefore cannot be summed, rendered, or presented as an estimate.
  The registries that would calibrate it ship EMPTY, so with the shipped state
  the spread, impact, and commission components are structurally uncalibrated.

What is a *scenario* and what is *consumed evidence*
----------------------------------------------------

* The **per-side bps cost tiers** ``5/10/25`` are transparent, explicitly
  labelled assumptions: for a tier ``b`` the tier cost is ``TC = b/10000 * GTN``
  where ``GTN`` is consumed from the ledger. A tier is a *labelled scenario*, not
  a coefficient that requires evidence, so the tiers ship as a frozen constant.
* The **regulatory-fee component** is the only calibrated component available in
  the shipped state. It is not reimplemented here: it is consumed from the
  ledger's ``regulatory_fees_total`` / ``regulatory_fee_lines``, which the
  execution engine produced through the registered kernel
  ``qme.quant.asymmetric_costs_v3.rebalance_with_historical_regulatory_fees_v3``
  (method :data:`REGULATORY_FEE_METHOD_ID`, schedule
  :data:`REGULATORY_FEE_SCHEDULE_ARTIFACT_ID`). Reusing the ledger's fee lines is
  what makes the reuse structural rather than a second fee implementation.
* **ADV**, **participation**, and **AUM capacity** are point-in-time scenarios
  built from a registered lookback ``L`` and a registered participation ceiling
  ``p_star``. Both registries ship EMPTY, so the engine fails closed with a typed
  ``BLOCKED_NO_REGISTERED_*`` state before it reads a single ledger row.

Numeric policy
--------------

No binary float appears anywhere in this module. Every value crosses the
boundary as a canonical base-10 string, is lifted to an exact
:class:`fractions.Fraction` through the frozen NEE-118 grammar
(:func:`qme.quant.execution_v1.to_exact`), and is carried as an exact rational.
Currency artifacts are rendered at the frozen ``1e-8`` ledger quantum with
``ROUND_HALF_EVEN`` (:func:`qme.quant.execution_v1.format_ledger`); ratios and
capacities additionally carry their exact ``numerator/denominator`` rational, the
authoritative form, beside the ``1e-8`` artifact.

Ordering and identity
---------------------

Output ordering is content-derived (securities by UTF-8 bytes ascending;
rebalances in ledger order), so input container order cannot leak into identity;
a shuffle of the liquidity evidence is sorted back before use. Every row and the
manifest carry the full grouped-SHA-256 lineage (input-data, cost-policy, config,
code, schema), and the replayable manifest additionally binds the output-content
hash.

This module makes no production, prospective-consumption, empirical-performance,
alpha, capacity-value, production-readiness, or live-order claim; see
:data:`NON_CLAIMS`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from fractions import Fraction
from typing import Any, ClassVar, Final

from qme.foundation.lineage import canonical_json_bytes
from qme.quant.asymmetric_costs_v3 import (
    DELEGATED_SCHEDULE_ARTIFACT_ID as REGULATORY_FEE_SCHEDULE_ARTIFACT_ID,
)
from qme.quant.asymmetric_costs_v3 import (
    IMPLEMENTATION_ID as REGULATORY_FEE_IMPLEMENTATION_ID,
)
from qme.quant.asymmetric_costs_v3 import (
    METHOD_ID as REGULATORY_FEE_METHOD_ID,
)
from qme.quant.equations import EQUATION_SPEC_ID
from qme.quant.execution_v1 import (
    ExecutedFill,
    ExecutionRun,
    RebalanceLedger,
    format_ledger,
    group_sha256,
    grouped_document_digest,
    to_exact,
)

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

KERNEL_ID: Final = "QME-NEE132-COST-TURNOVER-LIQUIDITY-PARTICIPATION-CAPACITY-SCENARIO-ENGINE-V1"
METHOD_ID: Final = "QME-NEE132-TRANSPARENT-COST-TURNOVER-CAPACITY-SCENARIO-V1"
SCHEMA_VERSION: Final = "qme.cost_turnover_capacity_scenarios.v1"

# The regulatory-fee kernel whose ledger output this engine consumes for the
# regulatory-fee component is imported above (REGULATORY_FEE_METHOD_ID,
# REGULATORY_FEE_IMPLEMENTATION_ID, REGULATORY_FEE_SCHEDULE_ARTIFACT_ID); it is
# cited in lineage and never re-run here.

#: The exact execution-ledger attribute paths this engine consumes rather than
#: recomputing. Recomputing any of them is the defect the ticket names.
CONSUMED_LEDGER_ATTRIBUTE_PATHS: Final[tuple[str, ...]] = (
    "run.program_id",
    "run.state",
    "run.manifest.self_sha256_grouped",
    "run.rebalance_ledgers[k].rebalance_id",
    "run.rebalance_ledgers[k].step",
    "run.rebalance_ledgers[k].fill_timing.signal_session.session_date",
    "run.rebalance_ledgers[k].nav_minus",
    "run.rebalance_ledgers[k].gross_trade_notional",
    "run.rebalance_ledgers[k].gtn_ratio",
    "run.rebalance_ledgers[k].one_way_turnover",
    "run.rebalance_ledgers[k].regulatory_fees_total",
    "run.rebalance_ledgers[k].regulatory_fee_lines[j].total_raw",
    "run.rebalance_ledgers[k].regulatory_fee_lines[j].side",
    "run.rebalance_ledgers[k].regulatory_fee_lines[j].symbol",
    "run.rebalance_ledgers[k].fill_states[i].security_id",
    "run.rebalance_ledgers[k].fill_states[i].side",
    "run.rebalance_ledgers[k].fill_states[i].delta_raw_shares",
    "run.rebalance_ledgers[k].fill_states[i].raw_execution_price",
    "run.rebalance_ledgers[k].fill_states[i].gross_notional",
)

#: All-false non-claims, bound into the manifest and every scenario report.
NON_CLAIMS: Final[Mapping[str, bool]] = {
    "alpha_demonstrated": False,
    "capacity_value_measured": False,
    "empirical_performance_measured": False,
    "freeze_blocker_changed": False,
    "impact_coefficient_calibrated": False,
    "independent_review_recorded": False,
    "live_order_authority": False,
    "production_deployment_authorized": False,
    "production_ready": False,
    "prospective_observations_consumable": False,
    "spread_coefficient_calibrated": False,
    "uncalibrated_coefficient_presented_as_estimate": False,
}

# ---------------------------------------------------------------------------
# Coordinate / naming discipline
# ---------------------------------------------------------------------------

#: The raw ADV coordinate: the only coordinate ADV consumes.
RAW_ADV_COORDINATE: Final = "raw_price_session_bar"
#: The sibling adjusted coordinate ADV structurally refuses.
ADJUSTED_DOLLAR_VOLUME_COORDINATE: Final = "adjusted_dollar_volume"

#: Value-field names per coordinate, pairwise disjoint so a raw ADV computation
#: cannot read an adjusted value even by name.
ADV_COORDINATE_VALUE_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    RAW_ADV_COORDINATE: ("raw_close", "raw_volume"),
    ADJUSTED_DOLLAR_VOLUME_COORDINATE: ("adjusted_dollar_volume",),
}

#: Field names shared across coordinates (the join keys).
ADV_COORDINATE_KEY_FIELDS: Final[tuple[str, ...]] = ("security_id", "session_id")

#: Generic market-data names no coordinate may publish. Same content as
#: ``qme.data.stores.prices_v1.FORBIDDEN_GENERIC_FIELD_NAMES``; kept local so the
#: engine imports no data-layer module.
FORBIDDEN_GENERIC_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "adj_close",
        "adjclose",
        "adjusted_close",
        "adjusted_volume",
        "close",
        "dollar_volume",
        "factor",
        "high",
        "index",
        "low",
        "open",
        "price",
        "rate",
        "return",
        "value",
        "volume",
    }
)

#: The ADV definition, written verbatim into the code binding and the manifest.
ADV_DEFINITION: Final = "ADV_(i,t,L) = mean(P_raw_i,u * V_raw_i,u) over the registered L completed prior sessions"  # noqa: E501
ADV_BASIS: Final = "RAW_CLOSE_TIMES_RAW_VOLUME"

# ---------------------------------------------------------------------------
# Formulae (ticket-verbatim; bound into identity, never re-derived per row)
# ---------------------------------------------------------------------------

FORMULA_GTN: Final = "GTN = sum(|dq_i| * P_i)"
FORMULA_GTN_RATIO: Final = "GTN_ratio = GTN / NAV_minus"
FORMULA_ONE_WAY_TURNOVER: Final = "one_way_turnover = GTN / (2 * NAV_minus)"
FORMULA_TIER_COST: Final = "TC_bps = (b / 10000) * GTN"
FORMULA_PARTICIPATION: Final = "participation_i = |dq_i| * P_i / ADV_i"
FORMULA_TARGET_WEIGHT_CHANGE: Final = "|dw_i| = |dq_i| * P_i / NAV_minus"
FORMULA_AUM_CAPACITY: Final = "AUM_capacity_i = p_star * ADV_i / |dw_i|"
FORMULA_PORTFOLIO_CAPACITY: Final = "AUM_capacity_portfolio = min_i(AUM_capacity_i)"

#: The required per-side cost tiers, in basis points. Explicitly labelled
#: scenarios, not empirical coefficients, so they ship as a constant.
REQUIRED_COST_TIERS_BPS: Final[tuple[int, ...]] = (5, 10, 25)
_BPS_DENOMINATOR: Final = 10000

# ---------------------------------------------------------------------------
# Cost components (separately named, disjoint; no component appears twice)
# ---------------------------------------------------------------------------

COMPONENT_COMMISSION: Final = "COMMISSION"
COMPONENT_REGULATORY_FEE: Final = "REGULATORY_FEE"
COMPONENT_SPREAD: Final = "SPREAD"
COMPONENT_IMPACT: Final = "IMPACT"

#: The frozen, disjoint component registry. Order is the canonical presentation
#: order; membership is a set, so a component can never be named twice.
COST_COMPONENTS: Final[tuple[str, ...]] = (
    COMPONENT_COMMISSION,
    COMPONENT_REGULATORY_FEE,
    COMPONENT_SPREAD,
    COMPONENT_IMPACT,
)

#: Which coefficient registry calibrates each component. The regulatory-fee
#: component is calibrated from the ledger, not from a coefficient registry.
COMPONENT_COEFFICIENT_KIND: Final[Mapping[str, str]] = {
    COMPONENT_COMMISSION: "COMMISSION_SCHEDULE",
    COMPONENT_REGULATORY_FEE: "LEDGER_REGULATORY_FEE_KERNEL",
    COMPONENT_SPREAD: "SPREAD_MODEL",
    COMPONENT_IMPACT: "IMPACT_MODEL",
}

# ---------------------------------------------------------------------------
# Typed states
# ---------------------------------------------------------------------------

SCENARIO_OK: Final = "SCENARIO_OK"

PARTICIPATION_MEASURED_SCENARIO: Final = "PARTICIPATION_MEASURED_SCENARIO"
PARTICIPATION_UNAVAILABLE_MISSING_ADV: Final = "PARTICIPATION_UNAVAILABLE_MISSING_ADV"
#: A traded name whose ADV window is present but non-positive (e.g. a fully
#: halted name with zero raw volume in every session of ``L``). ADV *is* measured
#: (and surfaced) at zero, but participation ``|dq|*P / ADV`` is undefined, so no
#: number is emitted. This is a DISTINCT condition from missing evidence, and is
#: never conflated with ``*_MISSING_ADV`` above.
PARTICIPATION_UNAVAILABLE_NON_POSITIVE_ADV: Final = "PARTICIPATION_UNAVAILABLE_NON_POSITIVE_ADV"

CAPACITY_MEASURED_SCENARIO: Final = "CAPACITY_MEASURED_SCENARIO"
CAPACITY_UNAVAILABLE_MISSING_ADV: Final = "CAPACITY_UNAVAILABLE_MISSING_ADV"
#: The capacity counterpart of the non-positive-ADV participation state: a
#: zero-liquidity window makes the per-name capacity scenario not claimable, so
#: it is declined rather than asserted (the conservative fail-closed choice).
CAPACITY_UNAVAILABLE_NON_POSITIVE_ADV: Final = "CAPACITY_UNAVAILABLE_NON_POSITIVE_ADV"

PORTFOLIO_CAPACITY_SCENARIO_COMPLETE: Final = "PORTFOLIO_CAPACITY_SCENARIO_COMPLETE"
PORTFOLIO_CAPACITY_INCOMPLETE_MISSING_ADV: Final = "PORTFOLIO_CAPACITY_INCOMPLETE_MISSING_ADV"
#: A non-positive ADV window on any traded name prevents a complete portfolio
#: capacity scenario (that name's participation is undefined), so the minimum is
#: not claimed. Distinct from the missing-evidence incompleteness above.
PORTFOLIO_CAPACITY_INCOMPLETE_NON_POSITIVE_ADV: Final = "PORTFOLIO_CAPACITY_INCOMPLETE_NON_POSITIVE_ADV"
PORTFOLIO_CAPACITY_ZERO_TRADE: Final = "PORTFOLIO_CAPACITY_ZERO_TRADE"

UNCALIBRATED_SCENARIO_STATE: Final = "UNCALIBRATED_SCENARIO"
CALIBRATED_COMPONENT_STATE: Final = "CALIBRATED_COMPONENT"

BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK: Final = "BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK"
BLOCKED_NO_REGISTERED_PARTICIPATION_SCENARIO: Final = "BLOCKED_NO_REGISTERED_PARTICIPATION_SCENARIO"
BLOCKED_NO_REGISTERED_COMMISSION_SCHEDULE: Final = "BLOCKED_NO_REGISTERED_COMMISSION_SCHEDULE"
BLOCKED_NO_REGISTERED_SPREAD_MODEL: Final = "BLOCKED_NO_REGISTERED_SPREAD_MODEL"
BLOCKED_NO_REGISTERED_IMPACT_MODEL: Final = "BLOCKED_NO_REGISTERED_IMPACT_MODEL"
BLOCKED_UNRESOLVED_LIQUIDITY_LOOKBACK: Final = "BLOCKED_UNRESOLVED_LIQUIDITY_LOOKBACK"
BLOCKED_UNRESOLVED_PARTICIPATION_SCENARIO: Final = "BLOCKED_UNRESOLVED_PARTICIPATION_SCENARIO"
BLOCKED_UNRESOLVED_COMMISSION_SCHEDULE: Final = "BLOCKED_UNRESOLVED_COMMISSION_SCHEDULE"
BLOCKED_UNRESOLVED_SPREAD_MODEL: Final = "BLOCKED_UNRESOLVED_SPREAD_MODEL"
BLOCKED_UNRESOLVED_IMPACT_MODEL: Final = "BLOCKED_UNRESOLVED_IMPACT_MODEL"
BLOCKED_AMBIGUOUS_REGISTRY_RECORD: Final = "BLOCKED_AMBIGUOUS_REGISTRY_RECORD"
BLOCKED_UNREGISTERED_SOURCE_KIND: Final = "BLOCKED_UNREGISTERED_SOURCE_KIND"
BLOCKED_MALFORMED_SCENARIO_VALUE: Final = "BLOCKED_MALFORMED_SCENARIO_VALUE"
BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV: Final = "BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV"
BLOCKED_INSUFFICIENT_ADV_HISTORY: Final = "BLOCKED_INSUFFICIENT_ADV_HISTORY"
BLOCKED_NON_PRIOR_ADV_SESSION: Final = "BLOCKED_NON_PRIOR_ADV_SESSION"
BLOCKED_DUPLICATE_ADV_SESSION: Final = "BLOCKED_DUPLICATE_ADV_SESSION"
BLOCKED_DUPLICATE_COST_COMPONENT: Final = "BLOCKED_DUPLICATE_COST_COMPONENT"
BLOCKED_UNREGISTERED_COST_COMPONENT: Final = "BLOCKED_UNREGISTERED_COST_COMPONENT"
BLOCKED_UNKNOWN_LIQUIDITY_EVIDENCE_TARGET: Final = "BLOCKED_UNKNOWN_LIQUIDITY_EVIDENCE_TARGET"
BLOCKED_MALFORMED_EXECUTION_RUN: Final = "BLOCKED_MALFORMED_EXECUTION_RUN"

#: Every fail-closed state this module raises, sorted. Callers may bind it.
SCENARIO_FAIL_CLOSED_STATES: Final[tuple[str, ...]] = (
    BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV,
    BLOCKED_AMBIGUOUS_REGISTRY_RECORD,
    BLOCKED_DUPLICATE_ADV_SESSION,
    BLOCKED_DUPLICATE_COST_COMPONENT,
    BLOCKED_INSUFFICIENT_ADV_HISTORY,
    BLOCKED_MALFORMED_EXECUTION_RUN,
    BLOCKED_MALFORMED_SCENARIO_VALUE,
    BLOCKED_NON_PRIOR_ADV_SESSION,
    BLOCKED_NO_REGISTERED_COMMISSION_SCHEDULE,
    BLOCKED_NO_REGISTERED_IMPACT_MODEL,
    BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK,
    BLOCKED_NO_REGISTERED_PARTICIPATION_SCENARIO,
    BLOCKED_NO_REGISTERED_SPREAD_MODEL,
    BLOCKED_UNKNOWN_LIQUIDITY_EVIDENCE_TARGET,
    BLOCKED_UNREGISTERED_COST_COMPONENT,
    BLOCKED_UNREGISTERED_SOURCE_KIND,
    BLOCKED_UNRESOLVED_COMMISSION_SCHEDULE,
    BLOCKED_UNRESOLVED_IMPACT_MODEL,
    BLOCKED_UNRESOLVED_LIQUIDITY_LOOKBACK,
    BLOCKED_UNRESOLVED_PARTICIPATION_SCENARIO,
    BLOCKED_UNRESOLVED_SPREAD_MODEL,
)


class ScenarioError(ValueError):
    """A scenario-engine refusal carrying a typed fail-closed state."""

    def __init__(
        self,
        state: str,
        message: str,
        *,
        rebalance_id: str | None = None,
        security_id: str | None = None,
        session_id: str | None = None,
        component: str | None = None,
    ) -> None:
        super().__init__(f"{state}: {message}")
        self.state = state
        self.message = message
        self.rebalance_id = rebalance_id
        self.security_id = security_id
        self.session_id = session_id
        self.component = component

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "message": self.message,
            "rebalance_id": self.rebalance_id,
            "security_id": self.security_id,
            "session_id": self.session_id,
            "component": self.component,
        }


def assert_states_are_complete() -> None:
    """Prove the published fail-closed tuple is sorted, unique, and BLOCKED-only."""

    if list(SCENARIO_FAIL_CLOSED_STATES) != sorted(set(SCENARIO_FAIL_CLOSED_STATES)):
        raise ScenarioError(
            BLOCKED_MALFORMED_SCENARIO_VALUE,
            "the fail-closed state tuple must be sorted and unique",
        )
    for state in SCENARIO_FAIL_CLOSED_STATES:
        if not state.startswith("BLOCKED_"):
            raise ScenarioError(
                BLOCKED_MALFORMED_SCENARIO_VALUE,
                f"fail-closed state {state!r} must be a BLOCKED_ state",
            )


def assert_adv_coordinates_non_joinable(
    coordinates: Mapping[str, Sequence[str]] = ADV_COORDINATE_VALUE_FIELDS,
    *,
    key_fields: Sequence[str] = ADV_COORDINATE_KEY_FIELDS,
    forbidden: frozenset[str] = FORBIDDEN_GENERIC_FIELD_NAMES,
) -> None:
    """Prove the raw and adjusted ADV coordinates cannot be joined or confused.

    Value-field names are pairwise disjoint, no value field shadows a join key,
    and no value field is a generic market-data name. So a raw ADV computation
    cannot read an adjusted dollar-volume value even by field name.
    """

    seen: dict[str, str] = {}
    for coordinate, names in coordinates.items():
        if not names:
            raise ScenarioError(
                BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV,
                f"coordinate {coordinate} declares no value field",
            )
        for name in names:
            if name in key_fields:
                raise ScenarioError(
                    BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV,
                    f"{coordinate}.{name} shadows a declared join key",
                )
            if name in forbidden:
                raise ScenarioError(
                    BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV,
                    f"{coordinate}.{name} is a generic market-data name",
                )
            if name in seen:
                raise ScenarioError(
                    BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV,
                    f"{coordinate}.{name} collides with {seen[name]}.{name}",
                )
            seen[name] = coordinate


def assert_components_disjoint(components: Sequence[str] = COST_COMPONENTS) -> None:
    """Prove no cost component is named twice; a duplicate is refused."""

    seen: set[str] = set()
    for component in components:
        if component in seen:
            raise ScenarioError(
                BLOCKED_DUPLICATE_COST_COMPONENT,
                f"cost component {component!r} appears twice; components are disjoint",
                component=component,
            )
        if component not in COMPONENT_COEFFICIENT_KIND:
            raise ScenarioError(
                BLOCKED_UNREGISTERED_COST_COMPONENT,
                f"unregistered cost component {component!r}",
                component=component,
            )
        seen.add(component)


assert_states_are_complete()
assert_adv_coordinates_non_joinable()
assert_components_disjoint()


# ---------------------------------------------------------------------------
# Exact-value helpers (delegating to the frozen NEE-118 grammar; no float)
# ---------------------------------------------------------------------------

_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ISO_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Working precision for rendering an exact rational at the ledger quantum.
_RENDER_PRECISION: Final = 60


def _identifier(value: object, *, what: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ScenarioError(
            BLOCKED_MALFORMED_SCENARIO_VALUE, f"{what} is not a valid identifier"
        )
    return value


def _iso_session(value: object, *, what: str) -> str:
    if not isinstance(value, str) or not _ISO_DATE_RE.fullmatch(value):
        raise ScenarioError(
            BLOCKED_MALFORMED_SCENARIO_VALUE, f"{what} is not an ISO date"
        )
    return value


def parse_exact(value: object, *, what: str) -> Fraction:
    """Lift a canonical base-10 string to an exact Fraction; binary float refused."""

    try:
        return to_exact(value, what=what)
    except Exception as exc:  # to_exact raises ExecutionAccountingError on bad input.
        raise ScenarioError(
            BLOCKED_MALFORMED_SCENARIO_VALUE,
            f"{what} is not a canonical base-10 value",
        ) from exc


def render_rational(value: Fraction) -> str:
    """Render an exact rational as ``numerator/denominator`` (never base-10-limited)."""

    return f"{value.numerator}/{value.denominator}"


def render_ledger_artifact(value: Fraction, *, what: str = "value") -> str:
    """Render an exact rational at the frozen ``1e-8`` ledger quantum, HALF_EVEN.

    The exact rational stays authoritative; this is the presentation artifact.
    """

    try:
        with localcontext() as context:
            context.prec = _RENDER_PRECISION
            context.rounding = ROUND_HALF_EVEN
            decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
    except InvalidOperation as exc:  # pragma: no cover - bounded domain
        raise ScenarioError(
            BLOCKED_MALFORMED_SCENARIO_VALUE, f"{what} is not renderable"
        ) from exc
    try:
        return format_ledger(decimal_value, what=what)
    except Exception as exc:
        raise ScenarioError(
            BLOCKED_MALFORMED_SCENARIO_VALUE,
            f"{what} is not postable at the ledger quantum",
        ) from exc


def grouped_digest(document: Mapping[str, Any]) -> str:
    """Grouped SHA-256 over the repository canonical JSON encoding of ``document``."""

    return grouped_document_digest(document)


# ---------------------------------------------------------------------------
# Raw ADV observation (the structural raw-coordinate wall)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawSessionBar:
    """One raw close and raw volume for one security-session.

    The **only** observation type :func:`compute_adv` accepts. ``raw_close`` and
    ``raw_volume`` are the raw (unadjusted) coordinate; the notional is their
    exact product. A split-adjusted or total-return dollar volume is a different
    type (:class:`AdjustedDollarVolumeObservation`) and cannot reach this one.
    """

    coordinate_system: ClassVar[str] = RAW_ADV_COORDINATE
    value_field_names: ClassVar[tuple[str, ...]] = ADV_COORDINATE_VALUE_FIELDS[RAW_ADV_COORDINATE]

    security_id: str
    session_id: str
    raw_close: str
    raw_volume: str

    def __post_init__(self) -> None:
        _identifier(self.security_id, what="security_id")
        _iso_session(self.session_id, what="session_id")
        close = parse_exact(self.raw_close, what="raw_close")
        volume = parse_exact(self.raw_volume, what="raw_volume")
        if close <= 0:
            raise ScenarioError(
                BLOCKED_MALFORMED_SCENARIO_VALUE,
                "raw_close must be positive",
                security_id=self.security_id,
                session_id=self.session_id,
            )
        if volume < 0:
            raise ScenarioError(
                BLOCKED_MALFORMED_SCENARIO_VALUE,
                "raw_volume must be non-negative",
                security_id=self.security_id,
                session_id=self.session_id,
            )

    @property
    def raw_dollar_volume(self) -> Fraction:
        """The exact raw dollar volume ``P_raw * V_raw`` for this session."""

        return parse_exact(self.raw_close, what="raw_close") * parse_exact(
            self.raw_volume, what="raw_volume"
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "coordinate_system": self.coordinate_system,
            "raw_close": self.raw_close,
            "raw_volume": self.raw_volume,
            "security_id": self.security_id,
            "session_id": self.session_id,
        }


@dataclass(frozen=True)
class AdjustedDollarVolumeObservation:
    """A split-adjusted or total-return dollar volume. A SIBLING of
    :class:`RawSessionBar`, never a subtype, and never accepted by an ADV
    computation: :func:`compute_adv` is typed for :class:`RawSessionBar` only, so
    an adjusted dollar volume is refused statically and at runtime.
    """

    coordinate_system: ClassVar[str] = ADJUSTED_DOLLAR_VOLUME_COORDINATE
    value_field_names: ClassVar[tuple[str, ...]] = ADV_COORDINATE_VALUE_FIELDS[
        ADJUSTED_DOLLAR_VOLUME_COORDINATE
    ]

    security_id: str
    session_id: str
    adjusted_dollar_volume: str

    def __post_init__(self) -> None:
        _identifier(self.security_id, what="security_id")
        _iso_session(self.session_id, what="session_id")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "adjusted_dollar_volume": self.adjusted_dollar_volume,
            "coordinate_system": self.coordinate_system,
            "security_id": self.security_id,
            "session_id": self.session_id,
        }


@dataclass(frozen=True)
class LiquidityEvidence:
    """The raw session bars for one security in one rebalance's ADV window."""

    rebalance_id: str
    security_id: str
    bars: tuple[RawSessionBar, ...]

    def __post_init__(self) -> None:
        _identifier(self.rebalance_id, what="rebalance_id")
        _identifier(self.security_id, what="security_id")
        for bar in self.bars:
            if type(bar) is not RawSessionBar:
                raise ScenarioError(
                    BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV,
                    "liquidity evidence admits only RawSessionBar observations",
                    rebalance_id=self.rebalance_id,
                    security_id=self.security_id,
                )
            if bar.security_id != self.security_id:
                raise ScenarioError(
                    BLOCKED_MALFORMED_SCENARIO_VALUE,
                    "a bar's security_id does not match its evidence",
                    rebalance_id=self.rebalance_id,
                    security_id=self.security_id,
                )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "bars": [bar.to_json_dict() for bar in self.bars],
            "rebalance_id": self.rebalance_id,
            "security_id": self.security_id,
        }


# ---------------------------------------------------------------------------
# Owner-gated registries (ship EMPTY -> typed BLOCKED)
# ---------------------------------------------------------------------------

SOURCE_KIND_OWNER_MANDATE: Final = "OWNER_MANDATE_RECORD"
SOURCE_KIND_EXECUTION_EVIDENCE: Final = "EXECUTION_EVIDENCE_RECORD"
SOURCE_KIND_TEST_CONSTRUCTED: Final = "TEST_CONSTRUCTED"
SOURCE_KINDS: Final = (
    SOURCE_KIND_OWNER_MANDATE,
    SOURCE_KIND_EXECUTION_EVIDENCE,
    SOURCE_KIND_TEST_CONSTRUCTED,
)
#: Kinds admissible in a shipped registry. ``TEST_CONSTRUCTED`` is not one.
REGISTERED_SOURCE_KINDS: Final = (SOURCE_KIND_OWNER_MANDATE, SOURCE_KIND_EXECUTION_EVIDENCE)


def _provenance(record: object, *, fields: Sequence[str]) -> None:
    for name in fields:
        value = getattr(record, name)
        if not isinstance(value, str) or not value:
            raise ScenarioError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"{name} must be explicit non-empty text",
            )


@dataclass(frozen=True)
class LiquidityLookbackPolicy:
    """The registered ADV lookback ``L`` with full empirical provenance."""

    lookback_id: str
    source_kind: str
    source: str
    source_reference: str
    owner: str
    effective_version: str
    lookback_sessions: int
    unit: str
    sensitivity_range: str

    def __post_init__(self) -> None:
        _identifier(self.lookback_id, what="lookback_id")
        if self.source_kind not in SOURCE_KINDS:
            raise ScenarioError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"unregistered source_kind {self.source_kind!r}",
            )
        _provenance(
            self,
            fields=("source", "source_reference", "owner", "effective_version", "unit",
                    "sensitivity_range"),
        )
        if type(self.lookback_sessions) is not int or self.lookback_sessions < 1:
            raise ScenarioError(
                BLOCKED_MALFORMED_SCENARIO_VALUE,
                "lookback_sessions (L) must be a positive integer",
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "effective_version": self.effective_version,
            "lookback_id": self.lookback_id,
            "lookback_sessions": self.lookback_sessions,
            "owner": self.owner,
            "sensitivity_range": self.sensitivity_range,
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class ParticipationScenario:
    """The registered participation ceiling ``p_star`` with full provenance."""

    scenario_id: str
    source_kind: str
    source: str
    source_reference: str
    owner: str
    effective_version: str
    participation_ceiling: str
    unit: str
    sensitivity_range: str

    def __post_init__(self) -> None:
        _identifier(self.scenario_id, what="scenario_id")
        if self.source_kind not in SOURCE_KINDS:
            raise ScenarioError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"unregistered source_kind {self.source_kind!r}",
            )
        _provenance(
            self,
            fields=("source", "source_reference", "owner", "effective_version", "unit",
                    "sensitivity_range"),
        )
        ceiling = parse_exact(self.participation_ceiling, what="participation_ceiling")
        if ceiling <= 0 or ceiling > 1:
            raise ScenarioError(
                BLOCKED_MALFORMED_SCENARIO_VALUE,
                "participation_ceiling (p_star) must be in (0, 1]",
            )

    @property
    def ceiling(self) -> Fraction:
        return parse_exact(self.participation_ceiling, what="participation_ceiling")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "effective_version": self.effective_version,
            "owner": self.owner,
            "participation_ceiling": self.participation_ceiling,
            "scenario_id": self.scenario_id,
            "sensitivity_range": self.sensitivity_range,
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class CommissionSchedule:
    """A registered commission coefficient (bps of notional) with provenance."""

    schedule_id: str
    source_kind: str
    source: str
    source_reference: str
    owner: str
    effective_version: str
    commission_bps: str
    unit: str
    sensitivity_range: str

    def __post_init__(self) -> None:
        _identifier(self.schedule_id, what="schedule_id")
        if self.source_kind not in SOURCE_KINDS:
            raise ScenarioError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"unregistered source_kind {self.source_kind!r}",
            )
        _provenance(
            self,
            fields=("source", "source_reference", "owner", "effective_version", "unit",
                    "sensitivity_range"),
        )
        if parse_exact(self.commission_bps, what="commission_bps") < 0:
            raise ScenarioError(
                BLOCKED_MALFORMED_SCENARIO_VALUE, "commission_bps must be non-negative"
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "commission_bps": self.commission_bps,
            "effective_version": self.effective_version,
            "owner": self.owner,
            "schedule_id": self.schedule_id,
            "sensitivity_range": self.sensitivity_range,
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class SpreadModel:
    """A registered half-spread coefficient (bps) with provenance."""

    model_id: str
    source_kind: str
    source: str
    source_reference: str
    owner: str
    effective_version: str
    half_spread_bps: str
    unit: str
    sensitivity_range: str

    def __post_init__(self) -> None:
        _identifier(self.model_id, what="model_id")
        if self.source_kind not in SOURCE_KINDS:
            raise ScenarioError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"unregistered source_kind {self.source_kind!r}",
            )
        _provenance(
            self,
            fields=("source", "source_reference", "owner", "effective_version", "unit",
                    "sensitivity_range"),
        )
        if parse_exact(self.half_spread_bps, what="half_spread_bps") < 0:
            raise ScenarioError(
                BLOCKED_MALFORMED_SCENARIO_VALUE, "half_spread_bps must be non-negative"
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "effective_version": self.effective_version,
            "half_spread_bps": self.half_spread_bps,
            "model_id": self.model_id,
            "owner": self.owner,
            "sensitivity_range": self.sensitivity_range,
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class ImpactModel:
    """A registered impact coefficient with provenance (a scenario, not a fact)."""

    model_id: str
    source_kind: str
    source: str
    source_reference: str
    owner: str
    effective_version: str
    impact_coefficient: str
    unit: str
    sensitivity_range: str

    def __post_init__(self) -> None:
        _identifier(self.model_id, what="model_id")
        if self.source_kind not in SOURCE_KINDS:
            raise ScenarioError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"unregistered source_kind {self.source_kind!r}",
            )
        _provenance(
            self,
            fields=("source", "source_reference", "owner", "effective_version", "unit",
                    "sensitivity_range"),
        )
        if parse_exact(self.impact_coefficient, what="impact_coefficient") < 0:
            raise ScenarioError(
                BLOCKED_MALFORMED_SCENARIO_VALUE, "impact_coefficient must be non-negative"
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "effective_version": self.effective_version,
            "impact_coefficient": self.impact_coefficient,
            "model_id": self.model_id,
            "owner": self.owner,
            "sensitivity_range": self.sensitivity_range,
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
            "unit": self.unit,
        }


#: Every owner-gated registry ships EMPTY by design. Until the owner registers a
#: record with execution/mandate evidence, resolving any of these fails closed.
REGISTERED_LIQUIDITY_LOOKBACKS: Final[tuple[LiquidityLookbackPolicy, ...]] = ()
REGISTERED_PARTICIPATION_SCENARIOS: Final[tuple[ParticipationScenario, ...]] = ()
REGISTERED_COMMISSION_SCHEDULES: Final[tuple[CommissionSchedule, ...]] = ()
REGISTERED_SPREAD_MODELS: Final[tuple[SpreadModel, ...]] = ()
REGISTERED_IMPACT_MODELS: Final[tuple[ImpactModel, ...]] = ()


def _validate_registry(
    records: Sequence[Any],
    *,
    shipped: Sequence[Any],
    id_attr: str,
    empty_state: str,
    empty_message: str,
) -> None:
    if not records:
        raise ScenarioError(empty_state, empty_message)
    identifiers: set[str] = set()
    for record in records:
        identifier = getattr(record, id_attr)
        if identifier in identifiers:
            raise ScenarioError(
                BLOCKED_AMBIGUOUS_REGISTRY_RECORD,
                f"duplicate {id_attr} in registry: {identifier}",
            )
        identifiers.add(identifier)
        if records is shipped and record.source_kind not in REGISTERED_SOURCE_KINDS:
            raise ScenarioError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"{identifier}: {record.source_kind} may not ship in the registry",
            )


def _resolve(
    identifier: str,
    records: Sequence[Any],
    *,
    id_attr: str,
    unresolved_state: str,
) -> Any:
    matches = [record for record in records if getattr(record, id_attr) == identifier]
    if not matches:
        raise ScenarioError(unresolved_state, f"{identifier!r} is not registered")
    if len(matches) > 1:  # pragma: no cover - _validate_registry rejects duplicates
        raise ScenarioError(
            BLOCKED_AMBIGUOUS_REGISTRY_RECORD, f"ambiguous record {identifier!r}"
        )
    return matches[0]


def resolve_liquidity_lookback(
    lookback_id: str,
    *,
    records: Sequence[LiquidityLookbackPolicy] = REGISTERED_LIQUIDITY_LOOKBACKS,
) -> LiquidityLookbackPolicy:
    """Return the registered lookback ``L``, or fail closed. Never invents one."""

    _validate_registry(
        records,
        shipped=REGISTERED_LIQUIDITY_LOOKBACKS,
        id_attr="lookback_id",
        empty_state=BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK,
        empty_message=(
            "no ADV lookback L is registered; L requires execution/mandate evidence "
            "and this engine refuses to assume a window"
        ),
    )
    resolved = _resolve(
        lookback_id, records, id_attr="lookback_id",
        unresolved_state=BLOCKED_UNRESOLVED_LIQUIDITY_LOOKBACK,
    )
    assert isinstance(resolved, LiquidityLookbackPolicy)
    return resolved


def resolve_participation_scenario(
    scenario_id: str,
    *,
    records: Sequence[ParticipationScenario] = REGISTERED_PARTICIPATION_SCENARIOS,
) -> ParticipationScenario:
    """Return the registered participation ceiling ``p_star``, or fail closed."""

    _validate_registry(
        records,
        shipped=REGISTERED_PARTICIPATION_SCENARIOS,
        id_attr="scenario_id",
        empty_state=BLOCKED_NO_REGISTERED_PARTICIPATION_SCENARIO,
        empty_message=(
            "no participation scenario p_star is registered; p_star requires "
            "execution/mandate evidence and this engine refuses to assume a ceiling"
        ),
    )
    resolved = _resolve(
        scenario_id, records, id_attr="scenario_id",
        unresolved_state=BLOCKED_UNRESOLVED_PARTICIPATION_SCENARIO,
    )
    assert isinstance(resolved, ParticipationScenario)
    return resolved


def resolve_commission_schedule(
    schedule_id: str,
    *,
    records: Sequence[CommissionSchedule] = REGISTERED_COMMISSION_SCHEDULES,
) -> CommissionSchedule:
    """Return the registered commission schedule, or fail closed."""

    _validate_registry(
        records,
        shipped=REGISTERED_COMMISSION_SCHEDULES,
        id_attr="schedule_id",
        empty_state=BLOCKED_NO_REGISTERED_COMMISSION_SCHEDULE,
        empty_message="no commission schedule is registered",
    )
    resolved = _resolve(
        schedule_id, records, id_attr="schedule_id",
        unresolved_state=BLOCKED_UNRESOLVED_COMMISSION_SCHEDULE,
    )
    assert isinstance(resolved, CommissionSchedule)
    return resolved


def resolve_spread_model(
    model_id: str,
    *,
    records: Sequence[SpreadModel] = REGISTERED_SPREAD_MODELS,
) -> SpreadModel:
    """Return the registered spread model, or fail closed."""

    _validate_registry(
        records,
        shipped=REGISTERED_SPREAD_MODELS,
        id_attr="model_id",
        empty_state=BLOCKED_NO_REGISTERED_SPREAD_MODEL,
        empty_message="no spread model is registered",
    )
    resolved = _resolve(
        model_id, records, id_attr="model_id",
        unresolved_state=BLOCKED_UNRESOLVED_SPREAD_MODEL,
    )
    assert isinstance(resolved, SpreadModel)
    return resolved


def resolve_impact_model(
    model_id: str,
    *,
    records: Sequence[ImpactModel] = REGISTERED_IMPACT_MODELS,
) -> ImpactModel:
    """Return the registered impact model, or fail closed."""

    _validate_registry(
        records,
        shipped=REGISTERED_IMPACT_MODELS,
        id_attr="model_id",
        empty_state=BLOCKED_NO_REGISTERED_IMPACT_MODEL,
        empty_message="no impact model is registered",
    )
    resolved = _resolve(
        model_id, records, id_attr="model_id",
        unresolved_state=BLOCKED_UNRESOLVED_IMPACT_MODEL,
    )
    assert isinstance(resolved, ImpactModel)
    return resolved


# ---------------------------------------------------------------------------
# The UNCALIBRATED_SCENARIO type wall
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibratedComponentCost:
    """A cost component with a calibrated exact amount.

    The **only** component-cost type that carries an amount. A number can be read
    off this type; it cannot be read off :class:`UncalibratedScenario`.
    """

    component: str
    state: ClassVar[str] = CALIBRATED_COMPONENT_STATE
    amount_rational: str
    amount_ledger: str
    basis: str
    coefficient_kind: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "amount_ledger": self.amount_ledger,
            "amount_rational": self.amount_rational,
            "basis": self.basis,
            "coefficient_kind": self.coefficient_kind,
            "component": self.component,
            "state": self.state,
        }


@dataclass(frozen=True)
class UncalibratedScenario:
    """A cost component whose coefficient is not registered.

    Carries **no** amount field of any kind, so it is structurally incapable of
    being summed, rendered, or presented as an estimate. Converting it to a
    number requires narrowing to :class:`CalibratedComponentCost`, which the type
    system forbids (proved by the in-test ``mypy --strict`` probe).
    """

    component: str
    state: ClassVar[str] = UNCALIBRATED_SCENARIO_STATE
    coefficient_kind: str
    reason: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "coefficient_kind": self.coefficient_kind,
            "component": self.component,
            "reason": self.reason,
            "state": self.state,
        }


ComponentCost = CalibratedComponentCost | UncalibratedScenario


def require_calibrated(cost: CalibratedComponentCost) -> Fraction:
    """Read the exact amount of a component cost.

    Accepts a :class:`CalibratedComponentCost` only. Passing an
    :class:`UncalibratedScenario` is a static type error: this is the wall that
    makes an uncalibrated coefficient structurally unable to become an estimate.
    """

    return parse_exact(cost.amount_rational, what="amount_rational")


def _regulatory_fee_component(regulatory_fees_total: str) -> CalibratedComponentCost:
    """Build the REGULATORY_FEE component from the ledger's consumed total."""

    exact = parse_exact(regulatory_fees_total, what="regulatory_fees_total")
    return CalibratedComponentCost(
        component=COMPONENT_REGULATORY_FEE,
        amount_rational=render_rational(exact),
        amount_ledger=format_ledger(regulatory_fees_total, what="regulatory_fees_total"),
        basis="LEDGER_REGULATORY_FEE_TOTAL",
        coefficient_kind=COMPONENT_COEFFICIENT_KIND[COMPONENT_REGULATORY_FEE],
    )


def component_costs(
    *,
    regulatory_fees_total: str,
    commission_schedules: Sequence[CommissionSchedule] = REGISTERED_COMMISSION_SCHEDULES,
    spread_models: Sequence[SpreadModel] = REGISTERED_SPREAD_MODELS,
    impact_models: Sequence[ImpactModel] = REGISTERED_IMPACT_MODELS,
) -> tuple[ComponentCost, ...]:
    """Decompose cost into the four disjoint named components, no double-count.

    The regulatory-fee component is calibrated from the ledger. Commission,
    spread, and impact are calibrated only if their coefficient registry is
    non-empty; with the shipped empty registries each is an
    :class:`UncalibratedScenario`, never a production estimate.
    """

    assert_components_disjoint()
    result: dict[str, ComponentCost] = {
        COMPONENT_REGULATORY_FEE: _regulatory_fee_component(regulatory_fees_total),
    }
    for component, registry in (
        (COMPONENT_COMMISSION, commission_schedules),
        (COMPONENT_SPREAD, spread_models),
        (COMPONENT_IMPACT, impact_models),
    ):
        if registry:  # A registered coefficient would be resolved and applied here.
            raise ScenarioError(
                BLOCKED_UNREGISTERED_COST_COMPONENT,
                f"calibrating the {component} component requires an owner registration "
                "path that is not enabled in this engine version",
                component=component,
            )
        result[component] = UncalibratedScenario(
            component=component,
            coefficient_kind=COMPONENT_COEFFICIENT_KIND[component],
            reason="no registered coefficient; component is a labelled scenario, not a fact",
        )
    return tuple(result[component] for component in COST_COMPONENTS)


# ---------------------------------------------------------------------------
# ADV, participation, capacity
# ---------------------------------------------------------------------------


def compute_adv(
    bars: Sequence[RawSessionBar],
    *,
    lookback: LiquidityLookbackPolicy,
    as_of_session: str,
) -> Fraction:
    """Exact ADV over exactly the registered ``L`` completed prior sessions.

    ``ADV = mean(P_raw_u * V_raw_u)`` over the ``L`` bars, each strictly before
    ``as_of_session`` (completed prior sessions only), unique, and raw. An
    adjusted dollar volume cannot enter: ``bars`` is typed for
    :class:`RawSessionBar`, refused statically and at runtime.
    """

    for bar in bars:
        if type(bar) is not RawSessionBar:
            raise ScenarioError(
                BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV,
                "ADV is computed from raw close * raw volume only; an adjusted dollar "
                "volume is refused",
            )
    _iso_session(as_of_session, what="as_of_session")
    sessions: set[str] = set()
    for bar in bars:
        if bar.session_id in sessions:
            raise ScenarioError(
                BLOCKED_DUPLICATE_ADV_SESSION,
                f"session {bar.session_id} appears twice in the ADV window",
                session_id=bar.session_id,
            )
        if bar.session_id >= as_of_session:
            raise ScenarioError(
                BLOCKED_NON_PRIOR_ADV_SESSION,
                f"ADV session {bar.session_id} is not strictly before the cutoff "
                f"{as_of_session}; ADV uses completed prior sessions only",
                session_id=bar.session_id,
            )
        sessions.add(bar.session_id)
    if len(bars) != lookback.lookback_sessions:
        raise ScenarioError(
            BLOCKED_INSUFFICIENT_ADV_HISTORY,
            f"ADV needs exactly L={lookback.lookback_sessions} sessions; got {len(bars)}",
        )
    total = sum((bar.raw_dollar_volume for bar in bars), start=Fraction(0))
    return total / lookback.lookback_sessions


@dataclass(frozen=True)
class TradeScenarioRow:
    """Per-fill cost, participation, and capacity scenario for one security."""

    rebalance_id: str
    security_id: str
    side: str
    gross_notional: str
    tier_costs_ledger: Mapping[str, str]
    tier_costs_rational: Mapping[str, str]
    adv_rational: str | None
    adv_ledger: str | None
    participation_state: str
    participation_rational: str | None
    participation_ledger: str | None
    target_weight_change_rational: str | None
    capacity_state: str
    aum_capacity_rational: str | None
    aum_capacity_ledger: str | None
    lineage: ScenarioLineage

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "adv_ledger": self.adv_ledger,
            "adv_rational": self.adv_rational,
            "aum_capacity_ledger": self.aum_capacity_ledger,
            "aum_capacity_rational": self.aum_capacity_rational,
            "capacity_state": self.capacity_state,
            "gross_notional": self.gross_notional,
            "lineage": self.lineage.to_json_dict(),
            "participation_ledger": self.participation_ledger,
            "participation_rational": self.participation_rational,
            "participation_state": self.participation_state,
            "rebalance_id": self.rebalance_id,
            "security_id": self.security_id,
            "side": self.side,
            "target_weight_change_rational": self.target_weight_change_rational,
            "tier_costs_ledger": dict(self.tier_costs_ledger),
            "tier_costs_rational": dict(self.tier_costs_rational),
        }


@dataclass(frozen=True)
class RebalanceScenario:
    """The cost/turnover/liquidity/capacity scenario for one rebalance."""

    rebalance_id: str
    step: str
    signal_session: str
    nav_minus: str
    gross_trade_notional: str
    gtn_ratio: str
    one_way_turnover: str
    tier_costs_ledger: Mapping[str, str]
    tier_costs_rational: Mapping[str, str]
    component_costs: tuple[ComponentCost, ...]
    regulatory_fee_component: CalibratedComponentCost
    portfolio_capacity_state: str
    portfolio_capacity_rational: str | None
    portfolio_capacity_ledger: str | None
    binding_security_id: str | None
    rows: tuple[TradeScenarioRow, ...]
    lineage: ScenarioLineage

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "binding_security_id": self.binding_security_id,
            "component_costs": [cost.to_json_dict() for cost in self.component_costs],
            "gross_trade_notional": self.gross_trade_notional,
            "gtn_ratio": self.gtn_ratio,
            "lineage": self.lineage.to_json_dict(),
            "nav_minus": self.nav_minus,
            "one_way_turnover": self.one_way_turnover,
            "portfolio_capacity_ledger": self.portfolio_capacity_ledger,
            "portfolio_capacity_rational": self.portfolio_capacity_rational,
            "portfolio_capacity_state": self.portfolio_capacity_state,
            "rebalance_id": self.rebalance_id,
            "regulatory_fee_component": self.regulatory_fee_component.to_json_dict(),
            "rows": [row.to_json_dict() for row in self.rows],
            "signal_session": self.signal_session,
            "step": self.step,
            "tier_costs_ledger": dict(self.tier_costs_ledger),
            "tier_costs_rational": dict(self.tier_costs_rational),
        }


# ---------------------------------------------------------------------------
# Lineage, schema, code binding, manifest
# ---------------------------------------------------------------------------

_GROUPED_RE: Final = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")

ROW_FIELD_NAMES: Final[tuple[str, ...]] = (
    "adv_ledger",
    "adv_rational",
    "aum_capacity_ledger",
    "aum_capacity_rational",
    "capacity_state",
    "gross_notional",
    "lineage",
    "participation_ledger",
    "participation_rational",
    "participation_state",
    "rebalance_id",
    "security_id",
    "side",
    "target_weight_change_rational",
    "tier_costs_ledger",
    "tier_costs_rational",
)

REBALANCE_FIELD_NAMES: Final[tuple[str, ...]] = (
    "binding_security_id",
    "component_costs",
    "gross_trade_notional",
    "gtn_ratio",
    "lineage",
    "nav_minus",
    "one_way_turnover",
    "portfolio_capacity_ledger",
    "portfolio_capacity_rational",
    "portfolio_capacity_state",
    "rebalance_id",
    "regulatory_fee_component",
    "rows",
    "signal_session",
    "step",
    "tier_costs_ledger",
    "tier_costs_rational",
)

MANIFEST_FIELD_NAMES: Final[tuple[str, ...]] = (
    "code_sha256_grouped",
    "config_sha256_grouped",
    "cost_policy_sha256_grouped",
    "input_sha256_grouped",
    "output_sha256_grouped",
    "schema_sha256_grouped",
)


@dataclass(frozen=True)
class ScenarioLineage:
    """The five grouped digests carried by every row and by the manifest."""

    input_sha256_grouped: str
    cost_policy_sha256_grouped: str
    config_sha256_grouped: str
    code_sha256_grouped: str
    schema_sha256_grouped: str

    def __post_init__(self) -> None:
        for name in (
            "input_sha256_grouped",
            "cost_policy_sha256_grouped",
            "config_sha256_grouped",
            "code_sha256_grouped",
            "schema_sha256_grouped",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _GROUPED_RE.fullmatch(value):
                raise ScenarioError(
                    BLOCKED_MALFORMED_SCENARIO_VALUE,
                    f"{name} must be eight lowercase 8-hex groups joined by ':'",
                )

    def to_json_dict(self) -> dict[str, str]:
        return {
            "code_sha256_grouped": self.code_sha256_grouped,
            "config_sha256_grouped": self.config_sha256_grouped,
            "cost_policy_sha256_grouped": self.cost_policy_sha256_grouped,
            "input_sha256_grouped": self.input_sha256_grouped,
            "schema_sha256_grouped": self.schema_sha256_grouped,
        }


def schema_digest() -> str:
    """Grouped digest over the declared output schema field names."""

    return grouped_digest(
        {
            "kernel_id": KERNEL_ID,
            "schema_version": SCHEMA_VERSION,
            "row_field_names": list(ROW_FIELD_NAMES),
            "rebalance_field_names": list(REBALANCE_FIELD_NAMES),
            "manifest_field_names": list(MANIFEST_FIELD_NAMES),
        }
    )


def code_binding_digest() -> str:
    """Grouped digest over the declared vocabulary and formulae, not source bytes.

    Like the wave-1 engines, identity binds *declared* policy (formulae, tiers,
    component names, coordinate names, the consumed ledger attribute paths, the
    regulatory-fee kernel identity), so the digest changes only when the policy
    changes, never on an incidental source edit.
    """

    return grouped_digest(
        {
            "adv_basis": ADV_BASIS,
            "adv_coordinate_value_fields": {
                key: list(value) for key, value in ADV_COORDINATE_VALUE_FIELDS.items()
            },
            "adv_definition": ADV_DEFINITION,
            "consumed_ledger_attribute_paths": list(CONSUMED_LEDGER_ATTRIBUTE_PATHS),
            "cost_components": list(COST_COMPONENTS),
            "equation_spec_id": EQUATION_SPEC_ID,
            "formulae": [
                FORMULA_GTN,
                FORMULA_GTN_RATIO,
                FORMULA_ONE_WAY_TURNOVER,
                FORMULA_TIER_COST,
                FORMULA_PARTICIPATION,
                FORMULA_TARGET_WEIGHT_CHANGE,
                FORMULA_AUM_CAPACITY,
                FORMULA_PORTFOLIO_CAPACITY,
            ],
            "kernel_id": KERNEL_ID,
            "method_id": METHOD_ID,
            "raw_adv_coordinate": RAW_ADV_COORDINATE,
            "regulatory_fee_implementation_id": REGULATORY_FEE_IMPLEMENTATION_ID,
            "regulatory_fee_method_id": REGULATORY_FEE_METHOD_ID,
            "regulatory_fee_schedule_artifact_id": REGULATORY_FEE_SCHEDULE_ARTIFACT_ID,
            "required_cost_tiers_bps": list(REQUIRED_COST_TIERS_BPS),
            "schema_version": SCHEMA_VERSION,
        }
    )


@dataclass(frozen=True)
class ScenarioManifest:
    """The replayable manifest binding input, cost-policy, config, code, and
    output-content hashes for one scenario report."""

    kernel_id: str
    method_id: str
    schema_version: str
    lookback_id: str
    participation_scenario_id: str
    lineage: ScenarioLineage
    output_sha256_grouped: str

    def _document(self) -> dict[str, Any]:
        return {
            "adv_definition": ADV_DEFINITION,
            "claims": dict(NON_CLAIMS),
            "code_sha256_grouped": self.lineage.code_sha256_grouped,
            "config_sha256_grouped": self.lineage.config_sha256_grouped,
            "cost_policy_sha256_grouped": self.lineage.cost_policy_sha256_grouped,
            "input_sha256_grouped": self.lineage.input_sha256_grouped,
            "kernel_id": self.kernel_id,
            "lookback_id": self.lookback_id,
            "method_id": self.method_id,
            "output_sha256_grouped": self.output_sha256_grouped,
            "participation_scenario_id": self.participation_scenario_id,
            "regulatory_fee_method_id": REGULATORY_FEE_METHOD_ID,
            "regulatory_fee_schedule_artifact_id": REGULATORY_FEE_SCHEDULE_ARTIFACT_ID,
            "schema_sha256_grouped": self.lineage.schema_sha256_grouped,
            "schema_version": self.schema_version,
        }

    @property
    def self_sha256_grouped(self) -> str:
        return grouped_digest(self._document())

    def to_json_dict(self) -> dict[str, Any]:
        document = self._document()
        document["self_sha256_grouped"] = self.self_sha256_grouped
        return document


@dataclass(frozen=True)
class ScenarioReport:
    """Every scenario one execution run produced, content-addressed."""

    kernel_id: str
    method_id: str
    schema_version: str
    state: str
    program_id: str
    run_manifest_sha256_grouped: str
    lookback_id: str
    participation_scenario_id: str
    rebalances: tuple[RebalanceScenario, ...]
    manifest: ScenarioManifest

    def rebalance(self, rebalance_id: str) -> RebalanceScenario:
        for scenario in self.rebalances:
            if scenario.rebalance_id == rebalance_id:
                return scenario
        raise ScenarioError(
            BLOCKED_MALFORMED_SCENARIO_VALUE, f"no scenario for rebalance {rebalance_id!r}"
        )

    def _content_document(self) -> dict[str, Any]:
        return {
            "kernel_id": self.kernel_id,
            "lookback_id": self.lookback_id,
            "method_id": self.method_id,
            "participation_scenario_id": self.participation_scenario_id,
            "program_id": self.program_id,
            "rebalances": [scenario.to_json_dict() for scenario in self.rebalances],
            "run_manifest_sha256_grouped": self.run_manifest_sha256_grouped,
            "schema_version": self.schema_version,
            "state": self.state,
        }

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "claims": dict(NON_CLAIMS),
            "manifest": self.manifest.to_json_dict(),
            "report": self._content_document(),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json_dict())

    @property
    def self_sha256_grouped(self) -> str:
        return group_sha256(self.canonical_bytes())


# ---------------------------------------------------------------------------
# Top-level evaluation
# ---------------------------------------------------------------------------


def _tier_costs(gtn: Fraction) -> tuple[dict[str, str], dict[str, str]]:
    """The per-side bps tier costs ``TC = b/10000 * GTN`` for the required tiers."""

    ledger: dict[str, str] = {}
    rational: dict[str, str] = {}
    for tier in REQUIRED_COST_TIERS_BPS:
        cost = Fraction(tier, _BPS_DENOMINATOR) * gtn
        key = str(tier)
        rational[key] = render_rational(cost)
        ledger[key] = render_ledger_artifact(cost, what=f"tier_{tier}_cost")
    return ledger, rational


def _fills_by_security(ledger: RebalanceLedger) -> dict[str, ExecutedFill]:
    grouped: dict[str, ExecutedFill] = {}
    for fill in ledger.fill_states:
        if fill.security_id in grouped:
            raise ScenarioError(
                BLOCKED_MALFORMED_EXECUTION_RUN,
                "a security is filled twice in one rebalance; cannot attribute participation",
                rebalance_id=ledger.rebalance_id,
                security_id=fill.security_id,
            )
        grouped[fill.security_id] = fill
    return grouped


def _rebalance_scenario(
    ledger: RebalanceLedger,
    *,
    lookback: LiquidityLookbackPolicy,
    participation: ParticipationScenario,
    evidence_by_security: Mapping[str, LiquidityEvidence],
    commission_schedules: Sequence[CommissionSchedule],
    spread_models: Sequence[SpreadModel],
    impact_models: Sequence[ImpactModel],
    lineage: ScenarioLineage,
) -> RebalanceScenario:
    gtn = parse_exact(ledger.gross_trade_notional, what="gross_trade_notional")
    nav_minus = parse_exact(ledger.nav_minus, what="nav_minus")
    if nav_minus <= 0:  # pragma: no cover - the ledger guarantees NAV_minus > 0
        raise ScenarioError(
            BLOCKED_MALFORMED_EXECUTION_RUN,
            "NAV_minus must be positive",
            rebalance_id=ledger.rebalance_id,
        )
    signal_session = ledger.fill_timing.signal_session.session_date.isoformat()
    p_star = participation.ceiling

    tier_ledger, tier_rational = _tier_costs(gtn)
    components = component_costs(
        regulatory_fees_total=ledger.regulatory_fees_total,
        commission_schedules=commission_schedules,
        spread_models=spread_models,
        impact_models=impact_models,
    )
    # The regulatory-fee handle is the SAME object already in the components
    # tuple, so the fee is surfaced once, never computed or counted twice.
    regulatory_fee_component = next(
        cost
        for cost in components
        if isinstance(cost, CalibratedComponentCost)
        and cost.component == COMPONENT_REGULATORY_FEE
    )

    fills = _fills_by_security(ledger)
    rows: list[TradeScenarioRow] = []
    capacities: list[tuple[Fraction, str]] = []
    any_missing = False
    any_non_positive_adv = False
    for security_id in sorted(fills, key=lambda item: item.encode("utf-8")):
        fill = fills[security_id]
        gross_notional = parse_exact(fill.gross_notional, what="gross_notional")
        row_tier_ledger, row_tier_rational = _tier_costs(gross_notional)
        target_weight_change = gross_notional / nav_minus

        evidence = evidence_by_security.get(security_id)
        if evidence is None:
            any_missing = True
            rows.append(
                TradeScenarioRow(
                    rebalance_id=ledger.rebalance_id,
                    security_id=security_id,
                    side=fill.side,
                    gross_notional=fill.gross_notional,
                    tier_costs_ledger=row_tier_ledger,
                    tier_costs_rational=row_tier_rational,
                    adv_rational=None,
                    adv_ledger=None,
                    participation_state=PARTICIPATION_UNAVAILABLE_MISSING_ADV,
                    participation_rational=None,
                    participation_ledger=None,
                    target_weight_change_rational=render_rational(target_weight_change),
                    capacity_state=CAPACITY_UNAVAILABLE_MISSING_ADV,
                    aum_capacity_rational=None,
                    aum_capacity_ledger=None,
                    lineage=lineage,
                )
            )
            continue

        adv = compute_adv(evidence.bars, lookback=lookback, as_of_session=signal_session)
        if adv <= 0:
            # A non-positive ADV window (every session of L carried zero raw
            # volume, i.e. a fully halted/illiquid name) makes participation
            # |dq|*P / ADV undefined. Fail closed to typed row states rather than
            # dividing by zero: surface the measured ADV (0), decline the
            # participation and capacity numbers, and mark the portfolio capacity
            # incomplete. This mirrors the missing-evidence path structurally but
            # is a distinct, honestly-named condition (the ADV is measured, not
            # missing).
            any_non_positive_adv = True
            rows.append(
                TradeScenarioRow(
                    rebalance_id=ledger.rebalance_id,
                    security_id=security_id,
                    side=fill.side,
                    gross_notional=fill.gross_notional,
                    tier_costs_ledger=row_tier_ledger,
                    tier_costs_rational=row_tier_rational,
                    adv_rational=render_rational(adv),
                    adv_ledger=render_ledger_artifact(adv, what="adv"),
                    participation_state=PARTICIPATION_UNAVAILABLE_NON_POSITIVE_ADV,
                    participation_rational=None,
                    participation_ledger=None,
                    target_weight_change_rational=render_rational(target_weight_change),
                    capacity_state=CAPACITY_UNAVAILABLE_NON_POSITIVE_ADV,
                    aum_capacity_rational=None,
                    aum_capacity_ledger=None,
                    lineage=lineage,
                )
            )
            continue
        participation_value = gross_notional / adv
        aum_capacity = p_star * adv / target_weight_change
        capacities.append((aum_capacity, security_id))
        rows.append(
            TradeScenarioRow(
                rebalance_id=ledger.rebalance_id,
                security_id=security_id,
                side=fill.side,
                gross_notional=fill.gross_notional,
                tier_costs_ledger=row_tier_ledger,
                tier_costs_rational=row_tier_rational,
                adv_rational=render_rational(adv),
                adv_ledger=render_ledger_artifact(adv, what="adv"),
                participation_state=PARTICIPATION_MEASURED_SCENARIO,
                participation_rational=render_rational(participation_value),
                participation_ledger=render_ledger_artifact(
                    participation_value, what="participation"
                ),
                target_weight_change_rational=render_rational(target_weight_change),
                capacity_state=CAPACITY_MEASURED_SCENARIO,
                aum_capacity_rational=render_rational(aum_capacity),
                aum_capacity_ledger=render_ledger_artifact(aum_capacity, what="aum_capacity"),
                lineage=lineage,
            )
        )

    portfolio_state, portfolio_rational, portfolio_ledger, binding = _portfolio_capacity(
        capacities,
        any_missing=any_missing,
        any_non_positive_adv=any_non_positive_adv,
        traded=bool(fills),
    )

    return RebalanceScenario(
        rebalance_id=ledger.rebalance_id,
        step=ledger.step,
        signal_session=signal_session,
        nav_minus=ledger.nav_minus,
        gross_trade_notional=ledger.gross_trade_notional,
        gtn_ratio=ledger.gtn_ratio,
        one_way_turnover=ledger.one_way_turnover,
        tier_costs_ledger=tier_ledger,
        tier_costs_rational=tier_rational,
        component_costs=components,
        regulatory_fee_component=regulatory_fee_component,
        portfolio_capacity_state=portfolio_state,
        portfolio_capacity_rational=portfolio_rational,
        portfolio_capacity_ledger=portfolio_ledger,
        binding_security_id=binding,
        rows=tuple(rows),
        lineage=lineage,
    )


def _portfolio_capacity(
    capacities: Sequence[tuple[Fraction, str]],
    *,
    any_missing: bool,
    any_non_positive_adv: bool,
    traded: bool,
) -> tuple[str, str | None, str | None, str | None]:
    """The portfolio capacity ``min_i(AUM_capacity_i)`` and its binding name.

    If any traded name lacks an ADV, or carries a non-positive (zero-liquidity)
    ADV window, the minimum is not claimable: that name could be — or, for a
    zero-ADV name, is — the binding constraint, so the portfolio capacity is
    incomplete rather than an over-stated minimum over the observed subset. The
    non-positive-ADV cause is reported ahead of a plain missing-evidence cause so
    the more specific degeneracy is named.
    """

    if not traded:
        return PORTFOLIO_CAPACITY_ZERO_TRADE, None, None, None
    if any_non_positive_adv:
        return PORTFOLIO_CAPACITY_INCOMPLETE_NON_POSITIVE_ADV, None, None, None
    if any_missing:
        return PORTFOLIO_CAPACITY_INCOMPLETE_MISSING_ADV, None, None, None
    minimum, binding = min(capacities, key=lambda item: (item[0], item[1].encode("utf-8")))
    return (
        PORTFOLIO_CAPACITY_SCENARIO_COMPLETE,
        render_rational(minimum),
        render_ledger_artifact(minimum, what="portfolio_capacity"),
        binding,
    )


def _input_digest(
    run: ExecutionRun,
    *,
    evidence: Sequence[LiquidityEvidence],
) -> str:
    """Grouped digest over the consumed ledger content and the ADV evidence.

    Order-invariant: rebalances in ledger order, fills and evidence sorted by
    content, so an input container permutation cannot change identity.
    """

    rebalances = []
    for ledger in run.rebalance_ledgers:
        rebalances.append(
            {
                "fill_states": [
                    {
                        "delta_raw_shares": fill.delta_raw_shares,
                        "gross_notional": fill.gross_notional,
                        "raw_execution_price": fill.raw_execution_price,
                        "security_id": fill.security_id,
                        "side": fill.side,
                    }
                    for fill in sorted(
                        ledger.fill_states, key=lambda item: item.security_id.encode("utf-8")
                    )
                ],
                "gross_trade_notional": ledger.gross_trade_notional,
                "gtn_ratio": ledger.gtn_ratio,
                "nav_minus": ledger.nav_minus,
                "one_way_turnover": ledger.one_way_turnover,
                "rebalance_id": ledger.rebalance_id,
                # Bind exactly the fee-line sub-fields declared in
                # CONSUMED_LEDGER_ATTRIBUTE_PATHS (total_raw / side / symbol), so
                # "what is hashed into input identity" equals "what is declared
                # consumed" rather than the whole opaque line dict.
                "regulatory_fee_lines": [
                    {
                        "side": line["side"],
                        "symbol": line["symbol"],
                        "total_raw": line["total_raw"],
                    }
                    for line in ledger.regulatory_fee_lines
                ],
                "regulatory_fees_total": ledger.regulatory_fees_total,
                "signal_session": ledger.fill_timing.signal_session.session_date.isoformat(),
                "step": ledger.step,
            }
        )
    evidence_rows = [item.to_json_dict() for item in _sorted_evidence(evidence)]
    return grouped_digest(
        {
            "consumed_attribute_paths": list(CONSUMED_LEDGER_ATTRIBUTE_PATHS),
            "liquidity_evidence": evidence_rows,
            "program_id": run.program_id,
            "rebalances": rebalances,
            "run_manifest_sha256_grouped": run.manifest.self_sha256_grouped,
            "run_state": run.state,
        }
    )


def _cost_policy_digest(
    *,
    commission_schedules: Sequence[CommissionSchedule],
    spread_models: Sequence[SpreadModel],
    impact_models: Sequence[ImpactModel],
) -> str:
    """Grouped digest over the cost policy: tiers, components, coefficient records."""

    return grouped_digest(
        {
            "component_coefficient_kind": dict(COMPONENT_COEFFICIENT_KIND),
            "cost_components": list(COST_COMPONENTS),
            "regulatory_fee_method_id": REGULATORY_FEE_METHOD_ID,
            "registered_commission_schedules": [
                record.to_json_dict() for record in _sorted_by(commission_schedules, "schedule_id")
            ],
            "registered_impact_models": [
                record.to_json_dict() for record in _sorted_by(impact_models, "model_id")
            ],
            "registered_spread_models": [
                record.to_json_dict() for record in _sorted_by(spread_models, "model_id")
            ],
            "required_cost_tiers_bps": list(REQUIRED_COST_TIERS_BPS),
            "tier_cost_formula": FORMULA_TIER_COST,
        }
    )


def _config_digest(
    *,
    lookback: LiquidityLookbackPolicy,
    participation: ParticipationScenario,
) -> str:
    """Grouped digest over the resolved lookback, participation, and definitions."""

    return grouped_digest(
        {
            "adv_definition": ADV_DEFINITION,
            "lookback": lookback.to_json_dict(),
            "participation_scenario": participation.to_json_dict(),
            "point_in_time_cutoff": "SIGNAL_SESSION_AS_KNOWN_AT_SIGNAL_CUTOFF",
        }
    )


def _sorted_by(records: Sequence[Any], id_attr: str) -> list[Any]:
    return sorted(records, key=lambda record: str(getattr(record, id_attr)))


def _sorted_evidence(evidence: Sequence[LiquidityEvidence]) -> list[LiquidityEvidence]:
    return sorted(
        evidence,
        key=lambda item: (item.rebalance_id, item.security_id.encode("utf-8")),
    )


def evaluate_cost_turnover_capacity_scenarios(
    run: ExecutionRun,
    *,
    liquidity_evidence: Sequence[LiquidityEvidence],
    lookback_id: str,
    participation_scenario_id: str,
    lookbacks: Sequence[LiquidityLookbackPolicy] = REGISTERED_LIQUIDITY_LOOKBACKS,
    participation_scenarios: Sequence[ParticipationScenario] = REGISTERED_PARTICIPATION_SCENARIOS,
    commission_schedules: Sequence[CommissionSchedule] = REGISTERED_COMMISSION_SCHEDULES,
    spread_models: Sequence[SpreadModel] = REGISTERED_SPREAD_MODELS,
    impact_models: Sequence[ImpactModel] = REGISTERED_IMPACT_MODELS,
) -> ScenarioReport:
    """Build transparent cost/turnover/liquidity/participation/capacity scenarios.

    With the shipped empty registries this raises ``BLOCKED_NO_REGISTERED_*``
    before it reads a single ledger row. Tests inject ``TEST_CONSTRUCTED``
    records through the ``lookbacks=`` / ``participation_scenarios=`` parameters,
    which the shipped registries forbid.
    """

    if type(run) is not ExecutionRun:
        raise ScenarioError(
            BLOCKED_MALFORMED_EXECUTION_RUN,
            "an execution run is required to derive scenarios",
        )
    lookback = resolve_liquidity_lookback(lookback_id, records=lookbacks)
    participation = resolve_participation_scenario(
        participation_scenario_id, records=participation_scenarios
    )
    assert_components_disjoint()

    known_rebalances = {ledger.rebalance_id for ledger in run.rebalance_ledgers}
    evidence_index: dict[str, dict[str, LiquidityEvidence]] = {}
    for item in _sorted_evidence(liquidity_evidence):
        if item.rebalance_id not in known_rebalances:
            raise ScenarioError(
                BLOCKED_UNKNOWN_LIQUIDITY_EVIDENCE_TARGET,
                f"liquidity evidence names rebalance {item.rebalance_id!r} not in the run",
                rebalance_id=item.rebalance_id,
                security_id=item.security_id,
            )
        per_rebalance = evidence_index.setdefault(item.rebalance_id, {})
        if item.security_id in per_rebalance:
            raise ScenarioError(
                BLOCKED_AMBIGUOUS_REGISTRY_RECORD,
                f"duplicate liquidity evidence for {item.security_id!r}",
                rebalance_id=item.rebalance_id,
                security_id=item.security_id,
            )
        per_rebalance[item.security_id] = item

    lineage = ScenarioLineage(
        input_sha256_grouped=_input_digest(run, evidence=liquidity_evidence),
        cost_policy_sha256_grouped=_cost_policy_digest(
            commission_schedules=commission_schedules,
            spread_models=spread_models,
            impact_models=impact_models,
        ),
        config_sha256_grouped=_config_digest(lookback=lookback, participation=participation),
        code_sha256_grouped=code_binding_digest(),
        schema_sha256_grouped=schema_digest(),
    )

    rebalances = tuple(
        _rebalance_scenario(
            ledger,
            lookback=lookback,
            participation=participation,
            evidence_by_security=evidence_index.get(ledger.rebalance_id, {}),
            commission_schedules=commission_schedules,
            spread_models=spread_models,
            impact_models=impact_models,
            lineage=lineage,
        )
        for ledger in run.rebalance_ledgers
    )

    output_document = {
        "rebalances": [scenario.to_json_dict() for scenario in rebalances],
        "run_manifest_sha256_grouped": run.manifest.self_sha256_grouped,
    }
    manifest = ScenarioManifest(
        kernel_id=KERNEL_ID,
        method_id=METHOD_ID,
        schema_version=SCHEMA_VERSION,
        lookback_id=lookback.lookback_id,
        participation_scenario_id=participation.scenario_id,
        lineage=lineage,
        output_sha256_grouped=grouped_digest(output_document),
    )
    return ScenarioReport(
        kernel_id=KERNEL_ID,
        method_id=METHOD_ID,
        schema_version=SCHEMA_VERSION,
        state=SCENARIO_OK,
        program_id=run.program_id,
        run_manifest_sha256_grouped=run.manifest.self_sha256_grouped,
        lookback_id=lookback.lookback_id,
        participation_scenario_id=participation.scenario_id,
        rebalances=rebalances,
        manifest=manifest,
    )


__all__ = [
    "ADV_DEFINITION",
    "BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV",
    "BLOCKED_DUPLICATE_COST_COMPONENT",
    "BLOCKED_INSUFFICIENT_ADV_HISTORY",
    "BLOCKED_NON_PRIOR_ADV_SESSION",
    "BLOCKED_NO_REGISTERED_COMMISSION_SCHEDULE",
    "BLOCKED_NO_REGISTERED_IMPACT_MODEL",
    "BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK",
    "BLOCKED_NO_REGISTERED_PARTICIPATION_SCENARIO",
    "BLOCKED_NO_REGISTERED_SPREAD_MODEL",
    "CONSUMED_LEDGER_ATTRIBUTE_PATHS",
    "COST_COMPONENTS",
    "CalibratedComponentCost",
    "CommissionSchedule",
    "ComponentCost",
    "ImpactModel",
    "KERNEL_ID",
    "LiquidityEvidence",
    "LiquidityLookbackPolicy",
    "METHOD_ID",
    "NON_CLAIMS",
    "CAPACITY_UNAVAILABLE_NON_POSITIVE_ADV",
    "PARTICIPATION_MEASURED_SCENARIO",
    "PARTICIPATION_UNAVAILABLE_MISSING_ADV",
    "PARTICIPATION_UNAVAILABLE_NON_POSITIVE_ADV",
    "PORTFOLIO_CAPACITY_INCOMPLETE_MISSING_ADV",
    "PORTFOLIO_CAPACITY_INCOMPLETE_NON_POSITIVE_ADV",
    "PORTFOLIO_CAPACITY_SCENARIO_COMPLETE",
    "PORTFOLIO_CAPACITY_ZERO_TRADE",
    "REGISTERED_COMMISSION_SCHEDULES",
    "REGISTERED_IMPACT_MODELS",
    "REGISTERED_LIQUIDITY_LOOKBACKS",
    "REGISTERED_PARTICIPATION_SCENARIOS",
    "REGISTERED_SPREAD_MODELS",
    "REGULATORY_FEE_METHOD_ID",
    "REQUIRED_COST_TIERS_BPS",
    "AdjustedDollarVolumeObservation",
    "RawSessionBar",
    "RebalanceScenario",
    "SCENARIO_FAIL_CLOSED_STATES",
    "SCENARIO_OK",
    "SCHEMA_VERSION",
    "SOURCE_KIND_TEST_CONSTRUCTED",
    "ScenarioError",
    "ScenarioLineage",
    "ScenarioManifest",
    "ScenarioReport",
    "SpreadModel",
    "TradeScenarioRow",
    "UNCALIBRATED_SCENARIO_STATE",
    "UncalibratedScenario",
    "assert_adv_coordinates_non_joinable",
    "assert_components_disjoint",
    "code_binding_digest",
    "compute_adv",
    "component_costs",
    "evaluate_cost_turnover_capacity_scenarios",
    "render_rational",
    "require_calibrated",
    "resolve_commission_schedule",
    "resolve_impact_model",
    "resolve_liquidity_lookback",
    "resolve_participation_scenario",
    "resolve_spread_model",
    "schema_digest",
]
