"""NEE-129 raw-price research execution and self-financing portfolio accounting.

This engine executes the FROZEN NEE-118 contract (``NEE-118-QME-ACCOUNTING-V1``)
in the registered ledger coordinate
``RAW_CASH_RAW_SHARES_RAW_EXECUTION_PRICES_RAW_MARKS``. It solves nothing the
owner has not registered, reuses every accepted kernel it can reach, and refuses
rather than substitutes.

Event sequence (frozen, applied in exactly this order)
------------------------------------------------------

1. apply effective share/action state and recognize dividend entitlement /
   receivables under the frozen timing rules;
2. mark pre-trade positions at declared raw execution marks;
3. compute ``NAV_minus``;
4. generate signed target deltas;
5. execute sells then cost-aware buys WITHOUT changing economic target priority;
6. apply costs, fees, taxes, cash updates;
7. verify post-trade invariants and publish fills / lots;
8. produce daily raw-close marks and receivable / cash transitions.

:data:`REGISTERED_EVENT_SEQUENCE` is the executable form of that list and every
emitted record names the step that produced it.

Registered equations (verbatim)
-------------------------------

``GTN = sum(|dq_i| * P_i)``

``C_plus = C_minus - sum(dq_i * P_i) - TC - TAX``

``at common marks NAV_plus = NAV_minus - TC - TAX``

``Target shares must be solved so C_plus >= 0 after costs AND rounding.``

Zero same-bar fills are STRUCTURAL
----------------------------------

A :class:`FillSession` cannot be built from a bare :class:`SessionRef`. It
admits only an :class:`EligibleFillSession`, whose ``__post_init__`` requires
``eligible.ordinal == signal.ordinal + 1`` and ``eligible.session_date >
signal.session_date`` on one content-bound calendar. A fill session must then
satisfy ``fill.ordinal >= eligible.ordinal``, so a fill on the signal bar is not
representable: the only sanctioned constructor refuses it as
``BLOCKED_SAME_SESSION_FILL``, and the type wall is proved statically by a
``mypy --strict`` probe in the test module. Every constructed fill session also
calls the frozen :func:`qme.quant.equations.validate_fill_timing`, closing the
timing gap the golden-fixture harness leaves open.

Adjusted prices are STRUCTURALLY confined to signal / diagnostic fields
-----------------------------------------------------------------------

The ledger observation union is ``RawExecutionPrice | RawMark`` -- the two typed
NEE-118 raw observations. :class:`AdjustedSignalObservation` is a *sibling* of
those types, not a subtype, and the only field that admits it is
:attr:`SignalDiagnostics.observations`. It cannot be placed in a
:class:`LedgerMarkSet` or a :class:`SignedTargetDelta` under a static type check,
and ``LedgerMarkSet.__post_init__`` refuses it at runtime.

An adjusted *number* wearing a raw type is caught by an ALLOWLIST, not by a
denylist: ``ExecutionProgram.__post_init__`` resolves every ledger evidence
``source_id`` -- opening marks, stage marks, and execution prices alike --
through :func:`resolve_ledger_coordinate_source` against a registry of
:class:`LedgerCoordinateSource` records whose ``coordinate_system`` must be the
registered raw coordinate :data:`RAW_LEDGER_COORDINATE_SYSTEM` (verbatim
``qme.data.stores.prices_v1.RAW_COORDINATE``, bound by observed digest). That
registry ships EMPTY, so an unregistered source identifier -- including one such
as ``TIME_SERIES_DAILY_ADJUSTED`` that no substring denylist would ever catch --
fails closed as :data:`BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE` before a
program can exist. :data:`FORBIDDEN_LEDGER_COORDINATE_TOKENS` is retained as a
construction-time defence in depth behind that allowlist, never as the wall.

Stage sessions are MONOTONE
---------------------------

Stages are applied in declared list order, and a dividend payment credits cash
when its action stage is applied. ``ExecutionProgram.__post_init__`` therefore
refuses (``BLOCKED_NON_MONOTONE_STAGE_SESSION``) any stage whose session
precedes the exit session of a stage already applied -- an action stage's exit
being its payment session when one is declared -- so a future-dated payment can
never finance an earlier-dated fill. Contradictory fill-availability
declarations (``halted`` or ``delisted_between_signal_and_fill`` combined with a
declared official open or regular-session print) are refused at construction as
``BLOCKED_CONTRADICTORY_FILL_AVAILABILITY`` rather than resolved by rung order.

Owner-gated values ship EMPTY
-----------------------------

Eight registries ship ``()`` and fail closed: the maximum fill deferral bound,
the transaction-cost rate policy, the participation limit, the spread/impact
model, the residual-cash disposition, the sourced unsupported-event outcome, the
supported-withholding policy, and the raw ledger coordinate source. Each follows
the shape already registered in :mod:`qme.data.alpha_vantage.plan_v1` and
:mod:`qme.data.stores.riskfree_v1`: a frozen record carrying the provenance
quintet, a ``validate_*_registry``, and a ``resolve_*`` that refuses.
``TEST_CONSTRUCTED`` records may be injected through a ``records=`` parameter and
may never ship: :func:`validate_shipped_registries` runs at import over
:data:`SHIPPED_REGISTRIES` and refuses any shipped record whose ``source_kind``
is not in :data:`REGISTERED_SOURCE_KINDS`, and a ``resolve_*`` call that passes
no override re-checks the shipped constant it reads at call time.

Three of those eight are REQUIRED rather than opt-in, so a run cannot simply
decline to name them: the cost rate policy (``ExecutionProgram.cost_policy_id``,
resolved through the registry -- a caller-built record can no longer drive a
run), the participation limit (``RebalanceStage.participation_limit_id``, which
NEE-118 calls a required run parameter), and the ledger coordinate source. A
fourth is required conditionally: a stage that declares a dividend must declare
a registered supported-withholding policy, and because NEE-118 ships no
withholding executable, only an explicitly registered ZERO-rate policy can
accompany an entitlement -- gross accrual is a registered decision, never a
silent default, and a nonzero rate refuses rather than being ignored.

Numerics
--------

Every ledger value is parsed through the canonical NEE-118 grammar
(:func:`qme.quant.equations._decimal`) and lifted to an exact
:class:`fractions.Fraction` for invariant proofs, exactly as
:mod:`qme.quant.capacity_solver_v2` does. Ledger amounts are quantized once at
``1e-8`` with ``ROUND_HALF_EVEN``. No binary float appears anywhere here.

Declared prices and marks are held to the same ``1e-8`` quantum as share
quantities (:func:`require_ledger_price_quantum`), and post-trade cash
non-negativity is asserted on the EXACT rational cash before any quantization --
the frozen kernel checks the already-rounded value, so a sub-quantum negative
would otherwise be published as ``0.00000000``.

Non-claims
----------

This engine claims no production deployment, no prospective consumption, no
empirical performance, no alpha, no capacity value, no production readiness, and
no live-order authority. :data:`NON_CLAIMS` is copied into every manifest.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from qme.foundation.lineage import canonical_json_bytes
from qme.quant.asymmetric_costs_v3 import (
    IMPLEMENTATION_ID as ASYMMETRIC_COST_IMPLEMENTATION_ID,
)
from qme.quant.asymmetric_costs_v3 import (
    METHOD_ID as ASYMMETRIC_COST_METHOD_ID,
)
from qme.quant.asymmetric_costs_v3 import (
    AsymmetricCostV3Error,
    AsymmetricRebalanceResultV3,
    RegulatoryTradeMetadataV3,
    asymmetric_self_financing_error_v3,
    rebalance_with_historical_regulatory_fees_v3,
)
from qme.quant.equations import (
    DECIMAL_PRECISION,
    DEFAULT_ORDER_QUANTUM,
    EQUATION_SPEC_ID,
    INTERNAL_CURRENCY_QUANTUM,
    SHARE_STORAGE_QUANTUM,
    ExchangeSessionRef,
    ExternalFlowNotSupported,
    PortfolioState,
    RawExecutionPrice,
    RawMark,
    RebalanceResult,
    Trade,
    TransactionTaxPolicy,
    _decimal,
    apply_split,
    dividend_receivable,
    rebalance,
    round_long_target_shares,
    self_financing_error,
    validate_fill_timing,
)
from qme.quant.regulatory_fees_v2 import (
    IMPLEMENTATION_ID as REGULATORY_FEE_IMPLEMENTATION_ID,
)
from qme.quant.tax_lots import (
    LOT_METHOD_FIFO,
    Fill,
    Split,
    TaxLotError,
    TaxLotLedger,
    build_tax_lot_ledger,
)

# ---------------------------------------------------------------------------
# Engine identity and registered vocabulary
# ---------------------------------------------------------------------------

ENGINE_ID: Final = "QME-NEE129-RAW-PRICE-EXECUTION-SELF-FINANCING-ENGINE-V1"
METHOD_ID: Final = "QME-NEE129-RAW-EXECUTION-SELF-FINANCING-ACCOUNTING-V1"
SCHEMA_VERSION: Final = "qme.execution_accounting.v1"

#: The frozen accounting coordinate this engine refuses to leave.
ACCOUNTING_COORDINATE: Final = "RAW_CASH_RAW_SHARES_RAW_EXECUTION_PRICES_RAW_MARKS"

#: NEE-118 ``configs/quant/accounting-equations-v1.json`` -> ``tax_scope``. The
#: config label and the spec-markdown label are BOTH frozen and hash-bound, and
#: they disagree. This engine records the config label as canonical and records
#: the markdown variant verbatim beside it, so the conflict stays visible rather
#: than being silently normalized. See the companion document, "Deviations".
CANONICAL_TAX_METRIC_LABEL: Final = (
    "PRE_CAPITAL_GAINS_TAX_AFTER_TRANSACTION_COSTS_AND_SUPPORTED_TRANSACTION_TAX"
)
UNRESOLVED_ALTERNATE_TAX_METRIC_LABEL: Final = (
    "PRE_CAPITAL_GAINS_TAX_AFTER_TRANSACTION_COSTS_AND_SUPPORTED_WITHHOLDING"
)
TAX_METRIC_LABEL_AUTHORITY: Final = "NEE_118_CONFIG_TAX_SCOPE_CANONICAL_METRIC_LABEL"

#: Downstream claims this engine has not earned. Written to every manifest.
NON_CLAIMS: Final[Mapping[str, bool]] = MappingProxyType(
    {
        "alpha_demonstrated": False,
        "capacity_value_registered": False,
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

#: The registered equations, verbatim from the ticket and the NEE-118 contract.
REGISTERED_EQUATIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "cash_after": "C_plus = C_minus - sum(dq_i * P_i) - TC - TAX",
        "common_mark_identity": "at common marks NAV_plus = NAV_minus - TC - TAX",
        "gross_trade_notional": "GTN = sum(|dq_i| * P_i)",
        "target_solution_invariant": (
            "Target shares must be solved so C_plus >= 0 after costs AND rounding."
        ),
    }
)

#: The frozen event sequence. ``index + 1`` is the ticket's step number.
REGISTERED_EVENT_SEQUENCE: Final[tuple[str, ...]] = (
    "APPLY_EFFECTIVE_SHARE_AND_ACTION_STATE_AND_RECOGNIZE_DIVIDEND_ENTITLEMENT",
    "MARK_PRE_TRADE_POSITIONS_AT_DECLARED_RAW_EXECUTION_MARKS",
    "COMPUTE_NAV_MINUS",
    "GENERATE_SIGNED_TARGET_DELTAS",
    "EXECUTE_SELLS_THEN_COST_AWARE_BUYS_WITHOUT_CHANGING_ECONOMIC_TARGET_PRIORITY",
    "APPLY_COSTS_FEES_TAXES_AND_CASH_UPDATES",
    "VERIFY_POST_TRADE_INVARIANTS_AND_PUBLISH_FILLS_AND_LOTS",
    "PRODUCE_DAILY_RAW_CLOSE_MARKS_AND_RECEIVABLE_AND_CASH_TRANSITIONS",
)

#: NEE-118 ``execution.fill_order``. The within-stage order is the NEE-119
#: registered stable-key order. Neither is invented here.
REGISTERED_FILL_ORDER: Final = "ALL_SELLS_THEN_ALL_BUYS"
REGISTERED_STABLE_KEY_ORDER: Final = "UTF8_BYTES_ASCENDING"
REGISTERED_SAME_SESSION_EVENT_ORDER: Final[tuple[str, ...]] = (
    "SPLIT",
    "DIVIDEND_ENTITLEMENT",
)
#: NEE-119 contract v2 ``weighting.negative_cash_repair``, verbatim.
REGISTERED_NEGATIVE_CASH_REPAIR_STEP: Final = "decrement_one_selected_target_order_quantum"
REGISTERED_NEGATIVE_CASH_REPAIR_CHOICE_ORDER: Final[tuple[str, ...]] = (
    "current_target_notional_descending",
    "security_id_utf8_bytes_descending",
)
REGISTERED_DIVIDEND_SHARE_BASIS: Final = "POST_SPLIT"

# --- fill-price hierarchy (frozen, reason-coded, in precedence order) -------

FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN: Final = "OFFICIAL_NEXT_SESSION_RAW_OPEN"
FILL_REASON_DECLARED_FIRST_REGULAR_SESSION_PRINT: Final = (
    "DECLARED_FIRST_REGULAR_SESSION_PRINT"
)
FILL_REASON_BOUNDED_NEXT_SESSION_DEFERRAL: Final = "BOUNDED_NEXT_SESSION_DEFERRAL"
FILL_REASON_SOURCED_DELISTING_OR_UNSUPPORTED_EVENT: Final = (
    "SOURCED_DELISTING_OR_UNSUPPORTED_EVENT_HANDLING"
)
#: The registered rungs, in registered order. Evaluation is NOT simply
#: "first satisfied wins": the delisting rung PRE-EMPTS the other three. A
#: security that delisted between signal and fill can only be filled through a
#: sourced outcome, so :func:`resolve_fill_reason` tests that rung first. The
#: remaining three are then evaluated top-down and the first satisfied wins.
#: Contradictory declarations never reach the rungs at all:
#: :class:`FillPriceAvailability` refuses ``halted`` combined with either priced
#: observation, and ``delisted_between_signal_and_fill`` combined with either,
#: as :data:`BLOCKED_CONTRADICTORY_FILL_AVAILABILITY` -- an inconsistent
#: declaration is refused, never resolved by rung order.
REGISTERED_FILL_REASON_PRECEDENCE: Final[tuple[str, ...]] = (
    FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN,
    FILL_REASON_DECLARED_FIRST_REGULAR_SESSION_PRINT,
    FILL_REASON_BOUNDED_NEXT_SESSION_DEFERRAL,
    FILL_REASON_SOURCED_DELISTING_OR_UNSUPPORTED_EVENT,
)

# --- share modes -----------------------------------------------------------

SHARE_MODE_WHOLE_SHARE_INTEGRAL_CUSTODY: Final = "WHOLE_SHARE_ORDERS_INTEGRAL_CUSTODY"
SHARE_MODE_INTEGER_ORDERS_FRACTIONAL_CUSTODY: Final = (
    "INTEGER_ORDERS_FRACTIONAL_CUSTODY"
)
REGISTERED_SHARE_MODES: Final[tuple[str, ...]] = (
    SHARE_MODE_INTEGER_ORDERS_FRACTIONAL_CUSTODY,
    SHARE_MODE_WHOLE_SHARE_INTEGRAL_CUSTODY,
)

# --- regulatory-fee posting modes ------------------------------------------

#: Post historical SEC 31 / FINRA TAF through the accepted V3 ledger adapter.
FEE_MODE_POSTED_HISTORICAL_V3: Final = "POSTED_HISTORICAL_REGULATORY_FEES_V3"
#: Reconciliation-only mode for a cost policy that explicitly declares itself a
#: non-regulatory synthetic source. A policy whose ``regulatory_authority`` flag
#: is true can never select it, so a real run cannot silently drop a fee.
FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY: Final = (
    "EXCLUDED_SYNTHETIC_NON_REGULATORY_SOURCE"
)
REGISTERED_REGULATORY_FEE_MODES: Final[tuple[str, ...]] = (
    FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY,
    FEE_MODE_POSTED_HISTORICAL_V3,
)

# --- coordinate wall -------------------------------------------------------

#: The only two ledger observation coordinates NEE-118 admits.
LEDGER_COORDINATES: Final[tuple[str, ...]] = ("RAW_EXECUTION_PRICE", "RAW_MARK")
#: Adjusted coordinates. Admissible ONLY inside :class:`SignalDiagnostics`.
SIGNAL_DIAGNOSTIC_COORDINATES: Final[tuple[str, ...]] = (
    "CUTOFF_AWARE_TOTAL_RETURN",
    "split_adjusted_price",
    "total_return",
)
#: The registered raw ledger price coordinate, verbatim from
#: ``qme.data.stores.prices_v1.RAW_COORDINATE``. Copied rather than imported
#: because ``tests/architecture/test_import_boundaries.py`` declares
#: :mod:`qme.data` off-limits to the research packages; the file is bound by
#: identity and OBSERVED digest in :data:`BOUND_ARTIFACT_ROLES`, so the label
#: cannot drift from its source without changing this run's config and code
#: hashes. See the companion document, "Deviations".
RAW_LEDGER_COORDINATE_SYSTEM: Final = "raw_price"
PRICE_STORE_ID: Final = "QME-NEE126-PRICE-STORE-V1"

#: Tokens that may not appear in any ledger evidence identifier. DEFENCE IN
#: DEPTH ONLY: a substring denylist cannot enumerate every adjusted identifier
#: (this repository's own ``TIME_SERIES_DAILY_ADJUSTED`` matches none of these),
#: so the wall is the :class:`LedgerCoordinateSource` ALLOWLIST resolved at run
#: time. This list stays because it refuses an obviously adjusted identifier at
#: construction time, before a program is ever assembled.
FORBIDDEN_LEDGER_COORDINATE_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "adj_close",
        "adjclose",
        "adjusted_close",
        "adjusted_volume",
        "cutoff_aware_total_return",
        "gross_return",
        "split_adjusted_close",
        "split_adjusted_price",
        "split_adjusted_volume",
        "split_adjustment_factor",
        "total_return",
        "total_return_index",
    }
)

# --- provenance vocabulary for every registry in this module ---------------

SOURCE_KIND_OWNER_DECISION_RECORD: Final = "OWNER_DECISION_RECORD"
SOURCE_KIND_PUBLISHER_REFERENCE: Final = "PUBLISHER_REFERENCE"
SOURCE_KIND_TEST_CONSTRUCTED: Final = "TEST_CONSTRUCTED"
SOURCE_KINDS: Final[tuple[str, ...]] = (
    SOURCE_KIND_OWNER_DECISION_RECORD,
    SOURCE_KIND_PUBLISHER_REFERENCE,
    SOURCE_KIND_TEST_CONSTRUCTED,
)
#: Kinds admissible in a shipped registry. ``TEST_CONSTRUCTED`` is not one.
REGISTERED_SOURCE_KINDS: Final[tuple[str, ...]] = (
    SOURCE_KIND_OWNER_DECISION_RECORD,
    SOURCE_KIND_PUBLISHER_REFERENCE,
)


# ---------------------------------------------------------------------------
# Typed fail-closed states
# ---------------------------------------------------------------------------

EXECUTION_OK: Final = "EXECUTION_OK"

BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT: Final = "BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT"
BLOCKED_CONTRADICTORY_FILL_AVAILABILITY: Final = "BLOCKED_CONTRADICTORY_FILL_AVAILABILITY"
BLOCKED_CORPORATE_ACTION_ON_FILL_DATE: Final = "BLOCKED_CORPORATE_ACTION_ON_FILL_DATE"
BLOCKED_DELISTING_BETWEEN_SIGNAL_AND_FILL: Final = (
    "BLOCKED_DELISTING_BETWEEN_SIGNAL_AND_FILL"
)
BLOCKED_DOUBLE_BOOKED_EVENT: Final = "BLOCKED_DOUBLE_BOOKED_EVENT"
BLOCKED_DUPLICATE_SECURITY_ROW: Final = "BLOCKED_DUPLICATE_SECURITY_ROW"
BLOCKED_HALTED_SECURITY_NO_REGULAR_SESSION_PRINT: Final = (
    "BLOCKED_HALTED_SECURITY_NO_REGULAR_SESSION_PRINT"
)
BLOCKED_INCONSISTENT_TAX_LOTS: Final = "BLOCKED_INCONSISTENT_TAX_LOTS"
BLOCKED_INVALID_FILL_TIMING: Final = "BLOCKED_INVALID_FILL_TIMING"
BLOCKED_MALFORMED_LEDGER_VALUE: Final = "BLOCKED_MALFORMED_LEDGER_VALUE"
BLOCKED_MISSING_BOUND_ARTIFACT: Final = "BLOCKED_MISSING_BOUND_ARTIFACT"
BLOCKED_MISSING_HELD_RAW_MARK: Final = "BLOCKED_MISSING_HELD_RAW_MARK"
BLOCKED_MISSING_OFFICIAL_RAW_OPEN: Final = "BLOCKED_MISSING_OFFICIAL_RAW_OPEN"
BLOCKED_NEGATIVE_POST_TRADE_CASH: Final = "BLOCKED_NEGATIVE_POST_TRADE_CASH"
BLOCKED_NONINTEGER_COST_RATE_BASIS_POINTS: Final = (
    "BLOCKED_NONINTEGER_COST_RATE_BASIS_POINTS"
)
BLOCKED_NONREPRESENTABLE_SHARE_QUANTITY: Final = (
    "BLOCKED_NONREPRESENTABLE_SHARE_QUANTITY"
)
BLOCKED_NON_MONOTONE_STAGE_SESSION: Final = "BLOCKED_NON_MONOTONE_STAGE_SESSION"
BLOCKED_NO_REGISTERED_COST_RATE_POLICY: Final = "BLOCKED_NO_REGISTERED_COST_RATE_POLICY"
BLOCKED_NO_REGISTERED_MAXIMUM_FILL_DEFERRAL: Final = (
    "BLOCKED_NO_REGISTERED_MAXIMUM_FILL_DEFERRAL"
)
BLOCKED_NO_REGISTERED_PARTICIPATION_LIMIT: Final = (
    "BLOCKED_NO_REGISTERED_PARTICIPATION_LIMIT"
)
BLOCKED_NO_REGISTERED_RESIDUAL_CASH_DISPOSITION: Final = (
    "BLOCKED_NO_REGISTERED_RESIDUAL_CASH_DISPOSITION"
)
BLOCKED_NO_REGISTERED_SPREAD_IMPACT_MODEL: Final = (
    "BLOCKED_NO_REGISTERED_SPREAD_IMPACT_MODEL"
)
BLOCKED_NO_REGISTERED_UNSUPPORTED_EVENT_OUTCOME: Final = (
    "BLOCKED_NO_REGISTERED_UNSUPPORTED_EVENT_OUTCOME"
)
BLOCKED_NO_REGISTERED_WITHHOLDING_POLICY: Final = (
    "BLOCKED_NO_REGISTERED_WITHHOLDING_POLICY"
)
BLOCKED_SAME_SESSION_FILL: Final = "BLOCKED_SAME_SESSION_FILL"
BLOCKED_SHORT_POSITION: Final = "BLOCKED_SHORT_POSITION"
BLOCKED_SPLIT_CONSERVATION_VIOLATED: Final = "BLOCKED_SPLIT_CONSERVATION_VIOLATED"
BLOCKED_UNAVAILABLE_FILL_AFTER_REGISTERED_BOUND: Final = (
    "BLOCKED_UNAVAILABLE_FILL_AFTER_REGISTERED_BOUND"
)
BLOCKED_UNREGISTERED_FILL_REASON_CODE: Final = "BLOCKED_UNREGISTERED_FILL_REASON_CODE"
BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE: Final = (
    "BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE"
)
BLOCKED_UNREGISTERED_REGULATORY_FEE_MODE: Final = (
    "BLOCKED_UNREGISTERED_REGULATORY_FEE_MODE"
)
BLOCKED_UNREGISTERED_SHARE_MODE: Final = "BLOCKED_UNREGISTERED_SHARE_MODE"
BLOCKED_UNREGISTERED_SOURCE_KIND: Final = "BLOCKED_UNREGISTERED_SOURCE_KIND"
BLOCKED_UNSUPPORTED_EXTERNAL_FLOW: Final = "BLOCKED_UNSUPPORTED_EXTERNAL_FLOW"
BLOCKED_UNSUPPORTED_HELD_CORPORATE_ACTION: Final = (
    "BLOCKED_UNSUPPORTED_HELD_CORPORATE_ACTION"
)

#: Every fail-closed state this module raises, sorted. Callers may bind it, and
#: the test module asserts that the observed union of raised states equals it
#: exactly -- neither an unraisable state nor an unregistered one may exist.
EXECUTION_FAIL_CLOSED_STATES: Final[tuple[str, ...]] = (
    BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT,
    BLOCKED_CONTRADICTORY_FILL_AVAILABILITY,
    BLOCKED_CORPORATE_ACTION_ON_FILL_DATE,
    BLOCKED_DELISTING_BETWEEN_SIGNAL_AND_FILL,
    BLOCKED_DOUBLE_BOOKED_EVENT,
    BLOCKED_DUPLICATE_SECURITY_ROW,
    BLOCKED_HALTED_SECURITY_NO_REGULAR_SESSION_PRINT,
    BLOCKED_INCONSISTENT_TAX_LOTS,
    BLOCKED_INVALID_FILL_TIMING,
    BLOCKED_MALFORMED_LEDGER_VALUE,
    BLOCKED_MISSING_BOUND_ARTIFACT,
    BLOCKED_MISSING_HELD_RAW_MARK,
    BLOCKED_MISSING_OFFICIAL_RAW_OPEN,
    BLOCKED_NEGATIVE_POST_TRADE_CASH,
    BLOCKED_NONINTEGER_COST_RATE_BASIS_POINTS,
    BLOCKED_NONREPRESENTABLE_SHARE_QUANTITY,
    BLOCKED_NON_MONOTONE_STAGE_SESSION,
    BLOCKED_NO_REGISTERED_COST_RATE_POLICY,
    BLOCKED_NO_REGISTERED_MAXIMUM_FILL_DEFERRAL,
    BLOCKED_NO_REGISTERED_PARTICIPATION_LIMIT,
    BLOCKED_NO_REGISTERED_RESIDUAL_CASH_DISPOSITION,
    BLOCKED_NO_REGISTERED_SPREAD_IMPACT_MODEL,
    BLOCKED_NO_REGISTERED_UNSUPPORTED_EVENT_OUTCOME,
    BLOCKED_NO_REGISTERED_WITHHOLDING_POLICY,
    BLOCKED_SAME_SESSION_FILL,
    BLOCKED_SHORT_POSITION,
    BLOCKED_SPLIT_CONSERVATION_VIOLATED,
    BLOCKED_UNAVAILABLE_FILL_AFTER_REGISTERED_BOUND,
    BLOCKED_UNREGISTERED_FILL_REASON_CODE,
    BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE,
    BLOCKED_UNREGISTERED_REGULATORY_FEE_MODE,
    BLOCKED_UNREGISTERED_SHARE_MODE,
    BLOCKED_UNREGISTERED_SOURCE_KIND,
    BLOCKED_UNSUPPORTED_EXTERNAL_FLOW,
    BLOCKED_UNSUPPORTED_HELD_CORPORATE_ACTION,
)


class ExecutionAccountingError(ValueError):
    """A typed fail-closed refusal carrying the state and the affected identity.

    ``state`` is one of :data:`EXECUTION_FAIL_CLOSED_STATES`. The identity fields
    are filled in whenever the refusal is attributable to a specific security,
    session, stage, or artifact, so a caller can report *which* input was refused
    rather than only that one was.
    """

    def __init__(
        self,
        state: str,
        message: str,
        *,
        security_id: str | None = None,
        session: str | None = None,
        stage_id: str | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(f"{state}: {message}")
        self.state = state
        self.security_id = security_id
        self.session = session
        self.stage_id = stage_id
        self.path = path

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "path": self.path,
            "security_id": self.security_id,
            "session": self.session,
            "stage_id": self.stage_id,
            "state": self.state,
        }


# ---------------------------------------------------------------------------
# Numeric and digest primitives
# ---------------------------------------------------------------------------

_ZERO: Final = Decimal(0)
_BPS_DENOMINATOR: Final = Decimal(10_000)
_EXACT_ZERO: Final = Fraction(0)
_LEDGER_QUANTUM: Final = Fraction(1, 100_000_000)


def to_ledger_decimal(value: object, *, what: str) -> Decimal:
    """Parse a finite base-10 value through the canonical NEE-118 grammar.

    Delegates to :func:`qme.quant.equations._decimal` -- the single frozen
    base-10 parser in this repository -- exactly as
    :mod:`qme.quant.capacity_solver_v2` does, so ``bool``, binary ``float``, and
    non-finite inputs are refused by the frozen kernel rather than by a second
    implementation of the same grammar.
    """

    try:
        return _decimal(value, what)
    except (TypeError, ValueError) as exc:
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE, f"{what} is not a canonical base-10 value"
        ) from exc


def to_exact(value: object, *, what: str) -> Fraction:
    """Lift a finite base-10 value to an EXACT rational; never a binary float."""

    return Fraction(to_ledger_decimal(value, what=what))


def quantize_ledger(value: Decimal, *, what: str) -> Decimal:
    """Quantize once at the protected ``1e-8`` ledger quantum, ``ROUND_HALF_EVEN``."""

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        try:
            return value.quantize(INTERNAL_CURRENCY_QUANTUM)
        except ArithmeticError as exc:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                f"{what} is not postable at the ledger quantum",
            ) from exc


def format_ledger(value: object, *, what: str = "value") -> str:
    """Render a ledger amount as a fixed-point ``Q8`` string with no signed zero."""

    quantized = quantize_ledger(to_ledger_decimal(value, what=what), what=what)
    if quantized == 0:
        quantized = abs(quantized)
    return format(quantized, "f")


def require_share_quantum(value: Decimal, *, what: str, security_id: str | None = None) -> Decimal:
    """Refuse a share quantity that is not exactly representable at ``1e-8``."""

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        quantized = value.quantize(SHARE_STORAGE_QUANTUM)
    if quantized != value:
        raise ExecutionAccountingError(
            BLOCKED_NONREPRESENTABLE_SHARE_QUANTITY,
            f"{what} is not representable at quantum {SHARE_STORAGE_QUANTUM}",
            security_id=security_id,
        )
    return quantized


def require_ledger_price_quantum(
    value: object, *, what: str, security_id: str | None = None
) -> Decimal:
    """Refuse a declared price or mark that is not exactly representable at ``1e-8``.

    Share quantities already pass :func:`require_share_quantum`. Prices and marks
    did not, so a value carrying more precision than the ledger quantum could
    produce an exact cash balance that is negative by less than one quantum and
    is then published as ``0.00000000`` by ``ROUND_HALF_EVEN``. A ledger
    observation that cannot be posted at the ledger quantum is refused instead.
    """

    amount = to_ledger_decimal(value, what=what)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        quantized = amount.quantize(INTERNAL_CURRENCY_QUANTUM)
    if quantized != amount:
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE,
            f"{what} carries more precision than the {INTERNAL_CURRENCY_QUANTUM} "
            "ledger quantum and is not exactly postable",
            security_id=security_id,
        )
    return quantized


def group_sha256(payload: bytes) -> str:
    """Return the grouped (eight lowercase 8-hex groups) SHA-256 of ``payload``.

    Local by design. The public grouped-hash helpers live in :mod:`qme.promotion`
    and :mod:`qme.governance` (T0 frozen-contract packages a T1 kernel must not
    import) and in :mod:`qme.data.stores.calendar_v1`, whose package initializer
    transitively imports the Alpha Vantage acquisition modules that
    ``tests/architecture/test_import_boundaries.py`` declares off-limits to
    research packages. :mod:`qme.foundation.lineage` supplies the canonical-JSON
    helper this module *does* import and carries no grouped form. This follows
    the precedent already set by ``qme/data/classification/rules_v1.py``.
    """

    digest = hashlib.sha256(payload).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def grouped_document_digest(document: Mapping[str, Any]) -> str:
    """Grouped SHA-256 over the repository's canonical JSON encoding."""

    return group_sha256(canonical_json_bytes(document))


def grouped_file_digest(path: Path) -> str:
    """Grouped SHA-256 of a bound artifact's bytes; a missing file fails closed."""

    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ExecutionAccountingError(
            BLOCKED_MISSING_BOUND_ARTIFACT,
            "bound artifact is not readable",
            path=str(path),
        ) from exc
    return group_sha256(payload)


def ungroup_sha256(value: object, *, what: str) -> str:
    """Return the contiguous lowercase digest behind a grouped rendering.

    The frozen NEE-118 evidence types require a contiguous 64-hex digest, while
    this repository forbids a contiguous 40/64-hex literal in source. Callers
    therefore store the grouped form and un-group it here at runtime; no
    contiguous digest is ever written as a literal.
    """

    if type(value) is not str:
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE, f"{what} must be exact text"
        )
    text = value
    groups = text.split(":")
    if len(groups) != 8 or any(
        len(group) != 8 or any(character not in "0123456789abcdef" for character in group)
        for group in groups
    ):
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE,
            f"{what} must be eight lowercase 8-hex groups joined by ':'",
        )
    return "".join(groups)


def _identifier(value: object, *, what: str) -> str:
    if type(value) is not str:
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE, f"{what} must be exact text"
        )
    text = value
    if not text or len(text) > 128 or text != text.strip():
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE, f"{what} must be a canonical identifier"
        )
    if any(character in text for character in "\x00\r\n\t"):
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE, f"{what} must be a canonical identifier"
        )
    return text


def _sorted_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    """The registered stable-key order: UTF-8 bytes ascending."""

    return tuple(sorted(symbols, key=lambda security_id: security_id.encode("utf-8")))


# ---------------------------------------------------------------------------
# Owner-gated registries -- ALL SHIP EMPTY AND FAIL CLOSED
# ---------------------------------------------------------------------------


def _provenance(
    *,
    record_id: object,
    source_kind: object,
    source: object,
    source_reference: object,
    effective_date: object,
) -> None:
    """Validate the provenance quintet every registry record must carry."""

    _identifier(record_id, what="record_id")
    if type(source_kind) is not str or source_kind not in SOURCE_KINDS:
        raise ExecutionAccountingError(
            BLOCKED_UNREGISTERED_SOURCE_KIND, f"unregistered source_kind {source_kind!r}"
        )
    for label, value in (("source", source), ("source_reference", source_reference)):
        if type(value) is not str or not value.strip():
            raise ExecutionAccountingError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"{label} must state where the value came from",
            )
    if type(effective_date) is not date:
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE, "effective_date must be an exact date"
        )


@dataclass(frozen=True)
class CostRatePolicy:
    """A registered transaction-cost rate in INTEGER basis points.

    NEE-119 requires ``INTEGER_BASIS_POINTS_FROM_REGISTERED_COST_POLICY`` over
    ``[0, 10000)``; the NEE-118 executable accepts any base-10 value in that
    range, so integrality is enforced here rather than assumed.
    """

    policy_id: str
    source_kind: str
    source: str
    source_reference: str
    effective_date: date
    transaction_cost_rate_bps: str
    regulatory_authority: bool

    def __post_init__(self) -> None:
        _provenance(
            record_id=self.policy_id,
            source_kind=self.source_kind,
            source=self.source,
            source_reference=self.source_reference,
            effective_date=self.effective_date,
        )
        if type(self.regulatory_authority) is not bool:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "regulatory_authority must be an exact bool"
            )
        rate = to_ledger_decimal(
            self.transaction_cost_rate_bps, what=f"{self.policy_id}.transaction_cost_rate_bps"
        )
        if rate < 0 or rate >= _BPS_DENOMINATOR:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "transaction-cost rate must be in [0, 10000) bps",
            )
        if rate != rate.to_integral_value():
            raise ExecutionAccountingError(
                BLOCKED_NONINTEGER_COST_RATE_BASIS_POINTS,
                "NEE-119 registers INTEGER basis points; a fractional rate is unregistered",
            )

    @property
    def rate_bps(self) -> Decimal:
        return to_ledger_decimal(
            self.transaction_cost_rate_bps, what=f"{self.policy_id}.transaction_cost_rate_bps"
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "effective_date": self.effective_date.isoformat(),
            "policy_id": self.policy_id,
            "regulatory_authority": self.regulatory_authority,
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
            "transaction_cost_rate_bps": self.transaction_cost_rate_bps,
        }


@dataclass(frozen=True)
class MaximumFillDeferral:
    """A registered bound on how many sessions a fill may be deferred."""

    bound_id: str
    source_kind: str
    source: str
    source_reference: str
    effective_date: date
    maximum_deferral_sessions: int

    def __post_init__(self) -> None:
        _provenance(
            record_id=self.bound_id,
            source_kind=self.source_kind,
            source=self.source,
            source_reference=self.source_reference,
            effective_date=self.effective_date,
        )
        if type(self.maximum_deferral_sessions) is not int or self.maximum_deferral_sessions < 0:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "maximum_deferral_sessions must be a non-negative exact int",
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "bound_id": self.bound_id,
            "effective_date": self.effective_date.isoformat(),
            "maximum_deferral_sessions": self.maximum_deferral_sessions,
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
        }


@dataclass(frozen=True)
class ParticipationLimit:
    """A registered maximum participation of ADV. NEE-118 calls it a REQUIRED run parameter."""

    limit_id: str
    source_kind: str
    source: str
    source_reference: str
    effective_date: date
    maximum_participation: str

    def __post_init__(self) -> None:
        _provenance(
            record_id=self.limit_id,
            source_kind=self.source_kind,
            source=self.source,
            source_reference=self.source_reference,
            effective_date=self.effective_date,
        )
        value = to_ledger_decimal(
            self.maximum_participation, what=f"{self.limit_id}.maximum_participation"
        )
        if value <= 0 or value > 1:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "maximum_participation must be in (0, 1]"
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "effective_date": self.effective_date.isoformat(),
            "limit_id": self.limit_id,
            "maximum_participation": self.maximum_participation,
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
        }


@dataclass(frozen=True)
class SpreadImpactModel:
    """A registered spread / market-impact parameterisation."""

    model_id: str
    source_kind: str
    source: str
    source_reference: str
    effective_date: date
    half_spread_bps: str
    impact_coefficient: str

    def __post_init__(self) -> None:
        _provenance(
            record_id=self.model_id,
            source_kind=self.source_kind,
            source=self.source,
            source_reference=self.source_reference,
            effective_date=self.effective_date,
        )
        for label, raw in (
            ("half_spread_bps", self.half_spread_bps),
            ("impact_coefficient", self.impact_coefficient),
        ):
            if to_ledger_decimal(raw, what=f"{self.model_id}.{label}") < 0:
                raise ExecutionAccountingError(
                    BLOCKED_MALFORMED_LEDGER_VALUE, f"{label} must be non-negative"
                )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "effective_date": self.effective_date.isoformat(),
            "half_spread_bps": self.half_spread_bps,
            "impact_coefficient": self.impact_coefficient,
            "model_id": self.model_id,
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
        }


@dataclass(frozen=True)
class ResidualCashDisposition:
    """A registered disposition for residual cash and fractional custody."""

    disposition_id: str
    source_kind: str
    source: str
    source_reference: str
    effective_date: date
    disposition: str

    def __post_init__(self) -> None:
        _provenance(
            record_id=self.disposition_id,
            source_kind=self.source_kind,
            source=self.source,
            source_reference=self.source_reference,
            effective_date=self.effective_date,
        )
        _identifier(self.disposition, what="disposition")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "disposition_id": self.disposition_id,
            "effective_date": self.effective_date.isoformat(),
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
        }


@dataclass(frozen=True)
class UnsupportedEventOutcome:
    """A sourced outcome for a delisting / merger / spinoff / rights event."""

    outcome_id: str
    source_kind: str
    source: str
    source_reference: str
    effective_date: date
    action_type: str
    terminal_value_per_share: str

    def __post_init__(self) -> None:
        _provenance(
            record_id=self.outcome_id,
            source_kind=self.source_kind,
            source=self.source,
            source_reference=self.source_reference,
            effective_date=self.effective_date,
        )
        _identifier(self.action_type, what="action_type")
        if to_ledger_decimal(
            self.terminal_value_per_share, what=f"{self.outcome_id}.terminal_value_per_share"
        ) < 0:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "terminal_value_per_share must be non-negative"
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "effective_date": self.effective_date.isoformat(),
            "outcome_id": self.outcome_id,
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
            "terminal_value_per_share": self.terminal_value_per_share,
        }


@dataclass(frozen=True)
class WithholdingPolicy:
    """A registered supported-withholding policy.

    NEE-119 lists ``SUPPORTED_WITHHOLDING_FROM_BOUND_NEE_118_EVENT_FUNCTION`` in
    its cash formula, but NEE-118 ships no withholding executable. Withholding is
    therefore blocked here until a policy is registered.
    """

    policy_id: str
    source_kind: str
    source: str
    source_reference: str
    effective_date: date
    withholding_rate: str

    def __post_init__(self) -> None:
        _provenance(
            record_id=self.policy_id,
            source_kind=self.source_kind,
            source=self.source,
            source_reference=self.source_reference,
            effective_date=self.effective_date,
        )
        rate = to_ledger_decimal(
            self.withholding_rate, what=f"{self.policy_id}.withholding_rate"
        )
        if rate < 0 or rate > 1:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "withholding_rate must be in [0, 1]"
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "effective_date": self.effective_date.isoformat(),
            "policy_id": self.policy_id,
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
            "withholding_rate": self.withholding_rate,
        }


@dataclass(frozen=True)
class LedgerCoordinateSource:
    """A registered evidence source whose observations live in the RAW coordinate.

    THE adjusted-price allowlist. Every ledger evidence ``source_id`` entering a
    :class:`LedgerMarkSet`, a :class:`SignedTargetDelta`, or an
    :class:`EqualWeightTargetProgram` must resolve to one of these records, and a
    record can only be constructed for the registered raw coordinate
    :data:`RAW_LEDGER_COORDINATE_SYSTEM`. The registry ships EMPTY, so an
    unregistered source identifier -- including ``TIME_SERIES_DAILY_ADJUSTED``,
    which no substring denylist catches -- fails closed as
    :data:`BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE`.
    """

    source_id: str
    source_kind: str
    source: str
    source_reference: str
    effective_date: date
    coordinate_system: str

    def __post_init__(self) -> None:
        _provenance(
            record_id=self.source_id,
            source_kind=self.source_kind,
            source=self.source,
            source_reference=self.source_reference,
            effective_date=self.effective_date,
        )
        if self.coordinate_system != RAW_LEDGER_COORDINATE_SYSTEM:
            raise ExecutionAccountingError(
                BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE,
                f"a ledger coordinate source may only register the raw coordinate "
                f"{RAW_LEDGER_COORDINATE_SYSTEM!r}; {self.coordinate_system!r} is not it",
            )
        lowered = self.source_id.lower()
        for token in FORBIDDEN_LEDGER_COORDINATE_TOKENS:
            if token in lowered:
                raise ExecutionAccountingError(
                    BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE,
                    f"source_id names the adjusted coordinate {token!r} and can never "
                    "be registered as a raw ledger coordinate source",
                )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "coordinate_system": self.coordinate_system,
            "effective_date": self.effective_date.isoformat(),
            "source": self.source,
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
        }


#: EMPTY BY DESIGN. The owner has registered no execution cost rate; NEE-119
#: says the rate comes from a registered cost policy and none exists.
REGISTERED_COST_RATE_POLICIES: Final[tuple[CostRatePolicy, ...]] = ()
#: EMPTY BY DESIGN. The maximum fill deferral must be REGISTERED, never assumed.
REGISTERED_MAXIMUM_FILL_DEFERRALS: Final[tuple[MaximumFillDeferral, ...]] = ()
#: EMPTY BY DESIGN. NEE-118 makes maximum participation a required run parameter
#: and registers no value for it.
REGISTERED_PARTICIPATION_LIMITS: Final[tuple[ParticipationLimit, ...]] = ()
#: EMPTY BY DESIGN. The golden fixture lists spread/impact in its explicitly
#: excluded unresolved scope; no coefficient is registered anywhere.
REGISTERED_SPREAD_IMPACT_MODELS: Final[tuple[SpreadImpactModel, ...]] = ()
#: EMPTY BY DESIGN. NEE-119 carries residual cash as
#: ``EXPLICIT_NOT_REDISTRIBUTED`` with no disposition handler registered.
REGISTERED_RESIDUAL_CASH_DISPOSITIONS: Final[tuple[ResidualCashDisposition, ...]] = ()
#: EMPTY BY DESIGN. NEE-125 records
#: ``sourced_unsupported_outcome_policy_registered: false``.
REGISTERED_UNSUPPORTED_EVENT_OUTCOMES: Final[tuple[UnsupportedEventOutcome, ...]] = ()
#: EMPTY BY DESIGN. No NEE-118 withholding executable exists.
REGISTERED_WITHHOLDING_POLICIES: Final[tuple[WithholdingPolicy, ...]] = ()
#: EMPTY BY DESIGN. The adjusted-price ALLOWLIST: no evidence source has been
#: registered as living in the raw ledger coordinate, so every ledger evidence
#: ``source_id`` fails closed until the owner registers one.
REGISTERED_LEDGER_COORDINATE_SOURCES: Final[tuple[LedgerCoordinateSource, ...]] = ()

#: Every record type a registry in this module may hold. The two generic helpers
#: below are deliberately typed against ``Any`` so one implementation serves all
#: eight registries; each public ``resolve_*`` narrows the result back to its own
#: record type with an ``isinstance`` assertion before returning it.
RegistryRecord = (
    CostRatePolicy
    | MaximumFillDeferral
    | ParticipationLimit
    | SpreadImpactModel
    | ResidualCashDisposition
    | UnsupportedEventOutcome
    | WithholdingPolicy
    | LedgerCoordinateSource
)

#: Every shipped registry constant, by name. ``validate_shipped_registries``
#: walks this at import time and refuses any shipped record whose ``source_kind``
#: is not in :data:`REGISTERED_SOURCE_KINDS`, so a ``TEST_CONSTRUCTED`` record
#: can be injected through ``RegistryOverrides`` but can never ship.
SHIPPED_REGISTRIES: Final[tuple[tuple[str, tuple[RegistryRecord, ...]], ...]] = (
    ("REGISTERED_COST_RATE_POLICIES", REGISTERED_COST_RATE_POLICIES),
    ("REGISTERED_LEDGER_COORDINATE_SOURCES", REGISTERED_LEDGER_COORDINATE_SOURCES),
    ("REGISTERED_MAXIMUM_FILL_DEFERRALS", REGISTERED_MAXIMUM_FILL_DEFERRALS),
    ("REGISTERED_PARTICIPATION_LIMITS", REGISTERED_PARTICIPATION_LIMITS),
    ("REGISTERED_RESIDUAL_CASH_DISPOSITIONS", REGISTERED_RESIDUAL_CASH_DISPOSITIONS),
    ("REGISTERED_SPREAD_IMPACT_MODELS", REGISTERED_SPREAD_IMPACT_MODELS),
    ("REGISTERED_UNSUPPORTED_EVENT_OUTCOMES", REGISTERED_UNSUPPORTED_EVENT_OUTCOMES),
    ("REGISTERED_WITHHOLDING_POLICIES", REGISTERED_WITHHOLDING_POLICIES),
)


def validate_shipped_registries() -> None:
    """Refuse a shipped registry record whose provenance kind may not ship.

    Runs at import (module bottom). Shipped registries are EMPTY by design, so
    this walk is normally a no-op; it exists so that the first shipped record
    someone adds is checked mechanically, not by convention.
    """

    for registry_name, records in SHIPPED_REGISTRIES:
        for record in records:
            if record.source_kind not in REGISTERED_SOURCE_KINDS:
                raise ExecutionAccountingError(
                    BLOCKED_UNREGISTERED_SOURCE_KIND,
                    f"{registry_name} ships a {record.source_kind!r} record; only "
                    f"{REGISTERED_SOURCE_KINDS} may ship",
                )


def _validate_registry(
    records: Sequence[Any],
    *,
    record_type: type[Any],
    shipped: Sequence[Any],
    empty_state: str,
    empty_message: str,
    identity: Callable[[Any], str],
) -> None:
    if not records:
        raise ExecutionAccountingError(empty_state, empty_message)
    seen: set[str] = set()
    for record in records:
        if type(record) is not record_type:
            raise ExecutionAccountingError(
                empty_state, f"registry entries must be {record_type.__name__} records"
            )
        key = identity(record)
        if key in seen:
            raise ExecutionAccountingError(empty_state, f"duplicate registry id: {key}")
        seen.add(key)
        if records is shipped and record.source_kind not in REGISTERED_SOURCE_KINDS:
            raise ExecutionAccountingError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"{key}: {record.source_kind} may not ship in the registry",
            )


def _resolve(
    record_id: str,
    records: Sequence[Any],
    *,
    record_type: type[Any],
    shipped: Sequence[Any],
    empty_state: str,
    empty_message: str,
    identity: Callable[[Any], str],
) -> Any:
    _validate_registry(
        records,
        record_type=record_type,
        shipped=shipped,
        empty_state=empty_state,
        empty_message=empty_message,
        identity=identity,
    )
    matches = [record for record in records if identity(record) == record_id]
    if len(matches) != 1:
        raise ExecutionAccountingError(
            empty_state, f"{record_id!r} is not registered in this registry"
        )
    return matches[0]


_COST_POLICY_EMPTY_MESSAGE: Final = (
    "no transaction-cost rate policy is registered; NEE-119 requires integer basis "
    "points from a registered cost policy and this engine refuses to assume a rate"
)
_DEFERRAL_EMPTY_MESSAGE: Final = (
    "no maximum fill deferral is registered; the bound must be registered rather "
    "than assumed, so a deferred fill cannot be admitted"
)
_PARTICIPATION_EMPTY_MESSAGE: Final = (
    "no participation limit is registered; NEE-118 makes maximum participation a "
    "required run parameter and registers no value"
)
_SPREAD_EMPTY_MESSAGE: Final = (
    "no spread or market-impact model is registered; execution price equal to the "
    "common mark is the only registered coordinate"
)
_RESIDUAL_EMPTY_MESSAGE: Final = (
    "no residual-cash disposition is registered; NEE-119 carries residual cash as "
    "EXPLICIT_NOT_REDISTRIBUTED with no handler"
)
_UNSUPPORTED_EVENT_EMPTY_MESSAGE: Final = (
    "no sourced unsupported-event outcome is registered; a delisting, merger, "
    "spinoff, or rights distribution has no terminal value this engine may assume"
)
_WITHHOLDING_EMPTY_MESSAGE: Final = (
    "no supported-withholding policy is registered; NEE-118 ships no withholding "
    "executable, so withholding cannot be booked"
)
_LEDGER_COORDINATE_EMPTY_MESSAGE: Final = (
    "no ledger coordinate source is registered; every ledger evidence source_id "
    "must resolve to a registered raw-coordinate source and none exists"
)


def validate_cost_rate_policy_registry(
    records: Sequence[CostRatePolicy] = REGISTERED_COST_RATE_POLICIES,
) -> None:
    """Fail closed on an empty, duplicated, or test-contaminated cost registry."""

    _validate_registry(
        records,
        record_type=CostRatePolicy,
        shipped=REGISTERED_COST_RATE_POLICIES,
        empty_state=BLOCKED_NO_REGISTERED_COST_RATE_POLICY,
        empty_message=_COST_POLICY_EMPTY_MESSAGE,
        identity=lambda record: record.policy_id,
    )


def resolve_cost_rate_policy(
    policy_id: str,
    *,
    records: Sequence[CostRatePolicy] = REGISTERED_COST_RATE_POLICIES,
) -> CostRatePolicy:
    """Return the registered cost policy, or fail closed. Never invents a rate."""

    resolved = _resolve(
        policy_id,
        records,
        record_type=CostRatePolicy,
        shipped=REGISTERED_COST_RATE_POLICIES,
        empty_state=BLOCKED_NO_REGISTERED_COST_RATE_POLICY,
        empty_message=_COST_POLICY_EMPTY_MESSAGE,
        identity=lambda record: record.policy_id,
    )
    assert isinstance(resolved, CostRatePolicy)
    return resolved


def validate_maximum_fill_deferral_registry(
    records: Sequence[MaximumFillDeferral] = REGISTERED_MAXIMUM_FILL_DEFERRALS,
) -> None:
    """Fail closed on an empty or contaminated deferral-bound registry."""

    _validate_registry(
        records,
        record_type=MaximumFillDeferral,
        shipped=REGISTERED_MAXIMUM_FILL_DEFERRALS,
        empty_state=BLOCKED_NO_REGISTERED_MAXIMUM_FILL_DEFERRAL,
        empty_message=_DEFERRAL_EMPTY_MESSAGE,
        identity=lambda record: record.bound_id,
    )


def resolve_maximum_fill_deferral(
    bound_id: str,
    *,
    records: Sequence[MaximumFillDeferral] = REGISTERED_MAXIMUM_FILL_DEFERRALS,
) -> MaximumFillDeferral:
    """Return the registered deferral bound, or fail closed. Never assumes one."""

    resolved = _resolve(
        bound_id,
        records,
        record_type=MaximumFillDeferral,
        shipped=REGISTERED_MAXIMUM_FILL_DEFERRALS,
        empty_state=BLOCKED_NO_REGISTERED_MAXIMUM_FILL_DEFERRAL,
        empty_message=_DEFERRAL_EMPTY_MESSAGE,
        identity=lambda record: record.bound_id,
    )
    assert isinstance(resolved, MaximumFillDeferral)
    return resolved


def resolve_participation_limit(
    limit_id: str,
    *,
    records: Sequence[ParticipationLimit] = REGISTERED_PARTICIPATION_LIMITS,
) -> ParticipationLimit:
    """Return the registered participation limit, or fail closed."""

    resolved = _resolve(
        limit_id,
        records,
        record_type=ParticipationLimit,
        shipped=REGISTERED_PARTICIPATION_LIMITS,
        empty_state=BLOCKED_NO_REGISTERED_PARTICIPATION_LIMIT,
        empty_message=_PARTICIPATION_EMPTY_MESSAGE,
        identity=lambda record: record.limit_id,
    )
    assert isinstance(resolved, ParticipationLimit)
    return resolved


def resolve_spread_impact_model(
    model_id: str,
    *,
    records: Sequence[SpreadImpactModel] = REGISTERED_SPREAD_IMPACT_MODELS,
) -> SpreadImpactModel:
    """Return the registered spread / impact model, or fail closed."""

    resolved = _resolve(
        model_id,
        records,
        record_type=SpreadImpactModel,
        shipped=REGISTERED_SPREAD_IMPACT_MODELS,
        empty_state=BLOCKED_NO_REGISTERED_SPREAD_IMPACT_MODEL,
        empty_message=_SPREAD_EMPTY_MESSAGE,
        identity=lambda record: record.model_id,
    )
    assert isinstance(resolved, SpreadImpactModel)
    return resolved


def resolve_residual_cash_disposition(
    disposition_id: str,
    *,
    records: Sequence[ResidualCashDisposition] = REGISTERED_RESIDUAL_CASH_DISPOSITIONS,
) -> ResidualCashDisposition:
    """Return the registered residual-cash disposition, or fail closed."""

    resolved = _resolve(
        disposition_id,
        records,
        record_type=ResidualCashDisposition,
        shipped=REGISTERED_RESIDUAL_CASH_DISPOSITIONS,
        empty_state=BLOCKED_NO_REGISTERED_RESIDUAL_CASH_DISPOSITION,
        empty_message=_RESIDUAL_EMPTY_MESSAGE,
        identity=lambda record: record.disposition_id,
    )
    assert isinstance(resolved, ResidualCashDisposition)
    return resolved


def resolve_unsupported_event_outcome(
    outcome_id: str,
    *,
    records: Sequence[UnsupportedEventOutcome] = REGISTERED_UNSUPPORTED_EVENT_OUTCOMES,
) -> UnsupportedEventOutcome:
    """Return the sourced unsupported-event outcome, or fail closed."""

    resolved = _resolve(
        outcome_id,
        records,
        record_type=UnsupportedEventOutcome,
        shipped=REGISTERED_UNSUPPORTED_EVENT_OUTCOMES,
        empty_state=BLOCKED_NO_REGISTERED_UNSUPPORTED_EVENT_OUTCOME,
        empty_message=_UNSUPPORTED_EVENT_EMPTY_MESSAGE,
        identity=lambda record: record.outcome_id,
    )
    assert isinstance(resolved, UnsupportedEventOutcome)
    return resolved


def resolve_withholding_policy(
    policy_id: str,
    *,
    records: Sequence[WithholdingPolicy] = REGISTERED_WITHHOLDING_POLICIES,
) -> WithholdingPolicy:
    """Return the registered supported-withholding policy, or fail closed."""

    resolved = _resolve(
        policy_id,
        records,
        record_type=WithholdingPolicy,
        shipped=REGISTERED_WITHHOLDING_POLICIES,
        empty_state=BLOCKED_NO_REGISTERED_WITHHOLDING_POLICY,
        empty_message=_WITHHOLDING_EMPTY_MESSAGE,
        identity=lambda record: record.policy_id,
    )
    assert isinstance(resolved, WithholdingPolicy)
    return resolved


def resolve_ledger_coordinate_source(
    source_id: str,
    *,
    records: Sequence[LedgerCoordinateSource] = REGISTERED_LEDGER_COORDINATE_SOURCES,
) -> LedgerCoordinateSource:
    """Return the registered raw ledger coordinate source, or fail closed.

    The allowlist wall for adjusted prices wearing a raw type: an evidence
    ``source_id`` that is not registered here -- ``TIME_SERIES_DAILY_ADJUSTED``
    included -- refuses as ``BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE``.
    """

    resolved = _resolve(
        source_id,
        records,
        record_type=LedgerCoordinateSource,
        shipped=REGISTERED_LEDGER_COORDINATE_SOURCES,
        empty_state=BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE,
        empty_message=_LEDGER_COORDINATE_EMPTY_MESSAGE,
        identity=lambda record: record.source_id,
    )
    assert isinstance(resolved, LedgerCoordinateSource)
    return resolved


# ---------------------------------------------------------------------------
# Session type wall -- zero same-bar fills, structurally
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionRef:
    """One exchange session on a content-bound calendar."""

    calendar_id: str
    calendar_sha256_grouped: str
    session_date: date
    ordinal: int

    def __post_init__(self) -> None:
        _identifier(self.calendar_id, what="calendar_id")
        ungroup_sha256(self.calendar_sha256_grouped, what="calendar_sha256_grouped")
        if type(self.session_date) is not date:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "session_date must be an exact date"
            )
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "session ordinal must be a non-negative exact int",
            )

    @property
    def calendar_identity(self) -> tuple[str, str]:
        return (self.calendar_id, self.calendar_sha256_grouped)

    def as_exchange_session_ref(self) -> ExchangeSessionRef:
        """Lift to the frozen NEE-118 session type, un-grouping the digest."""

        return ExchangeSessionRef(
            calendar_id=self.calendar_id,
            calendar_sha256=ungroup_sha256(
                self.calendar_sha256_grouped, what="calendar_sha256_grouped"
            ),
            session_date=self.session_date,
            ordinal=self.ordinal,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "calendar_id": self.calendar_id,
            "calendar_sha256_grouped": self.calendar_sha256_grouped,
            "ordinal": self.ordinal,
            "session_date": self.session_date.isoformat(),
        }


@dataclass(frozen=True)
class EligibleFillSession:
    """The consecutive next session after a signal session.

    Constructed only through :func:`derive_eligible_fill_session`. The
    ``signal_ordinal + 1`` rule and the strictly-later date are enforced here, so
    no later stage can widen them.
    """

    signal_session: SessionRef
    session: SessionRef

    def __post_init__(self) -> None:
        if type(self.signal_session) is not SessionRef or type(self.session) is not SessionRef:
            raise ExecutionAccountingError(
                BLOCKED_INVALID_FILL_TIMING, "eligibility requires typed SessionRef values"
            )
        if self.signal_session.calendar_identity != self.session.calendar_identity:
            raise ExecutionAccountingError(
                BLOCKED_INVALID_FILL_TIMING,
                "signal and eligible sessions do not share one calendar ID and hash",
            )
        if self.session.ordinal == self.signal_session.ordinal:
            raise ExecutionAccountingError(
                BLOCKED_SAME_SESSION_FILL,
                "a signal close on t cannot fill on t; the eligible session is t+1",
                session=self.session.session_date.isoformat(),
            )
        if self.session.ordinal != self.signal_session.ordinal + 1:
            raise ExecutionAccountingError(
                BLOCKED_INVALID_FILL_TIMING,
                "eligible session must be the consecutive exchange-session ordinal",
                session=self.session.session_date.isoformat(),
            )
        if self.session.session_date <= self.signal_session.session_date:
            raise ExecutionAccountingError(
                BLOCKED_INVALID_FILL_TIMING,
                "eligible session date must follow the signal session date",
                session=self.session.session_date.isoformat(),
            )

    @property
    def ordinal(self) -> int:
        return self.session.ordinal

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "eligible_session": self.session.to_json_dict(),
            "signal_session": self.signal_session.to_json_dict(),
        }


def derive_eligible_fill_session(
    signal_session: SessionRef, eligible_session: SessionRef
) -> EligibleFillSession:
    """The only sanctioned constructor for an eligible fill window."""

    return EligibleFillSession(signal_session=signal_session, session=eligible_session)


@dataclass(frozen=True)
class FillSession:
    """A fill session at or after the eligible session, with a registered reason.

    ``eligible`` is typed :class:`EligibleFillSession`, not :class:`SessionRef`,
    so no caller can hand this type a raw session and obtain a same-bar fill.
    """

    eligible: EligibleFillSession
    session: SessionRef
    reason_code: str

    def __post_init__(self) -> None:
        if type(self.eligible) is not EligibleFillSession:
            raise ExecutionAccountingError(
                BLOCKED_INVALID_FILL_TIMING,
                "a fill session admits an EligibleFillSession and nothing else",
            )
        if type(self.session) is not SessionRef:
            raise ExecutionAccountingError(
                BLOCKED_INVALID_FILL_TIMING, "fill timing requires typed SessionRef values"
            )
        if self.reason_code not in REGISTERED_FILL_REASON_PRECEDENCE:
            raise ExecutionAccountingError(
                BLOCKED_UNREGISTERED_FILL_REASON_CODE,
                f"unregistered fill reason code {self.reason_code!r}",
            )
        if self.session.calendar_identity != self.eligible.session.calendar_identity:
            raise ExecutionAccountingError(
                BLOCKED_INVALID_FILL_TIMING,
                "fill and eligible sessions do not share one calendar ID and hash",
            )
        if self.session.ordinal < self.eligible.ordinal:
            raise ExecutionAccountingError(
                BLOCKED_INVALID_FILL_TIMING,
                "fill precedes the declared eligible session",
                session=self.session.session_date.isoformat(),
            )
        if self.session.session_date < self.eligible.session.session_date:
            raise ExecutionAccountingError(
                BLOCKED_INVALID_FILL_TIMING,
                "fill date precedes the declared eligible session",
                session=self.session.session_date.isoformat(),
            )
        try:
            validate_fill_timing(
                signal_session=self.eligible.signal_session.as_exchange_session_ref(),
                eligible_session=self.eligible.session.as_exchange_session_ref(),
                fill_session=self.session.as_exchange_session_ref(),
            )
        except (TypeError, ValueError) as exc:
            raise ExecutionAccountingError(
                BLOCKED_INVALID_FILL_TIMING,
                f"frozen NEE-118 fill-timing validation refused this window: {exc}",
                session=self.session.session_date.isoformat(),
            ) from exc

    @property
    def deferral_sessions(self) -> int:
        """Sessions of deferral beyond the eligible session; zero when on time."""

        return self.session.ordinal - self.eligible.ordinal

    @property
    def signal_session(self) -> SessionRef:
        return self.eligible.signal_session

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "deferral_sessions": self.deferral_sessions,
            "eligible_session": self.eligible.session.to_json_dict(),
            "fill_session": self.session.to_json_dict(),
            "reason_code": self.reason_code,
            "signal_session": self.eligible.signal_session.to_json_dict(),
        }


# ---------------------------------------------------------------------------
# Adjusted-price wall
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdjustedSignalObservation:
    """A split-adjusted or total-return observation.

    A SIBLING of :class:`qme.quant.equations.RawMark` and
    :class:`qme.quant.equations.RawExecutionPrice`, never a subtype. The only
    field in this module that admits it is
    :attr:`SignalDiagnostics.observations`; every ledger field is typed for the
    raw observations, so an adjusted price cannot reach cash, shares, marks, or
    fills under a static type check.
    """

    security_id: str
    coordinate: str
    value: str
    session_date: date

    def __post_init__(self) -> None:
        _identifier(self.security_id, what="security_id")
        if self.coordinate not in SIGNAL_DIAGNOSTIC_COORDINATES:
            raise ExecutionAccountingError(
                BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT,
                f"unregistered signal-diagnostic coordinate {self.coordinate!r}",
                security_id=self.security_id,
            )
        to_ledger_decimal(self.value, what=f"{self.security_id}.adjusted_value")
        if type(self.session_date) is not date:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "session_date must be an exact date"
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate,
            "security_id": self.security_id,
            "session_date": self.session_date.isoformat(),
            "value": self.value,
        }


@dataclass(frozen=True)
class SignalDiagnostics:
    """The only container that may hold adjusted observations.

    Diagnostics never enter the ledger. They are carried through to the manifest
    so a run can report the adjusted series it consulted for signal construction
    while proving those values never touched cash, shares, marks, or fills.
    """

    observations: tuple[AdjustedSignalObservation, ...] = ()

    def __post_init__(self) -> None:
        if type(self.observations) is not tuple or any(
            type(item) is not AdjustedSignalObservation for item in self.observations
        ):
            raise ExecutionAccountingError(
                BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT,
                "signal diagnostics admit AdjustedSignalObservation values only",
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "coordinate_scope": "SIGNAL_AND_DIAGNOSTIC_ONLY_NEVER_LEDGER",
            "observations": [item.to_json_dict() for item in self.observations],
        }


def _assert_raw_ledger_evidence(
    observation: RawMark | RawExecutionPrice, *, traded: str, what: str
) -> None:
    """Refuse a raw-typed observation whose evidence names an adjusted series."""

    evidence = observation.evidence
    for label, text in (
        ("source_id", evidence.source_id),
        ("snapshot_id", evidence.snapshot_id),
        ("security_id", evidence.security_id),
    ):
        lowered = text.lower()
        for token in FORBIDDEN_LEDGER_COORDINATE_TOKENS:
            if token in lowered:
                raise ExecutionAccountingError(
                    BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT,
                    f"{what} evidence {label} names the adjusted coordinate {token!r}; "
                    "adjusted prices are confined to signal and diagnostic fields",
                    security_id=traded,
                )


@dataclass(frozen=True)
class LedgerMarkSet:
    """Declared raw marks for one ledger observation point.

    Runtime counterpart of the static wall: values must be exactly
    :class:`qme.quant.equations.RawMark`, and their evidence may not name an
    adjusted coordinate.
    """

    marks: Mapping[str, RawMark]

    def __post_init__(self) -> None:
        raw = self.marks
        if not isinstance(raw, Mapping):
            raise ExecutionAccountingError(
                BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT, "raw marks must be a mapping"
            )
        validated: dict[str, RawMark] = {}
        for raw_symbol, mark in raw.items():
            security_id = _identifier(raw_symbol, what="mark security_id")
            if security_id in validated:
                raise ExecutionAccountingError(
                    BLOCKED_DUPLICATE_SECURITY_ROW,
                    "raw marks contain a duplicate security_id",
                    security_id=security_id,
                )
            if type(mark) is not RawMark:
                raise ExecutionAccountingError(
                    BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT,
                    "a ledger mark admits the frozen RawMark observation and nothing else",
                    security_id=security_id,
                )
            if mark.evidence.security_id != security_id:
                raise ExecutionAccountingError(
                    BLOCKED_MALFORMED_LEDGER_VALUE,
                    "raw-mark evidence security_id does not match the mapping symbol",
                    security_id=security_id,
                )
            _assert_raw_ledger_evidence(mark, traded=security_id, what="raw mark")
            validated[security_id] = mark
        object.__setattr__(self, "marks", MappingProxyType(validated))

    def require(self, security_id: str, *, session: str | None = None) -> RawMark:
        mark = self.marks.get(security_id)
        if mark is None:
            raise ExecutionAccountingError(
                BLOCKED_MISSING_HELD_RAW_MARK,
                f"no declared raw mark for held security {security_id!r}",
                security_id=security_id,
                session=session,
            )
        return mark

    def to_json_dict(self) -> dict[str, Any]:
        return {
            security_id: format_ledger(self.marks[security_id].value, what=f"{security_id}.raw_mark")
            for security_id in _sorted_symbols(tuple(self.marks))
        }


# ---------------------------------------------------------------------------
# Bound kernels and lineage
# ---------------------------------------------------------------------------

#: Corporate-action factor kernel identity. NEE-125 is bound by identity and
#: observed digest rather than imported: ``qme/data/corporate_actions/__init__.py``
#: imports the Alpha Vantage acquisition and client modules, which
#: ``tests/architecture/test_import_boundaries.py`` declares off-limits to the
#: research packages, and ``import qme.data.corporate_actions.factors_v1``
#: executes that initializer. The test module -- which is not a research package
#: -- imports the kernel directly and cross-checks this engine's split
#: transition against :func:`verify_split_conservation`. See the companion
#: document, "Deviations".
CORPORATE_ACTION_FACTOR_KERNEL_ID: Final = "QME-NEE125-CORPORATE-ACTION-FACTOR-KERNEL-V1"
TAX_LOT_METHOD_LABEL: Final = "HIFO_IF_ACCOUNT_ELECTION_VERIFIED_ELSE_FIFO"

#: ``(role, repository-relative path, kernel identity)`` for every artifact this
#: engine binds -- each a kernel or config this engine calls or reads, and NEVER
#: its own source file. Every digest is OBSERVED at run time from
#: ``repository_root``; none is pinned as a literal, so this satisfies the T1
#: self-pinning policy (``forbidden_in_non_t0.python_source_patterns`` bans
#: literal ``EXPECTED_*SHA256`` pins, not run-time observation). This module's own
#: ``qme/quant/execution_v1.py`` is deliberately absent: T1 forbids self-pinning
#: outside the grandfathered paths, so engine identity is carried by the declared
#: ``ENGINE_ID`` literal in ``code_sha256_grouped``, not by hashing its own bytes.
#: A change to a called kernel's bytes changes that run's ``code_sha256_grouped``;
#: a change to only this module's own bytes need not.
BOUND_ARTIFACT_ROLES: Final[tuple[tuple[str, str, str], ...]] = (
    ("NEE_116_ASYMMETRIC_COST_LEDGER_ADAPTER_V3", "qme/quant/asymmetric_costs_v3.py",
     ASYMMETRIC_COST_IMPLEMENTATION_ID),
    ("NEE_116_TAX_LOT_KERNEL", "qme/quant/tax_lots.py", TAX_LOT_METHOD_LABEL),
    ("NEE_118_ACCOUNTING_CONFIG", "configs/quant/accounting-equations-v1.json",
     EQUATION_SPEC_ID),
    ("NEE_118_EQUATIONS_KERNEL", "qme/quant/equations.py", EQUATION_SPEC_ID),
    ("NEE_119_QUANTITATIVE_CONTRACT_V2", "configs/quant/qme-v0.1-contract-v2.json",
     "qme-long-only-momentum-v0.1"),
    ("NEE_125_CORPORATE_ACTION_FACTOR_KERNEL", "qme/data/corporate_actions/factors_v1.py",
     CORPORATE_ACTION_FACTOR_KERNEL_ID),
    ("NEE_126_PRICE_STORE_RAW_COORDINATE", "qme/data/stores/prices_v1.py", PRICE_STORE_ID),
    ("NEE_205_HISTORICAL_REGULATORY_FEE_KERNEL_V2", "qme/quant/regulatory_fees_v2.py",
     REGULATORY_FEE_IMPLEMENTATION_ID),
    ("NEE_205_REGULATORY_FEE_HISTORICAL_SCHEDULE",
     "configs/governance/regulatory-fee-historical-schedule-v1.json",
     "NEE-205-REGULATORY-FEE-HISTORICAL-SCHEDULE-V1"),
)

#: Where each bound kernel is actually called from, so a reviewer can audit the
#: reuse claim without reading the whole module.
KERNEL_CALL_SITES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "qme.quant.asymmetric_costs_v3.asymmetric_self_financing_error_v3": (
            "_self_financing (regulatory fee mode POSTED_HISTORICAL_REGULATORY_FEES_V3)",
        ),
        "qme.quant.asymmetric_costs_v3.rebalance_with_historical_regulatory_fees_v3": (
            "_execute_fills (regulatory fee mode POSTED_HISTORICAL_REGULATORY_FEES_V3)",
        ),
        "qme.quant.equations._decimal": ("to_ledger_decimal",),
        "qme.quant.equations.apply_split": ("apply_corporate_action_stage",),
        "qme.quant.equations.dividend_receivable": ("apply_corporate_action_stage",),
        "qme.quant.equations.rebalance": (
            "_execute_fills (batch)",
            "_replay_fills (per-fill staged replay)",
        ),
        "qme.quant.equations.round_long_target_shares": ("_equal_weight_targets",),
        "qme.quant.equations.self_financing_error": ("_self_financing",),
        "qme.quant.equations.validate_fill_timing": ("FillSession.__post_init__",),
        "qme.quant.regulatory_fees_v2.assess_regulatory_fees_historical": (
            "delegated by the V3 adapter; never called directly",
        ),
        "qme.quant.tax_lots.build_tax_lot_ledger": ("publish_tax_lots",),
    }
)


@dataclass(frozen=True)
class BoundArtifact:
    """One bound artifact with its OBSERVED grouped digest."""

    role: str
    path: str
    kernel_identity: str
    sha256_grouped: str

    def to_json_dict(self) -> dict[str, str]:
        return {
            "kernel_identity": self.kernel_identity,
            "path": self.path,
            "role": self.role,
            "sha256_grouped": self.sha256_grouped,
        }


@dataclass(frozen=True)
class KernelBindingSet:
    """Every artifact this engine binds, with its observed digest."""

    artifacts: tuple[BoundArtifact, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "artifacts": [artifact.to_json_dict() for artifact in self.artifacts],
            "call_sites": {key: list(value) for key, value in sorted(KERNEL_CALL_SITES.items())},
        }

    @property
    def config_sha256_grouped(self) -> str:
        return grouped_document_digest(self.to_json_dict())

    @property
    def code_sha256_grouped(self) -> str:
        """Grouped digest over the code identity of this run.

        Binds the OBSERVED source digest of every ``.py`` artifact in
        :data:`BOUND_ARTIFACT_ROLES` -- the kernels this engine calls or binds
        by identity, this module's own source deliberately excluded -- plus the
        declared identifier set, in which ``ENGINE_ID`` carries this engine's
        identity. A change to any called kernel's bytes therefore changes this
        value; a change to only this engine's own bytes need not. No digest is
        pinned as a literal anywhere; observation at run time is not the literal
        self-pinning the T1 policy
        (``forbidden_in_non_t0.python_source_patterns``) reserves for T0.
        """

        document = {
            "accounting_coordinate": ACCOUNTING_COORDINATE,
            "asymmetric_cost_implementation_id": ASYMMETRIC_COST_IMPLEMENTATION_ID,
            "asymmetric_cost_method_id": ASYMMETRIC_COST_METHOD_ID,
            "called_kernel_digests": {
                artifact.role: artifact.sha256_grouped
                for artifact in self.artifacts
                if artifact.path.endswith(".py")
            },
            "corporate_action_factor_kernel_id": CORPORATE_ACTION_FACTOR_KERNEL_ID,
            "engine_id": ENGINE_ID,
            "equation_spec_id": EQUATION_SPEC_ID,
            "method_id": METHOD_ID,
            "regulatory_fee_implementation_id": REGULATORY_FEE_IMPLEMENTATION_ID,
            "schema_version": SCHEMA_VERSION,
            "tax_lot_method_label": TAX_LOT_METHOD_LABEL,
        }
        return grouped_document_digest(document)


def bind_registered_kernels(repository_root: Path) -> KernelBindingSet:
    """Observe the grouped digest of every bound artifact; a missing file blocks."""

    if type(repository_root) is not type(Path()):
        raise ExecutionAccountingError(
            BLOCKED_MISSING_BOUND_ARTIFACT, "repository_root must be an exact pathlib.Path"
        )
    artifacts = tuple(
        BoundArtifact(
            role=role,
            path=path,
            kernel_identity=identity,
            sha256_grouped=grouped_file_digest(repository_root / path),
        )
        for role, path, identity in BOUND_ARTIFACT_ROLES
    )
    return KernelBindingSet(artifacts=artifacts)


#: The emitted row shapes, in declared field order. The grouped digest over this
#: descriptor is the run's ``schema_sha256_grouped``. This lane may not add a
#: file under ``schemas/**`` (T0 frozen contract, read-only here), so the schema
#: identity is the descriptor rather than a schema file digest.
OUTPUT_SCHEMA_DESCRIPTOR: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "corporate_action_outcome": (
            "stage_id", "step", "session", "event_order", "applied_event_registry_after",
            "post_split_raw_shares", "split_reference_value_before",
            "split_reference_value_after", "nav_after_split",
            "dividend_eligible_raw_shares", "dividend_receivable", "nav_after_entitlement",
            "cash_after_payment", "receivables_after_payment", "nav_after_payment",
            "lineage",
        ),
        "executed_fill": (
            "fill_index", "fill_id", "symbol", "side", "delta_raw_shares",
            "raw_execution_price", "gross_notional", "transaction_cost", "transaction_tax",
            "cash_after_fill", "positions_after_fill", "fill_reason_code", "lineage",
        ),
        "execution_manifest": (
            "schema_version", "engine_id", "method_id", "program_id",
            "accounting_coordinate", "equations", "event_sequence", "share_mode",
            "regulatory_fee_mode", "canonical_tax_metric_label",
            "unresolved_alternate_tax_metric_label", "tax_metric_label_authority",
            "bound_artifacts", "lineage", "claims", "self_sha256_grouped",
        ),
        "lot_publication": (
            "stage_id", "step", "method", "election_verified", "labels", "open_lots",
            "realized_events", "lineage",
        ),
        "rebalance_ledger": (
            "rebalance_id", "step", "fill_timing", "nav_minus", "gross_trade_notional",
            "transaction_cost", "transaction_tax", "regulatory_fees_total", "fill_states",
            "cash_plus", "positions_plus", "receivables_plus", "nav_plus",
            "self_financing_residual", "lineage",
        ),
        "session_close_record": (
            "stage_id", "step", "session", "raw_close_marks", "receivable_settlements",
            "cash_after", "receivables_after", "nav_after", "lineage",
        ),
    }
)


def output_schema_digest() -> str:
    """Grouped digest over the declared output schema descriptor."""

    return grouped_document_digest(
        {
            "rows": {key: list(value) for key, value in OUTPUT_SCHEMA_DESCRIPTOR.items()},
            "schema_version": SCHEMA_VERSION,
        }
    )


@dataclass(frozen=True)
class LineageBinding:
    """Input, config, code, and schema identities carried by every emitted row."""

    input_sha256_grouped: str
    config_sha256_grouped: str
    code_sha256_grouped: str
    schema_sha256_grouped: str

    def to_json_dict(self) -> dict[str, str]:
        return {
            "code_sha256_grouped": self.code_sha256_grouped,
            "config_sha256_grouped": self.config_sha256_grouped,
            "input_sha256_grouped": self.input_sha256_grouped,
            "schema_sha256_grouped": self.schema_sha256_grouped,
        }


# ---------------------------------------------------------------------------
# Declared program inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FillPriceAvailability:
    """What the run declares it observed for one security at the fill session.

    The four booleans are declared observations, never inferences. The fill
    hierarchy in :func:`resolve_fill_reason` reads them in the registered
    precedence order and reason-codes the outcome.
    """

    security_id: str
    official_next_session_raw_open_available: bool
    declared_first_regular_session_print_available: bool
    halted: bool
    delisted_between_signal_and_fill: bool
    registered_outcome_id: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.security_id, what="security_id")
        if self.registered_outcome_id is not None:
            _identifier(self.registered_outcome_id, what="registered_outcome_id")
        for label in (
            "official_next_session_raw_open_available",
            "declared_first_regular_session_print_available",
            "halted",
            "delisted_between_signal_and_fill",
        ):
            if type(getattr(self, label)) is not bool:
                raise ExecutionAccountingError(
                    BLOCKED_MALFORMED_LEDGER_VALUE,
                    f"{label} must be an exact bool",
                    security_id=self.security_id,
                )
        priced = (
            self.official_next_session_raw_open_available
            or self.declared_first_regular_session_print_available
        )
        if self.halted and priced:
            raise ExecutionAccountingError(
                BLOCKED_CONTRADICTORY_FILL_AVAILABILITY,
                "a halted declaration contradicts a declared official open or "
                "regular-session print; an inconsistent declaration is refused, "
                "never resolved by rung order",
                security_id=self.security_id,
            )
        if self.delisted_between_signal_and_fill and priced:
            raise ExecutionAccountingError(
                BLOCKED_CONTRADICTORY_FILL_AVAILABILITY,
                "a security delisted between signal and fill cannot also declare an "
                "official open or regular-session print for the fill session",
                security_id=self.security_id,
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "declared_first_regular_session_print_available": (
                self.declared_first_regular_session_print_available
            ),
            "delisted_between_signal_and_fill": self.delisted_between_signal_and_fill,
            "halted": self.halted,
            "official_next_session_raw_open_available": (
                self.official_next_session_raw_open_available
            ),
            "registered_outcome_id": self.registered_outcome_id,
            "security_id": self.security_id,
        }


@dataclass(frozen=True)
class SignedTargetDelta:
    """One signed raw-share delta at a declared raw execution price."""

    security_id: str
    delta_raw_shares: str
    raw_execution_price: RawExecutionPrice

    def __post_init__(self) -> None:
        security_id = _identifier(self.security_id, what="security_id")
        if type(self.raw_execution_price) is not RawExecutionPrice:
            raise ExecutionAccountingError(
                BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT,
                "a ledger fill admits the frozen RawExecutionPrice and nothing else",
                security_id=security_id,
            )
        if self.raw_execution_price.evidence.security_id != security_id:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "execution-price evidence security_id does not match the delta symbol",
                security_id=security_id,
            )
        _assert_raw_ledger_evidence(
            self.raw_execution_price, traded=security_id, what="raw execution price"
        )
        delta = to_ledger_decimal(self.delta_raw_shares, what=f"{security_id}.delta_raw_shares")
        require_share_quantum(delta, what=f"{security_id}.delta_raw_shares", security_id=security_id)
        if delta == 0:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "zero-share fills are not ledger events",
                security_id=security_id,
            )

    @property
    def delta(self) -> Decimal:
        return to_ledger_decimal(self.delta_raw_shares, what=f"{self.security_id}.delta_raw_shares")

    @property
    def is_sell(self) -> bool:
        return self.delta < 0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "delta_raw_shares": format_ledger(self.delta_raw_shares, what="delta_raw_shares"),
            "raw_execution_price": format_ledger(
                self.raw_execution_price.value, what="raw_execution_price"
            ),
            "symbol": self.security_id,
        }


@dataclass(frozen=True)
class DeclaredSignedDeltas:
    """Signed deltas supplied by the caller (fixture-driven reconciliation path)."""

    deltas: tuple[SignedTargetDelta, ...]

    def __post_init__(self) -> None:
        if type(self.deltas) is not tuple or not self.deltas:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "a rebalance requires at least one delta"
            )
        seen: set[str] = set()
        for delta in self.deltas:
            if type(delta) is not SignedTargetDelta:
                raise ExecutionAccountingError(
                    BLOCKED_MALFORMED_LEDGER_VALUE, "deltas must be SignedTargetDelta values"
                )
            traded = delta.security_id
            if traded in seen:
                raise ExecutionAccountingError(
                    BLOCKED_DUPLICATE_SECURITY_ROW,
                    "one delta row per security per rebalance",
                    security_id=traded,
                )
            seen.add(traded)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "deltas": [
                delta.to_json_dict()
                for delta in sorted(self.deltas, key=lambda item: item.security_id.encode("utf-8"))
            ],
            "kind": "DECLARED_SIGNED_DELTAS",
        }


@dataclass(frozen=True)
class EqualWeightTargetProgram:
    """NEE-119 equal-weight targets solved jointly with costs and rounding.

    ``selected_target_formula`` and ``unselected_current_holdings_target`` are
    the contract-v2 strings; nothing here is invented.
    """

    selected: tuple[str, ...]
    raw_execution_prices: Mapping[str, RawExecutionPrice]

    def __post_init__(self) -> None:
        if type(self.selected) is not tuple or not self.selected:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "an equal-weight target needs a selection"
            )
        seen: set[str] = set()
        for security_id in self.selected:
            _identifier(security_id, what="selected security_id")
            if security_id in seen:
                raise ExecutionAccountingError(
                    BLOCKED_DUPLICATE_SECURITY_ROW,
                    "a security may appear once in the selection",
                    security_id=security_id,
                )
            seen.add(security_id)
        prices = self.raw_execution_prices
        if not isinstance(prices, Mapping):
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "raw_execution_prices must be a mapping"
            )
        validated: dict[str, RawExecutionPrice] = {}
        for raw_symbol, price in prices.items():
            security_id = _identifier(raw_symbol, what="price security_id")
            if type(price) is not RawExecutionPrice:
                raise ExecutionAccountingError(
                    BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT,
                    "a ledger execution price admits the frozen RawExecutionPrice only",
                    security_id=security_id,
                )
            if price.evidence.security_id != security_id:
                raise ExecutionAccountingError(
                    BLOCKED_MALFORMED_LEDGER_VALUE,
                    "execution-price evidence security_id does not match the mapping symbol",
                    security_id=security_id,
                )
            _assert_raw_ledger_evidence(price, traded=security_id, what="raw execution price")
            validated[security_id] = price
        missing = [security_id for security_id in self.selected if security_id not in validated]
        if missing:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                f"selected securities without a declared execution price: {sorted(missing)}",
            )
        object.__setattr__(self, "raw_execution_prices", MappingProxyType(validated))

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "kind": "EQUAL_WEIGHT_TARGETS",
            "raw_execution_prices": {
                security_id: format_ledger(
                    self.raw_execution_prices[security_id].value, what="raw_execution_price"
                )
                for security_id in _sorted_symbols(tuple(self.raw_execution_prices))
            },
            "selected": list(_sorted_symbols(self.selected)),
            "selected_target_formula": (
                "fractional_residual_i + floor(((pre_trade_nav / K_t) - fractional_residual_i "
                "* raw_execution_price_i) / raw_execution_price_i / order_quantum) "
                "* order_quantum"
            ),
            "unselected_current_holdings_target": (
                "SELL_INTEGER_ORDERABLE_COMPONENT_CARRY_FRACTIONAL_RESIDUAL"
            ),
        }


TargetSpecification = DeclaredSignedDeltas | EqualWeightTargetProgram


@dataclass(frozen=True)
class SplitTerm:
    event_id: str
    security_id: str
    split_factor: str

    def __post_init__(self) -> None:
        _identifier(self.event_id, what="event_id")
        _identifier(self.security_id, what="security_id")
        if to_ledger_decimal(self.split_factor, what=f"{self.event_id}.split_factor") <= 0:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "split_factor must be positive",
                security_id=self.security_id,
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "security_id": self.security_id,
            "split_factor": self.split_factor,
        }


@dataclass(frozen=True)
class CashDividendTerm:
    event_id: str
    security_id: str
    share_basis: str
    raw_cash_per_share: str

    def __post_init__(self) -> None:
        _identifier(self.event_id, what="event_id")
        _identifier(self.security_id, what="security_id")
        if self.share_basis != REGISTERED_DIVIDEND_SHARE_BASIS:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "the registered dividend coordinate is POST_SPLIT cash per share",
                security_id=self.security_id,
            )
        if to_ledger_decimal(
            self.raw_cash_per_share, what=f"{self.event_id}.raw_cash_per_share"
        ) < 0:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "raw_cash_per_share must be non-negative",
                security_id=self.security_id,
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "raw_cash_per_share": self.raw_cash_per_share,
            "security_id": self.security_id,
            "share_basis": self.share_basis,
        }


@dataclass(frozen=True)
class DividendPaymentTerm:
    event_id: str
    dividend_event_id: str
    session: SessionRef

    def __post_init__(self) -> None:
        _identifier(self.event_id, what="event_id")
        _identifier(self.dividend_event_id, what="dividend_event_id")
        if type(self.session) is not SessionRef:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "a payment requires a typed SessionRef"
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "dividend_event_id": self.dividend_event_id,
            "event_id": self.event_id,
            "session": self.session.to_json_dict(),
        }


@dataclass(frozen=True)
class UnsupportedActionTerm:
    """A declared unsupported corporate action; fails closed on a held security."""

    event_id: str
    security_id: str
    action_type: str
    registered_outcome_id: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.event_id, what="event_id")
        _identifier(self.security_id, what="security_id")
        _identifier(self.action_type, what="action_type")
        if self.registered_outcome_id is not None:
            _identifier(self.registered_outcome_id, what="registered_outcome_id")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "event_id": self.event_id,
            "registered_outcome_id": self.registered_outcome_id,
            "security_id": self.security_id,
        }


@dataclass(frozen=True)
class CorporateActionStage:
    """Step 1 (and its step-8 receivable settlement) for one action session."""

    stage_id: str
    session: SessionRef
    applied_event_registry_before: tuple[str, ...]
    raw_marks_after_split: LedgerMarkSet
    raw_marks_after_entitlement: LedgerMarkSet
    split: SplitTerm | None = None
    dividend: CashDividendTerm | None = None
    payment: DividendPaymentTerm | None = None
    unsupported_actions: tuple[UnsupportedActionTerm, ...] = ()
    event_order: tuple[str, ...] = REGISTERED_SAME_SESSION_EVENT_ORDER
    declared_withholding_policy_id: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.stage_id, what="stage_id")
        if type(self.session) is not SessionRef:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "an action stage requires a typed SessionRef"
            )
        if tuple(self.event_order) != REGISTERED_SAME_SESSION_EVENT_ORDER:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "the registered same-session event order is SPLIT then DIVIDEND_ENTITLEMENT",
                stage_id=self.stage_id,
            )
        registry = tuple(self.applied_event_registry_before)
        if len(registry) != len(set(registry)):
            raise ExecutionAccountingError(
                BLOCKED_DOUBLE_BOOKED_EVENT,
                "the applied-event registry contains duplicates",
                stage_id=self.stage_id,
            )
        for event_id in registry:
            _identifier(event_id, what="applied event_id")
        for marks in (self.raw_marks_after_split, self.raw_marks_after_entitlement):
            if type(marks) is not LedgerMarkSet:
                raise ExecutionAccountingError(
                    BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT,
                    "action marks must be a LedgerMarkSet of frozen RawMark observations",
                    stage_id=self.stage_id,
                )
        if self.dividend is not None and self.declared_withholding_policy_id is None:
            raise ExecutionAccountingError(
                BLOCKED_NO_REGISTERED_WITHHOLDING_POLICY,
                "a dividend entitlement cannot assume silent gross accrual; the stage "
                "must declare a registered supported-withholding policy (a zero rate "
                "must be registered, not defaulted)",
                stage_id=self.stage_id,
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "applied_event_registry_before": list(self.applied_event_registry_before),
            "declared_withholding_policy_id": self.declared_withholding_policy_id,
            "dividend": None if self.dividend is None else self.dividend.to_json_dict(),
            "event_order": list(self.event_order),
            "kind": "CORPORATE_ACTION_STAGE",
            "payment": None if self.payment is None else self.payment.to_json_dict(),
            "raw_marks_after_entitlement": self.raw_marks_after_entitlement.to_json_dict(),
            "raw_marks_after_split": self.raw_marks_after_split.to_json_dict(),
            "session": self.session.to_json_dict(),
            "split": None if self.split is None else self.split.to_json_dict(),
            "stage_id": self.stage_id,
            "unsupported_actions": [item.to_json_dict() for item in self.unsupported_actions],
        }


@dataclass(frozen=True)
class RebalanceStage:
    """Steps 2 through 7 for one rebalance."""

    rebalance_id: str
    fill_session: FillSession
    raw_marks: LedgerMarkSet
    target: TargetSpecification
    trade_date: date
    charge_date: date
    availability: Mapping[str, FillPriceAvailability]
    regulatory_trade_metadata: Mapping[str, RegulatoryTradeMetadataV3]
    order_quantum: str = "1"
    declared_external_flow: str = "0"
    actions_effective_on_fill_session: tuple[str, ...] = ()
    maximum_fill_deferral_bound_id: str | None = None
    participation_limit_id: str | None = None
    declared_spread_impact_model_id: str | None = None
    declared_residual_cash_disposition_id: str | None = None
    signal_diagnostics: SignalDiagnostics | None = None

    def __post_init__(self) -> None:
        _identifier(self.rebalance_id, what="rebalance_id")
        if type(self.fill_session) is not FillSession:
            raise ExecutionAccountingError(
                BLOCKED_INVALID_FILL_TIMING,
                "a rebalance admits a FillSession and nothing else",
                stage_id=self.rebalance_id,
            )
        if type(self.raw_marks) is not LedgerMarkSet:
            raise ExecutionAccountingError(
                BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT,
                "rebalance marks must be a LedgerMarkSet of frozen RawMark observations",
                stage_id=self.rebalance_id,
            )
        if type(self.target) not in (DeclaredSignedDeltas, EqualWeightTargetProgram):
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "target must be DeclaredSignedDeltas or EqualWeightTargetProgram",
                stage_id=self.rebalance_id,
            )
        for label, value in (("trade_date", self.trade_date), ("charge_date", self.charge_date)):
            if type(value) is not date:
                raise ExecutionAccountingError(
                    BLOCKED_MALFORMED_LEDGER_VALUE,
                    f"{label} must be an exact date",
                    stage_id=self.rebalance_id,
                )
        quantum = to_ledger_decimal(self.order_quantum, what="order_quantum")
        if quantum != DEFAULT_ORDER_QUANTUM:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "order_quantum must remain the NEE-119 one-share quantum",
                stage_id=self.rebalance_id,
            )
        if self.participation_limit_id is None:
            raise ExecutionAccountingError(
                BLOCKED_NO_REGISTERED_PARTICIPATION_LIMIT,
                "NEE-118 makes maximum participation a REQUIRED run parameter; a "
                "rebalance cannot decline to name a participation limit",
                stage_id=self.rebalance_id,
            )
        _identifier(self.participation_limit_id, what="participation_limit_id")
        if not isinstance(self.availability, Mapping):
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "availability must be a mapping",
                stage_id=self.rebalance_id,
            )
        availability: dict[str, FillPriceAvailability] = {}
        for raw_symbol, item in self.availability.items():
            security_id = _identifier(raw_symbol, what="availability security_id")
            if type(item) is not FillPriceAvailability or item.security_id != security_id:
                raise ExecutionAccountingError(
                    BLOCKED_MALFORMED_LEDGER_VALUE,
                    "availability values must be FillPriceAvailability rows keyed by security",
                    security_id=security_id,
                    stage_id=self.rebalance_id,
                )
            availability[security_id] = item
        metadata: dict[str, RegulatoryTradeMetadataV3] = {}
        if not isinstance(self.regulatory_trade_metadata, Mapping):
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "regulatory_trade_metadata must be a mapping keyed by security_id",
                stage_id=self.rebalance_id,
            )
        for raw_symbol, declared in self.regulatory_trade_metadata.items():
            security_id = _identifier(raw_symbol, what="regulatory metadata security_id")
            if type(declared) is not RegulatoryTradeMetadataV3:
                raise ExecutionAccountingError(
                    BLOCKED_MALFORMED_LEDGER_VALUE,
                    "regulatory metadata values must be RegulatoryTradeMetadataV3 records",
                    security_id=security_id,
                    stage_id=self.rebalance_id,
                )
            metadata[security_id] = declared
        object.__setattr__(self, "availability", MappingProxyType(availability))
        object.__setattr__(self, "regulatory_trade_metadata", MappingProxyType(metadata))
        for event_id in self.actions_effective_on_fill_session:
            _identifier(event_id, what="fill-date action event_id")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "actions_effective_on_fill_session": list(self.actions_effective_on_fill_session),
            "availability": {
                security_id: self.availability[security_id].to_json_dict()
                for security_id in _sorted_symbols(tuple(self.availability))
            },
            "charge_date": self.charge_date.isoformat(),
            "declared_external_flow": self.declared_external_flow,
            "declared_residual_cash_disposition_id": self.declared_residual_cash_disposition_id,
            "declared_spread_impact_model_id": self.declared_spread_impact_model_id,
            "fill_session": self.fill_session.to_json_dict(),
            "kind": "REBALANCE_STAGE",
            "maximum_fill_deferral_bound_id": self.maximum_fill_deferral_bound_id,
            "order_quantum": self.order_quantum,
            "participation_limit_id": self.participation_limit_id,
            "raw_marks": self.raw_marks.to_json_dict(),
            "rebalance_id": self.rebalance_id,
            "regulatory_trade_metadata": {
                security_id: {
                    "coverage_classification": (
                        self.regulatory_trade_metadata[security_id].coverage_classification
                    ),
                    "regulatory_trade_id": (
                        self.regulatory_trade_metadata[security_id].regulatory_trade_id
                    ),
                    "transaction_status": (
                        self.regulatory_trade_metadata[security_id].transaction_status
                    ),
                }
                for security_id in _sorted_symbols(tuple(self.regulatory_trade_metadata))
            },
            "signal_diagnostics": (
                None if self.signal_diagnostics is None else self.signal_diagnostics.to_json_dict()
            ),
            "target": self.target.to_json_dict(),
            "trade_date": self.trade_date.isoformat(),
        }


@dataclass(frozen=True)
class ReceivableSettlement:
    event_id: str
    amount: str

    def __post_init__(self) -> None:
        _identifier(self.event_id, what="event_id")
        if to_ledger_decimal(self.amount, what=f"{self.event_id}.amount") < 0:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "a settlement amount must be non-negative"
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {"amount": self.amount, "event_id": self.event_id}


@dataclass(frozen=True)
class SessionCloseStage:
    """Step 8: daily raw-close marks and receivable / cash transitions."""

    stage_id: str
    session: SessionRef
    raw_close_marks: LedgerMarkSet
    receivable_settlements: tuple[ReceivableSettlement, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.stage_id, what="stage_id")
        if type(self.session) is not SessionRef:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "a session close requires a typed SessionRef"
            )
        if type(self.raw_close_marks) is not LedgerMarkSet:
            raise ExecutionAccountingError(
                BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT,
                "closing marks must be a LedgerMarkSet of frozen RawMark observations",
                stage_id=self.stage_id,
            )
        seen: set[str] = set()
        for settlement in self.receivable_settlements:
            if type(settlement) is not ReceivableSettlement:
                raise ExecutionAccountingError(
                    BLOCKED_MALFORMED_LEDGER_VALUE,
                    "settlements must be ReceivableSettlement values",
                    stage_id=self.stage_id,
                )
            if settlement.event_id in seen:
                raise ExecutionAccountingError(
                    BLOCKED_DOUBLE_BOOKED_EVENT,
                    "a receivable settlement event may be booked once",
                    stage_id=self.stage_id,
                )
            seen.add(settlement.event_id)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "kind": "SESSION_CLOSE_STAGE",
            "raw_close_marks": self.raw_close_marks.to_json_dict(),
            "receivable_settlements": [
                settlement.to_json_dict()
                for settlement in sorted(
                    self.receivable_settlements, key=lambda item: item.event_id.encode("utf-8")
                )
            ],
            "session": self.session.to_json_dict(),
            "stage_id": self.stage_id,
        }


ExecutionStage = CorporateActionStage | RebalanceStage | SessionCloseStage


@dataclass(frozen=True)
class RegistryOverrides:
    """Test-injected registry sequences. Defaults are the shipped EMPTY tuples."""

    cost_rate_policies: Sequence[CostRatePolicy] = REGISTERED_COST_RATE_POLICIES
    maximum_fill_deferrals: Sequence[MaximumFillDeferral] = REGISTERED_MAXIMUM_FILL_DEFERRALS
    participation_limits: Sequence[ParticipationLimit] = REGISTERED_PARTICIPATION_LIMITS
    spread_impact_models: Sequence[SpreadImpactModel] = REGISTERED_SPREAD_IMPACT_MODELS
    residual_cash_dispositions: Sequence[ResidualCashDisposition] = (
        REGISTERED_RESIDUAL_CASH_DISPOSITIONS
    )
    unsupported_event_outcomes: Sequence[UnsupportedEventOutcome] = (
        REGISTERED_UNSUPPORTED_EVENT_OUTCOMES
    )
    withholding_policies: Sequence[WithholdingPolicy] = REGISTERED_WITHHOLDING_POLICIES
    ledger_coordinate_sources: Sequence[LedgerCoordinateSource] = (
        REGISTERED_LEDGER_COORDINATE_SOURCES
    )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "cost_rate_policies": [item.to_json_dict() for item in self.cost_rate_policies],
            "ledger_coordinate_sources": [
                item.to_json_dict() for item in self.ledger_coordinate_sources
            ],
            "maximum_fill_deferrals": [
                item.to_json_dict() for item in self.maximum_fill_deferrals
            ],
            "participation_limits": [item.to_json_dict() for item in self.participation_limits],
            "residual_cash_dispositions": [
                item.to_json_dict() for item in self.residual_cash_dispositions
            ],
            "spread_impact_models": [item.to_json_dict() for item in self.spread_impact_models],
            "unsupported_event_outcomes": [
                item.to_json_dict() for item in self.unsupported_event_outcomes
            ],
            "withholding_policies": [item.to_json_dict() for item in self.withholding_policies],
        }


@dataclass(frozen=True)
class ExecutionProgram:
    """The complete declared input for one execution run.

    ``cost_policy_id`` is an IDENTIFIER, not a record: it is resolved through the
    cost-rate-policy registry at construction time, so a caller-built
    :class:`CostRatePolicy` record cannot drive a run -- the record must be
    registered (shipped, or injected as ``TEST_CONSTRUCTED`` through
    ``registries.cost_rate_policies``) or the program refuses to exist.
    """

    program_id: str
    share_mode: str
    regulatory_fee_mode: str
    cost_policy_id: str
    transaction_tax_policy: TransactionTaxPolicy
    opening_session: SessionRef
    opening_cash: str
    opening_positions: Mapping[str, str]
    opening_receivables: str
    opening_marks: LedgerMarkSet
    stages: tuple[ExecutionStage, ...]
    tax_lot_method: str = LOT_METHOD_FIFO
    tax_lot_election_verified: bool = False
    registries: RegistryOverrides = RegistryOverrides()

    def __post_init__(self) -> None:
        _identifier(self.program_id, what="program_id")
        if self.share_mode not in REGISTERED_SHARE_MODES:
            raise ExecutionAccountingError(
                BLOCKED_UNREGISTERED_SHARE_MODE, f"unregistered share mode {self.share_mode!r}"
            )
        if self.regulatory_fee_mode not in REGISTERED_REGULATORY_FEE_MODES:
            raise ExecutionAccountingError(
                BLOCKED_UNREGISTERED_REGULATORY_FEE_MODE,
                f"unregistered regulatory fee mode {self.regulatory_fee_mode!r}",
            )
        _identifier(self.cost_policy_id, what="cost_policy_id")
        if type(self.registries) is not RegistryOverrides:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "registries must be a RegistryOverrides record",
            )
        # THE registry gate: an unregistered identifier refuses here, so a
        # caller-built CostRatePolicy record can never drive a run.
        resolved_cost_policy = resolve_cost_rate_policy(
            self.cost_policy_id, records=self.registries.cost_rate_policies
        )
        if (
            self.regulatory_fee_mode == FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY
            and resolved_cost_policy.regulatory_authority
        ):
            raise ExecutionAccountingError(
                BLOCKED_UNREGISTERED_REGULATORY_FEE_MODE,
                "a regulatory-authority cost policy may not exclude regulatory fees",
            )
        if type(self.transaction_tax_policy) is not TransactionTaxPolicy:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "a run requires an explicit supported TransactionTaxPolicy",
            )
        if type(self.opening_marks) is not LedgerMarkSet:
            raise ExecutionAccountingError(
                BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT,
                "opening marks must be a LedgerMarkSet of frozen RawMark observations",
            )
        if type(self.opening_session) is not SessionRef:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "opening_session must be a typed SessionRef"
            )
        if type(self.stages) is not tuple or not self.stages:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE, "a program requires at least one stage"
            )
        for stage in self.stages:
            if type(stage) not in (CorporateActionStage, RebalanceStage, SessionCloseStage):
                raise ExecutionAccountingError(
                    BLOCKED_MALFORMED_LEDGER_VALUE, "unregistered execution stage type"
                )
        if type(self.tax_lot_election_verified) is not bool:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "tax_lot_election_verified must be an exact bool",
            )
        positions: dict[str, str] = {}
        for raw_symbol, quantity in self.opening_positions.items():
            security_id = _identifier(raw_symbol, what="opening position security_id")
            if security_id in positions:
                raise ExecutionAccountingError(
                    BLOCKED_DUPLICATE_SECURITY_ROW,
                    "opening positions contain a duplicate security_id",
                    security_id=security_id,
                )
            value = to_ledger_decimal(quantity, what=f"opening_positions[{security_id}]")
            if value < 0:
                raise ExecutionAccountingError(
                    BLOCKED_SHORT_POSITION,
                    "canonical long-only runs reject a negative opening position",
                    security_id=security_id,
                )
            require_share_quantum(
                value, what=f"opening_positions[{security_id}]", security_id=security_id
            )
            positions[security_id] = format_ledger(value, what=f"opening_positions[{security_id}]")
        object.__setattr__(self, "opening_positions", MappingProxyType(positions))
        to_ledger_decimal(self.opening_cash, what="opening_cash")
        to_ledger_decimal(self.opening_receivables, what="opening_receivables")
        self._require_registered_ledger_sources()
        self._require_monotone_stage_sessions()

    @property
    def cost_policy(self) -> CostRatePolicy:
        """The registry-resolved cost policy. Resolution is repeated on access so
        the record a run reads is always the registered one, never a cached copy."""

        return resolve_cost_rate_policy(
            self.cost_policy_id, records=self.registries.cost_rate_policies
        )

    def _ledger_evidence_source_ids(self) -> tuple[tuple[str, str], ...]:
        """``(source_id, where)`` for every declared ledger observation."""

        found: list[tuple[str, str]] = []

        def take(marks: LedgerMarkSet, where: str) -> None:
            for security_id, mark in marks.marks.items():
                found.append((mark.evidence.source_id, f"{where}[{security_id}]"))

        take(self.opening_marks, "opening_marks")
        for stage in self.stages:
            if type(stage) is CorporateActionStage:
                take(stage.raw_marks_after_split, f"{stage.stage_id}.raw_marks_after_split")
                take(
                    stage.raw_marks_after_entitlement,
                    f"{stage.stage_id}.raw_marks_after_entitlement",
                )
            elif type(stage) is RebalanceStage:
                take(stage.raw_marks, f"{stage.rebalance_id}.raw_marks")
                if type(stage.target) is DeclaredSignedDeltas:
                    for delta in stage.target.deltas:
                        found.append(
                            (
                                delta.raw_execution_price.evidence.source_id,
                                f"{stage.rebalance_id}.deltas[{delta.security_id}]",
                            )
                        )
                else:
                    target = stage.target
                    assert isinstance(target, EqualWeightTargetProgram)
                    for security_id, price in target.raw_execution_prices.items():
                        found.append(
                            (
                                price.evidence.source_id,
                                f"{stage.rebalance_id}.raw_execution_prices[{security_id}]",
                            )
                        )
            else:
                assert isinstance(stage, SessionCloseStage)
                take(stage.raw_close_marks, f"{stage.stage_id}.raw_close_marks")
        return tuple(found)

    def _require_registered_ledger_sources(self) -> None:
        """The adjusted-price ALLOWLIST wall, resolved for every ledger evidence.

        Every ``MarketEvidenceBinding.source_id`` entering a ledger field must
        resolve to a registered :class:`LedgerCoordinateSource`. The shipped
        registry is EMPTY, so an unregistered identifier -- including
        ``TIME_SERIES_DAILY_ADJUSTED``, which no substring denylist catches --
        fails closed before a program can exist.
        """

        for source_id, where in self._ledger_evidence_source_ids():
            try:
                resolve_ledger_coordinate_source(
                    source_id, records=self.registries.ledger_coordinate_sources
                )
            except ExecutionAccountingError as exc:
                raise ExecutionAccountingError(
                    exc.state,
                    f"{where} evidence source_id {source_id!r} does not resolve to a "
                    "registered raw ledger coordinate source",
                    path=where,
                ) from exc

    def _require_monotone_stage_sessions(self) -> None:
        """Refuse a stage list whose sessions run backwards in time.

        Stages are applied in declared list order, and a dividend PAYMENT credits
        cash when its action stage is applied. Without this wall a payment dated
        AFTER a later-declared fill could finance that earlier fill (look-ahead).
        Each stage's ENTRY session must therefore be at or after the EXIT session
        of everything already applied -- where an action stage's exit is its
        payment session when one is declared -- and every session must live on
        the opening session's calendar so ordinals are comparable at all.
        """

        calendar = self.opening_session.calendar_identity
        previous = self.opening_session
        previous_label = "opening_session"
        for stage in self.stages:
            if type(stage) is CorporateActionStage:
                stage_id = stage.stage_id
                entry = stage.session
                exit_session = (
                    stage.payment.session if stage.payment is not None else stage.session
                )
            elif type(stage) is RebalanceStage:
                stage_id = stage.rebalance_id
                entry = stage.fill_session.session
                exit_session = entry
            else:
                assert isinstance(stage, SessionCloseStage)
                stage_id = stage.stage_id
                entry = stage.session
                exit_session = entry
            for session in (entry, exit_session):
                if session.calendar_identity != calendar:
                    raise ExecutionAccountingError(
                        BLOCKED_NON_MONOTONE_STAGE_SESSION,
                        "a stage session on a different calendar than the opening "
                        "session has no comparable ordinal and cannot be ordered",
                        stage_id=stage_id,
                        session=session.session_date.isoformat(),
                    )
            if (
                entry.ordinal < previous.ordinal
                or entry.session_date < previous.session_date
            ):
                raise ExecutionAccountingError(
                    BLOCKED_NON_MONOTONE_STAGE_SESSION,
                    f"stage session precedes the session already applied by "
                    f"{previous_label!r}; a later-dated cash or share transition may "
                    "not finance an earlier-dated one",
                    stage_id=stage_id,
                    session=entry.session_date.isoformat(),
                )
            previous = exit_session
            previous_label = stage_id

    def to_input_document(self) -> dict[str, Any]:
        """The declared input document whose grouped digest is the input hash."""

        return {
            "cost_policy": self.cost_policy.to_json_dict(),
            "cost_policy_id": self.cost_policy_id,
            "opening_cash": format_ledger(self.opening_cash, what="opening_cash"),
            "opening_marks": self.opening_marks.to_json_dict(),
            "opening_positions": {
                security_id: self.opening_positions[security_id]
                for security_id in _sorted_symbols(tuple(self.opening_positions))
            },
            "opening_receivables": format_ledger(
                self.opening_receivables, what="opening_receivables"
            ),
            "opening_session": self.opening_session.to_json_dict(),
            "program_id": self.program_id,
            "registries": self.registries.to_json_dict(),
            "regulatory_fee_mode": self.regulatory_fee_mode,
            "schema_version": SCHEMA_VERSION,
            "share_mode": self.share_mode,
            "stages": [stage.to_json_dict() for stage in self.stages],
            "tax_lot_election_verified": self.tax_lot_election_verified,
            "tax_lot_method": self.tax_lot_method,
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

COMMON_MARK_IDENTITY_HELD: Final = "COMMON_MARK_IDENTITY_HELD"
COMMON_MARK_IDENTITY_NOT_APPLICABLE: Final = "COMMON_MARK_IDENTITY_NOT_APPLICABLE"


@dataclass(frozen=True)
class ExecutedFill:
    """One published fill, with the running state it produced."""

    fill_index: int
    fill_id: str
    security_id: str
    side: str
    delta_raw_shares: str
    raw_execution_price: str
    gross_notional: str
    transaction_cost: str
    transaction_tax: str
    cash_after_fill: str
    positions_after_fill: Mapping[str, str]
    fill_reason_code: str
    lineage: LineageBinding

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "cash_after_fill": self.cash_after_fill,
            "delta_raw_shares": self.delta_raw_shares,
            "fill_id": self.fill_id,
            "fill_index": self.fill_index,
            "fill_reason_code": self.fill_reason_code,
            "gross_notional": self.gross_notional,
            "lineage": self.lineage.to_json_dict(),
            "positions_after_fill": dict(self.positions_after_fill),
            "raw_execution_price": self.raw_execution_price,
            "side": self.side,
            "symbol": self.security_id,
            "transaction_cost": self.transaction_cost,
            "transaction_tax": self.transaction_tax,
        }

    def golden_projection(self) -> dict[str, Any]:
        """The nine fields the NEE-116A golden fixture pins for a fill state."""

        return {
            "cash_after_fill": self.cash_after_fill,
            "delta_raw_shares": self.delta_raw_shares,
            "fill_index": self.fill_index,
            "gross_notional": self.gross_notional,
            "positions_after_fill": dict(self.positions_after_fill),
            "side": self.side,
            "symbol": self.security_id,
            "transaction_cost": self.transaction_cost,
            "transaction_tax": self.transaction_tax,
        }


@dataclass(frozen=True)
class RebalanceLedger:
    """The published ledger for one rebalance."""

    rebalance_id: str
    step: str
    fill_timing: FillSession
    nav_minus: str
    gross_trade_notional: str
    transaction_cost: str
    transaction_tax: str
    regulatory_fees_total: str
    fill_states: tuple[ExecutedFill, ...]
    cash_plus: str
    positions_plus: Mapping[str, str]
    receivables_plus: str
    nav_plus: str
    self_financing_residual: str | None
    self_financing_status: str
    gtn_ratio: str
    one_way_turnover: str
    regulatory_fee_lines: tuple[Mapping[str, Any], ...]
    lineage: LineageBinding

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "cash_plus": self.cash_plus,
            "fill_states": [fill.to_json_dict() for fill in self.fill_states],
            "fill_timing": self.fill_timing.to_json_dict(),
            "gross_trade_notional": self.gross_trade_notional,
            "gtn_ratio": self.gtn_ratio,
            "lineage": self.lineage.to_json_dict(),
            "nav_minus": self.nav_minus,
            "nav_plus": self.nav_plus,
            "one_way_turnover": self.one_way_turnover,
            "positions_plus": dict(self.positions_plus),
            "rebalance_id": self.rebalance_id,
            "receivables_plus": self.receivables_plus,
            "regulatory_fee_lines": [dict(line) for line in self.regulatory_fee_lines],
            "regulatory_fees_total": self.regulatory_fees_total,
            "self_financing_residual": self.self_financing_residual,
            "self_financing_status": self.self_financing_status,
            "step": self.step,
            "transaction_cost": self.transaction_cost,
            "transaction_tax": self.transaction_tax,
        }

    def golden_projection(self) -> dict[str, Any]:
        """The eleven fields the NEE-116A golden fixture pins for a rebalance."""

        return {
            "cash_plus": self.cash_plus,
            "fill_states": [fill.golden_projection() for fill in self.fill_states],
            "gross_trade_notional": self.gross_trade_notional,
            "nav_minus": self.nav_minus,
            "nav_plus": self.nav_plus,
            "positions_plus": dict(self.positions_plus),
            "rebalance_id": self.rebalance_id,
            "receivables_plus": self.receivables_plus,
            "self_financing_residual": self.self_financing_residual,
            "transaction_cost": self.transaction_cost,
            "transaction_tax": self.transaction_tax,
        }


@dataclass(frozen=True)
class CorporateActionOutcome:
    """The published corporate-action transition for one action session."""

    stage_id: str
    step: str
    session: SessionRef
    event_order: tuple[str, ...]
    applied_event_registry_after: tuple[str, ...]
    post_split_raw_shares: str
    split_reference_value_before: str
    split_reference_value_after: str
    nav_after_split: str
    dividend_eligible_raw_shares: str
    dividend_receivable: str
    nav_after_entitlement: str
    cash_after_payment: str
    receivables_after_payment: str
    nav_after_payment: str
    excluded_unsupported_actions: tuple[Mapping[str, Any], ...]
    lineage: LineageBinding

    def to_json_dict(self) -> dict[str, Any]:
        document = self.golden_projection()
        document.update(
            {
                "excluded_unsupported_actions": [
                    dict(item) for item in self.excluded_unsupported_actions
                ],
                "lineage": self.lineage.to_json_dict(),
                "session": self.session.to_json_dict(),
                "stage_id": self.stage_id,
                "step": self.step,
            }
        )
        return document

    def golden_projection(self) -> dict[str, Any]:
        """The twelve fields the NEE-116A golden fixture pins for the action timeline."""

        return {
            "applied_event_registry_after": list(self.applied_event_registry_after),
            "cash_after_payment": self.cash_after_payment,
            "dividend_eligible_raw_shares": self.dividend_eligible_raw_shares,
            "dividend_receivable": self.dividend_receivable,
            "event_order": list(self.event_order),
            "nav_after_entitlement": self.nav_after_entitlement,
            "nav_after_payment": self.nav_after_payment,
            "nav_after_split": self.nav_after_split,
            "post_split_raw_shares": self.post_split_raw_shares,
            "receivables_after_payment": self.receivables_after_payment,
            "split_reference_value_after": self.split_reference_value_after,
            "split_reference_value_before": self.split_reference_value_before,
        }


@dataclass(frozen=True)
class SessionCloseRecord:
    """Step 8 output: daily raw-close marks and receivable / cash transitions."""

    stage_id: str
    step: str
    session: SessionRef
    raw_close_marks: Mapping[str, str]
    receivable_settlements: tuple[Mapping[str, str], ...]
    cash_after: str
    receivables_after: str
    nav_after: str
    lineage: LineageBinding

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "cash_after": self.cash_after,
            "lineage": self.lineage.to_json_dict(),
            "nav_after": self.nav_after,
            "raw_close_marks": dict(self.raw_close_marks),
            "receivable_settlements": [dict(item) for item in self.receivable_settlements],
            "receivables_after": self.receivables_after,
            "session": self.session.to_json_dict(),
            "stage_id": self.stage_id,
            "step": self.step,
        }


@dataclass(frozen=True)
class LotPublication:
    """The published tax-lot ledger for the run, from the accepted NEE-116 kernel."""

    stage_id: str
    step: str
    method: str
    election_verified: bool
    labels: tuple[str, ...]
    open_lots: tuple[Mapping[str, Any], ...]
    realized_events: tuple[Mapping[str, Any], ...]
    lineage: LineageBinding

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "election_verified": self.election_verified,
            "labels": list(self.labels),
            "lineage": self.lineage.to_json_dict(),
            "method": self.method,
            "open_lots": [dict(item) for item in self.open_lots],
            "realized_events": [dict(item) for item in self.realized_events],
            "stage_id": self.stage_id,
            "step": self.step,
        }


# ---------------------------------------------------------------------------
# Step 1: effective share / action state and dividend entitlement
# ---------------------------------------------------------------------------


def _portfolio(
    *,
    cash: Decimal,
    positions: Mapping[str, Decimal],
    receivables: Decimal,
    marks: LedgerMarkSet,
    stage_id: str,
    session: str,
) -> PortfolioState:
    """Build the frozen kernel state, refusing a held position without a mark."""

    for security_id in positions:
        marks.require(security_id, session=session)
    if cash < 0:
        raise ExecutionAccountingError(
            BLOCKED_NEGATIVE_POST_TRADE_CASH,
            "canonical no-margin cash must be non-negative",
            stage_id=stage_id,
            session=session,
        )
    try:
        return PortfolioState(
            cash=cash,
            positions=dict(positions),
            raw_marks=dict(marks.marks),
            receivables=receivables,
        )
    except (TypeError, ValueError) as exc:
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE,
            f"frozen NEE-118 portfolio state refused this ledger: {exc}",
            stage_id=stage_id,
            session=session,
        ) from exc


def _exact_value(value: Decimal) -> Fraction:
    return Fraction(value)


def apply_corporate_action_stage(
    *,
    stage: CorporateActionStage,
    cash: Decimal,
    positions: Mapping[str, Decimal],
    receivables: Decimal,
    marks_before: LedgerMarkSet,
    share_mode: str,
    registries: RegistryOverrides,
    lineage: LineageBinding,
) -> tuple[Decimal, dict[str, Decimal], Decimal, LedgerMarkSet, CorporateActionOutcome]:
    """Step 1: apply the effective share state and recognize the entitlement.

    Split first, then the POST_SPLIT dividend entitlement, in the registered
    same-session order. The split quantity comes from the frozen
    :func:`qme.quant.equations.apply_split`; the entitlement from the frozen
    :func:`qme.quant.equations.dividend_receivable`.
    """

    session_text = stage.session.session_date.isoformat()
    if stage.declared_withholding_policy_id is not None:
        withholding = resolve_withholding_policy(
            stage.declared_withholding_policy_id, records=registries.withholding_policies
        )
        if to_ledger_decimal(
            withholding.withholding_rate, what="withholding_rate"
        ) != 0:
            raise ExecutionAccountingError(
                BLOCKED_NO_REGISTERED_WITHHOLDING_POLICY,
                "NEE-118 ships no withholding executable, so a NONZERO withholding "
                "rate cannot be booked; only an explicitly registered zero-rate "
                "policy may accompany a dividend entitlement",
                stage_id=stage.stage_id,
                session=session_text,
            )
    registry = list(stage.applied_event_registry_before)
    excluded: list[Mapping[str, Any]] = []
    for action in stage.unsupported_actions:
        held = positions.get(action.security_id, _ZERO)
        if action.registered_outcome_id is not None:
            resolve_unsupported_event_outcome(
                action.registered_outcome_id, records=registries.unsupported_event_outcomes
            )
            continue
        if held > 0:
            raise ExecutionAccountingError(
                BLOCKED_UNSUPPORTED_HELD_CORPORATE_ACTION,
                f"{action.action_type} on held security {action.security_id!r} has no "
                "sourced outcome; the run is invalid rather than silently valued at zero",
                security_id=action.security_id,
                stage_id=stage.stage_id,
                session=session_text,
            )
        excluded.append(
            {
                "action_type": action.action_type,
                "event_id": action.event_id,
                "security_id": action.security_id,
                "state": "EXCLUDED_UNSUPPORTED_UNHELD_ACTION",
            }
        )

    security: str | None = None
    if stage.split is not None and stage.dividend is not None:
        if stage.split.security_id != stage.dividend.security_id:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "combined same-session events must bind one security",
                stage_id=stage.stage_id,
                session=session_text,
            )
        security = stage.split.security_id
    elif stage.split is not None:
        security = stage.split.security_id
    elif stage.dividend is not None:
        security = stage.dividend.security_id

    for event_id in (
        term.event_id
        for term in (stage.split, stage.dividend, stage.payment)
        if term is not None
    ):
        if event_id in registry:
            raise ExecutionAccountingError(
                BLOCKED_DOUBLE_BOOKED_EVENT,
                f"event {event_id!r} is already applied",
                stage_id=stage.stage_id,
                session=session_text,
            )

    updated = dict(positions)
    held_before = _ZERO if security is None else positions.get(security, _ZERO)
    if stage.split is not None and security is not None:
        factor = to_ledger_decimal(stage.split.split_factor, what="split_factor")
        try:
            held_after = apply_split(held_before, factor)
        except (TypeError, ValueError) as exc:
            raise ExecutionAccountingError(
                BLOCKED_NONREPRESENTABLE_SHARE_QUANTITY,
                f"frozen NEE-118 split refused this transition: {exc}",
                security_id=security,
                stage_id=stage.stage_id,
                session=session_text,
            ) from exc
        updated[security] = held_after
        registry.append(stage.split.event_id)
    else:
        held_after = held_before

    if share_mode == SHARE_MODE_WHOLE_SHARE_INTEGRAL_CUSTODY and security is not None:
        _require_integral_custody(held_after, security=security, stage_id=stage.stage_id)

    reference_before = _EXACT_ZERO
    reference_after = _EXACT_ZERO
    if security is not None:
        mark_before = marks_before.require(security, session=session_text)
        mark_after_split = stage.raw_marks_after_split.require(security, session=session_text)
        reference_before = _exact_value(held_before) * _exact_value(mark_before.value)
        reference_after = _exact_value(held_after) * _exact_value(mark_after_split.value)
        if reference_before != reference_after:
            raise ExecutionAccountingError(
                BLOCKED_SPLIT_CONSERVATION_VIOLATED,
                "q_after * P_after does not equal q_before * P_before across the split",
                security_id=security,
                stage_id=stage.stage_id,
                session=session_text,
            )

    state_after_split = _portfolio(
        cash=cash,
        positions=updated,
        receivables=receivables,
        marks=stage.raw_marks_after_split,
        stage_id=stage.stage_id,
        session=session_text,
    )

    entitlement = _ZERO
    if stage.dividend is not None and security is not None:
        cash_per_share = to_ledger_decimal(
            stage.dividend.raw_cash_per_share, what="raw_cash_per_share"
        )
        try:
            entitlement = dividend_receivable(held_after, cash_per_share)
        except (TypeError, ValueError) as exc:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                f"frozen NEE-118 dividend recognition refused this term: {exc}",
                security_id=security,
                stage_id=stage.stage_id,
                session=session_text,
            ) from exc
        registry.append(stage.dividend.event_id)

    receivables_after_entitlement = quantize_ledger(
        receivables + entitlement, what="receivables_after_entitlement"
    )
    state_after_entitlement = _portfolio(
        cash=cash,
        positions=updated,
        receivables=receivables_after_entitlement,
        marks=stage.raw_marks_after_entitlement,
        stage_id=stage.stage_id,
        session=session_text,
    )

    cash_after = cash
    receivables_after = receivables_after_entitlement
    if stage.payment is not None:
        if stage.dividend is None or stage.payment.dividend_event_id != stage.dividend.event_id:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "a payment must bind the dividend event it settles",
                stage_id=stage.stage_id,
                session=session_text,
            )
        payment_session = stage.payment.session
        if payment_session.calendar_identity != stage.session.calendar_identity:
            raise ExecutionAccountingError(
                BLOCKED_INVALID_FILL_TIMING,
                "payment and action sessions do not share one calendar ID and hash",
                stage_id=stage.stage_id,
                session=session_text,
            )
        if (
            payment_session.ordinal <= stage.session.ordinal
            or payment_session.session_date <= stage.session.session_date
        ):
            raise ExecutionAccountingError(
                BLOCKED_INVALID_FILL_TIMING,
                "a payment session must strictly follow its entitlement session",
                stage_id=stage.stage_id,
                session=payment_session.session_date.isoformat(),
            )
        cash_after = quantize_ledger(cash + entitlement, what="cash_after_payment")
        receivables_after = receivables
        registry.append(stage.payment.event_id)

    state_after_payment = _portfolio(
        cash=cash_after,
        positions=updated,
        receivables=receivables_after,
        marks=stage.raw_marks_after_entitlement,
        stage_id=stage.stage_id,
        session=session_text,
    )

    outcome = CorporateActionOutcome(
        stage_id=stage.stage_id,
        step=REGISTERED_EVENT_SEQUENCE[0],
        session=stage.session,
        event_order=tuple(stage.event_order),
        applied_event_registry_after=tuple(registry),
        post_split_raw_shares=format_ledger(held_after, what="post_split_raw_shares"),
        split_reference_value_before=_format_exact_fraction(reference_before),
        split_reference_value_after=_format_exact_fraction(reference_after),
        nav_after_split=format_ledger(state_after_split.nav, what="nav_after_split"),
        dividend_eligible_raw_shares=format_ledger(
            held_after, what="dividend_eligible_raw_shares"
        ),
        dividend_receivable=format_ledger(entitlement, what="dividend_receivable"),
        nav_after_entitlement=format_ledger(
            state_after_entitlement.nav, what="nav_after_entitlement"
        ),
        cash_after_payment=format_ledger(cash_after, what="cash_after_payment"),
        receivables_after_payment=format_ledger(
            receivables_after, what="receivables_after_payment"
        ),
        nav_after_payment=format_ledger(state_after_payment.nav, what="nav_after_payment"),
        excluded_unsupported_actions=tuple(excluded),
        lineage=lineage,
    )
    return cash_after, updated, receivables_after, stage.raw_marks_after_entitlement, outcome


def _format_exact_fraction(value: Fraction) -> str:
    """Render an exact rational at the ledger quantum; a non-representable value blocks."""

    scaled = value / _LEDGER_QUANTUM
    if scaled.denominator != 1:
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE,
            "an exact ledger value is not representable at the 1e-8 quantum",
        )
    units = scaled.numerator
    sign = "-" if units < 0 else ""
    whole, fraction = divmod(abs(units), 100_000_000)
    return f"{sign}{whole}.{fraction:08d}"


def _require_integral_custody(quantity: Decimal, *, security: str, stage_id: str) -> None:
    """Whole-share mode: stored custody must be integral at the order quantum."""

    if quantity != quantity.to_integral_value():
        raise ExecutionAccountingError(
            BLOCKED_NONREPRESENTABLE_SHARE_QUANTITY,
            "whole-share mode admits no fractional custody",
            security_id=security,
            stage_id=stage_id,
        )


# ---------------------------------------------------------------------------
# Steps 4 and 5: signed target deltas and the frozen fill order
# ---------------------------------------------------------------------------


def resolve_fill_reason(
    availability: FillPriceAvailability,
    *,
    deferral_sessions: int,
    maximum_fill_deferral_bound_id: str | None,
    registries: RegistryOverrides,
    stage_id: str,
    session: str,
) -> str:
    """Walk the frozen, reason-coded fill hierarchy for one security.

    A security delisted between signal and fill short-circuits to the last rung:
    it is the only rung that can admit a fill for such a name, and without a
    sourced outcome the run is refused rather than valued.
    """

    if availability.delisted_between_signal_and_fill:
        if availability.registered_outcome_id is None:
            raise ExecutionAccountingError(
                BLOCKED_DELISTING_BETWEEN_SIGNAL_AND_FILL,
                "the security delisted between signal and fill and no sourced outcome "
                "is declared; a terminal value may not be assumed",
                security_id=availability.security_id,
                stage_id=stage_id,
                session=session,
            )
        resolve_unsupported_event_outcome(
            availability.registered_outcome_id,
            records=registries.unsupported_event_outcomes,
        )
        return FILL_REASON_SOURCED_DELISTING_OR_UNSUPPORTED_EVENT
    priced = (
        availability.official_next_session_raw_open_available
        or availability.declared_first_regular_session_print_available
    )
    if deferral_sessions == 0:
        if availability.official_next_session_raw_open_available:
            return FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN
        if availability.declared_first_regular_session_print_available:
            return FILL_REASON_DECLARED_FIRST_REGULAR_SESSION_PRINT
    if not priced:
        if availability.halted:
            raise ExecutionAccountingError(
                BLOCKED_HALTED_SECURITY_NO_REGULAR_SESSION_PRINT,
                "the security was halted and produced no regular-session print",
                security_id=availability.security_id,
                stage_id=stage_id,
                session=session,
            )
        raise ExecutionAccountingError(
            BLOCKED_MISSING_OFFICIAL_RAW_OPEN,
            "no official next-session raw open and no declared regular-session print",
            security_id=availability.security_id,
            stage_id=stage_id,
            session=session,
        )
    if maximum_fill_deferral_bound_id is None:
        raise ExecutionAccountingError(
            BLOCKED_NO_REGISTERED_MAXIMUM_FILL_DEFERRAL,
            _DEFERRAL_EMPTY_MESSAGE,
            security_id=availability.security_id,
            stage_id=stage_id,
            session=session,
        )
    bound = resolve_maximum_fill_deferral(
        maximum_fill_deferral_bound_id, records=registries.maximum_fill_deferrals
    )
    if deferral_sessions > bound.maximum_deferral_sessions:
        raise ExecutionAccountingError(
            BLOCKED_UNAVAILABLE_FILL_AFTER_REGISTERED_BOUND,
            f"a fill deferred {deferral_sessions} sessions exceeds the registered bound "
            f"of {bound.maximum_deferral_sessions}",
            security_id=availability.security_id,
            stage_id=stage_id,
            session=session,
        )
    return FILL_REASON_BOUNDED_NEXT_SESSION_DEFERRAL


def order_fills(deltas: Sequence[SignedTargetDelta]) -> tuple[SignedTargetDelta, ...]:
    """Step 5: ALL_SELLS_THEN_ALL_BUYS, then UTF8_BYTES_ASCENDING within each stage.

    Both keys are registered (NEE-118 ``execution.fill_order`` and the NEE-119
    stable-key order). Ordering never changes economic target priority: it
    permutes the *sequence* of the same signed deltas, and the resulting target
    share vector is identical for every input permutation.
    """

    return tuple(
        sorted(
            deltas,
            key=lambda delta: (0 if delta.is_sell else 1, delta.security_id.encode("utf-8")),
        )
    )


def _fill_identity(delta: SignedTargetDelta, *, fill_session: FillSession) -> str:
    """Content-derived fill identifier; never a counter, never input order."""

    evidence = delta.raw_execution_price.evidence
    return grouped_document_digest(
        {
            "delta_raw_shares": format_ledger(delta.delta_raw_shares, what="delta_raw_shares"),
            "engine_id": ENGINE_ID,
            "evidence": {
                "calendar_id": evidence.calendar_id,
                "observation_end_session": evidence.observation_end_session.isoformat(),
                "observation_start_session": evidence.observation_start_session.isoformat(),
                "snapshot_id": evidence.snapshot_id,
                "source_id": evidence.source_id,
            },
            "fill_session": fill_session.session.to_json_dict(),
            "raw_execution_price": format_ledger(
                delta.raw_execution_price.value, what="raw_execution_price"
            ),
            "symbol": delta.security_id,
        }
    )


def _equal_weight_targets(
    *,
    program: EqualWeightTargetProgram,
    positions: Mapping[str, Decimal],
    nav_minus: Decimal,
    order_quantum: Decimal,
    stage_id: str,
) -> dict[str, Fraction]:
    """The NEE-119 registered equal-weight target vector, in exact rationals."""

    quantum = Fraction(order_quantum)
    count = len(program.selected)
    nav = Fraction(nav_minus)
    targets: dict[str, Fraction] = {}
    for security_id in program.selected:
        current = Fraction(positions.get(security_id, _ZERO))
        residual = current - (current // quantum) * quantum
        price = Fraction(program.raw_execution_prices[security_id].value)
        budget = nav / count - residual * price
        units = budget / price / quantum
        target = residual + (units // 1) * quantum
        if target < 0:
            raise ExecutionAccountingError(
                BLOCKED_SHORT_POSITION,
                "the registered target formula produced a negative long-only target",
                security_id=security_id,
                stage_id=stage_id,
            )
        targets[security_id] = target
        if budget >= 0:
            # Cross-check the floor term against the frozen NEE-118 rounder.
            kernel_units = round_long_target_shares(
                _decimal_from_fraction(budget),
                program.raw_execution_prices[security_id],
                order_quantum=order_quantum,
            )
            if Fraction(kernel_units) != (units // 1) * quantum:
                raise ExecutionAccountingError(
                    BLOCKED_MALFORMED_LEDGER_VALUE,
                    "the exact target floor disagrees with the frozen NEE-118 rounder",
                    security_id=security_id,
                    stage_id=stage_id,
                )
    for security_id, quantity in positions.items():
        if security_id in targets:
            continue
        current = Fraction(quantity)
        targets[security_id] = current - (current // quantum) * quantum
    return targets


def _decimal_from_fraction(value: Fraction) -> Decimal:
    """Render an exact rational as a Decimal at the working precision."""

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        return Decimal(value.numerator) / Decimal(value.denominator)


def _deltas_from_targets(
    *,
    targets: Mapping[str, Fraction],
    positions: Mapping[str, Decimal],
    prices: Mapping[str, RawExecutionPrice],
    stage_id: str,
) -> tuple[SignedTargetDelta, ...]:
    deltas: list[SignedTargetDelta] = []
    for security_id in _sorted_symbols(tuple(targets)):
        target = targets[security_id]
        current = Fraction(positions.get(security_id, _ZERO))
        delta = target - current
        if delta == 0:
            continue
        price = prices.get(security_id)
        if price is None:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "a traded security has no declared raw execution price",
                security_id=security_id,
                stage_id=stage_id,
            )
        deltas.append(
            SignedTargetDelta(
                security_id=security_id,
                delta_raw_shares=_format_exact_fraction(delta),
                raw_execution_price=price,
            )
        )
    return tuple(deltas)


# ---------------------------------------------------------------------------
# Steps 6 and 7: costs, fees, taxes, cash, invariants, published fills
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Execution:
    base: RebalanceResult
    after: PortfolioState
    regulatory_fees_total: Decimal
    fee_lines: tuple[Mapping[str, Any], ...]
    fee_by_security_id: Mapping[str, Decimal]
    posted: AsymmetricRebalanceResultV3 | None = None


def _trade(delta: SignedTargetDelta, *, stage_id: str) -> Trade:
    try:
        return Trade(
            symbol=delta.security_id,
            delta_shares=delta.delta,
            raw_execution_price=delta.raw_execution_price,
        )
    except (TypeError, ValueError) as exc:
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE,
            f"frozen NEE-118 trade construction refused this delta: {exc}",
            security_id=delta.security_id,
            stage_id=stage_id,
        ) from exc


def _translate_kernel_error(
    exc: Exception, *, stage_id: str, session: str
) -> ExecutionAccountingError:
    text = str(exc)
    if "negative cash" in text or "no-margin cash" in text:
        state = BLOCKED_NEGATIVE_POST_TRADE_CASH
    elif "short position" in text:
        state = BLOCKED_SHORT_POSITION
    elif "not representable at quantum" in text:
        state = BLOCKED_NONREPRESENTABLE_SHARE_QUANTITY
    else:
        state = BLOCKED_MALFORMED_LEDGER_VALUE
    return ExecutionAccountingError(
        state, f"frozen kernel refused this rebalance: {text}", stage_id=stage_id, session=session
    )


def _execute_fills(
    *,
    before: PortfolioState,
    ordered: Sequence[SignedTargetDelta],
    stage: RebalanceStage,
    program: ExecutionProgram,
    repository_root: Path,
) -> _Execution:
    """Run the canonical kernel once, posting historical fees when required."""

    session = stage.fill_session.session.session_date.isoformat()
    flow = to_ledger_decimal(stage.declared_external_flow, what="declared_external_flow")
    if flow != 0:
        raise ExecutionAccountingError(
            BLOCKED_UNSUPPORTED_EXTERNAL_FLOW,
            "canonical runs reject deposits and withdrawals",
            stage_id=stage.rebalance_id,
            session=session,
        )
    trades = [_trade(delta, stage_id=stage.rebalance_id) for delta in ordered]
    marks_after = dict(stage.raw_marks.marks)
    if program.regulatory_fee_mode == FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY:
        try:
            base = rebalance(
                before,
                trades,
                transaction_cost_rate_bps=program.cost_policy.rate_bps,
                transaction_tax_policy=program.transaction_tax_policy,
                raw_marks_after=marks_after,
            )
        except ExternalFlowNotSupported as exc:
            raise ExecutionAccountingError(
                BLOCKED_UNSUPPORTED_EXTERNAL_FLOW,
                str(exc),
                stage_id=stage.rebalance_id,
                session=session,
            ) from exc
        except (TypeError, ValueError) as exc:
            raise _translate_kernel_error(
                exc, stage_id=stage.rebalance_id, session=session
            ) from exc
        return _Execution(
            base=base,
            after=base.after,
            regulatory_fees_total=_ZERO,
            fee_lines=(),
            fee_by_security_id=MappingProxyType({}),
        )

    metadata: dict[int, RegulatoryTradeMetadataV3] = {}
    for index, delta in enumerate(ordered):
        if not delta.is_sell:
            continue
        declared = stage.regulatory_trade_metadata.get(delta.security_id)
        if declared is None:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "a SELL fill needs declared regulatory trade metadata",
                security_id=delta.security_id,
                stage_id=stage.rebalance_id,
                session=session,
            )
        metadata[index] = declared
    try:
        result: AsymmetricRebalanceResultV3 = rebalance_with_historical_regulatory_fees_v3(
            before,
            trades,
            trade_date=stage.trade_date,
            charge_date=stage.charge_date,
            regulatory_trade_metadata=metadata,
            transaction_cost_rate_bps=program.cost_policy.rate_bps,
            transaction_tax_policy=program.transaction_tax_policy,
            repository_root=repository_root,
            raw_marks_after=marks_after,
        )
    except AsymmetricCostV3Error as exc:
        text = str(exc)
        state = (
            BLOCKED_NEGATIVE_POST_TRADE_CASH
            if "negative cash" in text
            else BLOCKED_MALFORMED_LEDGER_VALUE
        )
        raise ExecutionAccountingError(
            state,
            f"the accepted V3 fee adapter refused this rebalance: {text}",
            stage_id=stage.rebalance_id,
            session=session,
        ) from exc
    except ExternalFlowNotSupported as exc:
        raise ExecutionAccountingError(
            BLOCKED_UNSUPPORTED_EXTERNAL_FLOW,
            str(exc),
            stage_id=stage.rebalance_id,
            session=session,
        ) from exc
    except (TypeError, ValueError) as exc:
        raise _translate_kernel_error(exc, stage_id=stage.rebalance_id, session=session) from exc
    fee_by_security_id: dict[str, Decimal] = {}
    lines: list[Mapping[str, Any]] = []
    for line in result.regulatory_fee_lines:
        amount = to_ledger_decimal(line.ledger_amount, what="regulatory ledger_amount")
        fee_by_security_id[line.symbol] = fee_by_security_id.get(line.symbol, _ZERO) + amount
        lines.append(
            {
                "charge_date": line.charge_date,
                "finra_taf_raw": line.finra_taf_raw,
                "kernel_status": line.kernel_status,
                "ledger_amount": line.ledger_amount,
                "regulatory_trade_id": line.regulatory_trade_id,
                "sec31_raw": line.sec31_raw,
                "side": line.side,
                "symbol": line.symbol,
                "total_raw": line.total_raw,
                "trade_date": line.trade_date,
            }
        )
    lines.sort(key=lambda item: (str(item["side"]), str(item["symbol"])))
    return _Execution(
        base=result.base,
        after=result.after,
        regulatory_fees_total=result.regulatory_fees_total,
        fee_lines=tuple(lines),
        fee_by_security_id=MappingProxyType(fee_by_security_id),
        posted=result,
    )


def _replay_fills(
    *,
    before: PortfolioState,
    ordered: Sequence[SignedTargetDelta],
    stage: RebalanceStage,
    program: ExecutionProgram,
    reason_by_security_id: Mapping[str, str],
    lineage: LineageBinding,
) -> tuple[tuple[ExecutedFill, ...], PortfolioState]:
    """Replay fill-by-fill so the published sequence is observable, not inferred."""

    session = stage.fill_session.session.session_date.isoformat()
    state = before
    rows: list[ExecutedFill] = []
    for index, delta in enumerate(ordered):
        trade = _trade(delta, stage_id=stage.rebalance_id)
        try:
            step = rebalance(
                state,
                [trade],
                transaction_cost_rate_bps=program.cost_policy.rate_bps,
                transaction_tax_policy=program.transaction_tax_policy,
                raw_marks_after=dict(stage.raw_marks.marks),
            )
        except (TypeError, ValueError) as exc:
            raise _translate_kernel_error(
                exc, stage_id=stage.rebalance_id, session=session
            ) from exc
        state = step.after
        if program.share_mode == SHARE_MODE_WHOLE_SHARE_INTEGRAL_CUSTODY:
            for security_id, quantity in state.positions.items():
                _require_integral_custody(
                    quantity, security=security_id, stage_id=stage.rebalance_id
                )
        rows.append(
            ExecutedFill(
                fill_index=index,
                fill_id=_fill_identity(delta, fill_session=stage.fill_session),
                security_id=delta.security_id,
                side="SELL" if delta.is_sell else "BUY",
                delta_raw_shares=format_ledger(delta.delta, what="delta_raw_shares"),
                raw_execution_price=format_ledger(
                    delta.raw_execution_price.value, what="raw_execution_price"
                ),
                gross_notional=format_ledger(trade.gross_notional, what="gross_notional"),
                transaction_cost=format_ledger(step.transaction_cost, what="transaction_cost"),
                transaction_tax=format_ledger(step.transaction_taxes, what="transaction_tax"),
                cash_after_fill=format_ledger(state.cash, what="cash_after_fill"),
                positions_after_fill=MappingProxyType(
                    {
                        security_id: format_ledger(state.positions[security_id], what="position")
                        for security_id in _sorted_symbols(tuple(state.positions))
                    }
                ),
                fill_reason_code=reason_by_security_id[delta.security_id],
                lineage=lineage,
            )
        )
    return tuple(rows), state


def _self_financing(execution: _Execution) -> tuple[str | None, str]:
    """Return the common-mark residual when the identity applies, else say why not.

    With posted regulatory fees the residual is the accepted V3 adapter's own
    extended identity ``NAV_plus = NAV_minus - TC - TAX - regulatory_fees``;
    without them it is the frozen NEE-118 identity. The identity is undefined
    when execution price differs from the common mark, receivables move, or the
    before/after marks differ, and that case is reason-coded rather than forced.
    """

    try:
        if execution.posted is not None:
            residual = asymmetric_self_financing_error_v3(execution.posted)
        else:
            residual = self_financing_error(execution.base)
    except ValueError:
        return None, COMMON_MARK_IDENTITY_NOT_APPLICABLE
    total = quantize_ledger(residual, what="self_financing_residual")
    return format_ledger(total, what="self_financing_residual"), COMMON_MARK_IDENTITY_HELD


def _execute_rebalance_stage(
    *,
    stage: RebalanceStage,
    cash: Decimal,
    positions: Mapping[str, Decimal],
    receivables: Decimal,
    program: ExecutionProgram,
    repository_root: Path,
    lineage: LineageBinding,
) -> tuple[
    Decimal,
    dict[str, Decimal],
    Decimal,
    LedgerMarkSet,
    RebalanceLedger,
    tuple[_LotEvent, ...],
]:
    """Steps 2 through 7 for one rebalance."""

    session = stage.fill_session.session.session_date.isoformat()
    if stage.actions_effective_on_fill_session:
        raise ExecutionAccountingError(
            BLOCKED_CORPORATE_ACTION_ON_FILL_DATE,
            "a corporate action effective on the fill session makes the fill coordinate "
            f"ambiguous: {sorted(stage.actions_effective_on_fill_session)}",
            stage_id=stage.rebalance_id,
            session=session,
        )
    if stage.declared_spread_impact_model_id is not None:
        resolve_spread_impact_model(
            stage.declared_spread_impact_model_id, records=registries_of(program).spread_impact_models
        )
    if stage.declared_residual_cash_disposition_id is not None:
        resolve_residual_cash_disposition(
            stage.declared_residual_cash_disposition_id,
            records=registries_of(program).residual_cash_dispositions,
        )
    if stage.participation_limit_id is None:
        # Unreachable through the public constructor (RebalanceStage refuses a
        # None participation limit), kept as run-time defence in depth.
        raise ExecutionAccountingError(
            BLOCKED_NO_REGISTERED_PARTICIPATION_LIMIT,
            _PARTICIPATION_EMPTY_MESSAGE,
            stage_id=stage.rebalance_id,
            session=session,
        )
    resolve_participation_limit(
        stage.participation_limit_id, records=registries_of(program).participation_limits
    )

    # Step 2 and step 3.
    before = _portfolio(
        cash=cash,
        positions=positions,
        receivables=receivables,
        marks=stage.raw_marks,
        stage_id=stage.rebalance_id,
        session=session,
    )
    nav_minus = before.nav

    # Step 4.
    if type(stage.target) is DeclaredSignedDeltas:
        candidate = stage.target.deltas
        prices = {row.security_id: row.raw_execution_price for row in candidate}
    else:
        program_target = stage.target
        assert isinstance(program_target, EqualWeightTargetProgram)
        prices = dict(program_target.raw_execution_prices)
        targets = _equal_weight_targets(
            program=program_target,
            positions=positions,
            nav_minus=nav_minus,
            order_quantum=to_ledger_decimal(stage.order_quantum, what="order_quantum"),
            stage_id=stage.rebalance_id,
        )
        candidate = _solve_with_negative_cash_repair(
            targets=targets,
            selected=program_target.selected,
            positions=positions,
            prices=prices,
            before=before,
            stage=stage,
            program=program,
            repository_root=repository_root,
            order_quantum=to_ledger_decimal(stage.order_quantum, what="order_quantum"),
        )

    ordered = order_fills(candidate)
    reason_by_security_id: dict[str, str] = {}
    for delta in ordered:
        availability = stage.availability.get(delta.security_id)
        if availability is None:
            raise ExecutionAccountingError(
                BLOCKED_MISSING_OFFICIAL_RAW_OPEN,
                "no declared fill-price availability for a traded security",
                security_id=delta.security_id,
                stage_id=stage.rebalance_id,
                session=session,
            )
        reason = resolve_fill_reason(
            availability,
            deferral_sessions=stage.fill_session.deferral_sessions,
            maximum_fill_deferral_bound_id=stage.maximum_fill_deferral_bound_id,
            registries=registries_of(program),
            stage_id=stage.rebalance_id,
            session=session,
        )
        if reason != stage.fill_session.reason_code:
            raise ExecutionAccountingError(
                BLOCKED_UNREGISTERED_FILL_REASON_CODE,
                f"resolved fill reason {reason!r} differs from the declared "
                f"{stage.fill_session.reason_code!r}",
                security_id=delta.security_id,
                stage_id=stage.rebalance_id,
                session=session,
            )
        reason_by_security_id[delta.security_id] = reason

    # Steps 5 and 6.
    execution = _execute_fills(
        before=before,
        ordered=ordered,
        stage=stage,
        program=program,
        repository_root=repository_root,
    )

    # Step 7.
    replayed, replay_state = _replay_fills(
        before=before,
        ordered=ordered,
        stage=stage,
        program=program,
        reason_by_security_id=reason_by_security_id,
        lineage=lineage,
    )
    if replay_state.positions != execution.base.after.positions:
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE,
            "the staged fill replay disagrees with the batch kernel result",
            stage_id=stage.rebalance_id,
            session=session,
        )
    if replay_state.cash != execution.base.after.cash:
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE,
            "the staged fill replay cash disagrees with the batch kernel result",
            stage_id=stage.rebalance_id,
            session=session,
        )
    residual, status = _self_financing(execution)
    after = execution.after
    ledger = RebalanceLedger(
        rebalance_id=stage.rebalance_id,
        step=REGISTERED_EVENT_SEQUENCE[6],
        fill_timing=stage.fill_session,
        nav_minus=format_ledger(nav_minus, what="nav_minus"),
        gross_trade_notional=format_ledger(
            execution.base.gross_trade_notional, what="gross_trade_notional"
        ),
        transaction_cost=format_ledger(
            execution.base.transaction_cost, what="transaction_cost"
        ),
        transaction_tax=format_ledger(
            execution.base.transaction_taxes, what="transaction_tax"
        ),
        regulatory_fees_total=format_ledger(
            execution.regulatory_fees_total, what="regulatory_fees_total"
        ),
        fill_states=replayed,
        cash_plus=format_ledger(after.cash, what="cash_plus"),
        positions_plus=MappingProxyType(
            {
                security_id: format_ledger(after.positions[security_id], what="position")
                for security_id in _sorted_symbols(tuple(after.positions))
            }
        ),
        receivables_plus=format_ledger(after.receivables, what="receivables_plus"),
        nav_plus=format_ledger(after.nav, what="nav_plus"),
        self_financing_residual=residual,
        self_financing_status=status,
        gtn_ratio=format(execution.base.gtn_ratio, "f"),
        one_way_turnover=format(execution.base.one_way_turnover, "f"),
        regulatory_fee_lines=execution.fee_lines,
        lineage=lineage,
    )
    published = tuple(
        _LotEvent(
            security_id=row_security_id(row),
            trade_date=stage.trade_date,
            side=row.side,
            shares=format_ledger(abs(to_ledger_decimal(row.delta_raw_shares, what="d")), what="d"),
            price=row.raw_execution_price,
            fees=format_ledger(
                to_ledger_decimal(row.transaction_cost, what="tc")
                + to_ledger_decimal(row.transaction_tax, what="tax")
                + execution.fee_by_security_id.get(row.security_id, _ZERO),
                what="fill_fees",
            ),
            fill_id=row.fill_id,
        )
        for row in replayed
    )
    return (
        after.cash,
        dict(after.positions),
        after.receivables,
        stage.raw_marks,
        ledger,
        published,
    )


def row_security_id(row: ExecutedFill) -> str:
    """The already-resolved security identifier a published fill carries."""

    return row.security_id


@dataclass(frozen=True)
class _LotEvent:
    """One published fill in the coordinate the NEE-116 tax-lot kernel consumes."""

    security_id: str
    trade_date: date
    side: str
    shares: str
    price: str
    fees: str
    fill_id: str


def registries_of(program: ExecutionProgram) -> RegistryOverrides:
    """The registry set this run resolves against (shipped EMPTY by default)."""

    return program.registries


def _solve_with_negative_cash_repair(
    *,
    targets: Mapping[str, Fraction],
    selected: Sequence[str],
    positions: Mapping[str, Decimal],
    prices: Mapping[str, RawExecutionPrice],
    before: PortfolioState,
    stage: RebalanceStage,
    program: ExecutionProgram,
    repository_root: Path,
    order_quantum: Decimal,
) -> tuple[SignedTargetDelta, ...]:
    """Run the NEE-119 repair loop to convergence BEFORE the kernel is asked to post.

    ``qme.quant.equations.rebalance`` refuses a cash-negative order set rather
    than repairing it, and the V3 fee adapter refuses to rescale trades. The
    contract registers the repair, so it runs here: decrement one selected
    target by one order quantum, recompute costs, taxes, fees, and rounding, and
    stop only when ``cash_post >= 0``. Each step strictly reduces the total
    target share count, so the loop terminates without an invented step limit.
    """

    quantum = Fraction(order_quantum)
    working = dict(targets)
    while True:
        deltas = _deltas_from_targets(
            targets=working, positions=positions, prices=prices, stage_id=stage.rebalance_id
        )
        if not deltas:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "the solved target vector produces no ledger event",
                stage_id=stage.rebalance_id,
            )
        try:
            _execute_fills(
                before=before,
                ordered=order_fills(deltas),
                stage=stage,
                program=program,
                repository_root=repository_root,
            )
        except ExecutionAccountingError as exc:
            if exc.state != BLOCKED_NEGATIVE_POST_TRADE_CASH:
                raise
            candidates = []
            for security_id in selected:
                current = Fraction(positions.get(security_id, _ZERO))
                residual = current - (current // quantum) * quantum
                if working[security_id] >= residual + quantum:
                    candidates.append(security_id)
            if not candidates:
                raise ExecutionAccountingError(
                    BLOCKED_NEGATIVE_POST_TRADE_CASH,
                    "the registered repair exhausted every selected target and cash "
                    "is still negative after costs and rounding",
                    stage_id=stage.rebalance_id,
                ) from exc
            chosen = max(
                candidates,
                key=lambda security_id: (
                    working[security_id] * Fraction(prices[security_id].value),
                    security_id.encode("utf-8"),
                ),
            )
            working[chosen] = working[chosen] - quantum
            continue
        return deltas


# ---------------------------------------------------------------------------
# Step 7 (lots) and step 8 (session close)
# ---------------------------------------------------------------------------


def publish_tax_lots(
    *,
    program: ExecutionProgram,
    opening_events: Sequence[_LotEvent],
    lot_events: Sequence[_LotEvent],
    splits: Sequence[Split],
    final_positions: Mapping[str, Decimal],
    lineage: LineageBinding,
) -> LotPublication:
    """Publish lots through the accepted NEE-116 tax-lot kernel and cross-check them.

    The engine seeds one opening lot per opening position at the opening raw
    mark, so the published open lots are comparable to the ledger positions. A
    per-security disagreement between open-lot shares and ledger shares is
    ``BLOCKED_INCONSISTENT_TAX_LOTS``.
    """

    fills = [
        Fill(
            security_id=event.security_id,
            trade_date=event.trade_date,
            side="BUY" if event.side == "BUY" else "SELL",
            shares=event.shares,
            price=event.price,
            fees=event.fees,
            fill_id=event.fill_id,
        )
        for event in (*opening_events, *lot_events)
    ]
    try:
        ledger: TaxLotLedger = build_tax_lot_ledger(
            fills,
            splits=list(splits),
            method=program.tax_lot_method,
            election_verified=program.tax_lot_election_verified,
        )
    except TaxLotError as exc:
        raise ExecutionAccountingError(
            BLOCKED_INCONSISTENT_TAX_LOTS,
            f"the accepted NEE-116 tax-lot kernel refused these fills: {exc}",
            stage_id=program.program_id,
        ) from exc
    open_by_security: dict[str, Fraction] = {}
    for lot in ledger.open_lots:
        open_by_security[lot.security_id] = open_by_security.get(
            lot.security_id, _EXACT_ZERO
        ) + to_exact(lot.shares, what="lot shares")
    for security_id, quantity in final_positions.items():
        expected = Fraction(quantity)
        observed = open_by_security.get(security_id, _EXACT_ZERO)
        if observed != expected:
            raise ExecutionAccountingError(
                BLOCKED_INCONSISTENT_TAX_LOTS,
                f"open-lot shares {observed} do not reconcile with ledger shares {expected}",
                security_id=security_id,
                stage_id=program.program_id,
            )
    for security_id, observed in open_by_security.items():
        if security_id not in final_positions and observed != _EXACT_ZERO:
            raise ExecutionAccountingError(
                BLOCKED_INCONSISTENT_TAX_LOTS,
                "an open lot exists for a security the ledger does not hold",
                security_id=security_id,
                stage_id=program.program_id,
            )
    return LotPublication(
        stage_id=program.program_id,
        step=REGISTERED_EVENT_SEQUENCE[6],
        method=ledger.method,
        election_verified=ledger.election_verified,
        labels=tuple(ledger.labels),
        open_lots=tuple(
            {
                "acquired": lot.acquired.isoformat(),
                "basis": lot.basis,
                "holding_start": lot.holding_start.isoformat(),
                "lot_id": lot.lot_id,
                "security_id": lot.security_id,
                "shares": lot.shares,
            }
            for lot in ledger.open_lots
        ),
        realized_events=tuple(
            {
                "basis": event.basis,
                "proceeds": event.proceeds,
                "raw_gain": event.raw_gain,
                "recognized_gain": event.recognized_gain,
                "sale_date": event.sale_date.isoformat(),
                "security_id": event.security_id,
                "shares": event.shares,
                "term": event.term,
                "wash_disallowed": event.wash_disallowed,
            }
            for event in ledger.realized
        ),
        lineage=lineage,
    )


def _apply_session_close(
    *,
    stage: SessionCloseStage,
    cash: Decimal,
    positions: Mapping[str, Decimal],
    receivables: Decimal,
    lineage: LineageBinding,
) -> tuple[Decimal, Decimal, LedgerMarkSet, SessionCloseRecord]:
    """Step 8: publish raw-close marks and settle declared receivables."""

    session = stage.session.session_date.isoformat()
    settled = _ZERO
    for settlement in stage.receivable_settlements:
        settled = quantize_ledger(
            settled + to_ledger_decimal(settlement.amount, what="settlement amount"),
            what="settled_receivables",
        )
    if settled > receivables:
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE,
            "a settlement exceeds the recognized receivable balance",
            stage_id=stage.stage_id,
            session=session,
        )
    cash_after = quantize_ledger(cash + settled, what="cash_after_close")
    receivables_after = quantize_ledger(receivables - settled, what="receivables_after_close")
    state = _portfolio(
        cash=cash_after,
        positions=positions,
        receivables=receivables_after,
        marks=stage.raw_close_marks,
        stage_id=stage.stage_id,
        session=session,
    )
    record = SessionCloseRecord(
        stage_id=stage.stage_id,
        step=REGISTERED_EVENT_SEQUENCE[7],
        session=stage.session,
        raw_close_marks=MappingProxyType(stage.raw_close_marks.to_json_dict()),
        receivable_settlements=tuple(
            {"amount": format_ledger(item.amount, what="amount"), "event_id": item.event_id}
            for item in sorted(
                stage.receivable_settlements, key=lambda item: item.event_id.encode("utf-8")
            )
        ),
        cash_after=format_ledger(cash_after, what="cash_after"),
        receivables_after=format_ledger(receivables_after, what="receivables_after"),
        nav_after=format_ledger(state.nav, what="nav_after"),
        lineage=lineage,
    )
    return cash_after, receivables_after, stage.raw_close_marks, record


# ---------------------------------------------------------------------------
# Run orchestration and manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionManifest:
    """The run manifest: identity, bindings, lineage, non-claims, self-hash."""

    program_id: str
    share_mode: str
    regulatory_fee_mode: str
    bound_artifacts: KernelBindingSet
    lineage: LineageBinding

    def _document(self) -> dict[str, Any]:
        return {
            "accounting_coordinate": ACCOUNTING_COORDINATE,
            "bound_artifacts": self.bound_artifacts.to_json_dict(),
            "canonical_tax_metric_label": CANONICAL_TAX_METRIC_LABEL,
            "claims": dict(NON_CLAIMS),
            "engine_id": ENGINE_ID,
            "equation_spec_id": EQUATION_SPEC_ID,
            "equations": dict(REGISTERED_EQUATIONS),
            "event_sequence": list(REGISTERED_EVENT_SEQUENCE),
            "fill_order": REGISTERED_FILL_ORDER,
            "fill_reason_precedence": list(REGISTERED_FILL_REASON_PRECEDENCE),
            "lineage": self.lineage.to_json_dict(),
            "method_id": METHOD_ID,
            "negative_cash_repair_choice_order": list(
                REGISTERED_NEGATIVE_CASH_REPAIR_CHOICE_ORDER
            ),
            "negative_cash_repair_step": REGISTERED_NEGATIVE_CASH_REPAIR_STEP,
            "program_id": self.program_id,
            "regulatory_fee_mode": self.regulatory_fee_mode,
            "schema_version": SCHEMA_VERSION,
            "share_mode": self.share_mode,
            "stable_key_order": REGISTERED_STABLE_KEY_ORDER,
            "tax_metric_label_authority": TAX_METRIC_LABEL_AUTHORITY,
            "unresolved_alternate_tax_metric_label": UNRESOLVED_ALTERNATE_TAX_METRIC_LABEL,
        }

    @property
    def self_sha256_grouped(self) -> str:
        return grouped_document_digest(self._document())

    def to_json_dict(self) -> dict[str, Any]:
        document = self._document()
        document["self_sha256_grouped"] = self.self_sha256_grouped
        return document


@dataclass(frozen=True)
class ExecutionRun:
    """Every artifact one execution program produced."""

    program_id: str
    manifest: ExecutionManifest
    state: str
    initial_nav: str
    final_nav: str
    final_cash: str
    final_positions: Mapping[str, str]
    final_receivables: str
    rebalance_ledgers: tuple[RebalanceLedger, ...]
    action_outcomes: tuple[CorporateActionOutcome, ...]
    session_close_records: tuple[SessionCloseRecord, ...]
    lots: LotPublication

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "action_outcomes": [item.to_json_dict() for item in self.action_outcomes],
            "final_cash": self.final_cash,
            "final_nav": self.final_nav,
            "final_positions": dict(self.final_positions),
            "final_receivables": self.final_receivables,
            "initial_nav": self.initial_nav,
            "lots": self.lots.to_json_dict(),
            "manifest": self.manifest.to_json_dict(),
            "program_id": self.program_id,
            "rebalance_ledgers": [item.to_json_dict() for item in self.rebalance_ledgers],
            "session_close_records": [
                item.to_json_dict() for item in self.session_close_records
            ],
            "state": self.state,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json_dict())

    @property
    def self_sha256_grouped(self) -> str:
        return group_sha256(self.canonical_bytes())

    def golden_path_projection(self) -> dict[str, Any]:
        """The NEE-116A golden two-rebalance path shape, for byte-exact comparison."""

        if len(self.rebalance_ledgers) != 2 or len(self.action_outcomes) != 1:
            raise ExecutionAccountingError(
                BLOCKED_MALFORMED_LEDGER_VALUE,
                "the golden projection needs exactly two rebalances and one action stage",
                stage_id=self.program_id,
            )
        return {
            "final_nav": self.final_nav,
            "initial_nav": self.initial_nav,
            "rebalance_1": self.rebalance_ledgers[0].golden_projection(),
            "rebalance_2": self.rebalance_ledgers[1].golden_projection(),
            "shared_action_timeline": self.action_outcomes[0].golden_projection(),
        }


def run_execution_program(
    program: ExecutionProgram, *, repository_root: Path
) -> ExecutionRun:
    """Walk the frozen event sequence for one program and publish every artifact."""

    if type(program) is not ExecutionProgram:
        raise ExecutionAccountingError(
            BLOCKED_MALFORMED_LEDGER_VALUE, "run_execution_program requires an ExecutionProgram"
        )
    bindings = bind_registered_kernels(repository_root)
    lineage = LineageBinding(
        input_sha256_grouped=program.input_digest(),
        config_sha256_grouped=bindings.config_sha256_grouped,
        code_sha256_grouped=bindings.code_sha256_grouped,
        schema_sha256_grouped=output_schema_digest(),
    )
    cash = to_ledger_decimal(program.opening_cash, what="opening_cash")
    receivables = to_ledger_decimal(program.opening_receivables, what="opening_receivables")
    positions = {
        security_id: to_ledger_decimal(quantity, what=f"opening_positions[{security_id}]")
        for security_id, quantity in program.opening_positions.items()
    }
    marks = program.opening_marks
    if program.share_mode == SHARE_MODE_WHOLE_SHARE_INTEGRAL_CUSTODY:
        for security_id, quantity in positions.items():
            _require_integral_custody(
                quantity, security=security_id, stage_id=program.program_id
            )
    opening_state = _portfolio(
        cash=cash,
        positions=positions,
        receivables=receivables,
        marks=marks,
        stage_id=program.program_id,
        session=program.opening_session.session_date.isoformat(),
    )
    opening_events = tuple(
        _LotEvent(
            security_id=security_id,
            trade_date=program.opening_session.session_date,
            side="BUY",
            shares=format_ledger(positions[security_id], what="opening shares"),
            price=format_ledger(
                marks.require(security_id).value, what="opening mark"
            ),
            fees="0.00000000",
            fill_id=f"OPENING_LOT:{security_id}",
        )
        for security_id in _sorted_symbols(tuple(positions))
        if positions[security_id] != 0
    )

    ledgers: list[RebalanceLedger] = []
    outcomes: list[CorporateActionOutcome] = []
    closes: list[SessionCloseRecord] = []
    lot_events: list[_LotEvent] = []
    splits: list[Split] = []

    for stage in program.stages:
        if type(stage) is CorporateActionStage:
            if stage.split is not None:
                splits.append(
                    Split(
                        security_id=stage.split.security_id,
                        effective_date=stage.session.session_date,
                        ratio=stage.split.split_factor,
                    )
                )
            cash, positions, receivables, marks, outcome = apply_corporate_action_stage(
                stage=stage,
                cash=cash,
                positions=positions,
                receivables=receivables,
                marks_before=marks,
                share_mode=program.share_mode,
                registries=program.registries,
                lineage=lineage,
            )
            outcomes.append(outcome)
        elif type(stage) is RebalanceStage:
            cash, positions, receivables, marks, ledger, events = _execute_rebalance_stage(
                stage=stage,
                cash=cash,
                positions=positions,
                receivables=receivables,
                program=program,
                repository_root=repository_root,
                lineage=lineage,
            )
            ledgers.append(ledger)
            lot_events.extend(events)
        else:
            assert isinstance(stage, SessionCloseStage)
            cash, receivables, marks, record = _apply_session_close(
                stage=stage,
                cash=cash,
                positions=positions,
                receivables=receivables,
                lineage=lineage,
            )
            closes.append(record)

    final_state = _portfolio(
        cash=cash,
        positions=positions,
        receivables=receivables,
        marks=marks,
        stage_id=program.program_id,
        session=program.opening_session.session_date.isoformat(),
    )
    lots = publish_tax_lots(
        program=program,
        opening_events=opening_events,
        lot_events=lot_events,
        splits=splits,
        final_positions=positions,
        lineage=lineage,
    )
    manifest = ExecutionManifest(
        program_id=program.program_id,
        share_mode=program.share_mode,
        regulatory_fee_mode=program.regulatory_fee_mode,
        bound_artifacts=bindings,
        lineage=lineage,
    )
    return ExecutionRun(
        program_id=program.program_id,
        manifest=manifest,
        state=EXECUTION_OK,
        initial_nav=format_ledger(opening_state.nav, what="initial_nav"),
        final_nav=format_ledger(final_state.nav, what="final_nav"),
        final_cash=format_ledger(cash, what="final_cash"),
        final_positions=MappingProxyType(
            {
                security_id: format_ledger(positions[security_id], what="position")
                for security_id in _sorted_symbols(tuple(positions))
            }
        ),
        final_receivables=format_ledger(receivables, what="final_receivables"),
        rebalance_ledgers=tuple(ledgers),
        action_outcomes=tuple(outcomes),
        session_close_records=tuple(closes),
        lots=lots,
    )


__all__ = [
    "ACCOUNTING_COORDINATE",
    "BOUND_ARTIFACT_ROLES",
    "CANONICAL_TAX_METRIC_LABEL",
    "ENGINE_ID",
    "EXECUTION_FAIL_CLOSED_STATES",
    "EXECUTION_OK",
    "FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY",
    "FEE_MODE_POSTED_HISTORICAL_V3",
    "FILL_REASON_BOUNDED_NEXT_SESSION_DEFERRAL",
    "FILL_REASON_DECLARED_FIRST_REGULAR_SESSION_PRINT",
    "FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN",
    "FILL_REASON_SOURCED_DELISTING_OR_UNSUPPORTED_EVENT",
    "FORBIDDEN_LEDGER_COORDINATE_TOKENS",
    "KERNEL_CALL_SITES",
    "LEDGER_COORDINATES",
    "METHOD_ID",
    "NON_CLAIMS",
    "OUTPUT_SCHEMA_DESCRIPTOR",
    "REGISTERED_COST_RATE_POLICIES",
    "REGISTERED_EQUATIONS",
    "REGISTERED_EVENT_SEQUENCE",
    "REGISTERED_FILL_ORDER",
    "REGISTERED_FILL_REASON_PRECEDENCE",
    "REGISTERED_LEDGER_COORDINATE_SOURCES",
    "REGISTERED_MAXIMUM_FILL_DEFERRALS",
    "REGISTERED_NEGATIVE_CASH_REPAIR_CHOICE_ORDER",
    "REGISTERED_NEGATIVE_CASH_REPAIR_STEP",
    "REGISTERED_PARTICIPATION_LIMITS",
    "REGISTERED_REGULATORY_FEE_MODES",
    "REGISTERED_RESIDUAL_CASH_DISPOSITIONS",
    "REGISTERED_SAME_SESSION_EVENT_ORDER",
    "REGISTERED_SHARE_MODES",
    "REGISTERED_SOURCE_KINDS",
    "REGISTERED_SPREAD_IMPACT_MODELS",
    "REGISTERED_STABLE_KEY_ORDER",
    "REGISTERED_UNSUPPORTED_EVENT_OUTCOMES",
    "REGISTERED_WITHHOLDING_POLICIES",
    "SCHEMA_VERSION",
    "SHARE_MODE_INTEGER_ORDERS_FRACTIONAL_CUSTODY",
    "SHARE_MODE_WHOLE_SHARE_INTEGRAL_CUSTODY",
    "SHIPPED_REGISTRIES",
    "SIGNAL_DIAGNOSTIC_COORDINATES",
    "SOURCE_KINDS",
    "SOURCE_KIND_OWNER_DECISION_RECORD",
    "SOURCE_KIND_PUBLISHER_REFERENCE",
    "SOURCE_KIND_TEST_CONSTRUCTED",
    "TAX_METRIC_LABEL_AUTHORITY",
    "UNRESOLVED_ALTERNATE_TAX_METRIC_LABEL",
    "AdjustedSignalObservation",
    "BoundArtifact",
    "CashDividendTerm",
    "CorporateActionOutcome",
    "CorporateActionStage",
    "CostRatePolicy",
    "DeclaredSignedDeltas",
    "DividendPaymentTerm",
    "EligibleFillSession",
    "EqualWeightTargetProgram",
    "ExecutedFill",
    "ExecutionAccountingError",
    "ExecutionManifest",
    "ExecutionProgram",
    "ExecutionRun",
    "FillPriceAvailability",
    "FillSession",
    "KernelBindingSet",
    "LedgerCoordinateSource",
    "LedgerMarkSet",
    "LineageBinding",
    "LotPublication",
    "MaximumFillDeferral",
    "ParticipationLimit",
    "RebalanceLedger",
    "RebalanceStage",
    "ReceivableSettlement",
    "RegistryOverrides",
    "ResidualCashDisposition",
    "SessionCloseRecord",
    "SessionCloseStage",
    "SessionRef",
    "SignalDiagnostics",
    "SignedTargetDelta",
    "SpreadImpactModel",
    "SplitTerm",
    "UnsupportedActionTerm",
    "UnsupportedEventOutcome",
    "WithholdingPolicy",
    "apply_corporate_action_stage",
    "bind_registered_kernels",
    "derive_eligible_fill_session",
    "format_ledger",
    "group_sha256",
    "grouped_document_digest",
    "grouped_file_digest",
    "order_fills",
    "output_schema_digest",
    "publish_tax_lots",
    "registries_of",
    "resolve_cost_rate_policy",
    "resolve_fill_reason",
    "resolve_ledger_coordinate_source",
    "resolve_maximum_fill_deferral",
    "resolve_participation_limit",
    "resolve_residual_cash_disposition",
    "resolve_spread_impact_model",
    "resolve_unsupported_event_outcome",
    "resolve_withholding_policy",
    "run_execution_program",
    "to_exact",
    "to_ledger_decimal",
    "ungroup_sha256",
    "validate_cost_rate_policy_registry",
    "validate_maximum_fill_deferral_registry",
    "validate_shipped_registries",
]

# Import-time gate: a shipped registry record whose provenance kind may not ship
# refuses the module itself, so ``TEST_CONSTRUCTED`` can never reach a run by
# default. Shipped registries are EMPTY by design, so this is normally a no-op.
validate_shipped_registries()
