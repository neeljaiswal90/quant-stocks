"""QME composition ticket A: deterministic target-construction kernel.

``QME-COMPOSITION-TARGET-CONSTRUCTION-KERNEL-V1`` converts a rank-ordered
selected set, prior raw holdings, typed raw fill prices, and opening cash /
receivables into signed INTEGER-quantum share deltas that implement the FROZEN
v0.1 weighting rules of ``configs/quant/qme-v0.1-contract-v2.json`` -- such
that the constructed program satisfies the NEE-129 execution engine's
self-financing walls BY CONSTRUCTION. The acceptance tests prove that claim
two-sidedly: every fixture case's deltas are wrapped in a
``DeclaredSignedDeltas`` program and executed through
:func:`qme.quant.execution_v1.run_execution_program` with the SAME registered
records, and the run must reach ``EXECUTION_OK`` with the kernel's exact deltas
and non-negative closing cash.

Frozen weighting rules implemented here (contract v2 ``weighting``, verbatim)
-----------------------------------------------------------------------------

* ``control_target``: ``EQUAL_WEIGHT``; ``ideal_weight_authority``:
  ``EXACT_RATIONAL``; ``target_weight_rational``: ``1 / K_t``;
  ``decimal_weight_is_display_only``: ``true``; ``decimal_display_formula``:
  ``round_half_even(Decimal(1) / Decimal(K_t), 18)``.
* ``order_quantum``: ``"1"``; ``orders_must_be_integer_quantum_multiples``:
  ``true``; ``fractional_raw_positions_allowed``: ``true``;
  ``raw_position_storage_quantum``: ``"0.00000001"``; ``leverage_allowed``:
  ``false``; ``residual_cash``: ``EXPLICIT_NOT_REDISTRIBUTED``.
* ``trade_universe``: ``UNION_CURRENT_HOLDINGS_AND_SELECTED_SECURITIES``.
* ``selected_target_formula``: ``fractional_residual_i + floor(((pre_trade_nav
  / K_t) - fractional_residual_i * raw_execution_price_i) /
  raw_execution_price_i / order_quantum) * order_quantum``.
* ``unselected_current_holdings_target``:
  ``SELL_INTEGER_ORDERABLE_COMPONENT_CARRY_FRACTIONAL_RESIDUAL``;
  ``fractional_position_residual``:
  ``CARRIED_UNTIL_BOUND_CASH_IN_LIEU_OR_FRACTIONAL_DISPOSITION_HANDLER``.
* ``pre_trade_nav_identity``: ``cash_pre + sum(raw_positions_i *
  common_raw_execution_mark_i) + receivables_pre`` with tolerance
  ``"0.000001"`` -- a caller-declared pre-trade NAV that violates it refuses,
  typed.
* ``cash_formula``: ``cash_pre + sum((raw_positions_i - target_raw_positions_i)
  * common_raw_execution_mark_i) - TC - TAX - supported_withholding - fees``
  with every component FROM THE BOUND NEE-118 FUNCTIONS and
  ``component_rounding``
  ``ROUND_HALF_EVEN_TO_BOUND_NEE_118_INTERNAL_CURRENCY_QUANTUM``;
  ``cost_rate_encoding``: ``INTEGER_BASIS_POINTS_FROM_REGISTERED_COST_POLICY``
  with ``0 <= bps < 10000``.
* ``negative_cash_repair``: ``enabled true``; ``step``
  ``decrement_one_selected_target_order_quantum``; ``choice_order``
  ``[current_target_notional_descending, security_id_utf8_bytes_descending]``;
  ``recompute_tc_tax_withholding_fees_and_rounding_after_each_step true``;
  ``terminal_invariant cash_post >= 0``.

Boundaries
----------

* The selected set is CONSUMED, never recomputed. The kernel takes the ordered
  tuple of security identifiers plus the caller-declared ``K_t`` and refuses a
  mismatch (typed). Selection and breadth logic belong to the signal kernel;
  nothing here imports or reimplements ranking, breadth, or selection.
* Every cash component in the projection comes from the FROZEN NEE-118
  surfaces: :func:`qme.quant.equations.rebalance` computes TC, TAX, and the
  per-fill ROUND_HALF_EVEN quantization; ``TransactionTaxPolicy.assess`` runs
  inside it; :func:`qme.quant.equations.round_long_target_shares` cross-checks
  the floor term; :class:`qme.quant.equations.PortfolioState` computes the
  pre-trade NAV. No cost, tax, or rounding formula is reimplemented here.
* ``supported_withholding`` is projected as zero because a target-construction
  projection contains no dividend-entitlement event: the bound NEE-118 event
  function books withholding only on entitlement events, and none exists in a
  rebalance-only projection. This is structural absence, not an assumed rate.
* ``fees``: only ``EXCLUDED_SYNTHETIC_NON_REGULATORY_SOURCE`` is supported, in
  which the bound engine books zero regulatory fees. The posted historical
  mode is refused (typed) rather than approximated, and -- mirroring the
  engine wall -- a ``regulatory_authority`` cost policy may not select the
  excluded mode.
* When the projected cash is negative, the exact would-be cash is OBSERVED
  through the same frozen kernel rather than recomputed by hand: the
  projection is re-run with the opening cash shifted by a cushion of
  ``3 * gross_trade_notional + 1``. Every per-fill amount in
  ``qme.quant.equations.rebalance`` is independent of the running cash level,
  so the shifted run's closing cash minus the cushion IS the refused run's
  exact closing cash. The accept/reject decision always comes from the
  UNSHIFTED call, so the kernel accepts exactly what the engine accepts.

Owner-gated values ship EMPTY
-----------------------------

The cost rate, participation limit, ledger coordinate source, withholding,
deferral, spread, residual-cash, and unsupported-event registries are the
NEE-129 engine's own (:class:`qme.quant.execution_v1.RegistryOverrides` --
shipped EMPTY there; ``TEST_CONSTRUCTED`` records may be injected and can
never ship). This kernel adds exactly ONE registry of its own, because the
contract names a handler that does not exist yet:
:data:`REGISTERED_FRACTIONAL_DISPOSITION_HANDLERS` ships ``()`` and a request
that names a handler fails closed as
:data:`BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER`, following the
registry pattern of :mod:`qme.data.alpha_vantage.plan_v1` and
:mod:`qme.data.stores.riskfree_v1`. No threshold, coefficient, or schedule
value is invented anywhere in this module.

Numerics
--------

Exact arithmetic only: canonical decimal strings in, ``Decimal`` /
``Fraction`` throughout, ``ROUND_HALF_EVEN`` at the bound NEE-118 internal
currency quantum, and no binary float anywhere. Output rows are frozen
dataclasses serialized as canonical JSON with a grouped (eight 8-hex groups)
SHA-256 self-hash and full input / config / code / schema lineage.

Non-claims
----------

This kernel claims no production deployment, no prospective consumption, no
empirical performance, no alpha, no capacity value, no production readiness,
and no live-order authority. ``qme.quant.execution_v1.NON_CLAIMS`` is copied
into every result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from qme.foundation.lineage import canonical_json_bytes
from qme.quant.equations import (
    DECIMAL_PRECISION,
    EQUATION_SPEC_ID,
    ExternalFlowNotSupported,
    PortfolioState,
    RawExecutionPrice,
    RawMark,
    RebalanceResult,
    Trade,
    TransactionTaxPolicy,
    rebalance,
    round_long_target_shares,
)
from qme.quant.execution_v1 import (
    BLOCKED_MISSING_BOUND_ARTIFACT,
    BLOCKED_NO_REGISTERED_COST_RATE_POLICY,
    BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE,
    FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY,
    NON_CLAIMS,
    REGISTERED_FILL_ORDER,
    REGISTERED_NEGATIVE_CASH_REPAIR_CHOICE_ORDER,
    REGISTERED_NEGATIVE_CASH_REPAIR_STEP,
    REGISTERED_SOURCE_KINDS,
    REGISTERED_STABLE_KEY_ORDER,
    ExecutionAccountingError,
    LineageBinding,
    RegistryOverrides,
    SignedTargetDelta,
    _assert_raw_ledger_evidence,
    _format_exact_fraction,
    _identifier,
    _provenance,
    _sorted_symbols,
    format_ledger,
    group_sha256,
    grouped_document_digest,
    grouped_file_digest,
    order_fills,
    require_ledger_price_quantum,
    require_share_quantum,
    resolve_cost_rate_policy,
    resolve_ledger_coordinate_source,
    to_ledger_decimal,
)
from qme.quant.execution_v1 import (
    ENGINE_ID as EXECUTION_ENGINE_ID,
)
from qme.quant.execution_v1 import (
    SCHEMA_VERSION as EXECUTION_SCHEMA_VERSION,
)
from qme.quant.execution_v1 import (
    BoundArtifact as ExecutionBoundArtifact,
)

# ---------------------------------------------------------------------------
# Kernel identity and frozen contract vocabulary
# ---------------------------------------------------------------------------

KERNEL_ID: Final = "QME-COMPOSITION-TARGET-CONSTRUCTION-KERNEL-V1"
SCHEMA_VERSION: Final = "qme.target_construction.v1"
#: Composition ticket A under gate NEE-108, lead plan 2026-08-25. The owner has
#: not yet assigned a Linear identifier; none is invented here.
TICKET_ID: Final = "PENDING_OWNER_ASSIGNMENT"

#: Contract v2 ``weighting`` strings, verbatim. Quoted so the emitted artifact
#: names the exact frozen rule it implements; none of these is invented here.
CONTROL_TARGET: Final = "EQUAL_WEIGHT"
IDEAL_WEIGHT_AUTHORITY: Final = "EXACT_RATIONAL"
DECIMAL_DISPLAY_FORMULA: Final = "round_half_even(Decimal(1) / Decimal(K_t), 18)"
TRADE_UNIVERSE_RULE: Final = "UNION_CURRENT_HOLDINGS_AND_SELECTED_SECURITIES"
SELECTED_TARGET_FORMULA: Final = (
    "fractional_residual_i + floor(((pre_trade_nav / K_t) - fractional_residual_i "
    "* raw_execution_price_i) / raw_execution_price_i / order_quantum) "
    "* order_quantum"
)
UNSELECTED_CURRENT_HOLDINGS_TARGET: Final = (
    "SELL_INTEGER_ORDERABLE_COMPONENT_CARRY_FRACTIONAL_RESIDUAL"
)
FRACTIONAL_POSITION_RESIDUAL_RULE: Final = (
    "CARRIED_UNTIL_BOUND_CASH_IN_LIEU_OR_FRACTIONAL_DISPOSITION_HANDLER"
)
PRE_TRADE_NAV_IDENTITY: Final = (
    "cash_pre + sum(raw_positions_i * common_raw_execution_mark_i) + receivables_pre"
)
PRE_TRADE_NAV_IDENTITY_TOLERANCE: Final = "0.000001"
CASH_FORMULA: Final = (
    "cash_pre + sum((raw_positions_i - target_raw_positions_i) "
    "* common_raw_execution_mark_i) - TC - TAX - supported_withholding - fees"
)
CASH_COMPONENTS: Final[tuple[str, ...]] = (
    "TC_FROM_BOUND_NEE_118_COST_FUNCTION",
    "TAX_FROM_BOUND_NEE_118_TAX_FUNCTION",
    "SUPPORTED_WITHHOLDING_FROM_BOUND_NEE_118_EVENT_FUNCTION",
    "FEES_FROM_BOUND_NEE_118_FEE_FUNCTION",
)
COMPONENT_ROUNDING: Final = "ROUND_HALF_EVEN_TO_BOUND_NEE_118_INTERNAL_CURRENCY_QUANTUM"
COST_RATE_ENCODING: Final = "INTEGER_BASIS_POINTS_FROM_REGISTERED_COST_POLICY"
RESIDUAL_CASH_RULE: Final = "EXPLICIT_NOT_REDISTRIBUTED"
ORDER_QUANTUM_TEXT: Final = "1"
RAW_POSITION_STORAGE_QUANTUM_TEXT: Final = "0.00000001"
NEGATIVE_CASH_REPAIR_TERMINAL_INVARIANT: Final = "cash_post >= 0"

#: ``numeric_policy.decimal_precision_digits`` from contract v2. Used only for
#: the display-only decimal weight; ledger arithmetic runs at the frozen
#: NEE-118 precision (:data:`qme.quant.equations.DECIMAL_PRECISION`).
CONTRACT_DECIMAL_PRECISION: Final = 50
WEIGHT_ARTIFACT_SCALE: Final = 18

MEMBERSHIP_SELECTED: Final = "SELECTED"
MEMBERSHIP_UNSELECTED_HOLDING: Final = "UNSELECTED_HOLDING"
ENGINE_PROJECTION_ACCEPTED: Final = "ENGINE_ACCEPTED"
ENGINE_PROJECTION_REFUSED_NEGATIVE_CASH: Final = "ENGINE_REFUSED_NEGATIVE_CASH"
SUPPORTED_WITHHOLDING_BASIS: Final = (
    "ZERO_BY_STRUCTURAL_ABSENCE_NO_DIVIDEND_ENTITLEMENT_EVENT_IN_PROJECTION"
)
FEES_BASIS: Final = "ZERO_BY_BOUND_ENGINE_EXCLUDED_SYNTHETIC_NON_REGULATORY_MODE"

_ZERO: Final = Decimal(0)
_EXACT_ZERO: Final = Fraction(0)
_DISPLAY_WEIGHT_QUANTUM: Final = Decimal("1E-18")

# ---------------------------------------------------------------------------
# Typed fail-closed states
# ---------------------------------------------------------------------------

TARGET_CONSTRUCTION_OK: Final = "TARGET_CONSTRUCTION_OK"

#: Kernel-native refusals. ``INVALID_ZERO_SELECTION_SIZE``,
#: ``INVALID_DUPLICATE_SECURITY_ID``, ``INVALID_WEIGHTING_INPUT``, and
#: ``INVALID_NEGATIVE_POST_TRADE_CASH`` mirror the contract-v2
#: ``fail_closed_states`` vocabulary verbatim.
INVALID_WEIGHTING_INPUT: Final = "INVALID_WEIGHTING_INPUT"
INVALID_DUPLICATE_SECURITY_ID: Final = "INVALID_DUPLICATE_SECURITY_ID"
INVALID_SELECTION_COUNT_MISMATCH: Final = "INVALID_SELECTION_COUNT_MISMATCH"
INVALID_ZERO_SELECTION_SIZE: Final = "INVALID_ZERO_SELECTION_SIZE"
INVALID_PRE_TRADE_NAV_IDENTITY: Final = "INVALID_PRE_TRADE_NAV_IDENTITY"
INVALID_NEGATIVE_LONG_ONLY_TARGET: Final = "INVALID_NEGATIVE_LONG_ONLY_TARGET"
INVALID_NEGATIVE_POST_TRADE_CASH: Final = "INVALID_NEGATIVE_POST_TRADE_CASH"
BLOCKED_REPAIR_ITERATION_CEILING: Final = "BLOCKED_REPAIR_ITERATION_CEILING"
BLOCKED_UNSUPPORTED_REGULATORY_FEE_MODE: Final = "BLOCKED_UNSUPPORTED_REGULATORY_FEE_MODE"
BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER: Final = (
    "BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER"
)
BLOCKED_BOUND_KERNEL_REFUSAL: Final = "BLOCKED_BOUND_KERNEL_REFUSAL"

#: Every state this kernel can refuse with. The three engine-named states pass
#: through from the bound NEE-129 registries and artifact binder with their
#: registered names so a caller sees the same vocabulary either way.
TARGET_CONSTRUCTION_FAIL_CLOSED_STATES: Final[tuple[str, ...]] = (
    BLOCKED_BOUND_KERNEL_REFUSAL,
    BLOCKED_MISSING_BOUND_ARTIFACT,
    BLOCKED_NO_REGISTERED_COST_RATE_POLICY,
    BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER,
    BLOCKED_REPAIR_ITERATION_CEILING,
    BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE,
    BLOCKED_UNSUPPORTED_REGULATORY_FEE_MODE,
    INVALID_DUPLICATE_SECURITY_ID,
    INVALID_NEGATIVE_LONG_ONLY_TARGET,
    INVALID_NEGATIVE_POST_TRADE_CASH,
    INVALID_PRE_TRADE_NAV_IDENTITY,
    INVALID_SELECTION_COUNT_MISMATCH,
    INVALID_WEIGHTING_INPUT,
    INVALID_ZERO_SELECTION_SIZE,
)

#: Engine states that keep their registered names when they surface here.
_PASS_THROUGH_ENGINE_STATES: Final[frozenset[str]] = frozenset(
    {
        BLOCKED_MISSING_BOUND_ARTIFACT,
        BLOCKED_NO_REGISTERED_COST_RATE_POLICY,
        BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE,
    }
)


def validate_fail_closed_states() -> None:
    """Completeness assertion for the typed state vocabulary (runs at import)."""

    states = TARGET_CONSTRUCTION_FAIL_CLOSED_STATES
    if len(states) != len(set(states)):
        raise AssertionError("fail-closed states must be unique")
    if list(states) != sorted(states):
        raise AssertionError("fail-closed states must be declared in sorted order")
    if TARGET_CONSTRUCTION_OK in states:
        raise AssertionError("the OK state is not a fail-closed state")
    if not _PASS_THROUGH_ENGINE_STATES.issubset(states):
        raise AssertionError("every pass-through engine state must be declared")


class TargetConstructionError(ValueError):
    """A typed fail-closed refusal carrying the state and the affected identity."""

    def __init__(
        self,
        state: str,
        message: str,
        *,
        security_id: str | None = None,
    ) -> None:
        if state not in TARGET_CONSTRUCTION_FAIL_CLOSED_STATES:
            raise AssertionError(f"undeclared fail-closed state {state!r}")
        super().__init__(f"{state}: {message}")
        self.state = state
        self.security_id = security_id

    def to_json_dict(self) -> dict[str, str | None]:
        return {"security_id": self.security_id, "state": self.state}


def _translated(
    exc: ExecutionAccountingError, *, fallback: str
) -> TargetConstructionError:
    """Carry a bound-surface refusal forward with its registered state name
    when that name is one this kernel declares, else under ``fallback``."""

    state = exc.state if exc.state in _PASS_THROUGH_ENGINE_STATES else fallback
    return TargetConstructionError(state, str(exc), security_id=exc.security_id)


# ---------------------------------------------------------------------------
# The one owner-gated registry this kernel adds -- EMPTY BY DESIGN
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FractionalDispositionHandler:
    """A registered handler for disposing carried fractional residuals.

    Contract v2 carries ``fractional_position_residual:
    CARRIED_UNTIL_BOUND_CASH_IN_LIEU_OR_FRACTIONAL_DISPOSITION_HANDLER``. No
    handler is registered anywhere, so carrying is the only registered
    behavior; a request that names a handler fails closed rather than having
    one invented. The record follows the provenance-quintet pattern of
    :mod:`qme.data.alpha_vantage.plan_v1` and
    :mod:`qme.data.stores.riskfree_v1`.
    """

    handler_id: str
    source_kind: str
    source: str
    source_reference: str
    effective_date: date
    disposition: str

    def __post_init__(self) -> None:
        _provenance(
            record_id=self.handler_id,
            source_kind=self.source_kind,
            source=self.source,
            source_reference=self.source_reference,
            effective_date=self.effective_date,
        )
        _identifier(self.disposition, what="disposition")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "effective_date": self.effective_date.isoformat(),
            "handler_id": self.handler_id,
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
        }


#: EMPTY BY DESIGN. The owner has registered no cash-in-lieu or fractional
#: disposition handler; residuals are CARRIED, never disposed.
REGISTERED_FRACTIONAL_DISPOSITION_HANDLERS: Final[
    tuple[FractionalDispositionHandler, ...]
] = ()

_FRACTIONAL_DISPOSITION_EMPTY_MESSAGE: Final = (
    "no fractional-disposition handler is registered; contract v2 carries "
    "fractional residuals until a bound cash-in-lieu or disposition handler "
    "exists, and this kernel refuses to invent one"
)


def validate_fractional_disposition_registry(
    records: Sequence[FractionalDispositionHandler] = (
        REGISTERED_FRACTIONAL_DISPOSITION_HANDLERS
    ),
) -> None:
    """Fail closed on an empty, duplicated, or test-contaminated registry."""

    if not records:
        raise TargetConstructionError(
            BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER,
            _FRACTIONAL_DISPOSITION_EMPTY_MESSAGE,
        )
    seen: set[str] = set()
    for record in records:
        if type(record) is not FractionalDispositionHandler:
            raise TargetConstructionError(
                BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER,
                "registry entries must be FractionalDispositionHandler records",
            )
        if record.handler_id in seen:
            raise TargetConstructionError(
                BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER,
                f"duplicate registry id: {record.handler_id}",
            )
        seen.add(record.handler_id)
        if (
            records is REGISTERED_FRACTIONAL_DISPOSITION_HANDLERS
            and record.source_kind not in REGISTERED_SOURCE_KINDS
        ):
            raise TargetConstructionError(
                BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER,
                f"{record.handler_id}: {record.source_kind} may not ship in the registry",
            )


def resolve_fractional_disposition_handler(
    handler_id: str,
    *,
    records: Sequence[FractionalDispositionHandler] = (
        REGISTERED_FRACTIONAL_DISPOSITION_HANDLERS
    ),
) -> FractionalDispositionHandler:
    """Return the registered handler, or fail closed. Never invents one."""

    validate_fractional_disposition_registry(records)
    matches = [record for record in records if record.handler_id == handler_id]
    if len(matches) != 1:
        raise TargetConstructionError(
            BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER,
            f"{handler_id!r} is not registered in this registry",
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Bound artifacts and lineage
# ---------------------------------------------------------------------------

#: ``(role, repository-relative path, kernel identity)`` for every artifact
#: this kernel binds -- each a kernel or config it calls or reads, never its
#: own source file (T1 forbids self-pinning; kernel identity is carried by the
#: declared ``KERNEL_ID`` literal). Digests are OBSERVED at run time.
TARGET_BOUND_ARTIFACT_ROLES: Final[tuple[tuple[str, str, str], ...]] = (
    ("NEE_118_ACCOUNTING_CONFIG", "configs/quant/accounting-equations-v1.json",
     EQUATION_SPEC_ID),
    ("NEE_118_EQUATIONS_KERNEL", "qme/quant/equations.py", EQUATION_SPEC_ID),
    ("NEE_119_QUANTITATIVE_CONTRACT_V2", "configs/quant/qme-v0.1-contract-v2.json",
     "qme-long-only-momentum-v0.1"),
    ("NEE_129_EXECUTION_ENGINE", "qme/quant/execution_v1.py", EXECUTION_ENGINE_ID),
)

#: Where each bound surface is actually called from, so a reviewer can audit
#: the reuse claim without reading the whole module.
TARGET_KERNEL_CALL_SITES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "qme.quant.equations.PortfolioState": (
            "construct_targets (pre-trade NAV identity via the frozen .nav)",
            "_project (cushion-shifted observation state)",
        ),
        "qme.quant.equations.Trade": ("_project (typed fill construction)",),
        "qme.quant.equations.TransactionTaxPolicy.assess": (
            "inside qme.quant.equations.rebalance; never called around it",
        ),
        "qme.quant.equations.rebalance": (
            "_project (TC, TAX, per-fill rounding, and cash -- true call and "
            "cushion-shifted observation call)",
        ),
        "qme.quant.equations.round_long_target_shares": (
            "_selected_target (frozen NEE-118 floor cross-check)",
        ),
        "qme.quant.execution_v1.SignedTargetDelta": ("_deltas (typed delta wall)",),
        "qme.quant.execution_v1.order_fills": (
            "_project (registered ALL_SELLS_THEN_ALL_BUYS fill order)",
        ),
        "qme.quant.execution_v1.require_ledger_price_quantum": (
            "TargetConstructionRequest.__post_init__",
        ),
        "qme.quant.execution_v1.require_share_quantum": (
            "TargetConstructionRequest.__post_init__",
        ),
        "qme.quant.execution_v1.resolve_cost_rate_policy": ("construct_targets",),
        "qme.quant.execution_v1.resolve_ledger_coordinate_source": (
            "construct_targets",
        ),
    }
)


@dataclass(frozen=True)
class TargetKernelBindingSet:
    """Every artifact this kernel binds, with its observed grouped digest."""

    artifacts: tuple[ExecutionBoundArtifact, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "artifacts": [artifact.to_json_dict() for artifact in self.artifacts],
            "call_sites": {
                key: list(value) for key, value in sorted(TARGET_KERNEL_CALL_SITES.items())
            },
        }

    @property
    def config_sha256_grouped(self) -> str:
        return grouped_document_digest(self.to_json_dict())

    @property
    def code_sha256_grouped(self) -> str:
        """Grouped digest over the code identity of this run: the OBSERVED
        source digests of every bound ``.py`` kernel plus the declared
        identifier set. This module's own bytes are deliberately excluded --
        its identity is the declared :data:`KERNEL_ID` -- so no self-pinning
        occurs outside T0."""

        document = {
            "called_kernel_digests": {
                artifact.role: artifact.sha256_grouped
                for artifact in self.artifacts
                if artifact.path.endswith(".py")
            },
            "equation_spec_id": EQUATION_SPEC_ID,
            "execution_engine_id": EXECUTION_ENGINE_ID,
            "execution_schema_version": EXECUTION_SCHEMA_VERSION,
            "kernel_id": KERNEL_ID,
            "schema_version": SCHEMA_VERSION,
        }
        return grouped_document_digest(document)


def bind_target_kernels(repository_root: Path) -> TargetKernelBindingSet:
    """Observe the grouped digest of every bound artifact; a missing file blocks."""

    if type(repository_root) is not type(Path()):
        raise TargetConstructionError(
            BLOCKED_MISSING_BOUND_ARTIFACT, "repository_root must be an exact pathlib.Path"
        )
    artifacts: list[ExecutionBoundArtifact] = []
    for role, path, identity in TARGET_BOUND_ARTIFACT_ROLES:
        try:
            digest = grouped_file_digest(repository_root / path)
        except ExecutionAccountingError as exc:
            raise _translated(exc, fallback=BLOCKED_MISSING_BOUND_ARTIFACT) from exc
        artifacts.append(
            ExecutionBoundArtifact(
                role=role, path=path, kernel_identity=identity, sha256_grouped=digest
            )
        )
    return TargetKernelBindingSet(artifacts=tuple(artifacts))


#: The emitted row shapes, in declared field order; the grouped digest over
#: this descriptor is the run's ``schema_sha256_grouped`` (this lane may not
#: add a file under ``schemas/**``).
TARGET_OUTPUT_SCHEMA_DESCRIPTOR: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "repair_step": (
            "step_index", "security_id", "target_before_raw_shares",
            "target_after_raw_shares", "recomputed_transaction_cost",
            "recomputed_transaction_tax", "recomputed_supported_withholding",
            "recomputed_fees", "recomputed_cash_post",
            "recomputed_gross_buy_notional", "engine_projection",
        ),
        "result": (
            "schema_version", "kernel_id", "ticket_id", "request_id", "state",
            "selection", "rows", "totals", "repair", "weighting_rules",
            "bound_artifacts", "lineage", "claims",
        ),
        "target_row": (
            "security_id", "membership", "prior_raw_shares",
            "target_weight_rational", "target_weight_decimal_display",
            "fractional_residual_in", "target_raw_shares",
            "signed_delta_raw_shares", "fractional_residual_out",
            "repair_decrements", "raw_execution_price", "lineage",
        ),
        "totals": (
            "selection_count_k_t", "pre_trade_nav", "declared_pre_trade_nav",
            "pre_trade_nav_identity_tolerance", "projected_transaction_cost",
            "projected_transaction_tax", "projected_supported_withholding",
            "projected_fees", "projected_cash_post", "initial_projected_cash_post",
            "projected_gross_buy_notional", "projected_gross_sell_notional",
            "repair_steps_total",
        ),
    }
)


def target_output_schema_digest() -> str:
    """Grouped digest over the declared output schema descriptor."""

    return grouped_document_digest(
        {
            "rows": {key: list(value) for key, value in TARGET_OUTPUT_SCHEMA_DESCRIPTOR.items()},
            "schema_version": SCHEMA_VERSION,
        }
    )


def _regrouped(digest: str) -> str:
    """Render a contiguous 64-hex digest in the grouped colon form."""

    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetConstructionRequest:
    """The complete declared input for one target construction.

    ``selected`` is the CONSUMED rank-ordered selected set;
    ``declared_selection_count`` is the caller's ``K_t`` and must equal
    ``len(selected)``. ``raw_execution_prices`` are the typed raw fill prices
    and serve as the ``common_raw_execution_mark_i`` of the frozen NAV
    identity and cash formula (the registered coordinate has execution price
    equal to the common mark). ``declared_pre_trade_nav`` is checked against
    the identity computed by the frozen NEE-118 ``PortfolioState.nav`` and
    refused beyond the registered tolerance; the identity value -- never the
    declaration -- drives the target formula.
    """

    request_id: str
    selected: tuple[str, ...]
    declared_selection_count: int
    prior_positions: Mapping[str, str]
    raw_execution_prices: Mapping[str, RawExecutionPrice]
    cash_pre: str
    receivables_pre: str
    declared_pre_trade_nav: str
    cost_policy_id: str
    transaction_tax_policy: TransactionTaxPolicy
    regulatory_fee_mode: str = FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY
    registries: RegistryOverrides = RegistryOverrides()
    fractional_disposition_handler_id: str | None = None
    order_quantum: str = ORDER_QUANTUM_TEXT

    def __post_init__(self) -> None:
        try:
            _identifier(self.request_id, what="request_id")
            _identifier(self.cost_policy_id, what="cost_policy_id")
        except ExecutionAccountingError as exc:
            raise _translated(exc, fallback=INVALID_WEIGHTING_INPUT) from exc
        if type(self.selected) is not tuple:
            raise TargetConstructionError(
                INVALID_WEIGHTING_INPUT, "selected must be a tuple of security ids"
            )
        seen: set[str] = set()
        for raw_symbol in self.selected:
            try:
                security_id = _identifier(raw_symbol, what="selected security_id")
            except ExecutionAccountingError as exc:
                raise _translated(exc, fallback=INVALID_WEIGHTING_INPUT) from exc
            if security_id in seen:
                raise TargetConstructionError(
                    INVALID_DUPLICATE_SECURITY_ID,
                    "a security may appear once in the selected set",
                    security_id=security_id,
                )
            seen.add(security_id)
        count = self.declared_selection_count
        if type(count) is not int:
            raise TargetConstructionError(
                INVALID_WEIGHTING_INPUT, "declared_selection_count must be an exact int"
            )
        if count != len(self.selected):
            raise TargetConstructionError(
                INVALID_SELECTION_COUNT_MISMATCH,
                f"declared K_t {count} does not equal the {len(self.selected)} "
                "consumed selected securities",
            )
        if count == 0:
            raise TargetConstructionError(
                INVALID_ZERO_SELECTION_SIZE,
                "a zero selection size is INVALID_ZERO_SELECTION_SIZE under the "
                "frozen contract; there is no equal-weight target over zero names",
            )
        if to_ledger_decimal(self.order_quantum, what="order_quantum") != Decimal(
            ORDER_QUANTUM_TEXT
        ):
            raise TargetConstructionError(
                INVALID_WEIGHTING_INPUT,
                "order_quantum must remain the frozen NEE-119 one-share quantum",
            )
        if not isinstance(self.prior_positions, Mapping):
            raise TargetConstructionError(
                INVALID_WEIGHTING_INPUT, "prior_positions must be a mapping"
            )
        positions: dict[str, str] = {}
        for raw_symbol, quantity in self.prior_positions.items():
            try:
                security_id = _identifier(raw_symbol, what="prior position security_id")
            except ExecutionAccountingError as exc:
                raise _translated(exc, fallback=INVALID_WEIGHTING_INPUT) from exc
            if security_id in positions:
                raise TargetConstructionError(
                    INVALID_DUPLICATE_SECURITY_ID,
                    "prior positions contain a duplicate security_id",
                    security_id=security_id,
                )
            if type(quantity) is not str:
                raise TargetConstructionError(
                    INVALID_WEIGHTING_INPUT,
                    "prior positions must be canonical decimal strings",
                    security_id=security_id,
                )
            try:
                value = to_ledger_decimal(
                    quantity, what=f"prior_positions[{security_id}]"
                )
                require_share_quantum(
                    value,
                    what=f"prior_positions[{security_id}]",
                    security_id=security_id,
                )
            except ExecutionAccountingError as exc:
                raise _translated(exc, fallback=INVALID_WEIGHTING_INPUT) from exc
            if value < 0:
                raise TargetConstructionError(
                    INVALID_WEIGHTING_INPUT,
                    "leverage_allowed is false and the book is long-only; a "
                    "negative prior position is refused",
                    security_id=security_id,
                )
            positions[security_id] = format_ledger(
                value, what=f"prior_positions[{security_id}]"
            )
        object.__setattr__(self, "prior_positions", MappingProxyType(positions))
        if not isinstance(self.raw_execution_prices, Mapping):
            raise TargetConstructionError(
                INVALID_WEIGHTING_INPUT, "raw_execution_prices must be a mapping"
            )
        prices: dict[str, RawExecutionPrice] = {}
        for raw_symbol, price in self.raw_execution_prices.items():
            try:
                security_id = _identifier(raw_symbol, what="price security_id")
            except ExecutionAccountingError as exc:
                raise _translated(exc, fallback=INVALID_WEIGHTING_INPUT) from exc
            if type(price) is not RawExecutionPrice:
                raise TargetConstructionError(
                    INVALID_WEIGHTING_INPUT,
                    "a raw fill price admits the frozen RawExecutionPrice and "
                    "nothing else",
                    security_id=security_id,
                )
            if price.evidence.security_id != security_id:
                raise TargetConstructionError(
                    INVALID_WEIGHTING_INPUT,
                    "execution-price evidence security_id does not match the "
                    "mapping symbol",
                    security_id=security_id,
                )
            try:
                _assert_raw_ledger_evidence(
                    price, traded=security_id, what="raw execution price"
                )
                require_ledger_price_quantum(
                    price.value,
                    what=f"raw_execution_prices[{security_id}]",
                    security_id=security_id,
                )
            except ExecutionAccountingError as exc:
                raise _translated(exc, fallback=INVALID_WEIGHTING_INPUT) from exc
            prices[security_id] = price
        object.__setattr__(self, "raw_execution_prices", MappingProxyType(prices))
        for label, text in (
            ("cash_pre", self.cash_pre),
            ("receivables_pre", self.receivables_pre),
            ("declared_pre_trade_nav", self.declared_pre_trade_nav),
        ):
            if type(text) is not str:
                raise TargetConstructionError(
                    INVALID_WEIGHTING_INPUT,
                    f"{label} must be a canonical decimal string",
                )
            try:
                to_ledger_decimal(text, what=label)
            except ExecutionAccountingError as exc:
                raise _translated(exc, fallback=INVALID_WEIGHTING_INPUT) from exc
        if type(self.transaction_tax_policy) is not TransactionTaxPolicy:
            raise TargetConstructionError(
                INVALID_WEIGHTING_INPUT,
                "a request requires an explicit supported TransactionTaxPolicy",
            )
        if type(self.registries) is not RegistryOverrides:
            raise TargetConstructionError(
                INVALID_WEIGHTING_INPUT, "registries must be a RegistryOverrides record"
            )
        if self.fractional_disposition_handler_id is not None:
            try:
                _identifier(
                    self.fractional_disposition_handler_id,
                    what="fractional_disposition_handler_id",
                )
            except ExecutionAccountingError as exc:
                raise _translated(exc, fallback=INVALID_WEIGHTING_INPUT) from exc

    @property
    def trade_universe(self) -> tuple[str, ...]:
        """UNION_CURRENT_HOLDINGS_AND_SELECTED_SECURITIES, UTF-8 bytes ascending."""

        return _sorted_symbols(tuple(set(self.selected) | set(self.prior_positions)))

    def to_input_document(self) -> dict[str, Any]:
        """The declared input document whose grouped digest is the input hash.

        The registered control is EQUAL_WEIGHT, under which rank order carries
        no weighting information, so the consumed selected set is serialized
        in the registered stable-key order and permuted inputs are
        byte-identical.
        """

        evidence_rows: dict[str, Any] = {}
        for security_id in _sorted_symbols(tuple(self.raw_execution_prices)):
            price = self.raw_execution_prices[security_id]
            evidence = price.evidence
            evidence_rows[security_id] = {
                "calendar_id": evidence.calendar_id,
                "calendar_sha256_grouped": _regrouped(evidence.calendar_sha256),
                "observation_end_session": evidence.observation_end_session.isoformat(),
                "observation_start_session": (
                    evidence.observation_start_session.isoformat()
                ),
                "snapshot_id": evidence.snapshot_id,
                "snapshot_sha256_grouped": _regrouped(evidence.snapshot_sha256),
                "source_id": evidence.source_id,
                "value": format_ledger(price.value, what="raw_execution_price"),
            }
        return {
            "cash_pre": format_ledger(self.cash_pre, what="cash_pre"),
            "cost_policy_id": self.cost_policy_id,
            "declared_pre_trade_nav": format_ledger(
                self.declared_pre_trade_nav, what="declared_pre_trade_nav"
            ),
            "declared_selection_count": self.declared_selection_count,
            "fractional_disposition_handler_id": self.fractional_disposition_handler_id,
            "kernel_id": KERNEL_ID,
            "order_quantum": ORDER_QUANTUM_TEXT,
            "prior_positions": {
                security_id: self.prior_positions[security_id]
                for security_id in _sorted_symbols(tuple(self.prior_positions))
            },
            "raw_execution_prices": evidence_rows,
            "receivables_pre": format_ledger(
                self.receivables_pre, what="receivables_pre"
            ),
            "registries": self.registries.to_json_dict(),
            "regulatory_fee_mode": self.regulatory_fee_mode,
            "request_id": self.request_id,
            "schema_version": SCHEMA_VERSION,
            "selected": list(_sorted_symbols(self.selected)),
            "transaction_tax_policy": {
                "assessment_base": self.transaction_tax_policy.assessment_base,
                "assessment_side": str(self.transaction_tax_policy.assessment_side),
                "policy_id": self.transaction_tax_policy.policy_id,
                "rate_bps": format_ledger(
                    self.transaction_tax_policy.rate_bps, what="transaction_tax_rate_bps"
                ),
                "source_id": self.transaction_tax_policy.source_id,
            },
        }

    def input_digest(self) -> str:
        return grouped_document_digest(self.to_input_document())


# ---------------------------------------------------------------------------
# Emitted records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetConstructionRow:
    """One trade-universe security with its constructed target and delta."""

    security_id: str
    membership: str
    prior_raw_shares: str
    target_weight_rational: Mapping[str, str] | None
    target_weight_decimal_display: str | None
    fractional_residual_in: str
    target_raw_shares: str
    signed_delta_raw_shares: str
    fractional_residual_out: str
    repair_decrements: int
    raw_execution_price: str
    lineage: LineageBinding

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "fractional_residual_in": self.fractional_residual_in,
            "fractional_residual_out": self.fractional_residual_out,
            "lineage": self.lineage.to_json_dict(),
            "membership": self.membership,
            "prior_raw_shares": self.prior_raw_shares,
            "raw_execution_price": self.raw_execution_price,
            "repair_decrements": self.repair_decrements,
            "security_id": self.security_id,
            "signed_delta_raw_shares": self.signed_delta_raw_shares,
            "target_raw_shares": self.target_raw_shares,
            "target_weight_decimal_display": self.target_weight_decimal_display,
            "target_weight_rational": (
                None
                if self.target_weight_rational is None
                else dict(self.target_weight_rational)
            ),
        }


@dataclass(frozen=True)
class RepairStep:
    """One registered repair decrement with the recomputed projection after it."""

    step_index: int
    security_id: str
    target_before_raw_shares: str
    target_after_raw_shares: str
    recomputed_transaction_cost: str
    recomputed_transaction_tax: str
    recomputed_supported_withholding: str
    recomputed_fees: str
    recomputed_cash_post: str
    recomputed_gross_buy_notional: str
    engine_projection: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "engine_projection": self.engine_projection,
            "recomputed_cash_post": self.recomputed_cash_post,
            "recomputed_fees": self.recomputed_fees,
            "recomputed_gross_buy_notional": self.recomputed_gross_buy_notional,
            "recomputed_supported_withholding": self.recomputed_supported_withholding,
            "recomputed_transaction_cost": self.recomputed_transaction_cost,
            "recomputed_transaction_tax": self.recomputed_transaction_tax,
            "security_id": self.security_id,
            "step_index": self.step_index,
            "target_after_raw_shares": self.target_after_raw_shares,
            "target_before_raw_shares": self.target_before_raw_shares,
        }


@dataclass(frozen=True)
class TargetConstructionTotals:
    """Portfolio-level identities and projected cash components."""

    selection_count_k_t: int
    pre_trade_nav: str
    declared_pre_trade_nav: str
    pre_trade_nav_identity_tolerance: str
    projected_transaction_cost: str
    projected_transaction_tax: str
    projected_supported_withholding: str
    projected_fees: str
    projected_cash_post: str
    initial_projected_cash_post: str
    projected_gross_buy_notional: str
    projected_gross_sell_notional: str
    repair_steps_total: int

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "declared_pre_trade_nav": self.declared_pre_trade_nav,
            "initial_projected_cash_post": self.initial_projected_cash_post,
            "pre_trade_nav": self.pre_trade_nav,
            "pre_trade_nav_identity_tolerance": self.pre_trade_nav_identity_tolerance,
            "projected_cash_post": self.projected_cash_post,
            "projected_fees": self.projected_fees,
            "projected_gross_buy_notional": self.projected_gross_buy_notional,
            "projected_gross_sell_notional": self.projected_gross_sell_notional,
            "projected_supported_withholding": self.projected_supported_withholding,
            "projected_transaction_cost": self.projected_transaction_cost,
            "projected_transaction_tax": self.projected_transaction_tax,
            "repair_steps_total": self.repair_steps_total,
            "selection_count_k_t": self.selection_count_k_t,
        }


@dataclass(frozen=True)
class TargetConstructionResult:
    """Every artifact one target construction produced."""

    request_id: str
    state: str
    selected: tuple[str, ...]
    rows: tuple[TargetConstructionRow, ...]
    totals: TargetConstructionTotals
    repair_steps: tuple[RepairStep, ...]
    repair_iteration_ceiling: int
    target_weight_rational: Mapping[str, str]
    target_weight_decimal_display: str
    bound_artifacts: TargetKernelBindingSet
    lineage: LineageBinding

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "bound_artifacts": self.bound_artifacts.to_json_dict(),
            "claims": dict(NON_CLAIMS),
            "kernel_id": KERNEL_ID,
            "lineage": self.lineage.to_json_dict(),
            "repair": {
                "choice_order": list(REGISTERED_NEGATIVE_CASH_REPAIR_CHOICE_ORDER),
                "enabled": True,
                "iteration_ceiling": self.repair_iteration_ceiling,
                "step": REGISTERED_NEGATIVE_CASH_REPAIR_STEP,
                "steps": [step.to_json_dict() for step in self.repair_steps],
                "terminal_invariant": NEGATIVE_CASH_REPAIR_TERMINAL_INVARIANT,
            },
            "request_id": self.request_id,
            "rows": [row.to_json_dict() for row in self.rows],
            "schema_version": SCHEMA_VERSION,
            "selection": {
                "decimal_weight_is_display_only": True,
                "k_t": self.totals.selection_count_k_t,
                "selected": list(_sorted_symbols(self.selected)),
                "target_weight_decimal_display": self.target_weight_decimal_display,
                "target_weight_rational": dict(self.target_weight_rational),
            },
            "state": self.state,
            "ticket_id": TICKET_ID,
            "totals": self.totals.to_json_dict(),
            "weighting_rules": {
                "cash_components": list(CASH_COMPONENTS),
                "cash_formula": CASH_FORMULA,
                "component_rounding": COMPONENT_ROUNDING,
                "control_target": CONTROL_TARGET,
                "cost_rate_encoding": COST_RATE_ENCODING,
                "decimal_display_formula": DECIMAL_DISPLAY_FORMULA,
                "fees_basis": FEES_BASIS,
                "fill_order": REGISTERED_FILL_ORDER,
                "fractional_position_residual": FRACTIONAL_POSITION_RESIDUAL_RULE,
                "ideal_weight_authority": IDEAL_WEIGHT_AUTHORITY,
                "leverage_allowed": False,
                "order_quantum": ORDER_QUANTUM_TEXT,
                "orders_must_be_integer_quantum_multiples": True,
                "pre_trade_nav_identity": PRE_TRADE_NAV_IDENTITY,
                "raw_position_storage_quantum": RAW_POSITION_STORAGE_QUANTUM_TEXT,
                "residual_cash": RESIDUAL_CASH_RULE,
                "selected_target_formula": SELECTED_TARGET_FORMULA,
                "stable_key_order": REGISTERED_STABLE_KEY_ORDER,
                "supported_withholding_basis": SUPPORTED_WITHHOLDING_BASIS,
                "trade_universe": TRADE_UNIVERSE_RULE,
                "unselected_current_holdings_target": (
                    UNSELECTED_CURRENT_HOLDINGS_TARGET
                ),
            },
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json_dict())

    @property
    def self_sha256_grouped(self) -> str:
        return group_sha256(self.canonical_bytes())

    def signed_deltas(self) -> Mapping[str, str]:
        """``security_id -> signed delta`` for every non-zero row, stable order."""

        return {
            row.security_id: row.signed_delta_raw_shares
            for row in self.rows
            if to_ledger_decimal(row.signed_delta_raw_shares, what="delta") != 0
        }


# ---------------------------------------------------------------------------
# Projection through the bound NEE-118 kernel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Projection:
    """One cash projection, decided by the UNSHIFTED bound-kernel call."""

    engine_accepts: bool
    cash_post: Decimal
    transaction_cost: Decimal
    transaction_tax: Decimal
    gross_buy_notional: Decimal
    gross_sell_notional: Decimal
    deltas: tuple[SignedTargetDelta, ...]


def _deltas(
    *,
    working: Mapping[str, Fraction],
    positions_exact: Mapping[str, Fraction],
    prices: Mapping[str, RawExecutionPrice],
) -> tuple[SignedTargetDelta, ...]:
    """Typed non-zero deltas in the registered stable-key order."""

    rows: list[SignedTargetDelta] = []
    for security_id in _sorted_symbols(tuple(working)):
        delta = working[security_id] - positions_exact.get(security_id, _EXACT_ZERO)
        if delta == 0:
            continue
        try:
            rows.append(
                SignedTargetDelta(
                    security_id=security_id,
                    delta_raw_shares=_format_exact_fraction(delta),
                    raw_execution_price=prices[security_id],
                )
            )
        except ExecutionAccountingError as exc:
            raise _translated(exc, fallback=BLOCKED_BOUND_KERNEL_REFUSAL) from exc
    return tuple(rows)


def _run_bound_rebalance(
    before: PortfolioState,
    trades: Sequence[Trade],
    *,
    cost_rate_bps: Decimal,
    tax_policy: TransactionTaxPolicy,
    marks: Mapping[str, RawMark],
) -> RebalanceResult:
    return rebalance(
        before,
        trades,
        transaction_cost_rate_bps=cost_rate_bps,
        transaction_tax_policy=tax_policy,
        raw_marks_after=dict(marks),
    )


def _project(
    *,
    before: PortfolioState,
    working: Mapping[str, Fraction],
    positions_exact: Mapping[str, Fraction],
    prices: Mapping[str, RawExecutionPrice],
    marks: Mapping[str, RawMark],
    cost_rate_bps: Decimal,
    tax_policy: TransactionTaxPolicy,
) -> _Projection:
    """Project TC, TAX, and cash by CALLING the frozen NEE-118 kernel.

    The accept/reject verdict is the unshifted call's own. When it refuses on
    negative cash, the exact would-be closing cash is observed by re-running
    the identical fill sequence with the opening cash shifted by a cushion of
    ``3 * gross_trade_notional + 1`` and subtracting the cushion back out:
    every per-fill amount in :func:`qme.quant.equations.rebalance` is
    independent of the running cash level, so the difference is exact.
    """

    candidate = _deltas(
        working=working, positions_exact=positions_exact, prices=prices
    )
    if not candidate:
        return _Projection(
            engine_accepts=True,
            cash_post=before.cash,
            transaction_cost=_ZERO,
            transaction_tax=_ZERO,
            gross_buy_notional=_ZERO,
            gross_sell_notional=_ZERO,
            deltas=(),
        )
    ordered = order_fills(candidate)
    trades: list[Trade] = []
    for row in ordered:
        try:
            trades.append(
                Trade(
                    symbol=row.security_id,
                    delta_shares=row.delta,
                    raw_execution_price=row.raw_execution_price,
                )
            )
        except (TypeError, ValueError) as exc:
            raise TargetConstructionError(
                BLOCKED_BOUND_KERNEL_REFUSAL,
                f"frozen NEE-118 trade construction refused this delta: {exc}",
                security_id=row.security_id,
            ) from exc
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        buy_notional = sum(
            (trade.gross_notional for trade in trades if trade.delta_shares > 0),
            start=_ZERO,
        )
        sell_notional = sum(
            (trade.gross_notional for trade in trades if trade.delta_shares < 0),
            start=_ZERO,
        )
    try:
        accepted = _run_bound_rebalance(
            before, trades, cost_rate_bps=cost_rate_bps, tax_policy=tax_policy, marks=marks
        )
        return _Projection(
            engine_accepts=True,
            cash_post=accepted.after.cash,
            transaction_cost=accepted.transaction_cost,
            transaction_tax=accepted.transaction_taxes,
            gross_buy_notional=buy_notional,
            gross_sell_notional=sell_notional,
            deltas=ordered,
        )
    except ExternalFlowNotSupported as exc:
        raise TargetConstructionError(
            BLOCKED_BOUND_KERNEL_REFUSAL, str(exc)
        ) from exc
    except (TypeError, ValueError) as exc:
        text = str(exc)
        if "negative cash" not in text and "no-margin cash" not in text:
            raise TargetConstructionError(
                BLOCKED_BOUND_KERNEL_REFUSAL,
                f"frozen NEE-118 kernel refused this projection: {text}",
            ) from exc
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        cushion = (buy_notional + sell_notional) * Decimal(3) + Decimal(1)
        shifted_cash = before.cash + cushion
    try:
        shifted_before = PortfolioState(
            cash=shifted_cash,
            positions=dict(before.positions),
            raw_marks=dict(before.raw_marks),
            receivables=before.receivables,
        )
        observed = _run_bound_rebalance(
            shifted_before,
            trades,
            cost_rate_bps=cost_rate_bps,
            tax_policy=tax_policy,
            marks=marks,
        )
    except (TypeError, ValueError) as exc:
        raise TargetConstructionError(
            BLOCKED_BOUND_KERNEL_REFUSAL,
            f"the cushion-shifted observation call was refused: {exc}",
        ) from exc
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        observed_cash = observed.after.cash - cushion
    return _Projection(
        engine_accepts=False,
        cash_post=observed_cash,
        transaction_cost=observed.transaction_cost,
        transaction_tax=observed.transaction_taxes,
        gross_buy_notional=buy_notional,
        gross_sell_notional=sell_notional,
        deltas=ordered,
    )


# ---------------------------------------------------------------------------
# The frozen target formulas
# ---------------------------------------------------------------------------


def _fractional_residual(position: Fraction, quantum: Fraction) -> Fraction:
    """``position - floor(position / quantum) * quantum`` -- the carried part."""

    return position - (position // quantum) * quantum


def _selected_target(
    *,
    security_id: str,
    position: Fraction,
    price: RawExecutionPrice,
    nav: Fraction,
    count: int,
    quantum: Fraction,
    order_quantum_decimal: Decimal,
) -> Fraction:
    """``selected_target_formula`` in exact rationals, cross-checked against
    the frozen NEE-118 rounder exactly as the execution engine does."""

    residual = _fractional_residual(position, quantum)
    price_exact = Fraction(price.value)
    budget = nav / count - residual * price_exact
    units = (budget / price_exact / quantum) // 1
    target = residual + units * quantum
    if target < 0:
        raise TargetConstructionError(
            INVALID_NEGATIVE_LONG_ONLY_TARGET,
            "the registered target formula produced a negative long-only target; "
            "the execution engine refuses this as a short position, so this "
            "kernel refuses it first",
            security_id=security_id,
        )
    if budget >= 0:
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            context.rounding = ROUND_HALF_EVEN
            budget_decimal = Decimal(budget.numerator) / Decimal(budget.denominator)
        try:
            kernel_units = round_long_target_shares(
                budget_decimal, price, order_quantum=order_quantum_decimal
            )
        except (TypeError, ValueError) as exc:
            raise TargetConstructionError(
                BLOCKED_BOUND_KERNEL_REFUSAL,
                f"the frozen NEE-118 rounder refused this target: {exc}",
                security_id=security_id,
            ) from exc
        if Fraction(kernel_units) != units * quantum:
            raise TargetConstructionError(
                BLOCKED_BOUND_KERNEL_REFUSAL,
                "the exact target floor disagrees with the frozen NEE-118 rounder",
                security_id=security_id,
            )
    return target


def _decimal_display_weight(count: int) -> str:
    """``round_half_even(Decimal(1) / Decimal(K_t), 18)`` -- DISPLAY ONLY."""

    with localcontext() as context:
        context.prec = CONTRACT_DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        return format(
            (Decimal(1) / Decimal(count)).quantize(_DISPLAY_WEIGHT_QUANTUM), "f"
        )


# ---------------------------------------------------------------------------
# Kernel entry point
# ---------------------------------------------------------------------------


def construct_targets(
    request: TargetConstructionRequest, *, repository_root: Path
) -> TargetConstructionResult:
    """Construct the frozen v0.1 equal-weight target program for one request."""

    if type(request) is not TargetConstructionRequest:
        raise TargetConstructionError(
            INVALID_WEIGHTING_INPUT,
            "construct_targets requires a TargetConstructionRequest",
        )
    bindings = bind_target_kernels(repository_root)
    lineage = LineageBinding(
        input_sha256_grouped=request.input_digest(),
        config_sha256_grouped=bindings.config_sha256_grouped,
        code_sha256_grouped=bindings.code_sha256_grouped,
        schema_sha256_grouped=target_output_schema_digest(),
    )

    try:
        cost_policy = resolve_cost_rate_policy(
            request.cost_policy_id, records=request.registries.cost_rate_policies
        )
    except ExecutionAccountingError as exc:
        raise _translated(exc, fallback=BLOCKED_NO_REGISTERED_COST_RATE_POLICY) from exc
    if request.regulatory_fee_mode != FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY:
        raise TargetConstructionError(
            BLOCKED_UNSUPPORTED_REGULATORY_FEE_MODE,
            f"this kernel projects fees only under "
            f"{FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY!r}; "
            f"{request.regulatory_fee_mode!r} is refused rather than approximated",
        )
    if cost_policy.regulatory_authority:
        raise TargetConstructionError(
            BLOCKED_UNSUPPORTED_REGULATORY_FEE_MODE,
            "a regulatory-authority cost policy may not exclude regulatory fees; "
            "the execution engine refuses that program, so this kernel refuses "
            "it first",
        )
    if request.fractional_disposition_handler_id is not None:
        resolve_fractional_disposition_handler(
            request.fractional_disposition_handler_id
        )
    for security_id in _sorted_symbols(tuple(request.raw_execution_prices)):
        source_id = request.raw_execution_prices[security_id].evidence.source_id
        try:
            resolve_ledger_coordinate_source(
                source_id, records=request.registries.ledger_coordinate_sources
            )
        except ExecutionAccountingError as exc:
            raise _translated(
                exc, fallback=BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE
            ) from exc

    universe = request.trade_universe
    missing = [
        security_id
        for security_id in universe
        if security_id not in request.raw_execution_prices
    ]
    if missing:
        raise TargetConstructionError(
            INVALID_WEIGHTING_INPUT,
            "every trade-universe security needs a typed raw execution price; "
            f"missing: {missing}",
        )
    marks: dict[str, RawMark] = {}
    for security_id in universe:
        price = request.raw_execution_prices[security_id]
        try:
            marks[security_id] = RawMark(value=price.value, evidence=price.evidence)
        except (TypeError, ValueError) as exc:
            raise TargetConstructionError(
                BLOCKED_BOUND_KERNEL_REFUSAL,
                f"the frozen RawMark observation refused this price: {exc}",
                security_id=security_id,
            ) from exc

    positions_decimal = {
        security_id: to_ledger_decimal(
            quantity, what=f"prior_positions[{security_id}]"
        )
        for security_id, quantity in request.prior_positions.items()
    }
    try:
        before = PortfolioState(
            cash=to_ledger_decimal(request.cash_pre, what="cash_pre"),
            positions=positions_decimal,
            raw_marks=marks,
            receivables=to_ledger_decimal(
                request.receivables_pre, what="receivables_pre"
            ),
        )
    except (TypeError, ValueError) as exc:
        raise TargetConstructionError(
            INVALID_WEIGHTING_INPUT,
            f"the frozen NEE-118 portfolio state refused this book: {exc}",
        ) from exc

    nav_decimal = before.nav
    if nav_decimal <= 0:
        raise TargetConstructionError(
            INVALID_WEIGHTING_INPUT,
            "the frozen NEE-118 kernel requires a positive pre-trade NAV",
        )
    declared_nav = to_ledger_decimal(
        request.declared_pre_trade_nav, what="declared_pre_trade_nav"
    )
    tolerance = to_ledger_decimal(
        PRE_TRADE_NAV_IDENTITY_TOLERANCE, what="nav identity tolerance"
    )
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        identity_gap = abs(declared_nav - nav_decimal)
    if identity_gap > tolerance:
        raise TargetConstructionError(
            INVALID_PRE_TRADE_NAV_IDENTITY,
            f"declared pre-trade NAV {format_ledger(declared_nav, what='nav')} "
            f"violates the identity value "
            f"{format_ledger(nav_decimal, what='nav')} beyond the registered "
            f"tolerance {PRE_TRADE_NAV_IDENTITY_TOLERANCE}",
        )

    quantum = Fraction(
        to_ledger_decimal(ORDER_QUANTUM_TEXT, what="order_quantum")
    )
    order_quantum_decimal = to_ledger_decimal(
        ORDER_QUANTUM_TEXT, what="order_quantum"
    )
    nav_exact = Fraction(nav_decimal)
    count = request.declared_selection_count
    positions_exact = {
        security_id: Fraction(value)
        for security_id, value in positions_decimal.items()
    }
    selected_set = set(request.selected)
    targets: dict[str, Fraction] = {}
    for security_id in request.selected:
        targets[security_id] = _selected_target(
            security_id=security_id,
            position=positions_exact.get(security_id, _EXACT_ZERO),
            price=request.raw_execution_prices[security_id],
            nav=nav_exact,
            count=count,
            quantum=quantum,
            order_quantum_decimal=order_quantum_decimal,
        )
    for security_id, position in positions_exact.items():
        if security_id not in targets:
            targets[security_id] = _fractional_residual(position, quantum)

    ceiling = 0
    for security_id in request.selected:
        residual = _fractional_residual(
            positions_exact.get(security_id, _EXACT_ZERO), quantum
        )
        available = (targets[security_id] - residual) / quantum
        if available.denominator != 1 or available < 0:
            raise TargetConstructionError(
                INVALID_WEIGHTING_INPUT,
                "a selected target is not an integer number of order quanta "
                "above its carried residual",
                security_id=security_id,
            )
        ceiling += available.numerator

    def project(working: Mapping[str, Fraction]) -> _Projection:
        return _project(
            before=before,
            working=working,
            positions_exact=positions_exact,
            prices=request.raw_execution_prices,
            marks=marks,
            cost_rate_bps=cost_policy.rate_bps,
            tax_policy=request.transaction_tax_policy,
        )

    projection = project(targets)
    initial_projection = projection
    trace: list[RepairStep] = []
    decrements: dict[str, int] = {}
    steps = 0
    while not projection.engine_accepts:
        candidates = []
        for security_id in request.selected:
            residual = _fractional_residual(
                positions_exact.get(security_id, _EXACT_ZERO), quantum
            )
            if targets[security_id] >= residual + quantum:
                candidates.append(security_id)
        if not candidates:
            raise TargetConstructionError(
                INVALID_NEGATIVE_POST_TRADE_CASH,
                "the registered repair exhausted every selected target and cash "
                "is still negative after costs and rounding",
            )
        steps += 1
        if steps > ceiling:
            # Unreachable: each iteration consumes one of the finitely many
            # decrementable order quanta counted by ``ceiling``, and the
            # candidate walk above refuses first when none remain. Kept as a
            # typed guard so the termination bound is enforced, not assumed.
            raise TargetConstructionError(
                BLOCKED_REPAIR_ITERATION_CEILING,
                f"the repair loop exceeded its {ceiling}-step termination bound",
            )
        chosen = max(
            candidates,
            key=lambda security_id: (
                targets[security_id]
                * Fraction(request.raw_execution_prices[security_id].value),
                security_id.encode("utf-8"),
            ),
        )
        target_before = targets[chosen]
        targets[chosen] = target_before - quantum
        decrements[chosen] = decrements.get(chosen, 0) + 1
        projection = project(targets)
        trace.append(
            RepairStep(
                step_index=steps,
                security_id=chosen,
                target_before_raw_shares=_format_exact_fraction(target_before),
                target_after_raw_shares=_format_exact_fraction(targets[chosen]),
                recomputed_transaction_cost=format_ledger(
                    projection.transaction_cost, what="transaction_cost"
                ),
                recomputed_transaction_tax=format_ledger(
                    projection.transaction_tax, what="transaction_tax"
                ),
                recomputed_supported_withholding=format_ledger(
                    _ZERO, what="supported_withholding"
                ),
                recomputed_fees=format_ledger(_ZERO, what="fees"),
                recomputed_cash_post=format_ledger(
                    projection.cash_post, what="cash_post"
                ),
                recomputed_gross_buy_notional=format_ledger(
                    projection.gross_buy_notional, what="gross_buy_notional"
                ),
                engine_projection=(
                    ENGINE_PROJECTION_ACCEPTED
                    if projection.engine_accepts
                    else ENGINE_PROJECTION_REFUSED_NEGATIVE_CASH
                ),
            )
        )
    if projection.cash_post < 0:
        raise TargetConstructionError(
            INVALID_NEGATIVE_POST_TRADE_CASH,
            "terminal invariant cash_post >= 0 does not hold",
        )

    weight_rational: Mapping[str, str] = MappingProxyType(
        {"denominator": str(count), "numerator": "1"}
    )
    display_weight = _decimal_display_weight(count)
    rows: list[TargetConstructionRow] = []
    for security_id in universe:
        position = positions_exact.get(security_id, _EXACT_ZERO)
        residual_in = _fractional_residual(position, quantum)
        target = targets[security_id]
        residual_out = _fractional_residual(target, quantum)
        if residual_out != residual_in:
            raise TargetConstructionError(
                INVALID_WEIGHTING_INPUT,
                "the carried fractional residual changed; the registered rule "
                "carries it unchanged until a bound disposition handler exists",
                security_id=security_id,
            )
        delta = target - position
        delta_quanta = delta / quantum
        if delta_quanta.denominator != 1:
            raise TargetConstructionError(
                INVALID_WEIGHTING_INPUT,
                "orders must be integer multiples of the order quantum",
                security_id=security_id,
            )
        selected_member = security_id in selected_set
        rows.append(
            TargetConstructionRow(
                security_id=security_id,
                membership=(
                    MEMBERSHIP_SELECTED
                    if selected_member
                    else MEMBERSHIP_UNSELECTED_HOLDING
                ),
                prior_raw_shares=_format_exact_fraction(position),
                target_weight_rational=weight_rational if selected_member else None,
                target_weight_decimal_display=(
                    display_weight if selected_member else None
                ),
                fractional_residual_in=_format_exact_fraction(residual_in),
                target_raw_shares=_format_exact_fraction(target),
                signed_delta_raw_shares=_format_exact_fraction(delta),
                fractional_residual_out=_format_exact_fraction(residual_out),
                repair_decrements=decrements.get(security_id, 0),
                raw_execution_price=format_ledger(
                    request.raw_execution_prices[security_id].value,
                    what="raw_execution_price",
                ),
                lineage=lineage,
            )
        )

    totals = TargetConstructionTotals(
        selection_count_k_t=count,
        pre_trade_nav=format_ledger(nav_decimal, what="pre_trade_nav"),
        declared_pre_trade_nav=format_ledger(
            declared_nav, what="declared_pre_trade_nav"
        ),
        pre_trade_nav_identity_tolerance=PRE_TRADE_NAV_IDENTITY_TOLERANCE,
        projected_transaction_cost=format_ledger(
            projection.transaction_cost, what="transaction_cost"
        ),
        projected_transaction_tax=format_ledger(
            projection.transaction_tax, what="transaction_tax"
        ),
        projected_supported_withholding=format_ledger(
            _ZERO, what="supported_withholding"
        ),
        projected_fees=format_ledger(_ZERO, what="fees"),
        projected_cash_post=format_ledger(projection.cash_post, what="cash_post"),
        initial_projected_cash_post=format_ledger(
            initial_projection.cash_post, what="initial_projected_cash_post"
        ),
        projected_gross_buy_notional=format_ledger(
            projection.gross_buy_notional, what="gross_buy_notional"
        ),
        projected_gross_sell_notional=format_ledger(
            projection.gross_sell_notional, what="gross_sell_notional"
        ),
        repair_steps_total=steps,
    )
    return TargetConstructionResult(
        request_id=request.request_id,
        state=TARGET_CONSTRUCTION_OK,
        selected=request.selected,
        rows=tuple(rows),
        totals=totals,
        repair_steps=tuple(trace),
        repair_iteration_ceiling=ceiling,
        target_weight_rational=weight_rational,
        target_weight_decimal_display=display_weight,
        bound_artifacts=bindings,
        lineage=lineage,
    )


validate_fail_closed_states()


__all__ = [
    "BLOCKED_BOUND_KERNEL_REFUSAL",
    "BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER",
    "BLOCKED_REPAIR_ITERATION_CEILING",
    "BLOCKED_UNSUPPORTED_REGULATORY_FEE_MODE",
    "CASH_COMPONENTS",
    "CASH_FORMULA",
    "CONTRACT_DECIMAL_PRECISION",
    "CONTROL_TARGET",
    "DECIMAL_DISPLAY_FORMULA",
    "FRACTIONAL_POSITION_RESIDUAL_RULE",
    "INVALID_DUPLICATE_SECURITY_ID",
    "INVALID_NEGATIVE_LONG_ONLY_TARGET",
    "INVALID_NEGATIVE_POST_TRADE_CASH",
    "INVALID_PRE_TRADE_NAV_IDENTITY",
    "INVALID_SELECTION_COUNT_MISMATCH",
    "INVALID_WEIGHTING_INPUT",
    "INVALID_ZERO_SELECTION_SIZE",
    "KERNEL_ID",
    "MEMBERSHIP_SELECTED",
    "MEMBERSHIP_UNSELECTED_HOLDING",
    "ORDER_QUANTUM_TEXT",
    "PRE_TRADE_NAV_IDENTITY",
    "PRE_TRADE_NAV_IDENTITY_TOLERANCE",
    "REGISTERED_FRACTIONAL_DISPOSITION_HANDLERS",
    "SCHEMA_VERSION",
    "SELECTED_TARGET_FORMULA",
    "TARGET_BOUND_ARTIFACT_ROLES",
    "TARGET_CONSTRUCTION_FAIL_CLOSED_STATES",
    "TARGET_CONSTRUCTION_OK",
    "TARGET_KERNEL_CALL_SITES",
    "TARGET_OUTPUT_SCHEMA_DESCRIPTOR",
    "TICKET_ID",
    "TRADE_UNIVERSE_RULE",
    "UNSELECTED_CURRENT_HOLDINGS_TARGET",
    "FractionalDispositionHandler",
    "RepairStep",
    "TargetConstructionError",
    "TargetConstructionRequest",
    "TargetConstructionResult",
    "TargetConstructionRow",
    "TargetConstructionTotals",
    "TargetKernelBindingSet",
    "bind_target_kernels",
    "construct_targets",
    "resolve_fractional_disposition_handler",
    "target_output_schema_digest",
    "validate_fail_closed_states",
    "validate_fractional_disposition_registry",
]
