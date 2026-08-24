"""Corporate-action factor / total-return kernel (NEE-125 prebuild, M1 Lane B).

A pure, deterministic, exact-rational kernel over **immutable** raw OHLCV and a
declared corporate-action stream. It computes the separate price and volume
factors and the total-return index, and it walks the share/cash ledger for the
same action stream. It reads nothing, writes nothing, and opens no socket.

Mathematical contract
---------------------

With ``s_t`` = new shares per old share on session ``t`` and ``d_t`` = cash per
**pre-action** share on session ``t``:

* ``gross_return_t = (s_t * P_t + d_t) / P_(t-1)``
* ``TRI_t = TRI_(t-1) * gross_return_t``
* Ledger: ``q_after = s_t * q_before``; split conservation
  ``q_after * (P_before / s_t) = q_before * P_before``
* ``receivable_t = q_eligible * d_t``; NAV includes the receivable between
  entitlement and payment
* Split-only adjustment to a cutoff ``a``: ``A_(u|a) = prod s_v for u < v <= a``;
  ``P_split_adjusted_(u|a) = P_raw_u / A_(u|a)``;
  ``V_split_adjusted_(u|a) = V_raw_u * A_(u|a)``; raw dollar volume stays
  ``P_raw_u * V_raw_u``; **dividends never adjust volume**; **actions after ``a``
  are prohibited** (typed fail-closed error, never a silent skip).

Registered-methodology bindings
-------------------------------

The same-day composite ordering is **not invented here**. It is bound from
``configs/quant/qme-v0.1-total-return-methodology.json`` (methodology
``qme-point-in-time-total-return-close-v1``), whose reviewed bytes hash to
:data:`METHODOLOGY_SHA256_GROUPED`. That config registers

* ``split_policy.event_order = "SPLIT_BEFORE_DIVIDEND_UNIT_CONVERSION"`` and
  ``split_applied_before_dividend_coordinate_conversion = true``;
* ``dividend_reinvestment.coordinate = "POST_SPLIT_CASH_PER_SHARE"``;
* ``split_policy.ambiguous_pre_or_post_split_dividend =
  "BLOCKED_AMBIGUOUS_EVENT_COORDINATE"``;
* ``revision_policy.post_cutoff_event_in_current_run = "BLOCKED_POST_CUTOFF_EVENT"``;
* ``numeric_policy`` = canonical decimal strings in, binary float forbidden,
  ``ROUND_HALF_EVEN``, ``artifact_scale = 18``;
* ``dividend_reinvestment.negative_distribution_allowed = false``.

The registered gross-factor formula is
``split_factor_t * (raw_close_t + dividend_post_split_per_share_t) /
raw_close_t_minus_1``. That is the ticket formula in the post-split dividend
coordinate: with ``d_t = s_t * d_post_split_t`` the two are algebraically
identical, and :func:`gross_return` computes the ticket form while asserting the
registered form on every call.

If a caller supplies an unregistered same-day event order, a session carrying a
same-day split **and** dividend fails closed with
``BLOCKED_UNREGISTERED_SAME_DAY_EVENT_ORDER`` rather than assuming one.

Numerics
--------

Every value is lifted from a canonical base-10 decimal string to an exact
:class:`fractions.Fraction`. There is no ``float`` anywhere in this module and no
intermediate rounding: results are rounded exactly once, at the artifact
boundary. Fields that are exact by construction (raw echoes, split adjustment
factors, split-adjusted volume, raw dollar volume) serialize as exact canonical
decimals; ratio fields (adjusted close, gross return, total-return index)
serialize at the registered ``artifact_scale = 18`` with ``ROUND_HALF_EVEN``.
Ledger currency and share values quantize at the NEE-118 ``1e-8`` quantum with
``ROUND_HALF_EVEN``. Every dataclass also carries the underlying exact
``Fraction`` so a downstream consumer never has to re-derive from a rounded
artifact.

Naming discipline
-----------------

Raw OHLCV is never mutated. The kernel consumes immutable inputs and emits
**separately named** derived series -- ``raw_close`` / ``raw_volume`` (verbatim
echoes), ``split_adjusted_close`` / ``split_adjusted_volume``,
``raw_dollar_volume``, ``gross_return`` / ``total_return_index`` -- so a raw
coordinate can never be silently read as an adjusted one. See
:data:`DERIVED_SERIES_NAMES`.

Non-claims
----------

* Synthetic-only. This module registers no vendor comparison, no tolerance, no
  identity join, and no security master. It is not evidence and clears no
  freeze blocker.
* Unsupported action types on a **held** position fail closed with
  ``RUN_INVALID_UNSUPPORTED_HELD_ACTION``. The
  ``unsupported_action_policy`` hook exists so a later owner registration can
  attach sourced outcome policies; its only accepted value today is ``None``.
* Nothing here imports :mod:`qme.data.alpha_vantage`; the raw-cache integration
  and the identity join are NEE-123 / NEE-127 scope.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Final

# ---------------------------------------------------------------------------
# Identity and registered bindings
# ---------------------------------------------------------------------------

KERNEL_ID: Final = "QME-NEE125-CORPORATE-ACTION-FACTOR-KERNEL-V1"
SCHEMA_VERSION: Final = "qme.corporate_action_factors.v1"

#: Registered total-return methodology this kernel binds (read-only citation).
METHODOLOGY_ID: Final = "qme-point-in-time-total-return-close-v1"
METHODOLOGY_PATH: Final = "configs/quant/qme-v0.1-total-return-methodology.json"
#: sha256 of the reviewed methodology bytes, written in the repository's grouped
#: form (eight 8-hex groups). This binds an upstream artifact; it is not a
#: self-pin, and this module never verifies its own bytes.
METHODOLOGY_SHA256_GROUPED: Final = (
    "95381821:c1c8ff00:e0e626b3:d7ee3646:6d12c3be:9e6b8cb7:5ee166f0:043454ac"
)

#: ``split_policy.event_order`` as registered by the methodology config.
REGISTERED_SAME_DAY_EVENT_ORDER: Final = "SPLIT_BEFORE_DIVIDEND_UNIT_CONVERSION"
#: ``dividend_reinvestment.coordinate`` as registered by the methodology config.
REGISTERED_DIVIDEND_COORDINATE: Final = "POST_SPLIT_CASH_PER_SHARE"
#: ``gross_factor_formula`` as registered by the methodology config.
REGISTERED_GROSS_FACTOR_FORMULA: Final = (
    "split_factor_t * (raw_close_t + dividend_post_split_per_share_t) / raw_close_t_minus_1"
)
#: The ticket-verbatim form this kernel evaluates; equal to the registered form.
TICKET_GROSS_RETURN_FORMULA: Final = "(s_t * P_t + d_t) / P_(t-1)"

#: ``numeric_policy.artifact_scale`` / ``numeric_policy.rounding_mode``.
ARTIFACT_SCALE: Final = 18
ROUNDING_MODE: Final = "ROUND_HALF_EVEN"
#: NEE-118 ledger quanta (``configs/quant/accounting-equations-v1.json`` units).
LEDGER_CURRENCY_QUANTUM: Final = Fraction(1, 100_000_000)
LEDGER_SHARE_QUANTUM: Final = Fraction(1, 100_000_000)

#: Derived series this kernel publishes. Raw names are verbatim echoes of the
#: immutable inputs; every other name is a distinct derived coordinate.
DERIVED_SERIES_NAMES: Final = (
    "raw_close",
    "raw_volume",
    "raw_dollar_volume",
    "split_adjustment_factor",
    "split_adjusted_close",
    "split_adjusted_volume",
    "split_adjusted_dollar_volume",
    "gross_return",
    "total_return_index",
)
RAW_SERIES_NAMES: Final = ("raw_close", "raw_volume")

#: Dividend share-basis coordinates this kernel accepts.
BASIS_POST_SPLIT: Final = "POST_SPLIT"
BASIS_PRE_ACTION: Final = "PRE_ACTION"
DIVIDEND_SHARE_BASES: Final = (BASIS_POST_SPLIT, BASIS_PRE_ACTION)

#: Dividend classifications the methodology supports with valid terms.
CLASSIFICATION_ORDINARY: Final = "ORDINARY"
CLASSIFICATION_SPECIAL: Final = "SPECIAL"
DIVIDEND_CLASSIFICATIONS: Final = (CLASSIFICATION_ORDINARY, CLASSIFICATION_SPECIAL)

#: Action types this kernel does not model. On a held position they invalidate
#: the run; on an unheld security they exclude it.
UNSUPPORTED_ACTION_TYPES: Final = (
    "MERGER",
    "SPINOFF",
    "RIGHTS",
    "LIQUIDATION",
    "UNKNOWN",
)

# ---------------------------------------------------------------------------
# Typed states
# ---------------------------------------------------------------------------

SERIES_OK: Final = "SERIES_OK"
#: Unsupported action against a held position: the whole run is invalid.
RUN_INVALID_UNSUPPORTED_HELD_ACTION: Final = "RUN_INVALID_UNSUPPORTED_HELD_ACTION"
#: Unsupported action against an unheld security: exclusion, not run invalidity.
EXCLUDED_UNSUPPORTED_UNHELD_ACTION: Final = "EXCLUDED_UNSUPPORTED_UNHELD_ACTION"

BLOCKED_AMBIGUOUS_EVENT_COORDINATE: Final = "BLOCKED_AMBIGUOUS_EVENT_COORDINATE"
BLOCKED_DUPLICATE_EVENT: Final = "BLOCKED_DUPLICATE_EVENT"
BLOCKED_DUPLICATE_SESSION: Final = "BLOCKED_DUPLICATE_SESSION"
BLOCKED_FOREIGN_SECURITY_ACTION: Final = "BLOCKED_FOREIGN_SECURITY_ACTION"
BLOCKED_MISSING_RAW_CLOSE: Final = "BLOCKED_MISSING_RAW_CLOSE"
BLOCKED_NEGATIVE_DISTRIBUTION: Final = "BLOCKED_NEGATIVE_DISTRIBUTION"
BLOCKED_NEGATIVE_HELD_SHARES: Final = "BLOCKED_NEGATIVE_HELD_SHARES"
BLOCKED_NEGATIVE_LEDGER_VALUE: Final = "BLOCKED_NEGATIVE_LEDGER_VALUE"
BLOCKED_NEGATIVE_RAW_VOLUME: Final = "BLOCKED_NEGATIVE_RAW_VOLUME"
BLOCKED_NONFINITE_INPUT: Final = "BLOCKED_NONFINITE_INPUT"
BLOCKED_NONPOSITIVE_RAW_CLOSE: Final = "BLOCKED_NONPOSITIVE_RAW_CLOSE"
BLOCKED_NONPOSITIVE_SPLIT_FACTOR: Final = "BLOCKED_NONPOSITIVE_SPLIT_FACTOR"
BLOCKED_NONREPRESENTABLE_LEDGER_QUANTUM: Final = "BLOCKED_NONREPRESENTABLE_LEDGER_QUANTUM"
BLOCKED_NONREPRESENTABLE_SHARE_QUANTUM: Final = "BLOCKED_NONREPRESENTABLE_SHARE_QUANTUM"
BLOCKED_PAYMENT_BEFORE_ENTITLEMENT: Final = "BLOCKED_PAYMENT_BEFORE_ENTITLEMENT"
BLOCKED_POST_CUTOFF_EVENT: Final = "BLOCKED_POST_CUTOFF_EVENT"
BLOCKED_POST_CUTOFF_SESSION: Final = "BLOCKED_POST_CUTOFF_SESSION"
BLOCKED_SPLIT_CONSERVATION_VIOLATED: Final = "BLOCKED_SPLIT_CONSERVATION_VIOLATED"
BLOCKED_SPLIT_WITHOUT_PRIOR_RAW_CLOSE: Final = "BLOCKED_SPLIT_WITHOUT_PRIOR_RAW_CLOSE"
BLOCKED_SUPPORTED_ACTION_AFTER_EXCLUSION: Final = "BLOCKED_SUPPORTED_ACTION_AFTER_EXCLUSION"
BLOCKED_UNKNOWN_ACTION_TYPE: Final = "BLOCKED_UNKNOWN_ACTION_TYPE"
BLOCKED_UNREGISTERED_SAME_DAY_EVENT_ORDER: Final = "BLOCKED_UNREGISTERED_SAME_DAY_EVENT_ORDER"
BLOCKED_UNREGISTERED_UNSUPPORTED_ACTION_POLICY: Final = (
    "BLOCKED_UNREGISTERED_UNSUPPORTED_ACTION_POLICY"
)

#: Every fail-closed state this kernel can raise, sorted. Callers may bind this
#: tuple; a new state is an interface change.
FAIL_CLOSED_STATES: Final = (
    BLOCKED_AMBIGUOUS_EVENT_COORDINATE,
    BLOCKED_DUPLICATE_EVENT,
    BLOCKED_DUPLICATE_SESSION,
    BLOCKED_FOREIGN_SECURITY_ACTION,
    BLOCKED_MISSING_RAW_CLOSE,
    BLOCKED_NEGATIVE_DISTRIBUTION,
    BLOCKED_NEGATIVE_HELD_SHARES,
    BLOCKED_NEGATIVE_LEDGER_VALUE,
    BLOCKED_NEGATIVE_RAW_VOLUME,
    BLOCKED_NONFINITE_INPUT,
    BLOCKED_NONPOSITIVE_RAW_CLOSE,
    BLOCKED_NONPOSITIVE_SPLIT_FACTOR,
    BLOCKED_NONREPRESENTABLE_LEDGER_QUANTUM,
    BLOCKED_NONREPRESENTABLE_SHARE_QUANTUM,
    BLOCKED_PAYMENT_BEFORE_ENTITLEMENT,
    BLOCKED_POST_CUTOFF_EVENT,
    BLOCKED_POST_CUTOFF_SESSION,
    BLOCKED_SPLIT_CONSERVATION_VIOLATED,
    BLOCKED_SPLIT_WITHOUT_PRIOR_RAW_CLOSE,
    BLOCKED_SUPPORTED_ACTION_AFTER_EXCLUSION,
    BLOCKED_UNKNOWN_ACTION_TYPE,
    BLOCKED_UNREGISTERED_SAME_DAY_EVENT_ORDER,
    BLOCKED_UNREGISTERED_UNSUPPORTED_ACTION_POLICY,
    RUN_INVALID_UNSUPPORTED_HELD_ACTION,
)

#: Downstream claims this prebuild has not earned. Written to every artifact.
NON_CLAIMS: Final[Mapping[str, bool]] = {
    "vendor_adjusted_comparison_run": False,
    "security_identity_join_applied": False,
    "raw_cache_integration_applied": False,
    "sourced_unsupported_outcome_policy_registered": False,
    "independent_review_recorded": False,
    "freeze_blocker_changed": False,
}

_CANONICAL_DECIMAL_RE: Final = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_SESSION_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_ONE: Final = Fraction(1)
_ZERO: Final = Fraction(0)


# ---------------------------------------------------------------------------
# Typed error
# ---------------------------------------------------------------------------


class CorporateActionFactorError(ValueError):
    """A typed fail-closed refusal carrying the state and the event identity.

    ``state`` is one of :data:`FAIL_CLOSED_STATES`. Identity fields are populated
    whenever the refusal is attributable to a specific event or session, so a
    caller can report *which* action invalidated the run rather than only that
    something did.
    """

    def __init__(
        self,
        state: str,
        message: str,
        *,
        security_id: str | None = None,
        event_id: str | None = None,
        session: str | None = None,
        action_type: str | None = None,
    ) -> None:
        super().__init__(f"{state}: {message}")
        self.state = state
        self.security_id = security_id
        self.event_id = event_id
        self.session = session
        self.action_type = action_type

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "state": self.state,
            "security_id": self.security_id,
            "event_id": self.event_id,
            "session": self.session,
            "action_type": self.action_type,
        }


# ---------------------------------------------------------------------------
# Exact base-10 arithmetic
#
# Deliberately local: this module imports nothing from
# ``qme.data.alpha_vantage`` or ``qme.data.corporate_actions.registered_events``
# so the kernel stays a leaf with no vendor-cache coupling.
# ---------------------------------------------------------------------------


def canonical_decimal(value: str, *, what: str) -> str:
    """Normalize a base-10 decimal string; anything else fails closed.

    ``"4.0000"`` -> ``"4"``, ``"15.0"`` -> ``"15"``, ``"-0.500"`` -> ``"-0.5"``.
    """
    if not isinstance(value, str) or not _CANONICAL_DECIMAL_RE.fullmatch(value):
        raise CorporateActionFactorError(
            BLOCKED_NONFINITE_INPUT, f"{what} is not a canonical base-10 decimal string"
        )
    negative = value.startswith("-")
    digits = value[1:] if negative else value
    if "." in digits:
        digits = digits.rstrip("0").rstrip(".")
        if not digits:
            digits = "0"
    if digits == "0":
        return "0"
    return ("-" if negative else "") + digits


def parse_exact(value: str, *, what: str) -> Fraction:
    """Lift a canonical base-10 decimal string to an exact :class:`Fraction`."""
    return Fraction(canonical_decimal(value, what=what))


def quantize_half_even(value: Fraction, quantum: Fraction) -> Fraction:
    """Round ``value`` to a multiple of ``quantum`` using ``ROUND_HALF_EVEN``."""
    if quantum <= 0:
        raise CorporateActionFactorError(BLOCKED_NONFINITE_INPUT, "quantum must be positive")
    scaled = value / quantum
    sign = -1 if scaled < 0 else 1
    absolute = abs(scaled)
    quotient, remainder = divmod(absolute.numerator, absolute.denominator)
    twice = remainder * 2
    if twice > absolute.denominator or (twice == absolute.denominator and quotient % 2 == 1):
        quotient += 1
    return sign * quotient * quantum


def render_exact(value: Fraction, *, what: str = "value") -> str:
    """Render an exactly base-10 representable ``Fraction`` as a canonical decimal."""
    numerator, denominator = value.numerator, value.denominator
    twos = fives = 0
    remaining = denominator
    while remaining % 2 == 0:
        remaining //= 2
        twos += 1
    while remaining % 5 == 0:
        remaining //= 5
        fives += 1
    if remaining != 1:
        raise CorporateActionFactorError(
            BLOCKED_NONFINITE_INPUT, f"{what} is not exactly representable in base 10"
        )
    scale = max(twos, fives)
    scaled = numerator * 10**scale // denominator
    negative = scaled < 0
    digits = str(abs(scaled)).rjust(scale + 1, "0")
    text = digits if scale == 0 else f"{digits[:-scale]}.{digits[-scale:]}"
    return canonical_decimal(("-" if negative else "") + text, what=what)


def render_artifact(value: Fraction, *, scale: int = ARTIFACT_SCALE) -> str:
    """Render at the registered artifact scale with ``ROUND_HALF_EVEN``."""
    if scale < 1:
        raise CorporateActionFactorError(BLOCKED_NONFINITE_INPUT, "artifact scale must be >= 1")
    quantum = Fraction(1, 10**scale)
    rounded = quantize_half_even(value, quantum) / quantum
    if rounded.denominator != 1:  # pragma: no cover - quantization guarantees this
        raise CorporateActionFactorError(
            BLOCKED_NONFINITE_INPUT, "quantized artifact value is not an integral multiple"
        )
    units = rounded.numerator
    sign = "-" if units < 0 else ""
    whole, fractional = divmod(abs(units), 10**scale)
    return f"{sign}{whole}.{fractional:0{scale}d}"


def render_ledger(value: Fraction, quantum: Fraction = LEDGER_CURRENCY_QUANTUM) -> str:
    """Render a ledger value at the NEE-118 ``1e-8`` quantum, ``ROUND_HALF_EVEN``."""
    return render_exact(quantize_half_even(value, quantum), what="ledger value")


def _session(value: str, *, what: str) -> str:
    if not isinstance(value, str) or not _SESSION_RE.fullmatch(value):
        raise CorporateActionFactorError(
            BLOCKED_NONFINITE_INPUT, f"{what} is not an ISO-8601 session date"
        )
    try:
        date.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - regex already constrains shape
        raise CorporateActionFactorError(
            BLOCKED_NONFINITE_INPUT, f"{what} is not a real calendar date"
        ) from exc
    return value


def _identifier(value: str, *, what: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise CorporateActionFactorError(
            BLOCKED_NONFINITE_INPUT, f"{what} is not a valid identifier"
        )
    return value


# ---------------------------------------------------------------------------
# Immutable inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawSessionBar:
    """One immutable raw (unadjusted) session bar. Never mutated by this kernel."""

    session: str
    raw_close: str
    raw_volume: str


@dataclass(frozen=True)
class SplitAction:
    """A split (or reverse split): ``s_t`` new shares per old share, ``s_t > 0``."""

    event_id: str
    security_id: str
    session: str
    split_factor: str


@dataclass(frozen=True)
class CashDividendAction:
    """A cash distribution with an explicit per-share coordinate.

    ``share_basis`` declares the coordinate of ``cash_per_share``:
    :data:`BASIS_POST_SPLIT` (cash per post-split share, the registered artifact
    coordinate) or :data:`BASIS_PRE_ACTION` (``d_t``, cash per pre-action share).
    ``None`` is accepted only when no split shares the session -- otherwise the
    coordinate is ambiguous and the session fails closed with
    ``BLOCKED_AMBIGUOUS_EVENT_COORDINATE``, exactly as the methodology registers.
    """

    event_id: str
    security_id: str
    session: str
    cash_per_share: str
    payment_session: str
    share_basis: str | None = None
    classification: str = CLASSIFICATION_ORDINARY


@dataclass(frozen=True)
class UnsupportedAction:
    """An action type this kernel does not model (merger, spinoff, rights, ...)."""

    event_id: str
    security_id: str
    session: str
    action_type: str


CorporateAction = SplitAction | CashDividendAction | UnsupportedAction

#: Deterministic within-session class ordering, from the registered
#: ``SPLIT_BEFORE_DIVIDEND_UNIT_CONVERSION`` order.
_CLASS_RANK: Final[Mapping[str, int]] = {
    "SPLIT": 0,
    "CASH_DIVIDEND": 1,
    "UNSUPPORTED": 2,
}


def _action_class(action: CorporateAction) -> str:
    if isinstance(action, SplitAction):
        return "SPLIT"
    if isinstance(action, CashDividendAction):
        return "CASH_DIVIDEND"
    return "UNSUPPORTED"


# ---------------------------------------------------------------------------
# Contract-line primitives (each is independently callable and tested)
# ---------------------------------------------------------------------------


def gross_return(
    *,
    split_factor: Fraction,
    raw_close: Fraction,
    raw_close_previous: Fraction,
    dividend_pre_action_per_share: Fraction,
    dividend_post_split_per_share: Fraction | None = None,
) -> Fraction:
    """``gross_return_t = (s_t * P_t + d_t) / P_(t-1)`` in exact arithmetic.

    When the caller supplies the distribution in **both** coordinates, the two
    are cross-checked (``d_t == s_t * d_post_split_t``) and the ticket form is
    then evaluated alongside the registered methodology form
    ``s_t * (P_t + d_post_split_t) / P_(t-1)``. An inconsistent pair fails closed
    with ``BLOCKED_AMBIGUOUS_EVENT_COORDINATE`` rather than silently preferring
    one coordinate. Omitting ``dividend_post_split_per_share`` derives it.
    """
    if raw_close_previous <= 0 or raw_close <= 0:
        raise CorporateActionFactorError(
            BLOCKED_NONPOSITIVE_RAW_CLOSE, "gross return needs positive raw closes"
        )
    if split_factor <= 0:
        raise CorporateActionFactorError(
            BLOCKED_NONPOSITIVE_SPLIT_FACTOR, "split factor must be positive"
        )
    if dividend_pre_action_per_share < 0:
        raise CorporateActionFactorError(
            BLOCKED_NEGATIVE_DISTRIBUTION, "negative distributions are not allowed"
        )
    post_split = (
        dividend_pre_action_per_share / split_factor
        if dividend_post_split_per_share is None
        else dividend_post_split_per_share
    )
    if post_split < 0:
        raise CorporateActionFactorError(
            BLOCKED_NEGATIVE_DISTRIBUTION, "negative distributions are not allowed"
        )
    if dividend_pre_action_per_share != split_factor * post_split:
        raise CorporateActionFactorError(
            BLOCKED_AMBIGUOUS_EVENT_COORDINATE,
            "pre-action and post-split distribution coordinates disagree",
        )
    ticket = (split_factor * raw_close + dividend_pre_action_per_share) / raw_close_previous
    registered = split_factor * (raw_close + post_split) / raw_close_previous
    if ticket != registered:  # pragma: no cover - exact arithmetic identity
        raise CorporateActionFactorError(
            BLOCKED_AMBIGUOUS_EVENT_COORDINATE,
            "ticket and registered gross-factor forms disagree",
        )
    return ticket


def verify_split_conservation(
    *,
    shares_before: Fraction,
    shares_after: Fraction,
    raw_close_before: Fraction,
    split_factor: Fraction,
) -> Fraction:
    """Check ``q_after * (P_before / s_t) == q_before * P_before``; return the value.

    Raises ``BLOCKED_SPLIT_CONSERVATION_VIOLATED`` when the identity does not
    hold exactly. In exact arithmetic a correctly derived ``q_after`` can never
    violate it; the check is the executable form of the contract line and guards
    any future refactor that derives shares another way.
    """
    if split_factor <= 0:
        raise CorporateActionFactorError(
            BLOCKED_NONPOSITIVE_SPLIT_FACTOR, "split factor must be positive"
        )
    if raw_close_before <= 0:
        raise CorporateActionFactorError(
            BLOCKED_NONPOSITIVE_RAW_CLOSE, "split conservation needs a positive prior raw close"
        )
    reference_before = shares_before * raw_close_before
    reference_after = shares_after * (raw_close_before / split_factor)
    if reference_before != reference_after:
        raise CorporateActionFactorError(
            BLOCKED_SPLIT_CONSERVATION_VIOLATED,
            "split does not conserve q * P across the transition",
        )
    return reference_before


def split_adjustment_factor(
    split_factors_after_session: Sequence[Fraction],
) -> Fraction:
    """``A_(u|a) = prod s_v`` over the supplied ``u < v <= a`` split factors."""
    product = _ONE
    for factor in split_factors_after_session:
        if factor <= 0:
            raise CorporateActionFactorError(
                BLOCKED_NONPOSITIVE_SPLIT_FACTOR, "split factor must be positive"
            )
        product *= factor
    return product


# ---------------------------------------------------------------------------
# Normalized action stream
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionActions:
    """The registered-order collapse of one session's actions.

    ``split_factor`` is the product of that session's split factors (commutative,
    so input order cannot change it). ``dividend_post_split_per_share`` is the sum
    of that session's distributions converted to the registered post-split
    coordinate; ``dividend_pre_action_per_share`` is the same total expressed as
    ``d_t``, cash per pre-action share.
    """

    session: str
    split_factor: Fraction
    dividend_post_split_per_share: Fraction
    dividend_pre_action_per_share: Fraction
    split_event_ids: tuple[str, ...]
    dividend_event_ids: tuple[str, ...]
    payments: tuple[tuple[str, str, Fraction], ...]
    """``(event_id, payment_session, post_split_cash_per_share)`` per distribution."""


@dataclass(frozen=True)
class ExclusionRecord:
    """Typed exclusion of an unheld security carrying the event identity."""

    state: str
    security_id: str
    event_id: str
    session: str
    action_type: str

    def to_json_dict(self) -> dict[str, str]:
        return {
            "state": self.state,
            "security_id": self.security_id,
            "event_id": self.event_id,
            "session": self.session,
            "action_type": self.action_type,
        }


@dataclass(frozen=True)
class NormalizedActions:
    """Deterministic, validated action stream for one security."""

    security_id: str
    adjustment_cutoff_session: str
    same_day_event_order: str | None
    by_session: Mapping[str, SessionActions]
    ordered_sessions: tuple[str, ...]
    exclusion: ExclusionRecord | None


def _sort_key(action: CorporateAction) -> tuple[str, int, str]:
    return (action.session, _CLASS_RANK[_action_class(action)], action.event_id)


def _validate_policy_hook(unsupported_action_policy: object) -> None:
    if unsupported_action_policy is not None:
        raise CorporateActionFactorError(
            BLOCKED_UNREGISTERED_UNSUPPORTED_ACTION_POLICY,
            "no sourced outcome policy is registered; the only accepted value is None",
        )


def normalize_actions(
    actions: Sequence[CorporateAction],
    *,
    security_id: str,
    adjustment_cutoff_session: str,
    held_raw_shares: Fraction,
    same_day_event_order: str | None = REGISTERED_SAME_DAY_EVENT_ORDER,
    unsupported_action_policy: object = None,
) -> NormalizedActions:
    """Validate, order, and collapse an action stream. Input order is irrelevant."""
    _validate_policy_hook(unsupported_action_policy)
    security = _identifier(security_id, what="security_id")
    cutoff = _session(adjustment_cutoff_session, what="adjustment_cutoff_session")
    if held_raw_shares < 0:
        raise CorporateActionFactorError(
            BLOCKED_NEGATIVE_HELD_SHARES,
            "held_raw_shares must be non-negative",
            security_id=security,
        )

    seen_event_ids: set[str] = set()
    for action in actions:
        event_id = _identifier(action.event_id, what="event_id")
        _session(action.session, what=f"{event_id}.session")
        if _identifier(action.security_id, what="security_id") != security:
            raise CorporateActionFactorError(
                BLOCKED_FOREIGN_SECURITY_ACTION,
                "action does not belong to this security",
                security_id=action.security_id,
                event_id=event_id,
                session=action.session,
            )
        if event_id in seen_event_ids:
            raise CorporateActionFactorError(
                BLOCKED_DUPLICATE_EVENT,
                "the same event id appears more than once",
                security_id=security,
                event_id=event_id,
                session=action.session,
            )
        seen_event_ids.add(event_id)
        if action.session > cutoff:
            raise CorporateActionFactorError(
                BLOCKED_POST_CUTOFF_EVENT,
                f"action falls after the adjustment cutoff {cutoff}",
                security_id=security,
                event_id=event_id,
                session=action.session,
                action_type=_action_class(action),
            )

    ordered = sorted(actions, key=_sort_key)

    exclusion: ExclusionRecord | None = None
    for action in ordered:
        if not isinstance(action, UnsupportedAction):
            continue
        action_type = action.action_type
        if action_type not in UNSUPPORTED_ACTION_TYPES:
            raise CorporateActionFactorError(
                BLOCKED_UNKNOWN_ACTION_TYPE,
                "unsupported action carries an unregistered action type",
                security_id=security,
                event_id=action.event_id,
                session=action.session,
                action_type=action_type,
            )
        if held_raw_shares > 0:
            raise CorporateActionFactorError(
                RUN_INVALID_UNSUPPORTED_HELD_ACTION,
                "unsupported corporate action against a held position",
                security_id=security,
                event_id=action.event_id,
                session=action.session,
                action_type=action_type,
            )
        if exclusion is None:
            exclusion = ExclusionRecord(
                state=EXCLUDED_UNSUPPORTED_UNHELD_ACTION,
                security_id=security,
                event_id=action.event_id,
                session=action.session,
                action_type=action_type,
            )

    if exclusion is not None:
        for action in ordered:
            if isinstance(action, UnsupportedAction):
                continue
            if action.session >= exclusion.session:
                raise CorporateActionFactorError(
                    BLOCKED_SUPPORTED_ACTION_AFTER_EXCLUSION,
                    "a supported action falls at or after the exclusion session",
                    security_id=security,
                    event_id=action.event_id,
                    session=action.session,
                    action_type=_action_class(action),
                )

    splits: dict[str, list[tuple[str, Fraction]]] = {}
    dividends: dict[str, list[tuple[str, Fraction, str | None, str]]] = {}
    for action in ordered:
        if isinstance(action, SplitAction):
            factor = parse_exact(action.split_factor, what=f"{action.event_id}.split_factor")
            if factor <= 0:
                raise CorporateActionFactorError(
                    BLOCKED_NONPOSITIVE_SPLIT_FACTOR,
                    "split factor must be positive",
                    security_id=security,
                    event_id=action.event_id,
                    session=action.session,
                )
            splits.setdefault(action.session, []).append((action.event_id, factor))
        elif isinstance(action, CashDividendAction):
            cash = parse_exact(action.cash_per_share, what=f"{action.event_id}.cash_per_share")
            if cash < 0:
                raise CorporateActionFactorError(
                    BLOCKED_NEGATIVE_DISTRIBUTION,
                    "negative distributions are not allowed",
                    security_id=security,
                    event_id=action.event_id,
                    session=action.session,
                )
            if action.classification not in DIVIDEND_CLASSIFICATIONS:
                raise CorporateActionFactorError(
                    BLOCKED_UNKNOWN_ACTION_TYPE,
                    "dividend classification is not registered",
                    security_id=security,
                    event_id=action.event_id,
                    session=action.session,
                    action_type=action.classification,
                )
            if action.share_basis is not None and action.share_basis not in DIVIDEND_SHARE_BASES:
                raise CorporateActionFactorError(
                    BLOCKED_AMBIGUOUS_EVENT_COORDINATE,
                    "dividend share basis is not a registered coordinate",
                    security_id=security,
                    event_id=action.event_id,
                    session=action.session,
                )
            payment = _session(action.payment_session, what=f"{action.event_id}.payment_session")
            if payment < action.session:
                raise CorporateActionFactorError(
                    BLOCKED_PAYMENT_BEFORE_ENTITLEMENT,
                    "payment session precedes the entitlement session",
                    security_id=security,
                    event_id=action.event_id,
                    session=action.session,
                )
            if payment > cutoff:
                raise CorporateActionFactorError(
                    BLOCKED_POST_CUTOFF_EVENT,
                    f"payment leg falls after the adjustment cutoff {cutoff}",
                    security_id=security,
                    event_id=action.event_id,
                    session=payment,
                    action_type="CASH_DIVIDEND_PAYMENT",
                )
            dividends.setdefault(action.session, []).append(
                (action.event_id, cash, action.share_basis, payment)
            )

    by_session: dict[str, SessionActions] = {}
    for session in sorted(set(splits) | set(dividends)):
        session_splits = splits.get(session, [])
        session_dividends = dividends.get(session, [])
        factor = split_adjustment_factor([item[1] for item in session_splits])
        post_split_total = _ZERO
        payments: list[tuple[str, str, Fraction]] = []
        for event_id, cash, basis, payment in session_dividends:
            if basis is None:
                if session_splits and factor != _ONE:
                    if same_day_event_order != REGISTERED_SAME_DAY_EVENT_ORDER:
                        raise CorporateActionFactorError(
                            BLOCKED_UNREGISTERED_SAME_DAY_EVENT_ORDER,
                            "no registered same-day split/dividend order was supplied",
                            security_id=security,
                            event_id=event_id,
                            session=session,
                        )
                    raise CorporateActionFactorError(
                        BLOCKED_AMBIGUOUS_EVENT_COORDINATE,
                        "same-day split requires an explicit dividend share basis",
                        security_id=security,
                        event_id=event_id,
                        session=session,
                    )
                post_split = cash
            elif basis == BASIS_POST_SPLIT:
                post_split = cash
            else:
                post_split = cash / factor
            if session_splits and same_day_event_order != REGISTERED_SAME_DAY_EVENT_ORDER:
                raise CorporateActionFactorError(
                    BLOCKED_UNREGISTERED_SAME_DAY_EVENT_ORDER,
                    "no registered same-day split/dividend order was supplied",
                    security_id=security,
                    event_id=event_id,
                    session=session,
                )
            post_split_total += post_split
            payments.append((event_id, payment, post_split))
        by_session[session] = SessionActions(
            session=session,
            split_factor=factor,
            dividend_post_split_per_share=post_split_total,
            dividend_pre_action_per_share=post_split_total * factor,
            split_event_ids=tuple(item[0] for item in session_splits),
            dividend_event_ids=tuple(item[0] for item in session_dividends),
            payments=tuple(payments),
        )

    return NormalizedActions(
        security_id=security,
        adjustment_cutoff_session=cutoff,
        same_day_event_order=same_day_event_order,
        by_session=by_session,
        ordered_sessions=tuple(sorted(by_session)),
        exclusion=exclusion,
    )


def _normalized_bars(
    bars: Sequence[RawSessionBar],
    *,
    adjustment_cutoff_session: str,
) -> tuple[tuple[str, Fraction, Fraction], ...]:
    """Validate and deterministically order the immutable raw bars."""
    seen: set[str] = set()
    rows: list[tuple[str, Fraction, Fraction]] = []
    for bar in bars:
        session = _session(bar.session, what="bar.session")
        if session in seen:
            raise CorporateActionFactorError(
                BLOCKED_DUPLICATE_SESSION,
                "the same session appears more than once in the raw bars",
                session=session,
            )
        seen.add(session)
        if session > adjustment_cutoff_session:
            raise CorporateActionFactorError(
                BLOCKED_POST_CUTOFF_SESSION,
                f"raw bar falls after the adjustment cutoff {adjustment_cutoff_session}",
                session=session,
            )
        close = parse_exact(bar.raw_close, what=f"{session}.raw_close")
        if close <= 0:
            raise CorporateActionFactorError(
                BLOCKED_NONPOSITIVE_RAW_CLOSE, "raw close must be positive", session=session
            )
        volume = parse_exact(bar.raw_volume, what=f"{session}.raw_volume")
        if volume < 0:
            raise CorporateActionFactorError(
                BLOCKED_NEGATIVE_RAW_VOLUME, "raw volume must be non-negative", session=session
            )
        rows.append((session, close, volume))
    if not rows:
        raise CorporateActionFactorError(
            BLOCKED_MISSING_RAW_CLOSE, "at least one raw bar is required"
        )
    rows.sort(key=lambda row: row[0])
    return tuple(rows)


# ---------------------------------------------------------------------------
# Factor / total-return series
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionFactors:
    """Derived coordinates for one session. Raw fields are verbatim echoes."""

    session: str
    raw_close: Fraction
    raw_volume: Fraction
    raw_dollar_volume: Fraction
    split_adjustment_factor: Fraction
    split_adjusted_close: Fraction
    split_adjusted_volume: Fraction
    split_adjusted_dollar_volume: Fraction
    applied_split_factor: Fraction
    applied_dividend_post_split_per_share: Fraction
    applied_dividend_pre_action_per_share: Fraction
    gross_return: Fraction | None
    total_return_index: Fraction

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "session": self.session,
            "raw_close": render_exact(self.raw_close, what="raw_close"),
            "raw_volume": render_exact(self.raw_volume, what="raw_volume"),
            "raw_dollar_volume": render_exact(
                self.raw_dollar_volume, what="raw_dollar_volume"
            ),
            "split_adjustment_factor": render_exact(
                self.split_adjustment_factor, what="split_adjustment_factor"
            ),
            "split_adjusted_close": render_artifact(self.split_adjusted_close),
            "split_adjusted_volume": render_exact(
                self.split_adjusted_volume, what="split_adjusted_volume"
            ),
            "split_adjusted_dollar_volume": render_exact(
                self.split_adjusted_dollar_volume, what="split_adjusted_dollar_volume"
            ),
            "applied_split_factor": render_exact(
                self.applied_split_factor, what="applied_split_factor"
            ),
            "applied_dividend_post_split_per_share": render_artifact(
                self.applied_dividend_post_split_per_share
            ),
            "applied_dividend_pre_action_per_share": render_exact(
                self.applied_dividend_pre_action_per_share,
                what="applied_dividend_pre_action_per_share",
            ),
            "gross_return": None if self.gross_return is None else render_artifact(self.gross_return),
            "total_return_index": render_artifact(self.total_return_index),
        }


@dataclass(frozen=True)
class FactorSeries:
    """The complete derived output for one security under one adjustment cutoff."""

    security_id: str
    adjustment_cutoff_session: str
    state: str
    sessions: tuple[SessionFactors, ...]
    exclusion: ExclusionRecord | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kernel_id": KERNEL_ID,
            "methodology_id": METHODOLOGY_ID,
            "methodology_path": METHODOLOGY_PATH,
            "methodology_sha256_grouped": METHODOLOGY_SHA256_GROUPED,
            "same_day_event_order": REGISTERED_SAME_DAY_EVENT_ORDER,
            "artifact_scale": ARTIFACT_SCALE,
            "rounding_mode": ROUNDING_MODE,
            "security_id": self.security_id,
            "adjustment_cutoff_session": self.adjustment_cutoff_session,
            "state": self.state,
            "sessions": [item.to_json_dict() for item in self.sessions],
            "exclusion": None if self.exclusion is None else self.exclusion.to_json_dict(),
            "derived_series_names": list(DERIVED_SERIES_NAMES),
            "claims": dict(NON_CLAIMS),
        }


def build_factor_series(
    bars: Sequence[RawSessionBar],
    actions: Sequence[CorporateAction],
    *,
    security_id: str,
    adjustment_cutoff_session: str,
    held_raw_shares: str = "0",
    base_index: str = "1",
    same_day_event_order: str | None = REGISTERED_SAME_DAY_EVENT_ORDER,
    unsupported_action_policy: object = None,
) -> FactorSeries:
    """Build the split-adjusted price/volume factors and the total-return index.

    ``bars`` and ``actions`` are immutable and may arrive in any order; the
    kernel sorts both deterministically, so a permutation of either container
    cannot change the output. Raw OHLCV is echoed verbatim and never mutated.

    ``held_raw_shares`` is the position held against this security when an
    unsupported action lands: a positive holding makes the run invalid
    (``RUN_INVALID_UNSUPPORTED_HELD_ACTION``); a zero holding excludes the
    security from the exclusion session onward. ``unsupported_action_policy``
    is the owner-registration hook; its only accepted value today is ``None``.
    """
    cutoff = _session(adjustment_cutoff_session, what="adjustment_cutoff_session")
    held = parse_exact(held_raw_shares, what="held_raw_shares")
    base = parse_exact(base_index, what="base_index")
    if base <= 0:
        raise CorporateActionFactorError(
            BLOCKED_NONFINITE_INPUT, "base_index must be positive"
        )
    normalized = normalize_actions(
        actions,
        security_id=security_id,
        adjustment_cutoff_session=cutoff,
        held_raw_shares=held,
        same_day_event_order=same_day_event_order,
        unsupported_action_policy=unsupported_action_policy,
    )
    rows = _normalized_bars(bars, adjustment_cutoff_session=cutoff)

    exclusion = normalized.exclusion
    if exclusion is not None:
        rows = tuple(row for row in rows if row[0] < exclusion.session)

    for session in normalized.ordered_sessions:
        if all(row[0] != session for row in rows):
            raise CorporateActionFactorError(
                BLOCKED_MISSING_RAW_CLOSE,
                "an action session has no raw bar",
                security_id=normalized.security_id,
                session=session,
            )

    # A_(u|a) = product of s_v for u < v <= a, accumulated backwards.
    adjustment: list[Fraction] = []
    running = _ONE
    for session, _close, _volume in reversed(rows):
        adjustment.append(running)
        action = normalized.by_session.get(session)
        if action is not None:
            running *= action.split_factor
    adjustment.reverse()

    sessions: list[SessionFactors] = []
    index = base
    previous_close: Fraction | None = None
    for position, (session, close, volume) in enumerate(rows):
        action = normalized.by_session.get(session)
        factor = _ONE if action is None else action.split_factor
        dividend_post = _ZERO if action is None else action.dividend_post_split_per_share
        dividend_pre = _ZERO if action is None else action.dividend_pre_action_per_share
        adjustment_u = adjustment[position]
        adjusted_close = close / adjustment_u
        adjusted_volume = volume * adjustment_u
        raw_dollar_volume = close * volume
        adjusted_dollar_volume = adjusted_close * adjusted_volume
        if adjusted_dollar_volume != raw_dollar_volume:  # pragma: no cover - exact identity
            raise CorporateActionFactorError(
                BLOCKED_NONFINITE_INPUT,
                "split adjustment did not preserve raw dollar volume",
                session=session,
            )
        session_gross: Fraction | None = None
        if previous_close is not None:
            session_gross = gross_return(
                split_factor=factor,
                raw_close=close,
                raw_close_previous=previous_close,
                dividend_pre_action_per_share=dividend_pre,
                dividend_post_split_per_share=dividend_post,
            )
            index = index * session_gross
        sessions.append(
            SessionFactors(
                session=session,
                raw_close=close,
                raw_volume=volume,
                raw_dollar_volume=raw_dollar_volume,
                split_adjustment_factor=adjustment_u,
                split_adjusted_close=adjusted_close,
                split_adjusted_volume=adjusted_volume,
                split_adjusted_dollar_volume=adjusted_dollar_volume,
                applied_split_factor=factor,
                applied_dividend_post_split_per_share=dividend_post,
                applied_dividend_pre_action_per_share=dividend_pre,
                gross_return=session_gross,
                total_return_index=index,
            )
        )
        previous_close = close

    return FactorSeries(
        security_id=normalized.security_id,
        adjustment_cutoff_session=cutoff,
        state=SERIES_OK if exclusion is None else exclusion.state,
        sessions=tuple(sessions),
        exclusion=exclusion,
    )


# ---------------------------------------------------------------------------
# Ledger walk
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerState:
    """Share / cash / receivable state for one security sleeve."""

    raw_shares: Fraction
    cash: Fraction
    receivables: Fraction

    def nav(self, mark: Fraction) -> Fraction:
        """NAV at ``mark``; the receivable is included between entitlement and payment."""
        return self.cash + self.receivables + self.raw_shares * mark

    def to_json_dict(self) -> dict[str, str]:
        return {
            "raw_shares": render_ledger(self.raw_shares, LEDGER_SHARE_QUANTUM),
            "cash": render_ledger(self.cash),
            "receivables": render_ledger(self.receivables),
        }


def _validate_ledger_state(state: LedgerState) -> LedgerState:
    """Reject negative or non-representable ledger values before any transition."""
    if state.raw_shares < 0 or state.cash < 0 or state.receivables < 0:
        raise CorporateActionFactorError(
            BLOCKED_NEGATIVE_LEDGER_VALUE, "ledger values must be non-negative"
        )
    if (state.raw_shares / LEDGER_SHARE_QUANTUM).denominator != 1:
        raise CorporateActionFactorError(
            BLOCKED_NONREPRESENTABLE_SHARE_QUANTUM,
            "shares are not representable at the registered 1e-8 quantum",
        )
    for value, label in ((state.cash, "cash"), (state.receivables, "receivables")):
        if (value / LEDGER_CURRENCY_QUANTUM).denominator != 1:
            raise CorporateActionFactorError(
                BLOCKED_NONREPRESENTABLE_LEDGER_QUANTUM,
                f"{label} is not representable at the registered 1e-8 quantum",
            )
    return state


def opening_ledger_state(
    *, raw_shares: str = "0", cash: str = "0", receivables: str = "0"
) -> LedgerState:
    """Build a validated opening ledger state from canonical decimal strings."""
    return _validate_ledger_state(
        LedgerState(
            raw_shares=parse_exact(raw_shares, what="raw_shares"),
            cash=parse_exact(cash, what="cash"),
            receivables=parse_exact(receivables, what="receivables"),
        )
    )


@dataclass(frozen=True)
class LedgerTransition:
    """One session of the ledger walk, in the registered within-session order."""

    session: str
    raw_close: Fraction
    raw_close_previous: Fraction | None
    state_before: LedgerState
    state_after: LedgerState
    split_factor: Fraction
    shares_before_split: Fraction
    shares_after_split: Fraction
    split_reference_price: Fraction | None
    split_reference_value_before: Fraction | None
    split_reference_value_after: Fraction | None
    dividend_eligible_raw_shares: Fraction
    dividend_pre_action_per_share: Fraction
    dividend_post_split_per_share: Fraction
    receivable_recognized: Fraction
    receivable_settled: Fraction
    nav_after: Fraction

    def to_json_dict(self) -> dict[str, object]:
        return {
            "session": self.session,
            "raw_close": render_exact(self.raw_close, what="raw_close"),
            "split_factor": render_exact(self.split_factor, what="split_factor"),
            "shares_before_split": render_ledger(self.shares_before_split, LEDGER_SHARE_QUANTUM),
            "shares_after_split": render_ledger(self.shares_after_split, LEDGER_SHARE_QUANTUM),
            "split_reference_price": (
                None
                if self.split_reference_price is None
                else render_artifact(self.split_reference_price)
            ),
            "split_reference_value_before": (
                None
                if self.split_reference_value_before is None
                else render_ledger(self.split_reference_value_before)
            ),
            "split_reference_value_after": (
                None
                if self.split_reference_value_after is None
                else render_ledger(self.split_reference_value_after)
            ),
            "dividend_eligible_raw_shares": render_ledger(
                self.dividend_eligible_raw_shares, LEDGER_SHARE_QUANTUM
            ),
            "dividend_receivable": render_ledger(self.receivable_recognized),
            "receivable_settled": render_ledger(self.receivable_settled),
            "state_after": self.state_after.to_json_dict(),
            "nav_after": render_ledger(self.nav_after),
        }


@dataclass(frozen=True)
class LedgerWalk:
    """The ledger result: per-session transitions plus any unsettled receivable."""

    security_id: str
    state: str
    opening_state: LedgerState
    transitions: tuple[LedgerTransition, ...]
    closing_state: LedgerState
    pending_receivables: tuple[tuple[str, str, Fraction], ...]
    """``(event_id, payment_session, amount)`` recognized but not yet settled."""
    exclusion: ExclusionRecord | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kernel_id": KERNEL_ID,
            "security_id": self.security_id,
            "state": self.state,
            "opening_state": self.opening_state.to_json_dict(),
            "transitions": [item.to_json_dict() for item in self.transitions],
            "closing_state": self.closing_state.to_json_dict(),
            "pending_receivables": [
                {
                    "event_id": event_id,
                    "payment_session": payment_session,
                    "amount": render_ledger(amount),
                }
                for event_id, payment_session, amount in self.pending_receivables
            ],
            "exclusion": None if self.exclusion is None else self.exclusion.to_json_dict(),
            "claims": dict(NON_CLAIMS),
        }


def walk_ledger(
    opening: LedgerState,
    bars: Sequence[RawSessionBar],
    actions: Sequence[CorporateAction],
    *,
    security_id: str,
    adjustment_cutoff_session: str,
    same_day_event_order: str | None = REGISTERED_SAME_DAY_EVENT_ORDER,
    unsupported_action_policy: object = None,
) -> LedgerWalk:
    """Walk the share/cash ledger across the action stream.

    Within one session the registered order applies: the split is applied to the
    prior share entitlement, the distribution is converted to the post-split
    coordinate and recognized as a receivable, and only then are previously
    recognized receivables whose payment session is this session settled to cash.
    Settlement moves receivable to cash and never touches shares, so its position
    in the session cannot change any value.

    Unsupported actions are evaluated against the position actually held at that
    session: a positive holding raises ``RUN_INVALID_UNSUPPORTED_HELD_ACTION``;
    a zero holding excludes the security from that session onward.
    """
    cutoff = _session(adjustment_cutoff_session, what="adjustment_cutoff_session")
    _validate_ledger_state(opening)
    normalized = normalize_actions(
        actions,
        security_id=security_id,
        adjustment_cutoff_session=cutoff,
        held_raw_shares=opening.raw_shares,
        same_day_event_order=same_day_event_order,
        unsupported_action_policy=unsupported_action_policy,
    )
    rows = _normalized_bars(bars, adjustment_cutoff_session=cutoff)

    exclusion = normalized.exclusion
    if exclusion is not None:
        rows = tuple(row for row in rows if row[0] < exclusion.session)

    for session in normalized.ordered_sessions:
        if all(row[0] != session for row in rows):
            raise CorporateActionFactorError(
                BLOCKED_MISSING_RAW_CLOSE,
                "an action session has no raw bar",
                security_id=normalized.security_id,
                session=session,
            )

    state = opening
    pending: list[tuple[str, str, Fraction]] = []
    transitions: list[LedgerTransition] = []
    previous_close: Fraction | None = None
    for session, close, _volume in rows:
        action = normalized.by_session.get(session)
        state_before = state
        factor = _ONE if action is None else action.split_factor
        shares_before = state.raw_shares
        reference_price: Fraction | None = None
        reference_before: Fraction | None = None
        reference_after: Fraction | None = None
        shares_after = shares_before
        if action is not None and action.split_event_ids:
            if previous_close is None:
                raise CorporateActionFactorError(
                    BLOCKED_SPLIT_WITHOUT_PRIOR_RAW_CLOSE,
                    "a split needs a prior raw close to conserve q * P against",
                    security_id=normalized.security_id,
                    event_id=action.split_event_ids[0],
                    session=session,
                )
            shares_after = factor * shares_before
            if (shares_after / LEDGER_SHARE_QUANTUM).denominator != 1:
                raise CorporateActionFactorError(
                    BLOCKED_NONREPRESENTABLE_SHARE_QUANTUM,
                    "split creates shares that are not representable at the 1e-8 quantum",
                    security_id=normalized.security_id,
                    event_id=action.split_event_ids[0],
                    session=session,
                )
            reference_before = verify_split_conservation(
                shares_before=shares_before,
                shares_after=shares_after,
                raw_close_before=previous_close,
                split_factor=factor,
            )
            reference_price = previous_close / factor
            reference_after = shares_after * reference_price
            state = LedgerState(
                raw_shares=shares_after, cash=state.cash, receivables=state.receivables
            )

        recognized = _ZERO
        eligible = state.raw_shares
        dividend_post = _ZERO if action is None else action.dividend_post_split_per_share
        dividend_pre = _ZERO if action is None else action.dividend_pre_action_per_share
        if action is not None and action.payments:
            # receivable_t = q_eligible * d_t, in either coordinate.
            post_split_total = eligible * dividend_post
            pre_action_total = shares_before * dividend_pre
            if post_split_total != pre_action_total:  # pragma: no cover - exact identity
                raise CorporateActionFactorError(
                    BLOCKED_AMBIGUOUS_EVENT_COORDINATE,
                    "pre-action and post-split receivable coordinates disagree",
                    security_id=normalized.security_id,
                    session=session,
                )
            for event_id, payment_session, per_share in action.payments:
                amount = quantize_half_even(eligible * per_share, LEDGER_CURRENCY_QUANTUM)
                recognized += amount
                pending.append((event_id, payment_session, amount))
            state = LedgerState(
                raw_shares=state.raw_shares,
                cash=state.cash,
                receivables=state.receivables + recognized,
            )

        settled = _ZERO
        remaining: list[tuple[str, str, Fraction]] = []
        for event_id, payment_session, amount in pending:
            if payment_session == session:
                settled += amount
            else:
                remaining.append((event_id, payment_session, amount))
        pending = remaining
        if settled:
            state = LedgerState(
                raw_shares=state.raw_shares,
                cash=state.cash + settled,
                receivables=state.receivables - settled,
            )
        if state.receivables < 0 or state.cash < 0 or state.raw_shares < 0:
            raise CorporateActionFactorError(
                BLOCKED_NEGATIVE_LEDGER_VALUE,
                "ledger transition produced a negative value",
                security_id=normalized.security_id,
                session=session,
            )

        transitions.append(
            LedgerTransition(
                session=session,
                raw_close=close,
                raw_close_previous=previous_close,
                state_before=state_before,
                state_after=state,
                split_factor=factor,
                shares_before_split=shares_before,
                shares_after_split=shares_after,
                split_reference_price=reference_price,
                split_reference_value_before=reference_before,
                split_reference_value_after=reference_after,
                dividend_eligible_raw_shares=eligible,
                dividend_pre_action_per_share=dividend_pre,
                dividend_post_split_per_share=dividend_post,
                receivable_recognized=recognized,
                receivable_settled=settled,
                nav_after=state.nav(close),
            )
        )
        previous_close = close

    return LedgerWalk(
        security_id=normalized.security_id,
        state=SERIES_OK if exclusion is None else exclusion.state,
        opening_state=opening,
        transitions=tuple(transitions),
        closing_state=state,
        pending_receivables=tuple(pending),
        exclusion=exclusion,
    )


__all__ = [
    "ARTIFACT_SCALE",
    "BASIS_POST_SPLIT",
    "BASIS_PRE_ACTION",
    "BLOCKED_AMBIGUOUS_EVENT_COORDINATE",
    "BLOCKED_DUPLICATE_EVENT",
    "BLOCKED_DUPLICATE_SESSION",
    "BLOCKED_FOREIGN_SECURITY_ACTION",
    "BLOCKED_MISSING_RAW_CLOSE",
    "BLOCKED_NEGATIVE_DISTRIBUTION",
    "BLOCKED_NEGATIVE_HELD_SHARES",
    "BLOCKED_NEGATIVE_LEDGER_VALUE",
    "BLOCKED_NEGATIVE_RAW_VOLUME",
    "BLOCKED_NONFINITE_INPUT",
    "BLOCKED_NONPOSITIVE_RAW_CLOSE",
    "BLOCKED_NONPOSITIVE_SPLIT_FACTOR",
    "BLOCKED_NONREPRESENTABLE_LEDGER_QUANTUM",
    "BLOCKED_NONREPRESENTABLE_SHARE_QUANTUM",
    "BLOCKED_PAYMENT_BEFORE_ENTITLEMENT",
    "BLOCKED_POST_CUTOFF_EVENT",
    "BLOCKED_POST_CUTOFF_SESSION",
    "BLOCKED_SPLIT_CONSERVATION_VIOLATED",
    "BLOCKED_SPLIT_WITHOUT_PRIOR_RAW_CLOSE",
    "BLOCKED_SUPPORTED_ACTION_AFTER_EXCLUSION",
    "BLOCKED_UNKNOWN_ACTION_TYPE",
    "BLOCKED_UNREGISTERED_SAME_DAY_EVENT_ORDER",
    "BLOCKED_UNREGISTERED_UNSUPPORTED_ACTION_POLICY",
    "CLASSIFICATION_ORDINARY",
    "CLASSIFICATION_SPECIAL",
    "DERIVED_SERIES_NAMES",
    "DIVIDEND_CLASSIFICATIONS",
    "DIVIDEND_SHARE_BASES",
    "EXCLUDED_UNSUPPORTED_UNHELD_ACTION",
    "FAIL_CLOSED_STATES",
    "KERNEL_ID",
    "LEDGER_CURRENCY_QUANTUM",
    "LEDGER_SHARE_QUANTUM",
    "METHODOLOGY_ID",
    "METHODOLOGY_PATH",
    "METHODOLOGY_SHA256_GROUPED",
    "NON_CLAIMS",
    "RAW_SERIES_NAMES",
    "REGISTERED_DIVIDEND_COORDINATE",
    "REGISTERED_GROSS_FACTOR_FORMULA",
    "REGISTERED_SAME_DAY_EVENT_ORDER",
    "ROUNDING_MODE",
    "RUN_INVALID_UNSUPPORTED_HELD_ACTION",
    "SCHEMA_VERSION",
    "SERIES_OK",
    "TICKET_GROSS_RETURN_FORMULA",
    "UNSUPPORTED_ACTION_TYPES",
    "CashDividendAction",
    "CorporateAction",
    "CorporateActionFactorError",
    "ExclusionRecord",
    "FactorSeries",
    "LedgerState",
    "LedgerTransition",
    "LedgerWalk",
    "NormalizedActions",
    "RawSessionBar",
    "SessionActions",
    "SessionFactors",
    "SplitAction",
    "UnsupportedAction",
    "build_factor_series",
    "canonical_decimal",
    "gross_return",
    "normalize_actions",
    "opening_ledger_state",
    "parse_exact",
    "quantize_half_even",
    "render_artifact",
    "render_exact",
    "render_ledger",
    "split_adjustment_factor",
    "verify_split_conservation",
    "walk_ledger",
]
