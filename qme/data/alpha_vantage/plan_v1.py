"""Provider-plan evidence for the Alpha Vantage acquisition boundary.

The quota contract (token bucket ``tokens(t) = min(B, tokens(t0) + R * (t - t0))``
plus separately modelled daily and per-endpoint caps) is only as trustworthy as
the plan numbers it is fed. ``R``, ``B``, and the daily cap therefore come from a
**registered provider-plan evidence record** that carries its own source and
effective window; a stale hard-coded assumption may never silently apply.

Fail-closed rules (see :func:`resolve_plan`):

* the request timestamp must be timezone-aware;
* exactly one registered plan must be effective at that timestamp;
* a timestamp before the earliest ``effective_date`` — or after a plan's
  ``expires_after`` — resolves to nothing and raises, it does not fall back;
* a malformed plan record raises at construction time, not at request time.

Placement note: the builder spec's first choice was a JSON artifact at
``configs/data/alpha-vantage-plan-v1.json``. ``configs/data/**`` matches **no**
rule in ``configs/governance/change-tier-policy-v1.json``, so a tracked file
there is *unclassified* and fails ``python -m qme.foundation.change_tiers``;
adding a rule would mean editing a frozen T0 governance config. The spec's
declared fallback is therefore used: typed constants under ``qme/**`` (T2), with
the same source and effective-date fields the JSON artifact would have carried.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

PLAN_SCHEMA_VERSION = "qme.av_provider_plan.v1"

#: Stable identity of the acquisition provider.
PROVIDER_ID = "alpha_vantage"

#: Provider surface version that participates in the cache identity
#: (``request_key = SHA256(provider_version || endpoint || canonical_parameters)``).
#: Bump this whenever the provider changes response semantics for the same
#: endpoint and parameters, so old cache entries stop being reused silently.
PROVIDER_VERSION = "alphavantage.co/query/v1"

#: Recognised provenance kinds for a plan record.
SOURCE_KINDS: frozenset[str] = frozenset(
    {
        "OWNER_STATEMENT",
        "OWNER_DECISION_RECORD",
        "PROVIDER_DOCUMENTATION",
        "OWNER_DECISION_AND_PROVIDER_DOCUMENTATION",
        "TEST_CONSTRUCTED",
    }
)


class ProviderPlanError(ValueError):
    """Raised when plan evidence is absent, malformed, or not effective."""


def _parse_date(value: str, *, what: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ProviderPlanError(f"{what} must be an ISO YYYY-MM-DD date, got {value!r}") from exc


@dataclass(frozen=True)
class EndpointQuota:
    """A per-endpoint override of the plan-wide quota numbers."""

    endpoint: str
    requests_per_minute: float | None = None
    burst: float | None = None
    daily_cap: int | None = None

    def __post_init__(self) -> None:
        if not self.endpoint or self.endpoint != self.endpoint.strip():
            raise ProviderPlanError(f"endpoint override name is not canonical: {self.endpoint!r}")
        if self.requests_per_minute is not None and self.requests_per_minute <= 0:
            raise ProviderPlanError(f"{self.endpoint}: requests_per_minute must be > 0")
        if self.burst is not None and self.burst < 1:
            raise ProviderPlanError(f"{self.endpoint}: burst must be >= 1")
        if self.daily_cap is not None and self.daily_cap < 1:
            raise ProviderPlanError(f"{self.endpoint}: daily_cap must be >= 1")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "requests_per_minute": self.requests_per_minute,
            "burst": self.burst,
            "daily_cap": self.daily_cap,
        }


@dataclass(frozen=True)
class ProviderPlan:
    """One registered provider-plan evidence record.

    ``requests_per_minute`` is the token-bucket refill rate ``R`` expressed per
    minute; ``burst`` is the bucket capacity ``B``. ``daily_cap`` is ``None``
    when the recorded source states there is no daily limit — that is a modelled
    fact from the source, not an unbounded default.
    """

    plan_id: str
    plan_name: str
    source_kind: str
    source: str
    source_reference: str
    effective_date: str
    requests_per_minute: float
    burst: float
    daily_cap: int | None = None
    expires_after: str | None = None
    provider_id: str = PROVIDER_ID
    provider_version: str = PROVIDER_VERSION
    schema_version: str = PLAN_SCHEMA_VERSION
    endpoint_overrides: tuple[EndpointQuota, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.plan_id.strip():
            raise ProviderPlanError("plan_id must be non-empty")
        if not self.plan_name.strip():
            raise ProviderPlanError(f"{self.plan_id}: plan_name must be non-empty")
        if self.source_kind not in SOURCE_KINDS:
            raise ProviderPlanError(
                f"{self.plan_id}: source_kind {self.source_kind!r} is not one of "
                f"{sorted(SOURCE_KINDS)}"
            )
        if not self.source.strip():
            raise ProviderPlanError(f"{self.plan_id}: source must state where the numbers came from")
        if not self.source_reference.strip():
            raise ProviderPlanError(f"{self.plan_id}: source_reference must cite a document or URL")
        if self.schema_version != PLAN_SCHEMA_VERSION:
            raise ProviderPlanError(f"{self.plan_id}: unsupported schema_version")
        if self.provider_id != PROVIDER_ID:
            raise ProviderPlanError(f"{self.plan_id}: provider_id must be {PROVIDER_ID!r}")
        if not self.provider_version.strip():
            raise ProviderPlanError(f"{self.plan_id}: provider_version must be non-empty")
        effective = _parse_date(self.effective_date, what=f"{self.plan_id}: effective_date")
        if self.expires_after is not None:
            expires = _parse_date(self.expires_after, what=f"{self.plan_id}: expires_after")
            if expires < effective:
                raise ProviderPlanError(f"{self.plan_id}: expires_after precedes effective_date")
        if self.requests_per_minute <= 0:
            raise ProviderPlanError(f"{self.plan_id}: requests_per_minute (R) must be > 0")
        if self.burst < 1:
            raise ProviderPlanError(f"{self.plan_id}: burst (B) must be >= 1")
        if self.daily_cap is not None and self.daily_cap < 1:
            raise ProviderPlanError(f"{self.plan_id}: daily_cap must be >= 1 or None")
        seen: set[str] = set()
        for override in self.endpoint_overrides:
            if not isinstance(override, EndpointQuota):
                raise ProviderPlanError(f"{self.plan_id}: endpoint_overrides must be EndpointQuota")
            if override.endpoint in seen:
                raise ProviderPlanError(
                    f"{self.plan_id}: duplicate endpoint override {override.endpoint!r}"
                )
            seen.add(override.endpoint)

    # -- derived numbers ----------------------------------------------------

    @property
    def refill_rate_per_second(self) -> float:
        """``R`` in tokens per second."""
        return self.requests_per_minute / 60.0

    def override_for(self, endpoint: str) -> EndpointQuota | None:
        for override in self.endpoint_overrides:
            if override.endpoint == endpoint:
                return override
        return None

    def endpoint_rate_per_second(self, endpoint: str) -> float:
        override = self.override_for(endpoint)
        if override is not None and override.requests_per_minute is not None:
            return override.requests_per_minute / 60.0
        return self.refill_rate_per_second

    def endpoint_burst(self, endpoint: str) -> float:
        override = self.override_for(endpoint)
        if override is not None and override.burst is not None:
            return override.burst
        return self.burst

    def endpoint_daily_cap(self, endpoint: str) -> int | None:
        override = self.override_for(endpoint)
        if override is not None:
            return override.daily_cap
        return None

    def is_effective_on(self, day: date) -> bool:
        if day < _parse_date(self.effective_date, what=f"{self.plan_id}: effective_date"):
            return False
        if self.expires_after is None:
            return True
        return day <= _parse_date(self.expires_after, what=f"{self.plan_id}: expires_after")

    # -- serialization ------------------------------------------------------

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "plan_name": self.plan_name,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
            "effective_date": self.effective_date,
            "expires_after": self.expires_after,
            "requests_per_minute": self.requests_per_minute,
            "burst": self.burst,
            "daily_cap": self.daily_cap,
            "endpoint_overrides": [item.to_json_dict() for item in self.endpoint_overrides],
        }


# ---------------------------------------------------------------------------
# Registered plan evidence
# ---------------------------------------------------------------------------

#: The AV premium burst the owner decided on 2026-08-12. Both numbers below are
#: recorded facts in this repository, not assumptions:
#:
#: * ``momentum_strategy_audit.md`` records the premium tier as 75 requests per
#:   minute with no daily limit, citing https://www.alphavantage.co/premium/;
#: * ``docs/implementation/M0_CLOSEOUT_EXECUTION_PLAN_2026-08-12.md`` §6.4
#:   records the owner decision of 2026-08-12 to buy **one month** of premium
#:   scoped to M0-blocker evidence "then cancel".
#:
#: The one-month wording is what bounds ``expires_after``; once that day passes
#: the loader fails closed rather than keep spending against a plan that the
#: recorded decision says was cancelled.
PREMIUM_BURST_2026_08 = ProviderPlan(
    plan_id="alphavantage-premium-75rpm-2026-08",
    plan_name="Alpha Vantage premium (one-month M0 burst)",
    source_kind="OWNER_DECISION_AND_PROVIDER_DOCUMENTATION",
    source=(
        "Owner decision of 2026-08-12 to purchase one month of Alpha Vantage premium "
        "scoped to M0-blocker evidence and then cancel (M0_CLOSEOUT_EXECUTION_PLAN "
        "2026-08-12 section 6.4); premium rate of 75 requests per minute with no daily "
        "limit as recorded in momentum_strategy_audit.md from the Alpha Vantage premium "
        "pricing page. expires_after is the one-month bound implied by that decision."
    ),
    source_reference=(
        "docs/implementation/M0_CLOSEOUT_EXECUTION_PLAN_2026-08-12.md#64 ; "
        "momentum_strategy_audit.md ; https://www.alphavantage.co/premium/"
    ),
    effective_date="2026-08-12",
    expires_after="2026-09-12",
    requests_per_minute=75.0,
    burst=75.0,
    daily_cap=None,
)

#: Every plan this repository has evidence for, oldest first.
REGISTERED_PLANS: tuple[ProviderPlan, ...] = (PREMIUM_BURST_2026_08,)


def validate_registry(plans: Sequence[ProviderPlan] = REGISTERED_PLANS) -> None:
    """Fail closed on a malformed or ambiguous plan registry."""
    if not plans:
        raise ProviderPlanError("no provider-plan evidence is registered")
    identifiers: set[str] = set()
    windows: list[tuple[date, date | None, str]] = []
    for plan in plans:
        if not isinstance(plan, ProviderPlan):
            raise ProviderPlanError("registry entries must be ProviderPlan records")
        if plan.plan_id in identifiers:
            raise ProviderPlanError(f"duplicate plan_id in registry: {plan.plan_id}")
        identifiers.add(plan.plan_id)
        start = _parse_date(plan.effective_date, what=f"{plan.plan_id}: effective_date")
        end = (
            None
            if plan.expires_after is None
            else _parse_date(plan.expires_after, what=f"{plan.plan_id}: expires_after")
        )
        windows.append((start, end, plan.plan_id))
    windows.sort(key=lambda item: item[0])
    for (start_a, end_a, id_a), (start_b, _end_b, id_b) in zip(windows, windows[1:], strict=False):
        if end_a is None or start_b <= end_a:
            raise ProviderPlanError(
                f"plan windows overlap: {id_a} (from {start_a}) and {id_b} (from {start_b})"
            )


def resolve_plan(
    requested_at: datetime,
    *,
    plans: Sequence[ProviderPlan] = REGISTERED_PLANS,
    plan_id: str | None = None,
) -> ProviderPlan:
    """Return the plan effective at ``requested_at``, or fail closed.

    ``requested_at`` must be timezone-aware; the effective window is evaluated
    against its UTC calendar date.
    """
    if not isinstance(requested_at, datetime) or requested_at.tzinfo is None:
        raise ProviderPlanError("requested_at must be a timezone-aware datetime")
    validate_registry(plans)
    utc_day = requested_at.astimezone(UTC).date()
    effective = [plan for plan in plans if plan.is_effective_on(utc_day)]
    if plan_id is not None:
        effective = [plan for plan in effective if plan.plan_id == plan_id]
        if not effective:
            raise ProviderPlanError(
                f"plan {plan_id!r} is not registered or is not effective on {utc_day.isoformat()}"
            )
    if not effective:
        raise ProviderPlanError(
            "no registered Alpha Vantage plan evidence is effective on "
            f"{utc_day.isoformat()}; register a plan record with its source and "
            "effective date before acquiring (refusing to assume a rate limit)"
        )
    if len(effective) > 1:
        names = ", ".join(sorted(plan.plan_id for plan in effective))
        raise ProviderPlanError(f"ambiguous plan evidence on {utc_day.isoformat()}: {names}")
    return effective[0]


def plan_evidence_dict(plan: ProviderPlan) -> Mapping[str, Any]:
    """The subset a run manifest attaches: who said what, and from when."""
    return {
        "plan_id": plan.plan_id,
        "plan_name": plan.plan_name,
        "provider_id": plan.provider_id,
        "provider_version": plan.provider_version,
        "source_kind": plan.source_kind,
        "source": plan.source,
        "source_reference": plan.source_reference,
        "effective_date": plan.effective_date,
        "expires_after": plan.expires_after,
        "requests_per_minute": plan.requests_per_minute,
        "burst": plan.burst,
        "daily_cap": plan.daily_cap,
        "endpoint_overrides": [item.to_json_dict() for item in plan.endpoint_overrides],
    }
