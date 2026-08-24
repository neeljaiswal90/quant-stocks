"""Token-bucket quota accounting for the Alpha Vantage acquisition boundary.

The contract is exactly the one in NEE-123::

    tokens(t) = min(B, tokens(t0) + R * (t - t0))

One token is spent per request, and only when a token is actually available.
Daily and per-endpoint caps are modelled **separately** from the bucket: the
bucket smooths the rate, the caps are hard ceilings that waiting cannot lift
inside the same UTC day.

Everything here is driven by an injected clock, so the fake-clock tests in
``tests/data/test_av_acquisition_boundary.py`` can prove the invariant
``granted <= B + R * elapsed`` without sleeping.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from qme.data.alpha_vantage.plan_v1 import ProviderPlan

#: Sentinel for "the caller declined to wait for the bucket to refill".
NO_WAIT = 0.0


class QuotaError(ValueError):
    """Raised for quota misuse (naive timestamps, clocks moving backwards)."""


class QuotaExhaustedError(RuntimeError):
    """Raised when a hard cap is spent: waiting inside the same day cannot help."""


class QuotaUnavailableError(RuntimeError):
    """Raised when a token is not available and the caller refused to wait for it."""


def _require_aware(moment: datetime, *, what: str) -> datetime:
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise QuotaError(f"{what} must be a timezone-aware datetime")
    return moment.astimezone(UTC)


@dataclass
class TokenBucket:
    """``tokens(t) = min(B, tokens(t0) + R * (t - t0))`` with an injected clock."""

    rate_per_second: float
    burst: float
    _tokens: float = field(default=0.0)
    _updated_at: datetime | None = field(default=None)

    def __post_init__(self) -> None:
        if self.rate_per_second <= 0:
            raise QuotaError("rate_per_second (R) must be > 0")
        if self.burst < 1:
            raise QuotaError("burst (B) must be >= 1")
        if self._updated_at is not None:
            self._updated_at = _require_aware(self._updated_at, what="bucket start")

    @classmethod
    def full(cls, *, rate_per_second: float, burst: float, started_at: datetime) -> TokenBucket:
        """A bucket that starts full, i.e. ``tokens(t0) = B``."""
        return cls(
            rate_per_second=rate_per_second,
            burst=burst,
            _tokens=burst,
            _updated_at=_require_aware(started_at, what="started_at"),
        )

    def tokens_at(self, now: datetime) -> float:
        moment = _require_aware(now, what="now")
        if self._updated_at is None:
            self._updated_at = moment
            self._tokens = self.burst
            return self._tokens
        elapsed = (moment - self._updated_at).total_seconds()
        if elapsed < 0:
            raise QuotaError("clock moved backwards; refusing to invent quota")
        return min(self.burst, self._tokens + self.rate_per_second * elapsed)

    def seconds_until(self, now: datetime, amount: float = 1.0) -> float:
        """Seconds the caller must wait before ``amount`` tokens exist."""
        if amount > self.burst:
            raise QuotaError(f"cannot ever grant {amount} tokens from a bucket of {self.burst}")
        available = self.tokens_at(now)
        if available >= amount:
            return NO_WAIT
        return (amount - available) / self.rate_per_second

    def consume(self, now: datetime, amount: float = 1.0) -> float:
        """Spend ``amount`` tokens at ``now``; return the tokens left.

        Raises :class:`QuotaUnavailableError` when the tokens are not there —
        the bucket never goes negative and never grants on credit.
        """
        moment = _require_aware(now, what="now")
        available = self.tokens_at(moment)
        if available < amount:
            raise QuotaUnavailableError(
                f"token bucket has {available:.6f} token(s); {amount} required"
            )
        self._tokens = available - amount
        self._updated_at = moment
        return self._tokens


@dataclass(frozen=True)
class QuotaSnapshot:
    """What the ledger looked like at one instant; attached to run manifests."""

    observed_at: str
    plan_id: str
    requests_per_minute: float
    burst: float
    daily_cap: int | None
    tokens_available: float
    granted_total: int
    daily_used: int
    utc_day: str
    endpoint_daily_used: Mapping[str, int]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "plan_id": self.plan_id,
            "requests_per_minute": self.requests_per_minute,
            "burst": self.burst,
            "daily_cap": self.daily_cap,
            "tokens_available": self.tokens_available,
            "granted_total": self.granted_total,
            "daily_used": self.daily_used,
            "utc_day": self.utc_day,
            "endpoint_daily_used": dict(sorted(self.endpoint_daily_used.items())),
        }


@dataclass(frozen=True)
class QuotaGrant:
    """One granted request slot."""

    endpoint: str
    granted_at: str
    waited_seconds: float
    tokens_remaining: float
    daily_used: int
    daily_cap: int | None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "granted_at": self.granted_at,
            "waited_seconds": self.waited_seconds,
            "tokens_remaining": self.tokens_remaining,
            "daily_used": self.daily_used,
            "daily_cap": self.daily_cap,
        }


class QuotaLedger:
    """Plan-bound quota accounting: one shared bucket, per-endpoint buckets, hard caps.

    The ledger never talks to a clock itself; ``acquire`` takes the clock and an
    optional sleep function so a fake clock can drive it deterministically.
    """

    def __init__(self, plan: ProviderPlan, *, started_at: datetime) -> None:
        if not isinstance(plan, ProviderPlan):
            raise QuotaError("plan must be a ProviderPlan evidence record")
        start = _require_aware(started_at, what="started_at")
        self._plan = plan
        self._started_at = start
        self._bucket = TokenBucket.full(
            rate_per_second=plan.refill_rate_per_second,
            burst=plan.burst,
            started_at=start,
        )
        self._endpoint_buckets: dict[str, TokenBucket] = {}
        self._granted_total = 0
        self._daily_used: dict[date, int] = {}
        self._endpoint_daily_used: dict[tuple[date, str], int] = {}

    @property
    def plan(self) -> ProviderPlan:
        return self._plan

    @property
    def granted_total(self) -> int:
        return self._granted_total

    def _endpoint_bucket(self, endpoint: str) -> TokenBucket | None:
        override = self._plan.override_for(endpoint)
        if override is None or (override.requests_per_minute is None and override.burst is None):
            return None
        bucket = self._endpoint_buckets.get(endpoint)
        if bucket is None:
            bucket = TokenBucket.full(
                rate_per_second=self._plan.endpoint_rate_per_second(endpoint),
                burst=self._plan.endpoint_burst(endpoint),
                started_at=self._started_at,
            )
            self._endpoint_buckets[endpoint] = bucket
        return bucket

    def daily_used(self, now: datetime) -> int:
        return self._daily_used.get(_require_aware(now, what="now").date(), 0)

    def endpoint_daily_used(self, endpoint: str, now: datetime) -> int:
        return self._endpoint_daily_used.get((_require_aware(now, what="now").date(), endpoint), 0)

    def _check_caps(self, endpoint: str, now: datetime) -> None:
        day = now.date()
        cap = self._plan.daily_cap
        if cap is not None and self._daily_used.get(day, 0) >= cap:
            raise QuotaExhaustedError(
                f"daily cap of {cap} request(s) for plan {self._plan.plan_id} is spent "
                f"for {day.isoformat()} (UTC); no wait can restore it today"
            )
        endpoint_cap = self._plan.endpoint_daily_cap(endpoint)
        if endpoint_cap is not None and self._endpoint_daily_used.get((day, endpoint), 0) >= endpoint_cap:
            raise QuotaExhaustedError(
                f"daily cap of {endpoint_cap} request(s) for endpoint {endpoint} is spent "
                f"for {day.isoformat()} (UTC); no wait can restore it today"
            )

    def seconds_until_available(self, endpoint: str, now: datetime) -> float:
        moment = _require_aware(now, what="now")
        wait = self._bucket.seconds_until(moment)
        endpoint_bucket = self._endpoint_bucket(endpoint)
        if endpoint_bucket is not None:
            wait = max(wait, endpoint_bucket.seconds_until(moment))
        return wait

    def acquire(
        self,
        endpoint: str,
        *,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None] | None = None,
        max_wait_seconds: float = 0.0,
    ) -> QuotaGrant:
        """Spend one token for ``endpoint``, waiting only if allowed to.

        Raises :class:`QuotaExhaustedError` for a spent hard cap and
        :class:`QuotaUnavailableError` when a token would need a longer wait
        than ``max_wait_seconds``.
        """
        if not endpoint or endpoint != endpoint.strip():
            raise QuotaError(f"endpoint {endpoint!r} is not canonical")
        now = _require_aware(clock(), what="clock()")
        self._check_caps(endpoint, now)
        wait = self.seconds_until_available(endpoint, now)
        waited = 0.0
        if wait > 0:
            if sleep is None or wait > max_wait_seconds:
                raise QuotaUnavailableError(
                    f"no token available for {endpoint}; {wait:.6f}s of refill required "
                    f"(max_wait_seconds={max_wait_seconds})"
                )
            sleep(wait)
            waited = wait
            now = _require_aware(clock(), what="clock()")
            self._check_caps(endpoint, now)
            if self.seconds_until_available(endpoint, now) > 0:
                raise QuotaUnavailableError(
                    f"clock did not advance past the {wait:.6f}s refill wait for {endpoint}"
                )
        remaining = self._bucket.consume(now)
        endpoint_bucket = self._endpoint_bucket(endpoint)
        if endpoint_bucket is not None:
            endpoint_bucket.consume(now)
        day = now.date()
        self._granted_total += 1
        self._daily_used[day] = self._daily_used.get(day, 0) + 1
        key = (day, endpoint)
        self._endpoint_daily_used[key] = self._endpoint_daily_used.get(key, 0) + 1
        return QuotaGrant(
            endpoint=endpoint,
            granted_at=now.isoformat(timespec="microseconds"),
            waited_seconds=waited,
            tokens_remaining=remaining,
            daily_used=self._daily_used[day],
            daily_cap=self._plan.daily_cap,
        )

    def snapshot(self, now: datetime) -> QuotaSnapshot:
        moment = _require_aware(now, what="now")
        day = moment.date()
        return QuotaSnapshot(
            observed_at=moment.isoformat(timespec="microseconds"),
            plan_id=self._plan.plan_id,
            requests_per_minute=self._plan.requests_per_minute,
            burst=self._plan.burst,
            daily_cap=self._plan.daily_cap,
            tokens_available=self._bucket.tokens_at(moment),
            granted_total=self._granted_total,
            daily_used=self._daily_used.get(day, 0),
            utc_day=day.isoformat(),
            endpoint_daily_used={
                endpoint: count
                for (used_day, endpoint), count in sorted(self._endpoint_daily_used.items())
                if used_day == day
            },
        )
