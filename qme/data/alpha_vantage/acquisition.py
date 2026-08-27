"""The Alpha Vantage acquisition boundary (NEE-123).

One call in, one auditable record out::

    inputs   canonical endpoint, normalized parameters, credential reference,
             provider/account quota profile, retry policy, acquisition purpose,
             request timestamp
    outputs  raw response bytes, content type, HTTP metadata, request-parameter
             hash, response SHA-256, acquisition timestamp, retry/throttle log,
             provider metadata, parser/schema status, immutable local URI

Ordering rule, enforced here and provable by test: **the raw bytes are durably
stored before any parser is invoked.** ``RawPullStore`` writes to a temporary
file, ``fsync``-s it, and publishes it under its final name atomically; only
after that does :meth:`AcquisitionBoundary.acquire` hand the bytes to a parser,
and a parser that raises cannot unwrite them.

Cache rule: the identity is
``request_key = SHA256(provider_version || endpoint || canonical_parameters)``
with the credential excluded by construction. A request whose key is already in
the immutable store is served from disk, spends no quota, and needs no
transport — which is what makes replays work with the network disabled.

Quota rule: ``R``, ``B``, and any daily cap come from registered provider-plan
evidence (:mod:`qme.data.alpha_vantage.plan_v1`) resolved against the request
timestamp. Missing or expired evidence fails closed; it never falls back to a
hard-coded assumption.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import threading
import time
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn, Self, cast

from qme.data.alpha_vantage.client import (
    CREDENTIAL_PARAM_NAMES,
    STATE_CLOCK_REGRESSION,
    STATE_DATA,
    STATE_TRANSPORT_FAILURE,
    AlphaVantageClient,
    ClockRegressionError,
    CredentialError,
    FetchOutcome,
    ImplementationIdentityError,
    PayloadState,
    ProviderAuthorityError,
    RetryEvent,
    RetryPolicy,
    _evidence_value_contains_encoded_secret,
    body_contains_credential_material,
    callable_implementation_identity,
    canonical_endpoint,
    canonical_parameters,
    parameters_hash_from_pairs,
    request_key,
)
from qme.data.alpha_vantage.normalize import (
    DIVIDENDS_MAX_ROWS,
    LISTING_STATUS_MAX_ROWS_PER_RESPONSE,
    MAX_ALPHA_VANTAGE_AUXILIARY_JSON_NODES,
    MAX_ALPHA_VANTAGE_JSON_CONTAINER_DEPTH,
    NORMALIZER_VERSION,
    SPLITS_MAX_ROWS,
    TIME_SERIES_DAILY_MAX_ROWS,
)
from qme.data.alpha_vantage.plan_v1 import (
    PROVIDER_ID,
    PROVIDER_VERSION,
    REGISTERED_PLANS,
    ProviderPlan,
    ProviderPlanError,
    plan_evidence_dict,
    resolve_plan,
)
from qme.data.alpha_vantage.quota import QuotaGrant, QuotaLedger, QuotaSnapshot
from qme.data.alpha_vantage.store import (
    RawCacheMissError,
    RawPullRecord,
    RawPullStore,
    RawPullStoreError,
    ReplayLineage,
    RequestKeyEntry,
)
from qme.data.alpha_vantage.validators import (
    ValidationSummary,
    validate_dividends,
    validate_listing_status,
    validate_splits,
    validate_time_series_daily,
)
from qme.foundation.data_root import DataRootError, DataRootLayout
from qme.foundation.lineage import canonical_json_bytes, write_manifest_new

ACQUISITION_RUN_SCHEMA_VERSION = "qme.av_acquisition_run.v1"
_MANIFEST_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", flags=re.ASCII)
_RUNTIME_SEAL_KEY = secrets.token_bytes(32)
_REGISTERED_PROVIDER_AUTHORITY = (PROVIDER_ID, PROVIDER_VERSION)
_REGISTERED_PROVIDER_AUTHORITY_SEAL = hmac.new(
    _RUNTIME_SEAL_KEY,
    "\x00".join(_REGISTERED_PROVIDER_AUTHORITY).encode("utf-8"),
    hashlib.sha256,
).hexdigest()
_RAW_STORAGE_COMPONENT_RE = re.compile(
    r"(?:_|[A-Za-z0-9][A-Za-z0-9._-]{0,63})", flags=re.ASCII
)

#: Version stamped on every parse produced by the shipped shape validators.
#: Bump it whenever a validator changes what it accepts or what it reports, so a
#: replayed parse hash cannot silently mean two different things.
VALIDATOR_PARSER_VERSION = "qme.av_validators.v1"


def _validate_registered_provider_authority() -> None:
    observed_seal = hmac.new(
        _RUNTIME_SEAL_KEY,
        "\x00".join(_REGISTERED_PROVIDER_AUTHORITY).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(
        observed_seal,
        _REGISTERED_PROVIDER_AUTHORITY_SEAL,
    ):
        raise AcquisitionError("registered provider authority changed")


def _require_registered_plan_authority(plan: ProviderPlan) -> None:
    _validate_registered_provider_authority()
    if (plan.provider_id, plan.provider_version) != _REGISTERED_PROVIDER_AUTHORITY:
        raise AcquisitionError("provider-plan provider authority is not registered")

# Parser status values.
PARSER_STATUS_PARSED = "PARSED"
PARSER_STATUS_ERROR = "PARSER_ERROR"
PARSER_STATUS_SKIPPED_NON_DATA = "SKIPPED_NON_DATA"
PARSER_STATUS_NO_PARSER = "NO_PARSER_DECLARED"
PARSER_STATUS_NOT_INVOKED = "NOT_INVOKED"

PARSER_OUTPUT_PARSED_DATA = "PARSED_DATA"
PARSER_OUTPUT_NORMALIZED_DATA = "NORMALIZED_DATA"
_NORMALIZATION_LIMIT_FAILURES = frozenset(
    {
        "NORMALIZATION_ROW_LIMIT_EXCEEDED",
        "NORMALIZATION_AUXILIARY_NODE_LIMIT_EXCEEDED",
        "NORMALIZATION_JSON_DEPTH_LIMIT_EXCEEDED",
    }
)


def _normalization_failure_detail(error: BaseException) -> str | None:
    detail = str(error)
    return detail if detail in _NORMALIZATION_LIMIT_FAILURES else None
EFFECTIVE_ACCEPTED_PARSED_DATA = "ACCEPTED_PARSED_DATA"
EFFECTIVE_ACCEPTED_NORMALIZED_DATA = "ACCEPTED_NORMALIZED_DATA"


def _default_manifest_claims() -> dict[str, bool]:
    return {
        "raw_bytes_stored_before_parse": True,
        "network_client_reachable_from_backtest": False,
        "production_pit_evidence_registered": False,
        "freeze_blocker_changed": False,
    }


class AcquisitionError(RuntimeError):
    """Raised when an acquisition cannot complete safely. Never carries a credential."""


class CredentialEvidenceError(AcquisitionError):
    """Raised when a response body carries the active credential.

    Credential non-persistence outranks raw-body retention: the body and its
    cache entry are withheld entirely. Only the two non-reversible facts about
    the withheld bytes -- how many there were and their digest -- are carried,
    so the rejection stays auditable without republishing the secret.
    """

    def __init__(self, *, byte_length: int, body_sha256: str) -> None:
        super().__init__("RAW_EVIDENCE_CONTAINS_CREDENTIAL_MATERIAL")
        self.byte_length = byte_length
        self.body_sha256 = body_sha256


class CacheLineageError(AcquisitionError):
    """A committed v1 cache entry lacks the Wave B authority needed for replay."""


def _runtime_seal(material: object) -> str:
    return hmac.new(
        _RUNTIME_SEAL_KEY,
        canonical_json_value_bytes(material),
        hashlib.sha256,
    ).hexdigest()


def canonical_json_value_bytes(document: object) -> bytes:
    """Serialize any JSON-compatible value with the foundation byte contract."""
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _callable_implementation_sha256(function: Callable[..., object]) -> str:
    try:
        return callable_implementation_identity(function)[1]
    except (ImplementationIdentityError, RecursionError) as exc:
        detail = str(exc)
        if "cyclic" in detail:
            raise AcquisitionError(f"cyclic parser identity value: {detail}") from exc
        raise AcquisitionError(f"unsupported parser identity value: {detail}") from exc


class _CyclicContainerError(TypeError):
    """A parser or manifest value contains a reference cycle."""


class _TraversalLimitError(TypeError):
    """Remote or attached material exceeded a deterministic traversal bound."""


_TRAVERSAL_MAX_DEPTH = 128
_TRAVERSAL_MAX_ITEMS = 100_000


class _ImmutableJSONDict(dict[str, Any]):
    """A JSON-serializable mapping whose public mutation methods fail closed."""

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> NoReturn:
        raise TypeError("registered normalized output is immutable")

    def __setitem__(self, *_args: object, **_kwargs: object) -> NoReturn:
        self._immutable()

    def __delitem__(self, *_args: object, **_kwargs: object) -> NoReturn:
        self._immutable()

    def __ior__(self, _value: object, /) -> Self:  # type: ignore[override,misc]
        self._immutable()

    def clear(self) -> NoReturn:
        self._immutable()

    def pop(self, *_args: object, **_kwargs: object) -> NoReturn:
        self._immutable()

    def popitem(self) -> NoReturn:
        self._immutable()

    def setdefault(self, *_args: object, **_kwargs: object) -> NoReturn:
        self._immutable()

    def update(self, *_args: object, **_kwargs: object) -> NoReturn:
        self._immutable()


_REGISTERED_NORMALIZED_OUTPUT_TOKEN = object()


class _RegisteredNormalizedOutput(_ImmutableJSONDict):
    """Schema-validated closed-registry output carried opaquely through generic bounds."""

    def __init__(
        self,
        document: dict[str, Any],
        *,
        endpoint: str,
        row_count: int,
        token: object,
    ) -> None:
        if token is not _REGISTERED_NORMALIZED_OUTPUT_TOKEN:
            raise AcquisitionError("registered normalized output authority is unavailable")
        dict.__init__(self, document)
        canonical = canonical_json_value_bytes(self)
        self._registered_endpoint = endpoint
        self._registered_row_count = row_count
        self._registered_content_sha256 = hashlib.sha256(canonical).hexdigest()
        self._registered_seal = hmac.new(
            _RUNTIME_SEAL_KEY,
            endpoint.encode("utf-8") + b"\x00" + canonical,
            hashlib.sha256,
        ).hexdigest()

    def _validated_canonical_bytes(self) -> bytes:
        try:
            canonical = canonical_json_value_bytes(self)
        except (RecursionError, TypeError, ValueError) as exc:
            raise AcquisitionError("registered normalized output is invalid") from exc
        content_sha256 = hashlib.sha256(canonical).hexdigest()
        seal = hmac.new(
            _RUNTIME_SEAL_KEY,
            self._registered_endpoint.encode("utf-8") + b"\x00" + canonical,
            hashlib.sha256,
        ).hexdigest()
        if content_sha256 != self._registered_content_sha256 or not hmac.compare_digest(
            seal, self._registered_seal
        ):
            raise AcquisitionError("registered normalized output changed after validation")
        return canonical

    @property
    def content_sha256(self) -> str:
        self._validated_canonical_bytes()
        return self._registered_content_sha256

    def runtime_seal_material(self) -> dict[str, object]:
        return {
            "registered_endpoint": self._registered_endpoint,
            "registered_row_count": self._registered_row_count,
            "registered_content_sha256": self.content_sha256,
        }

    def to_json_value(self) -> dict[str, Any]:
        decoded = json.loads(self._validated_canonical_bytes())
        if not isinstance(decoded, dict):  # pragma: no cover - construction guarantees this
            raise AcquisitionError("registered normalized output is invalid")
        return decoded

    def contains_secret_material(self, *, secrets: tuple[str, ...]) -> bool:
        self._validated_canonical_bytes()
        found = False
        stack: list[object] = [self]
        while stack:
            current = stack.pop()
            if isinstance(current, str):
                if _evidence_value_contains_encoded_secret(current, secrets=secrets):
                    found = True
                continue
            if isinstance(current, dict):
                for key, item in dict.items(current):
                    if key.strip().lower() in CREDENTIAL_PARAM_NAMES:
                        found = True
                    if _evidence_value_contains_encoded_secret(key, secrets=secrets):
                        found = True
                    stack.append(item)
                continue
            if isinstance(current, tuple):
                stack.extend(current)
        return found


@dataclass(frozen=True)
class _RegisteredNormalizedSchema:
    max_rows: int
    row_fields: frozenset[str]
    canonical_key_field: str
    cutoff_status: str
    allows_extra: bool
    max_notes: int


_REGISTERED_NORMALIZED_SCHEMAS: Mapping[str, _RegisteredNormalizedSchema] = (
    MappingProxyType(
        {
            "TIME_SERIES_DAILY": _RegisteredNormalizedSchema(
                max_rows=TIME_SERIES_DAILY_MAX_ROWS,
                row_fields=frozenset({"date", "open", "high", "low", "close", "volume"}),
                canonical_key_field="date",
                cutoff_status="OBSERVATIONS_ON_OR_BEFORE_ANALYSIS_AS_OF",
                allows_extra=True,
                max_notes=1,
            ),
            "DIVIDENDS": _RegisteredNormalizedSchema(
                max_rows=DIVIDENDS_MAX_ROWS,
                row_fields=frozenset(
                    {
                        "ex_dividend_date",
                        "declaration_date",
                        "record_date",
                        "payment_date",
                        "amount",
                    }
                ),
                canonical_key_field="complete_normalized_row",
                cutoff_status="AVAILABILITY_AT_ACQUISITION_BOUND_ONLY",
                allows_extra=True,
                max_notes=0,
            ),
            "SPLITS": _RegisteredNormalizedSchema(
                max_rows=SPLITS_MAX_ROWS,
                row_fields=frozenset({"effective_date", "split_factor"}),
                canonical_key_field="complete_normalized_row",
                cutoff_status="AVAILABILITY_AT_ACQUISITION_BOUND_ONLY",
                allows_extra=True,
                max_notes=0,
            ),
            "LISTING_STATUS": _RegisteredNormalizedSchema(
                max_rows=LISTING_STATUS_MAX_ROWS_PER_RESPONSE,
                row_fields=frozenset(
                    {
                        "symbol",
                        "name",
                        "exchange",
                        "asset_type",
                        "ipo_date",
                        "delisting_date",
                        "status",
                    }
                ),
                canonical_key_field="symbol",
                cutoff_status="AVAILABLE_AT_ACQUISITION",
                allows_extra=False,
                max_notes=0,
            ),
        }
    )
)


@dataclass
class _RegisteredAuxiliaryBudget:
    consumed: int = 0

    def consume(self) -> None:
        self.consumed += 1
        if self.consumed > MAX_ALPHA_VANTAGE_AUXILIARY_JSON_NODES:
            raise _TraversalLimitError("auxiliary node limit exceeded")


def _freeze_registered_auxiliary_value(
    value: object,
    *,
    budget: _RegisteredAuxiliaryBudget,
    container_depth: int,
    active: set[int],
) -> object:
    if value is None or type(value) is str:
        return value
    if type(value) is list:
        if container_depth > MAX_ALPHA_VANTAGE_JSON_CONTAINER_DEPTH:
            raise _TraversalLimitError("container depth limit exceeded")
        identity = id(value)
        if identity in active:
            raise _CyclicContainerError("cyclic container")
        active.add(identity)
        try:
            frozen_sequence: list[object] = []
            for item in value:
                budget.consume()
                frozen_sequence.append(
                    _freeze_registered_auxiliary_value(
                        item,
                        budget=budget,
                        container_depth=container_depth + 1,
                        active=active,
                    )
                )
            return tuple(frozen_sequence)
        finally:
            active.remove(identity)
    if type(value) is dict:
        if container_depth > MAX_ALPHA_VANTAGE_JSON_CONTAINER_DEPTH:
            raise _TraversalLimitError("container depth limit exceeded")
        identity = id(value)
        if identity in active:
            raise _CyclicContainerError("cyclic container")
        active.add(identity)
        try:
            if any(type(key) is not str for key in value):
                raise TypeError("registered auxiliary mappings must use text keys")
            frozen_mapping: dict[str, object] = {}
            for key in sorted(value):
                budget.consume()
                frozen_mapping[key] = _freeze_registered_auxiliary_value(
                    value[key],
                    budget=budget,
                    container_depth=container_depth + 1,
                    active=active,
                )
            return _ImmutableJSONDict(frozen_mapping)
        finally:
            active.remove(identity)
    raise TypeError("registered auxiliary output contains a non-JSON value")


def _freeze_registered_auxiliary_members(
    value: object,
    *,
    budget: _RegisteredAuxiliaryBudget,
    child_container_depth: int,
) -> _ImmutableJSONDict:
    if type(value) is not dict:
        raise TypeError("registered auxiliary output is not an object")
    if any(type(key) is not str for key in value):
        raise TypeError("registered auxiliary mappings must use text keys")
    frozen: dict[str, object] = {}
    active = {id(value)}
    for key in sorted(value):
        budget.consume()
        frozen[key] = _freeze_registered_auxiliary_value(
            value[key],
            budget=budget,
            container_depth=child_container_depth,
            active=active,
        )
    return _ImmutableJSONDict(frozen)


@dataclass
class _TraversalBudget:
    items: int = 0

    def enter(self, depth: int) -> None:
        if depth > _TRAVERSAL_MAX_DEPTH:
            raise _TraversalLimitError("container depth limit exceeded")
        self.items += 1
        if self.items > _TRAVERSAL_MAX_ITEMS:
            raise _TraversalLimitError("container item limit exceeded")


def _bounded_mapping_items(
    value: Mapping[Any, Any],
    *,
    budget: _TraversalBudget,
) -> list[tuple[Any, Any]]:
    remaining = _TRAVERSAL_MAX_ITEMS - budget.items
    if remaining < 0 or len(value) > remaining:
        raise _TraversalLimitError("container item limit exceeded")
    items: list[tuple[Any, Any]] = []
    for item in value.items():
        if len(items) >= remaining:
            raise _TraversalLimitError("container item limit exceeded")
        items.append(item)
    return items


def _deep_freeze(
    value: Any,
    *,
    _active: set[int] | None = None,
    _budget: _TraversalBudget | None = None,
    _depth: int = 0,
) -> Any:
    if isinstance(value, _RegisteredNormalizedOutput):
        value._validated_canonical_bytes()
        return value
    active = set() if _active is None else _active
    budget = _TraversalBudget() if _budget is None else _budget
    budget.enter(_depth)
    if isinstance(value, Mapping):
        items = _bounded_mapping_items(value, budget=budget)
        if any(not isinstance(key, str) for key, _item in items):
            raise TypeError("parser mappings must use text keys")
        identity = id(value)
        if identity in active:
            raise _CyclicContainerError("cyclic container")
        active.add(identity)
        try:
            return MappingProxyType(
                {
                    key: _deep_freeze(
                        item,
                        _active=active,
                        _budget=budget,
                        _depth=_depth + 1,
                    )
                    for key, item in sorted(items)
                }
            )
        finally:
            active.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise _CyclicContainerError("cyclic container")
        active.add(identity)
        try:
            return tuple(
                _deep_freeze(
                    item,
                    _active=active,
                    _budget=budget,
                    _depth=_depth + 1,
                )
                for item in value
            )
        finally:
            active.remove(identity)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise TypeError(
        f"parser output contains non-JSON value of type {type(value).__name__}"
    )


def _deep_thaw(
    value: Any,
    *,
    _active: set[int] | None = None,
    _budget: _TraversalBudget | None = None,
    _depth: int = 0,
) -> Any:
    if isinstance(value, _RegisteredNormalizedOutput):
        return value.to_json_value()
    active = set() if _active is None else _active
    budget = _TraversalBudget() if _budget is None else _budget
    budget.enter(_depth)
    if isinstance(value, Mapping):
        items = _bounded_mapping_items(value, budget=budget)
        identity = id(value)
        if identity in active:
            raise _CyclicContainerError("cyclic container")
        active.add(identity)
        try:
            return {
                str(key): _deep_thaw(
                    item,
                    _active=active,
                    _budget=budget,
                    _depth=_depth + 1,
                )
                for key, item in sorted(items, key=lambda pair: str(pair[0]))
            }
        finally:
            active.remove(identity)
    if isinstance(value, tuple):
        identity = id(value)
        if identity in active:
            raise _CyclicContainerError("cyclic container")
        active.add(identity)
        try:
            return [
                _deep_thaw(
                    item,
                    _active=active,
                    _budget=budget,
                    _depth=_depth + 1,
                )
                for item in value
            ]
        finally:
            active.remove(identity)
    return value


def _runtime_seal_value(
    value: Any,
    *,
    active: set[int],
    budget: _TraversalBudget,
    depth: int,
) -> Any:
    if isinstance(value, _RegisteredNormalizedOutput):
        return value.runtime_seal_material()
    budget.enter(depth)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AcquisitionError("runtime seal material contains a non-finite float")
        return {"float": repr(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise AcquisitionError("runtime seal material contains a naive datetime")
        return {"datetime": value.astimezone(UTC).isoformat(timespec="microseconds")}
    if isinstance(value, Mapping):
        items = _bounded_mapping_items(value, budget=budget)
        identity = id(value)
        if identity in active:
            raise AcquisitionError("runtime seal material is cyclic")
        if any(not isinstance(key, str) for key, _item in items):
            raise AcquisitionError("runtime seal mapping keys must be text")
        active.add(identity)
        try:
            return {
                key: _runtime_seal_value(
                    item, active=active, budget=budget, depth=depth + 1
                )
                for key, item in sorted(items)
            }
        finally:
            active.remove(identity)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        identity = id(value)
        if identity in active:
            raise AcquisitionError("runtime seal material is cyclic")
        active.add(identity)
        try:
            return [
                _runtime_seal_value(
                    item, active=active, budget=budget, depth=depth + 1
                )
                for item in value
            ]
        finally:
            active.remove(identity)
    if is_dataclass(value) and not isinstance(value, type):
        identity = id(value)
        if identity in active:
            raise AcquisitionError("runtime seal material is cyclic")
        active.add(identity)
        try:
            return {
                "type": f"{type(value).__module__}.{type(value).__qualname__}",
                "fields": {
                    item.name: _runtime_seal_value(
                        getattr(value, item.name),
                        active=active,
                        budget=budget,
                        depth=depth + 1,
                    )
                    for item in fields(value)
                    if not item.name.startswith("_")
                },
            }
        finally:
            active.remove(identity)
    raise AcquisitionError("runtime seal material contains an unsupported value")


def _runtime_object_seal(value: Any) -> str:
    material = _runtime_seal_value(
        value,
        active=set(),
        budget=_TraversalBudget(),
        depth=0,
    )
    if not isinstance(material, Mapping):
        raise AcquisitionError("runtime seal material must be an object")
    return _runtime_seal(material)


def _runtime_verifier_private_seal(verifier: Callable[[], None] | None) -> str:
    if verifier is None:
        return _runtime_seal({"bound": False})
    try:
        implementation_identity, implementation_sha256 = callable_implementation_identity(
            verifier
        )
    except (ImplementationIdentityError, RecursionError) as exc:
        raise AcquisitionError("manifest runtime verifier identity is unsupported") from exc
    return _runtime_seal(
        {
            "bound": True,
            "instance_id": id(verifier),
            "implementation_identity": implementation_identity,
            "implementation_sha256": implementation_sha256,
        }
    )


def _contains_secret_material(
    value: object,
    *,
    secrets: tuple[str, ...],
    active: set[int] | None = None,
) -> bool:
    seen = set() if active is None else active
    budget = _TraversalBudget()
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if isinstance(current, _RegisteredNormalizedOutput):
            if current.contains_secret_material(secrets=secrets):
                return True
            continue
        budget.enter(depth)
        if isinstance(current, str):
            if _evidence_value_contains_encoded_secret(current, secrets=secrets):
                return True
            continue
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            items = _bounded_mapping_items(current, budget=budget)
            for key, item in items:
                if (
                    isinstance(key, str)
                    and key.strip().lower() in CREDENTIAL_PARAM_NAMES
                ):
                    return True
                budget.enter(depth + 1)
                stack.append((key, depth + 1))
                stack.append((item, depth + 1))
            continue
        if isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            remaining = _TRAVERSAL_MAX_ITEMS - budget.items
            if len(current) > remaining:
                raise _TraversalLimitError("container item limit exceeded")
            for item in current:
                budget.enter(depth + 1)
                stack.append((item, depth + 1))
    return False


def _manifest_attached_copy(value: object) -> object:
    try:
        return _deep_thaw(_deep_freeze(value))
    except (
        _TraversalLimitError,
        _CyclicContainerError,
        RecursionError,
        TypeError,
        ValueError,
    ) as exc:
        raise AcquisitionError("manifest attached material is invalid") from exc


def _reject_attached_credential_material(
    value: object,
    *,
    secrets: tuple[str, ...] = (),
) -> None:
    try:
        if _contains_secret_material(value, secrets=secrets):
            raise AcquisitionError("manifest attached material contains credential material")
    except AcquisitionError:
        raise
    except (_TraversalLimitError, RecursionError, TypeError, ValueError) as exc:
        raise AcquisitionError("manifest attached material is invalid") from exc


def _write_bound_publication_verifier(
    artifacts: tuple[tuple[str, str, int], ...],
    verifier: Callable[[], None] | None,
) -> Callable[[], None]:
    """Close raw/meta identity plus a caller verifier for publication-time use."""

    def verify() -> None:
        if verifier is not None:
            verifier()
        for path_text, expected_sha256, expected_length in artifacts:
            try:
                with open(path_text, "rb") as handle:  # noqa: PTH123
                    descriptor = os.fstat(handle.fileno())
                    if not stat.S_ISREG(descriptor.st_mode):
                        raise AcquisitionError(
                            "manifest raw evidence changed after build"
                        )
                    digest = hashlib.sha256()
                    observed_length = 0
                    while True:
                        chunk = handle.read(1024 * 1024)
                        if not chunk:
                            break
                        observed_length += len(chunk)
                        if observed_length > expected_length:
                            raise AcquisitionError(
                                "manifest raw evidence changed after build"
                            )
                        digest.update(chunk)
            except AcquisitionError:
                raise
            except (OSError, ValueError) as exc:
                raise AcquisitionError("manifest raw evidence changed after build") from exc
            if (
                observed_length != expected_length
                or digest.hexdigest() != expected_sha256
            ):
                raise AcquisitionError("manifest raw evidence changed after build")

    return verify


def _open_windows_directory_handle(path: Path) -> int:
    """Open one real directory without delete sharing or reparse traversal."""
    import ctypes
    from ctypes import wintypes

    generic_read = 0x80000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    file_attribute_tag_info = 9

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        generic_read,
        file_share_read | file_share_write,
        None,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    numeric_handle = int(handle)
    try:
        get_information = kernel32.GetFileInformationByHandleEx
        get_information.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        get_information.restype = wintypes.BOOL
        information = FileAttributeTagInfo()
        if not get_information(
            handle,
            file_attribute_tag_info,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not information.FileAttributes & file_attribute_directory:
            raise OSError("publication component is not a directory")
        if information.FileAttributes & file_attribute_reparse_point:
            raise OSError("publication component is a reparse point")

        get_final_path = kernel32.GetFinalPathNameByHandleW
        get_final_path.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        get_final_path.restype = wintypes.DWORD
        required = get_final_path(handle, None, 0, 0)
        if required == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        final_buffer = ctypes.create_unicode_buffer(required + 1)
        if get_final_path(handle, final_buffer, len(final_buffer), 0) == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        observed_path = final_buffer.value
        if observed_path.startswith("\\\\?\\UNC\\"):
            observed_path = "\\\\" + observed_path[8:]
        elif observed_path.startswith("\\\\?\\"):
            observed_path = observed_path[4:]
        if os.path.normcase(os.path.abspath(observed_path)) != os.path.normcase(
            os.path.abspath(path)
        ):
            raise OSError("publication directory handle resolved to another path")
        return numeric_handle
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _write_manifest_new_posix(
    *,
    root: Path,
    run_kind: str,
    run_id: str,
    document: Mapping[str, Any],
) -> str:
    """Publish through one held no-follow descriptor chain on POSIX."""

    supports_dir_fd: Collection[Callable[..., Any]] = getattr(
        os, "supports_dir_fd", frozenset()
    )
    supports_follow_symlinks: Collection[Callable[..., Any]] = getattr(
        os, "supports_follow_symlinks", frozenset()
    )
    o_directory = getattr(os, "O_DIRECTORY", None)
    o_nofollow = getattr(os, "O_NOFOLLOW", None)
    required_dir_fd = (os.open, os.mkdir, os.unlink, os.link)
    if (
        os.name == "nt"
        or not isinstance(o_directory, int)
        or not isinstance(o_nofollow, int)
        or any(function not in supports_dir_fd for function in required_dir_fd)
        or os.link not in supports_follow_symlinks
    ):
        raise AcquisitionError("MANIFEST_STORAGE_UNSUPPORTED")

    directory_flags = (
        os.O_RDONLY
        | o_directory
        | o_nofollow
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | o_nofollow
        | getattr(os, "O_CLOEXEC", 0)
    )
    payload = canonical_json_bytes(document)
    directory_descriptors: list[int] = []
    held_entries: list[tuple[int, str, int]] = []
    temporary_descriptor: int | None = None
    temporary_name: str | None = None
    final_published = False
    final_removed = False

    def checked_directory(descriptor: int) -> None:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("manifest publication component is not a directory")

    def open_directory(name: str, *, parent_fd: int | None = None) -> int:
        if parent_fd is None:
            descriptor = os.open(name, directory_flags)
        else:
            descriptor = os.open(name, directory_flags, dir_fd=parent_fd)
        try:
            checked_directory(descriptor)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def create_and_open_directory(parent_fd: int, name: str) -> int:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        else:
            os.fsync(parent_fd)
        return open_directory(name, parent_fd=parent_fd)

    def reverify_held_entries() -> None:
        for parent_fd, name, expected_fd in held_entries:
            observed_fd = open_directory(name, parent_fd=parent_fd)
            try:
                expected = os.fstat(expected_fd)
                observed = os.fstat(observed_fd)
                if (expected.st_dev, expected.st_ino) != (observed.st_dev, observed.st_ino):
                    raise OSError("manifest publication directory entry changed")
            finally:
                os.close(observed_fd)

    def remove_final_after_failure(run_fd: int) -> None:
        """Retract the final link, or say so when the retraction is unprovable.

        Swallowing this failure would report a clean failure while
        ``manifest.json`` is still published -- the caller would be told the run
        produced nothing when a reader can already see a manifest. That is the
        one cleanup whose outcome must be named, so it raises its own state that
        no other publication failure uses.
        """
        nonlocal final_removed
        if not final_published or final_removed:
            return
        try:
            os.unlink("manifest.json", dir_fd=run_fd)
            os.fsync(run_fd)
        except FileNotFoundError:
            final_removed = True
        except OSError as exc:
            raise AcquisitionError("MANIFEST_PUBLICATION_INDETERMINATE") from exc
        else:
            final_removed = True

    try:
        root_path = Path(root)
        if not root_path.is_absolute() or root_path.anchor != os.sep:
            raise AcquisitionError("MANIFEST_STORAGE_UNSUPPORTED")
        parts = root_path.parts
        current_fd = open_directory(root_path.anchor)
        directory_descriptors.append(current_fd)
        for component in parts[1:]:
            if component in {"", ".", ".."} or os.sep in component:
                raise AcquisitionError("MANIFEST_STORAGE_UNSUPPORTED")
            child_fd = open_directory(component, parent_fd=current_fd)
            directory_descriptors.append(child_fd)
            current_fd = child_fd

        for component in ("runs", run_kind, run_id):
            child_fd = create_and_open_directory(current_fd, component)
            directory_descriptors.append(child_fd)
            held_entries.append((current_fd, component, child_fd))
            current_fd = child_fd
        run_fd = current_fd

        for _attempt in range(16):
            candidate = f".manifest.json.{secrets.token_hex(16)}.tmp"
            try:
                temporary_descriptor = os.open(
                    candidate,
                    file_flags,
                    0o600,
                    dir_fd=run_fd,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_descriptor is None or temporary_name is None:
            raise OSError("unable to allocate exclusive manifest temporary file")
        if not stat.S_ISREG(os.fstat(temporary_descriptor).st_mode):
            raise OSError("manifest temporary artifact is not a regular file")

        offset = 0
        while offset < len(payload):
            written = os.write(temporary_descriptor, payload[offset:])
            if written <= 0:
                raise OSError("manifest temporary write made no progress")
            offset += written
        os.fsync(temporary_descriptor)
        reverify_held_entries()
        os.link(
            temporary_name,
            "manifest.json",
            src_dir_fd=run_fd,
            dst_dir_fd=run_fd,
            follow_symlinks=False,
        )
        final_published = True
        try:
            os.fsync(run_fd)
            reverify_held_entries()
        except BaseException:
            remove_final_after_failure(run_fd)
            raise
        return hashlib.sha256(payload).hexdigest()
    finally:
        cleanup_error: OSError | None = None
        try:
            if temporary_descriptor is not None:
                os.close(temporary_descriptor)
            if temporary_name is not None and directory_descriptors:
                run_fd = directory_descriptors[-1]
                try:
                    os.unlink(temporary_name, dir_fd=run_fd)
                    os.fsync(run_fd)
                except FileNotFoundError:
                    pass
        except OSError as exc:
            cleanup_error = exc
            if final_published and directory_descriptors:
                remove_final_after_failure(directory_descriptors[-1])
        finally:
            for descriptor in reversed(directory_descriptors):
                try:
                    os.close(descriptor)
                except OSError as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
        if cleanup_error is not None:
            raise cleanup_error


@contextmanager
def _hold_windows_directories(
    components: Sequence[Path],
    *,
    unsupported_code: str,
) -> Iterator[None]:
    """Create, validate, and hold path components across one publication."""
    if os.name != "nt":
        yield
        return
    handles: list[int] = []
    try:
        for component in components:
            component.mkdir(exist_ok=True)
            handles.append(_open_windows_directory_handle(component))
    except OSError as exc:
        for handle in reversed(handles):
            _close_windows_handle(handle)
        raise AcquisitionError(unsupported_code) from exc
    try:
        yield
    finally:
        for handle in reversed(handles):
            _close_windows_handle(handle)


@dataclass(frozen=True)
class Parser:
    """A source-digested, versioned reader of raw bytes."""

    name: str
    version: str
    parse: Callable[[bytes], Mapping[str, Any]]
    parse_with_context: Callable[[bytes, str, str], Mapping[str, Any]] | None = None
    output_kind: str = PARSER_OUTPUT_PARSED_DATA
    implementation_sha256: str = field(init=False)
    _execution: Callable[..., object] = field(init=False, repr=False, compare=False)
    _execution_seal: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.strip():
            raise AcquisitionError("parser name must be non-empty canonical text")
        if not self.version or self.version != self.version.strip():
            raise AcquisitionError("parser version must be non-empty canonical text")
        if self.output_kind not in {PARSER_OUTPUT_PARSED_DATA, PARSER_OUTPUT_NORMALIZED_DATA}:
            raise AcquisitionError(f"unsupported parser output kind {self.output_kind!r}")
        implementation = self.parse_with_context or self.parse
        implementation_sha256 = _callable_implementation_sha256(implementation)
        execution = implementation
        object.__setattr__(
            self,
            "implementation_sha256",
            implementation_sha256,
        )
        object.__setattr__(self, "_execution", execution)
        object.__setattr__(self, "_execution_seal", self._seal_execution(execution))

    def _seal_execution(self, execution: Callable[..., object]) -> str:
        material = canonical_json_bytes(
            {
                "execution_object": id(execution),
                "implementation_sha256": self.implementation_sha256,
                "name": self.name,
                "version": self.version,
            }
        )
        return hmac.new(_RUNTIME_SEAL_KEY, material, hashlib.sha256).hexdigest()

    def validate_identity(self) -> None:
        implementation = self.parse_with_context or self.parse
        if _callable_implementation_sha256(implementation) != self.implementation_sha256:
            raise AcquisitionError("parser identity changed after construction")

    def execute(self, body: bytes, endpoint: str, expected: str) -> object:
        execution = self._execution
        if not hmac.compare_digest(self._execution_seal, self._seal_execution(execution)):
            raise AcquisitionError("parser closed execution changed after construction")
        self.validate_identity()
        if self.parse_with_context is not None:
            result = execution(body, endpoint, expected)
        else:
            result = execution(body)
        self.validate_identity()
        return result

    def to_json_dict(self) -> dict[str, str]:
        self.validate_identity()
        return {
            "parser": self.name,
            "parser_version": self.version,
            "parser_implementation_sha256": self.implementation_sha256,
            "parser_output_kind": self.output_kind,
        }


def _shape_parser(name: str, validator: Callable[[bytes], ValidationSummary]) -> Parser:
    def _parse(body: bytes) -> Mapping[str, Any]:
        return validator(body).to_json_dict()

    return Parser(name=name, version=VALIDATOR_PARSER_VERSION, parse=_parse)


DEFAULT_PARSERS: Mapping[str, Parser] = {
    "TIME_SERIES_DAILY": _shape_parser("TIME_SERIES_DAILY", validate_time_series_daily),
    "DIVIDENDS": _shape_parser("DIVIDENDS", validate_dividends),
    "SPLITS": _shape_parser("SPLITS", validate_splits),
    "LISTING_STATUS": _shape_parser("LISTING_STATUS", validate_listing_status),
}

#: Shape validators that also assert an expected identity (symbol / listing state).
_EXPECTING_VALIDATORS: Mapping[str, tuple[Callable[..., ValidationSummary], str]] = {
    "TIME_SERIES_DAILY": (validate_time_series_daily, "expect_symbol"),
    "DIVIDENDS": (validate_dividends, "expect_symbol"),
    "SPLITS": (validate_splits, "expect_symbol"),
    "LISTING_STATUS": (validate_listing_status, "expect_state"),
}


def expecting_parser(endpoint: str, expected: str) -> Parser:
    """A default parser that also checks the payload is about ``expected``.

    Used instead of per-call parser kwargs so the parser stays a single-argument
    callable and its version travels with it into the run manifest.
    """
    canonical = canonical_endpoint(endpoint)
    if canonical not in _EXPECTING_VALIDATORS:
        raise AcquisitionError(f"no shape validator is declared for {canonical}")
    validator, keyword = _EXPECTING_VALIDATORS[canonical]

    def _parse(body: bytes) -> Mapping[str, Any]:
        return validator(body, **{keyword: expected}).to_json_dict()

    return Parser(name=f"{canonical}[{keyword}={expected}]", version=VALIDATOR_PARSER_VERSION,
                  parse=_parse)


def parse_hash(
    parser: Parser,
    result: Mapping[str, Any],
    *,
    output_kind: str | None = None,
) -> str:
    """A stable digest of parser implementation, declaration, and output."""
    parser.validate_identity()
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "parser": parser.name,
                "parser_version": parser.version,
                "parser_implementation_sha256": parser.implementation_sha256,
                "parser_output_kind": output_kind or parser.output_kind,
                "result": _deep_thaw(result),
            }
        )
    ).hexdigest()


def _parse_hash_from_result(result: AcquisitionResult) -> str:
    assert result.parser_name is not None
    assert result.parser_version is not None
    assert result.parser_implementation_sha256 is not None
    assert result.parser_output_kind is not None
    assert result.parse_result is not None
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "parser": result.parser_name,
                "parser_version": result.parser_version,
                "parser_implementation_sha256": result.parser_implementation_sha256,
                "parser_output_kind": result.parser_output_kind,
                "result": _deep_thaw(result.parse_result),
            }
        )
    ).hexdigest()


# ---------------------------------------------------------------------------
# Request / result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcquisitionRequest:
    """Everything the boundary needs, and nothing it must not have."""

    endpoint: str
    parameters: Mapping[str, str] = field(default_factory=dict)
    purpose: str = "unspecified"
    requested_at: datetime | None = None
    symbol: str | None = None
    _construction_seal: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        endpoint = canonical_endpoint(self.endpoint)
        if not self.purpose or not self.purpose.strip():
            raise AcquisitionError("acquisition purpose must be stated")
        if self.requested_at is not None and self.requested_at.tzinfo is None:
            raise AcquisitionError("requested_at must be timezone-aware")
        normalized_keys = {str(key).strip().lower() for key in self.parameters}
        if normalized_keys & CREDENTIAL_PARAM_NAMES:
            raise AcquisitionError("parameters must never carry a credential")
        if "function" in normalized_keys:
            raise AcquisitionError(
                "function parameter is forbidden; endpoint is the sole function coordinate"
            )
        frozen_parameters = MappingProxyType(dict(canonical_parameters(self.parameters)))
        parameter_symbol = frozen_parameters.get("symbol")
        if endpoint == "LISTING_STATUS" and (parameter_symbol is not None or self.symbol is not None):
            raise AcquisitionError("LISTING_STATUS has no security symbol coordinate")
        if self.symbol is not None and parameter_symbol != self.symbol:
            raise AcquisitionError(
                f"symbol coordinate mismatch: parameters['symbol']={parameter_symbol!r} "
                f"but symbol={self.symbol!r}"
            )
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "parameters", frozen_parameters)
        object.__setattr__(self, "_construction_seal", _runtime_seal(self._seal_material()))

    def _seal_material(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "parameters": dict(sorted(self.parameters.items())),
            "purpose": self.purpose,
            "requested_at": (
                None
                if self.requested_at is None
                else self.requested_at.astimezone(UTC).isoformat(timespec="microseconds")
            ),
            "symbol": self.symbol,
        }

    def validate_integrity(self) -> None:
        if not hmac.compare_digest(
            _runtime_seal(self._seal_material()),
            self._construction_seal,
        ):
            raise AcquisitionError("request construction coordinates changed")

    @property
    def canonical_endpoint(self) -> str:
        return canonical_endpoint(self.endpoint)

    @property
    def canonical_parameters(self) -> tuple[tuple[str, str], ...]:
        return canonical_parameters(self.parameters)

    @property
    def resolved_symbol(self) -> str | None:
        return dict(canonical_parameters(self.parameters)).get("symbol")


_REGISTERED_NORMALIZER_LOCK = threading.Lock()
_REGISTERED_NORMALIZERS: Mapping[
    tuple[str, tuple[tuple[str, str], ...]], Parser
] = MappingProxyType({})
_REGISTERED_NORMALIZERS_SEAL: str | None = None


def _normalizer_coordinate_key(
    endpoint: str, parameters: tuple[tuple[str, str], ...]
) -> tuple[str, tuple[tuple[str, str], ...]]:
    return endpoint, parameters


def _registered_normalizer_material(
    registry: Mapping[tuple[str, tuple[tuple[str, str], ...]], Parser],
) -> list[dict[str, Any]]:
    material: list[dict[str, Any]] = []
    for (endpoint, parameters), parser in sorted(registry.items()):
        material.append(
            {
                "endpoint": endpoint,
                "parameters": [list(pair) for pair in parameters],
                "parser_name": parser.name,
                "parser_version": parser.version,
                "parser_output_kind": parser.output_kind,
                "parser_implementation_sha256": parser.implementation_sha256,
                "parser_instance_id": id(parser),
            }
        )
    return material


def _install_registered_normalizing_parsers(
    entries: Iterable[tuple[AcquisitionRequest, Parser]],
) -> None:
    """Install the closed import-time normalizer registry exactly once."""
    global _REGISTERED_NORMALIZERS, _REGISTERED_NORMALIZERS_SEAL
    registry: dict[tuple[str, tuple[tuple[str, str], ...]], Parser] = {}
    for request, parser in entries:
        request.validate_integrity()
        parser.validate_identity()
        if parser.output_kind != PARSER_OUTPUT_NORMALIZED_DATA:
            raise AcquisitionError("registered normalizer must declare normalized output")
        key = _normalizer_coordinate_key(
            request.canonical_endpoint, request.canonical_parameters
        )
        if key in registry:
            raise AcquisitionError("duplicate registered normalizer coordinate")
        registry[key] = parser
    if not registry:
        raise AcquisitionError("registered normalizer registry must not be empty")
    with _REGISTERED_NORMALIZER_LOCK:
        if _REGISTERED_NORMALIZERS_SEAL is not None:
            raise AcquisitionError("registered normalizer registry is already installed")
        frozen = MappingProxyType(dict(registry))
        _REGISTERED_NORMALIZERS = frozen
        _REGISTERED_NORMALIZERS_SEAL = _runtime_seal(
            _registered_normalizer_material(frozen)
        )


def _validate_registered_normalizer_registry() -> None:
    seal = _REGISTERED_NORMALIZERS_SEAL
    if seal is None:
        raise AcquisitionError("registered normalizer registry is unavailable")
    if not hmac.compare_digest(
        seal,
        _runtime_seal(_registered_normalizer_material(_REGISTERED_NORMALIZERS)),
    ):
        raise AcquisitionError("registered normalizer registry identity changed")


def _registered_normalizer_for_request(request: AcquisitionRequest) -> Parser:
    _validate_registered_normalizer_registry()
    key = _normalizer_coordinate_key(
        request.canonical_endpoint, request.canonical_parameters
    )
    try:
        parser = _REGISTERED_NORMALIZERS[key]
    except KeyError as exc:
        raise AcquisitionError("request has no registered normalizer authority") from exc
    parser.validate_identity()
    return parser


def _matching_registered_normalizer(
    request: AcquisitionRequest, parser: Parser | None
) -> Parser | None:
    if parser is None:
        return None
    try:
        registered = _registered_normalizer_for_request(request)
    except AcquisitionError:
        return None
    return registered if parser is registered else None


def _freeze_registered_normalized_output(
    request: AcquisitionRequest,
    parser: Parser,
    parsed: object,
    *,
    analysis_as_of: str,
    available_at: str,
) -> _RegisteredNormalizedOutput:
    """Validate/freeze only output produced by the exact closed-registry parser."""
    if _matching_registered_normalizer(request, parser) is not parser:
        raise AcquisitionError("registered normalized output lacks parser authority")
    endpoint = request.canonical_endpoint
    try:
        schema = _REGISTERED_NORMALIZED_SCHEMAS[endpoint]
    except KeyError as exc:  # pragma: no cover - the registry has only these endpoints
        raise AcquisitionError("registered normalized output lacks endpoint authority") from exc
    if type(parsed) is not dict:
        raise TypeError("registered normalized output is not an object")
    if any(type(key) is not str for key in parsed):
        raise TypeError("registered normalized output keys must be text")
    document = cast(dict[str, object], parsed)
    expected_root_fields = {
        "endpoint",
        "schema_version",
        "canonical_key_field",
        "provider_symbol",
        "source_metadata",
        "row_count",
        "rows",
        "notes",
        "analysis_as_of",
        "available_at",
        "cutoff_status",
    }
    if set(document) != expected_root_fields:
        raise TypeError("registered normalized output root fields are not canonical")
    if (
        document["endpoint"] != endpoint
        or document["schema_version"] != NORMALIZER_VERSION
        or document["canonical_key_field"] != schema.canonical_key_field
        or document["analysis_as_of"] != analysis_as_of
        or document["available_at"] != available_at
        or document["cutoff_status"] != schema.cutoff_status
    ):
        raise TypeError("registered normalized output authority fields do not match")
    expected_symbol = None if endpoint == "LISTING_STATUS" else request.resolved_symbol
    if document["provider_symbol"] != expected_symbol:
        raise TypeError("registered normalized output provider symbol does not match")

    row_count = document["row_count"]
    rows_value = document["rows"]
    notes_value = document["notes"]
    if type(row_count) is not int or row_count < 0 or row_count > schema.max_rows:
        raise _TraversalLimitError("registered row limit exceeded")
    if type(rows_value) is not list or len(rows_value) != row_count:
        raise TypeError("registered normalized row count does not match rows")
    if type(notes_value) is not list or len(notes_value) > schema.max_notes:
        raise TypeError("registered normalized notes are not canonical")
    if any(type(note) is not str for note in notes_value):
        raise TypeError("registered normalized notes must be text")

    auxiliary_budget = _RegisteredAuxiliaryBudget()
    frozen_metadata = _freeze_registered_auxiliary_members(
        document["source_metadata"],
        budget=auxiliary_budget,
        child_container_depth=2,
    )
    if endpoint == "LISTING_STATUS" and frozen_metadata:
        raise TypeError("registered listing output has unexpected source metadata")

    frozen_rows: list[_ImmutableJSONDict] = []
    for raw_row in rows_value:
        if type(raw_row) is not dict or any(type(key) is not str for key in raw_row):
            raise TypeError("registered normalized row is not a canonical object")
        row = cast(dict[str, object], raw_row)
        row_fields = set(row)
        expected_row_fields = set(schema.row_fields)
        if schema.allows_extra and "extra" in row_fields:
            expected_row_fields.add("extra")
        if row_fields != expected_row_fields:
            raise TypeError("registered normalized row fields are not canonical")
        frozen_row: dict[str, object] = {}
        for key in sorted(schema.row_fields):
            value = row[key]
            if value is not None and type(value) is not str:
                raise TypeError("registered normalized row value is not canonical")
            frozen_row[key] = value
        if "extra" in row:
            extra = _freeze_registered_auxiliary_members(
                row["extra"],
                budget=auxiliary_budget,
                child_container_depth=4,
            )
            if not extra:
                raise TypeError("registered normalized row has an empty extra object")
            frozen_row["extra"] = extra
        frozen_rows.append(_ImmutableJSONDict(frozen_row))

    frozen_document: dict[str, Any] = {
        "endpoint": endpoint,
        "schema_version": NORMALIZER_VERSION,
        "canonical_key_field": schema.canonical_key_field,
        "provider_symbol": expected_symbol,
        "source_metadata": frozen_metadata,
        "row_count": row_count,
        "rows": tuple(frozen_rows),
        "notes": tuple(cast(list[str], notes_value)),
        "analysis_as_of": analysis_as_of,
        "available_at": available_at,
        "cutoff_status": schema.cutoff_status,
    }
    return _RegisteredNormalizedOutput(
        frozen_document,
        endpoint=endpoint,
        row_count=row_count,
        token=_REGISTERED_NORMALIZED_OUTPUT_TOKEN,
    )


@dataclass(frozen=True)
class AttemptPlanAuthority:
    """Content-addressed provider-plan authority used for one transport attempt."""

    attempt: int
    plan_id: str
    plan_evidence_sha256: str

    def to_json_dict(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "plan_id": self.plan_id,
            "plan_evidence_sha256": self.plan_evidence_sha256,
        }


@dataclass(frozen=True)
class AcquisitionResult:
    """The full, credential-free record of one acquisition."""

    endpoint: str
    purpose: str
    request_key: str
    parameters_sha256: str
    provider_id: str
    provider_version: str
    canonical_parameters: tuple[tuple[str, str], ...]
    parameters_redacted: Mapping[str, str]
    public_url: str
    observed_final_url: str | None
    plan_id: str
    requested_at: str
    caller_requested_at_label: str | None
    acquired_at: str
    analysis_as_of: str | None
    available_at: str | None
    cutoff_status: str
    http_status: int | None
    content_type: str
    http_headers: Mapping[str, str]
    provider_metadata: Mapping[str, str]
    response_sha256: str | None
    byte_length: int
    attempts: int
    retry_log: tuple[RetryEvent, ...]
    attempt_plan_authority: tuple[AttemptPlanAuthority, ...]
    source_plan_authority: tuple[AttemptPlanAuthority, ...]
    payload_state: PayloadState
    parser_name: str | None
    parser_version: str | None
    parser_implementation_sha256: str | None
    parser_output_kind: str | None
    parser_status: str
    parser_detail: str | None
    parse_hash: str | None
    parse_result: Mapping[str, Any] | None
    raw_local_uri: str | None
    meta_local_uri: str | None
    pull_id: str | None
    served_from_cache: bool
    quota_grant: QuotaGrant | None
    _construction_seal: str = field(init=False, repr=False, compare=False)
    _normalized_execution_seal: str | None = field(
        init=False, default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "canonical_parameters",
            tuple(tuple(pair) for pair in self.canonical_parameters),
        )
        object.__setattr__(self, "parameters_redacted", _deep_freeze(self.parameters_redacted))
        object.__setattr__(self, "http_headers", _deep_freeze(self.http_headers))
        object.__setattr__(self, "provider_metadata", _deep_freeze(self.provider_metadata))
        object.__setattr__(self, "retry_log", tuple(self.retry_log))
        object.__setattr__(
            self,
            "attempt_plan_authority",
            tuple(self.attempt_plan_authority),
        )
        object.__setattr__(
            self,
            "source_plan_authority",
            tuple(self.source_plan_authority),
        )
        if tuple(item.attempt for item in self.attempt_plan_authority) != tuple(
            range(1, self.attempts + 1)
        ):
            raise AcquisitionError("result attempt-plan authority is incomplete")
        if (
            self.attempt_plan_authority
            and self.attempt_plan_authority[-1].plan_id != self.plan_id
        ):
            raise AcquisitionError("result final plan disagrees with attempt authority")
        if not self.source_plan_authority:
            raise AcquisitionError("result source-plan authority is incomplete")
        if self.source_plan_authority[-1].plan_id != self.plan_id:
            raise AcquisitionError("result final plan disagrees with source authority")
        if self.attempts and self.source_plan_authority != self.attempt_plan_authority:
            raise AcquisitionError("live source authority disagrees with execution attempts")
        if self.parse_result is not None:
            object.__setattr__(self, "parse_result", _deep_freeze(self.parse_result))
        object.__setattr__(self, "_construction_seal", _runtime_object_seal(self))

    def validate_integrity(self) -> None:
        if not hmac.compare_digest(
            _runtime_object_seal(self),
            self._construction_seal,
        ):
            raise AcquisitionError("result object no longer matches its construction seal")

    @property
    def raw_payload_is_data(self) -> bool:
        return self.payload_state.is_data

    @property
    def effective_state(self) -> str:
        if not self.payload_state.is_data:
            return self.payload_state.state
        if self.parser_status != PARSER_STATUS_PARSED:
            return f"REJECTED_DATA_{self.parser_status}"
        if self.parser_output_kind == PARSER_OUTPUT_NORMALIZED_DATA:
            return EFFECTIVE_ACCEPTED_NORMALIZED_DATA
        return EFFECTIVE_ACCEPTED_PARSED_DATA

    @property
    def accepted_normalized_data(self) -> bool:
        return self.effective_state == EFFECTIVE_ACCEPTED_NORMALIZED_DATA

    @property
    def is_data(self) -> bool:
        return self.effective_state in {
            EFFECTIVE_ACCEPTED_PARSED_DATA,
            EFFECTIVE_ACCEPTED_NORMALIZED_DATA,
        }

    @property
    def stored(self) -> bool:
        return self.raw_local_uri is not None

    def validate_parse_integrity(self) -> None:
        if self.parse_result is None:
            if self.parse_hash is not None:
                raise AcquisitionError("parse hash exists without a parse result")
            self.validate_integrity()
            return
        try:
            observed_parse_hash = _parse_hash_from_result(self)
        except (RecursionError, TypeError, ValueError) as exc:
            raise AcquisitionError("result parse material is invalid") from exc
        if self.parse_hash is None or observed_parse_hash != self.parse_hash:
            raise AcquisitionError(
                f"parse hash no longer authenticates result for request {self.request_key[:12]}..."
            )
        self.validate_integrity()
        _validate_normalized_result_authority(self)

    def _json_dict(self, *, preserve_registered_output: bool) -> dict[str, Any]:
        self.validate_parse_integrity()
        return {
            "endpoint": self.endpoint,
            "purpose": self.purpose,
            "request_key": self.request_key,
            "parameters_sha256": self.parameters_sha256,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "canonical_parameters": [list(pair) for pair in self.canonical_parameters],
            "parameters_redacted": dict(sorted(self.parameters_redacted.items())),
            "public_url": self.public_url,
            "observed_final_url": self.observed_final_url,
            "plan_id": self.plan_id,
            "requested_at": self.requested_at,
            "requested_at_authority": "OBSERVED_BOUNDARY_CLOCK",
            "caller_requested_at_label": self.caller_requested_at_label,
            "acquired_at": self.acquired_at,
            "analysis_as_of": self.analysis_as_of,
            "available_at": self.available_at,
            "cutoff_status": self.cutoff_status,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "http_headers": {str(k): str(v) for k, v in sorted(self.http_headers.items())},
            "provider_metadata": dict(sorted(self.provider_metadata.items())),
            "response_sha256": self.response_sha256,
            "byte_length": self.byte_length,
            "attempts": self.attempts,
            "retry_log": [event.to_json_dict() for event in self.retry_log],
            "attempt_plan_authority": [
                item.to_json_dict() for item in self.attempt_plan_authority
            ],
            "source_plan_authority": [
                item.to_json_dict() for item in self.source_plan_authority
            ],
            "payload_state": self.payload_state.to_json_dict(),
            "raw_payload_is_data": self.raw_payload_is_data,
            "effective_state": self.effective_state,
            "accepted_normalized_data": self.accepted_normalized_data,
            "parser": self.parser_name,
            "parser_version": self.parser_version,
            "parser_implementation_sha256": self.parser_implementation_sha256,
            "parser_output_kind": self.parser_output_kind,
            "parser_status": self.parser_status,
            "parser_detail": self.parser_detail,
            "parse_hash": self.parse_hash,
            "parse_result": (
                None
                if self.parse_result is None
                else (
                    self.parse_result
                    if preserve_registered_output
                    and isinstance(self.parse_result, _RegisteredNormalizedOutput)
                    else _deep_thaw(self.parse_result)
                )
            ),
            "raw_local_uri": self.raw_local_uri,
            "meta_local_uri": self.meta_local_uri,
            "pull_id": self.pull_id,
            "served_from_cache": self.served_from_cache,
            "quota_grant": None if self.quota_grant is None else self.quota_grant.to_json_dict(),
        }

    def _manifest_json_dict(self) -> dict[str, Any]:
        return self._json_dict(preserve_registered_output=True)

    def to_json_dict(self) -> dict[str, Any]:
        return self._json_dict(preserve_registered_output=False)


def _normalized_result_execution_material(
    result: AcquisitionResult, parser: Parser
) -> dict[str, Any]:
    return {
        "result_seal": _runtime_object_seal(result),
        "parser_instance_id": id(parser),
        "parser_implementation_sha256": parser.implementation_sha256,
    }


def _validate_normalized_result_authority(result: AcquisitionResult) -> None:
    if result.parser_output_kind != PARSER_OUTPUT_NORMALIZED_DATA:
        return
    _validate_registered_normalizer_registry()
    key = _normalizer_coordinate_key(result.endpoint, result.canonical_parameters)
    try:
        parser = _REGISTERED_NORMALIZERS[key]
    except KeyError as exc:
        raise AcquisitionError("normalized result has no registered coordinate authority") from exc
    if (
        result.parser_name != parser.name
        or result.parser_version != parser.version
        or result.parser_implementation_sha256 != parser.implementation_sha256
    ):
        raise AcquisitionError("normalized result parser authority changed")
    expected = _runtime_seal(_normalized_result_execution_material(result, parser))
    if result._normalized_execution_seal is None or not hmac.compare_digest(
        result._normalized_execution_seal, expected
    ):
        raise AcquisitionError("normalized result lacks registered execution authority")


def _grant_normalized_result_authority(
    result: AcquisitionResult, request: AcquisitionRequest, parser: Parser
) -> AcquisitionResult:
    registered = _matching_registered_normalizer(request, parser)
    if registered is None or result.parser_output_kind != PARSER_OUTPUT_NORMALIZED_DATA:
        raise AcquisitionError("cannot grant unregistered normalized parser authority")
    if (
        result.parser_name != registered.name
        or result.parser_version != registered.version
        or result.parser_implementation_sha256 != registered.implementation_sha256
    ):
        raise AcquisitionError("normalized result parser identity is inconsistent")
    object.__setattr__(
        result,
        "_normalized_execution_seal",
        _runtime_seal(_normalized_result_execution_material(result, registered)),
    )
    _validate_normalized_result_authority(result)
    return result


# ---------------------------------------------------------------------------
# Run manifest (attachable provenance)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcquisitionRunManifest:
    """Provider-policy source, request logs, raw hashes, and parser versions.

    This is the structure a run attaches so that anything downstream can cite
    *which plan evidence was in force*, *what was requested*, *what bytes came
    back*, and *which parser version read them*.
    """

    run_id: str
    purpose: str
    started_at: str
    finished_at: str
    plan_evidence: tuple[Mapping[str, Any], ...]
    retry_policy: Mapping[str, Any]
    parser_versions: Mapping[str, str]
    results: tuple[AcquisitionResult, ...]
    provider_id: str = _REGISTERED_PROVIDER_AUTHORITY[0]
    provider_version: str = _REGISTERED_PROVIDER_AUTHORITY[1]
    claims: Mapping[str, bool] = field(default_factory=_default_manifest_claims)
    parser_implementations: Mapping[str, str] = field(default_factory=dict)
    request_set: tuple[Mapping[str, Any], ...] = ()
    request_set_sha256: str | None = None
    run_configuration: Mapping[str, Any] = field(default_factory=dict)
    configuration_sha256: str | None = None
    code_source_lineage: Mapping[str, str] = field(default_factory=dict)
    run_evidence: Mapping[str, Any] = field(default_factory=dict)
    run_evidence_sha256: str | None = None
    content_addressed_run_domain: str | None = None
    content_addressed_run_mode: str = "EXPLICIT"
    content_addressed_payload_sha256: str | None = None
    analysis_as_of_policy: str = "PER_REQUEST_OBSERVED_ACQUISITION_TIME"
    quota_snapshots: tuple[QuotaSnapshot, ...] = ()
    schema_version: str = ACQUISITION_RUN_SCHEMA_VERSION
    _construction_seal: str = field(init=False, repr=False, compare=False)
    _runtime_verifier: Callable[[], None] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _runtime_verifier_seal: str = field(init=False, repr=False, compare=False)
    _runtime_verifier_execution: Callable[[], None] | None = field(
        init=False,
        repr=False,
        compare=False,
    )
    _runtime_verifier_execution_seal: str = field(init=False, repr=False, compare=False)
    _expected_code_source_lineage: tuple[tuple[str, str], ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "plan_evidence",
            tuple(_deep_freeze(item) for item in self.plan_evidence),
        )
        object.__setattr__(self, "retry_policy", _deep_freeze(self.retry_policy))
        object.__setattr__(self, "parser_versions", _deep_freeze(self.parser_versions))
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "claims", _deep_freeze(self.claims))
        object.__setattr__(
            self,
            "parser_implementations",
            _deep_freeze(self.parser_implementations),
        )
        object.__setattr__(
            self,
            "request_set",
            tuple(_deep_freeze(item) for item in self.request_set),
        )
        object.__setattr__(self, "run_configuration", _deep_freeze(self.run_configuration))
        object.__setattr__(self, "code_source_lineage", _deep_freeze(self.code_source_lineage))
        object.__setattr__(
            self,
            "_expected_code_source_lineage",
            tuple(sorted(self.code_source_lineage.items())),
        )
        object.__setattr__(self, "run_evidence", _deep_freeze(self.run_evidence))
        object.__setattr__(self, "quota_snapshots", tuple(self.quota_snapshots))
        verifier_execution = (
            None
            if self._runtime_verifier is None
            else self._runtime_verifier
        )
        object.__setattr__(self, "_runtime_verifier_execution", verifier_execution)
        object.__setattr__(
            self,
            "_runtime_verifier_seal",
            _runtime_verifier_private_seal(self._runtime_verifier),
        )
        object.__setattr__(
            self,
            "_runtime_verifier_execution_seal",
            _runtime_seal(
                {
                    "bound": verifier_execution is not None,
                    "execution_object": (
                        None if verifier_execution is None else id(verifier_execution)
                    ),
                }
            ),
        )
        object.__setattr__(
            self,
            "_construction_seal",
            _runtime_seal(self._construction_material()),
        )
        self.validate_integrity()

    def _construction_material(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "purpose": self.purpose,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "plan_evidence": [_deep_thaw(item) for item in self.plan_evidence],
            "retry_policy": _deep_thaw(self.retry_policy),
            "parser_versions": _deep_thaw(self.parser_versions),
            "results": [result.to_json_dict() for result in self.results],
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "claims": _deep_thaw(self.claims),
            "parser_implementations": _deep_thaw(self.parser_implementations),
            "request_set": [_deep_thaw(item) for item in self.request_set],
            "request_set_sha256": self.request_set_sha256,
            "run_configuration": _deep_thaw(self.run_configuration),
            "configuration_sha256": self.configuration_sha256,
            "code_source_lineage": _deep_thaw(self.code_source_lineage),
            "run_evidence": _deep_thaw(self.run_evidence),
            "run_evidence_sha256": self.run_evidence_sha256,
            "content_addressed_run_domain": self.content_addressed_run_domain,
            "content_addressed_run_mode": self.content_addressed_run_mode,
            "content_addressed_payload_sha256": self.content_addressed_payload_sha256,
            "analysis_as_of_policy": self.analysis_as_of_policy,
            "quota_snapshots": [snapshot.to_json_dict() for snapshot in self.quota_snapshots],
            "schema_version": self.schema_version,
        }

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            key = result.effective_state
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def raw_payload_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            key = result.payload_state.state
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def accepted_normalized_data_count(self) -> int:
        return sum(result.accepted_normalized_data for result in self.results)

    @property
    def parser_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.parser_status] = counts.get(result.parser_status, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def raw_hashes(self) -> dict[str, str]:
        """``request_key -> response sha256`` for every stored acquisition."""
        return {
            result.request_key: result.response_sha256
            for result in self.results
            if result.response_sha256 is not None
        }

    def _attached_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "purpose": self.purpose,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "provider": {
                "provider_id": self.provider_id,
                "provider_version": self.provider_version,
            },
            "plan_evidence": [_deep_thaw(item) for item in self.plan_evidence],
            "retry_policy": _deep_thaw(self.retry_policy),
            "parser_versions": dict(sorted(self.parser_versions.items())),
            "parser_implementations": dict(sorted(self.parser_implementations.items())),
            "request_set": [_deep_thaw(item) for item in self.request_set],
            "request_set_sha256": self.request_set_sha256,
            "run_configuration": _deep_thaw(self.run_configuration),
            "configuration_sha256": self.configuration_sha256,
            "code_source_lineage": dict(sorted(self.code_source_lineage.items())),
            "content_addressed_run_domain": self.content_addressed_run_domain,
            "content_addressed_run_mode": self.content_addressed_run_mode,
            "analysis_as_of_policy": self.analysis_as_of_policy,
            "counts": self.counts,
            "raw_payload_counts": self.raw_payload_counts,
            "accepted_normalized_data_count": self.accepted_normalized_data_count,
            "parser_counts": self.parser_counts,
            "raw_hashes": dict(sorted(self.raw_hashes.items())),
            "requests": [result.to_json_dict() for result in self.results],
            "quota_snapshots": [snapshot.to_json_dict() for snapshot in self.quota_snapshots],
            "claims": _deep_thaw(self.claims),
        }

    def validate_integrity(self) -> None:
        _validate_registered_provider_authority()
        verifier = self._runtime_verifier
        verifier_execution = self._runtime_verifier_execution
        verifier_seal = _runtime_verifier_private_seal(verifier)
        if not hmac.compare_digest(verifier_seal, self._runtime_verifier_seal):
            raise AcquisitionError("manifest runtime verifier no longer matches construction")
        execution_seal = _runtime_seal(
            {
                "bound": verifier_execution is not None,
                "execution_object": (
                    None if verifier_execution is None else id(verifier_execution)
                ),
            }
        )
        if not hmac.compare_digest(
            execution_seal,
            self._runtime_verifier_execution_seal,
        ):
            raise AcquisitionError("manifest runtime verifier execution changed")
        if tuple(sorted(self.code_source_lineage.items())) != self._expected_code_source_lineage:
            raise AcquisitionError("manifest source lineage no longer matches closed evidence")
        for plan in self.plan_evidence:
            if (
                plan.get("provider_id"),
                plan.get("provider_version"),
            ) != _REGISTERED_PROVIDER_AUTHORITY:
                raise AcquisitionError(
                    "manifest plan evidence provider authority is not registered"
                )
        if verifier is not None:
            if verifier_execution is None:
                raise AcquisitionError("manifest runtime verifier execution is unavailable")
            try:
                verifier_execution()
            except AcquisitionError:
                raise
            except BaseException as exc:
                raise AcquisitionError("manifest runtime verifier failed") from exc
            verifier_seal = _runtime_verifier_private_seal(verifier)
            if not hmac.compare_digest(verifier_seal, self._runtime_verifier_seal):
                raise AcquisitionError("manifest runtime verifier changed during invocation")
        if self.schema_version != ACQUISITION_RUN_SCHEMA_VERSION:
            raise AcquisitionError("manifest schema version is not registered")
        if self.analysis_as_of_policy != "PER_REQUEST_OBSERVED_ACQUISITION_TIME":
            raise AcquisitionError("manifest analysis-as-of policy is not registered")
        if (self.provider_id, self.provider_version) != _REGISTERED_PROVIDER_AUTHORITY:
            raise AcquisitionError("manifest provider authority is not registered")
        if self.content_addressed_run_mode not in {"EXPLICIT", "SHA256"}:
            raise AcquisitionError("manifest run identity mode is not registered")
        if self.content_addressed_run_mode == "SHA256" and not self.content_addressed_run_domain:
            raise AcquisitionError("content-addressed manifest has no run domain")
        if self.content_addressed_run_mode == "EXPLICIT" and self.content_addressed_run_domain:
            raise AcquisitionError("explicit manifest unexpectedly declares a run domain")
        for result in self.results:
            result.validate_parse_integrity()
            if (result.provider_id, result.provider_version) != _REGISTERED_PROVIDER_AUTHORITY:
                raise AcquisitionError(
                    "manifest result does not match registered provider authority"
                )
        expected_hashes = {
            "request_set": hashlib.sha256(
                canonical_json_value_bytes([_deep_thaw(item) for item in self.request_set])
            ).hexdigest(),
            "configuration": hashlib.sha256(
                canonical_json_bytes(_deep_thaw(self.run_configuration))
            ).hexdigest(),
            "run evidence": hashlib.sha256(
                canonical_json_bytes(_deep_thaw(self.run_evidence))
            ).hexdigest(),
        }
        stored_hashes = {
            "request_set": self.request_set_sha256,
            "configuration": self.configuration_sha256,
            "run evidence": self.run_evidence_sha256,
        }
        for label, expected in expected_hashes.items():
            if stored_hashes[label] != expected:
                raise AcquisitionError(
                    f"manifest {label} hash no longer authenticates its content"
                )
        attached_payload = self._attached_payload()
        expected_payload_digest = hashlib.sha256(
            canonical_json_bytes(attached_payload)
        ).hexdigest()
        attached_run_evidence = {
            **attached_payload,
            "content_addressed_payload_sha256": expected_payload_digest,
            "run_id": self.run_id,
        }
        stored_run_evidence = _deep_thaw(self.run_evidence)
        if (
            not isinstance(stored_run_evidence, dict)
            or stored_run_evidence.get("run_id") != self.run_id
        ):
            raise AcquisitionError(
                "content-addressed run_id no longer matches attached run evidence"
            )
        if stored_run_evidence != attached_run_evidence:
            raise AcquisitionError("manifest run evidence no longer matches attached content")
        if self.content_addressed_payload_sha256 != expected_payload_digest:
            raise AcquisitionError(
                "manifest content-addressed payload hash no longer authenticates its content"
            )
        if self.content_addressed_run_mode == "SHA256":
            expected_run_id = f"{self.content_addressed_run_domain}-{expected_payload_digest}"
            if self.run_id != expected_run_id:
                raise AcquisitionError(
                    "content-addressed run_id no longer matches the payload hash"
                )
        if not hmac.compare_digest(
            _runtime_seal(self._construction_material()),
            self._construction_seal,
        ):
            raise AcquisitionError("manifest object no longer matches its construction seal")

    def to_json_dict(self) -> dict[str, Any]:
        self.validate_integrity()
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "purpose": self.purpose,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "provider": {
                "provider_id": self.provider_id,
                "provider_version": self.provider_version,
            },
            "plan_evidence": [_deep_thaw(item) for item in self.plan_evidence],
            "retry_policy": _deep_thaw(self.retry_policy),
            "parser_versions": dict(sorted(self.parser_versions.items())),
            "parser_implementations": dict(sorted(self.parser_implementations.items())),
            "request_set": [_deep_thaw(item) for item in self.request_set],
            "request_set_sha256": self.request_set_sha256,
            "run_configuration": _deep_thaw(self.run_configuration),
            "configuration_sha256": self.configuration_sha256,
            "code_source_lineage": dict(sorted(self.code_source_lineage.items())),
            "run_evidence": _deep_thaw(self.run_evidence),
            "run_evidence_sha256": self.run_evidence_sha256,
            "content_addressed_run_domain": self.content_addressed_run_domain,
            "content_addressed_run_mode": self.content_addressed_run_mode,
            "content_addressed_payload_sha256": self.content_addressed_payload_sha256,
            "analysis_as_of_policy": self.analysis_as_of_policy,
            "counts": self.counts,
            "raw_payload_counts": self.raw_payload_counts,
            "accepted_normalized_data_count": self.accepted_normalized_data_count,
            "parser_counts": self.parser_counts,
            "raw_hashes": dict(sorted(self.raw_hashes.items())),
            "requests": [result.to_json_dict() for result in self.results],
            "quota_snapshots": [snapshot.to_json_dict() for snapshot in self.quota_snapshots],
            "claims": _deep_thaw(self.claims),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json_dict())

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def write(self, layout: DataRootLayout, *, run_kind: str = "av-acquisition") -> str:
        """Publish the manifest under ``<data_root>/runs`` and return its logical id.

        Uses the same temp-file + ``fsync`` + atomic-publish discipline as the raw
        store, so a run manifest is never half-written and never replaced.
        """
        self.validate_integrity()
        if (
            not isinstance(run_kind, str)
            or _MANIFEST_COMPONENT_RE.fullmatch(run_kind) is None
            or _MANIFEST_COMPONENT_RE.fullmatch(self.run_id) is None
        ):
            raise AcquisitionError(
                "manifest run_kind and run_id must be filesystem-safe identifiers "
                "of at most 128 ASCII characters"
            )
        directory = layout.runs / run_kind / self.run_id
        path = directory / "manifest.json"
        try:
            logical_id = layout.logical_artifact_id(path)
        except DataRootError as exc:
            raise AcquisitionError(
                "manifest destination is outside the configured data root"
            ) from exc
        components = (
            layout.root,
            layout.runs,
            layout.runs / run_kind,
            directory,
        )
        if os.name != "nt":
            try:
                _write_manifest_new_posix(
                    root=layout.root,
                    run_kind=run_kind,
                    run_id=self.run_id,
                    document=self.to_json_dict(),
                )
            except FileExistsError as exc:
                raise AcquisitionError(f"run manifest already exists: {logical_id}") from exc
            except AcquisitionError:
                raise
            except OSError as exc:
                raise AcquisitionError("MANIFEST_STORAGE_FAILURE") from exc
            return logical_id
        with _hold_windows_directories(
            components,
            unsupported_code="MANIFEST_STORAGE_UNSUPPORTED",
        ):
            if path.exists():
                raise AcquisitionError(f"run manifest already exists: {logical_id}")
            try:
                write_manifest_new(path, self.to_json_dict())
            except FileExistsError as exc:
                raise AcquisitionError(f"run manifest already exists: {logical_id}") from exc
            except OSError as exc:
                raise AcquisitionError("MANIFEST_STORAGE_FAILURE") from exc
        return logical_id


# ---------------------------------------------------------------------------
# The boundary
# ---------------------------------------------------------------------------


def _replay_lineage_for_publication(
    *,
    outcome: FetchOutcome,
    parser: Parser | None,
    registered_normalizer: Parser | None,
    parse_status: str,
    parse_hash_value: str | None,
    parsed: Mapping[str, Any] | None,
    requested_at: datetime,
    analysis_as_of: str | None,
    available_at: str,
    cutoff_status: str,
    source_plan_authority: tuple[AttemptPlanAuthority, ...],
    source_plan_observed_at: tuple[str, ...],
) -> ReplayLineage | None:
    """Record replay lineage only for an accepted registered-normalizer result.

    Anything else -- a shape-only parser, a non-data payload, a clock
    regression, a rejected parse -- keeps a v1 entry, which can never be
    replayed. That is deliberate: replay authority is granted by observed
    evidence at acquisition time, never reconstructed afterwards.
    """
    if (
        parser is None
        or registered_normalizer is not parser
        or parse_status != PARSER_STATUS_PARSED
        or parse_hash_value is None
        or parsed is None
        or analysis_as_of is None
        or outcome.http_status is None
        or outcome.payload_state.state == STATE_CLOCK_REGRESSION
    ):
        return None
    return ReplayLineage(
        parameters_sha256=outcome.parameters_sha256,
        public_url=outcome.public_url,
        observed_final_url=outcome.observed_final_url,
        http_status=outcome.http_status,
        http_headers=dict(outcome.http_headers),
        provider_metadata=dict(outcome.provider_metadata),
        attempts=outcome.attempts,
        requested_at=requested_at.astimezone(UTC).isoformat(timespec="microseconds"),
        acquired_at=available_at,
        analysis_as_of=analysis_as_of,
        available_at=available_at,
        cutoff_status=cutoff_status,
        parser_name=parser.name,
        parser_version=parser.version,
        parser_implementation_sha256=parser.implementation_sha256,
        parser_output_kind=PARSER_OUTPUT_NORMALIZED_DATA,
        parse_hash=parse_hash_value,
        normalized_output_sha256=hashlib.sha256(
            canonical_json_value_bytes(_deep_thaw(parsed))
        ).hexdigest(),
        source_plan_authority=tuple(
            (item.attempt, item.plan_id, item.plan_evidence_sha256)
            for item in source_plan_authority
        ),
        source_plan_observed_at=source_plan_observed_at,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AcquisitionBoundary:
    """Quota-aware acquisition with lineage-complete cache replay.

    A cache hit is replayed only when the index entry records the full replay
    lineage (temporal cutoff coordinates plus parser, parse-output, and
    normalized-output identity). Committed v1 entries record none of that, so
    they fail closed with a typed error instead of being replayed against
    reconstructed authority.
    """

    def __init__(
        self,
        *,
        layout: DataRootLayout,
        client: AlphaVantageClient,
        plans: Sequence[ProviderPlan] = REGISTERED_PLANS,
        plan_id: str | None = None,
        retry_policy: RetryPolicy | None = None,
        parsers: Mapping[str, Parser] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        max_quota_wait_seconds: float = 0.0,
    ) -> None:
        self._layout = layout
        self._client = client
        self._plans = tuple(plans)
        self._pinned_plan_id = plan_id
        self._retry_policy = retry_policy or RetryPolicy()
        self._parsers: dict[str, Parser] = dict(DEFAULT_PARSERS if parsers is None else parsers)
        self._clock = clock or _utc_now
        self._sleep = sleep or time.sleep
        self._max_quota_wait_seconds = max_quota_wait_seconds
        self._store = RawPullStore(layout)
        self._index = self._store.request_key_index
        self._ledgers: dict[str, QuotaLedger] = {}
        self._plans_used: dict[str, ProviderPlan] = {}
        self._results: list[AcquisitionResult] = []
        self._flight_guard = threading.Lock()
        self._flights: dict[str, threading.Lock] = {}
        self._quota_guard = threading.Lock()
        self._clock_guard = threading.Lock()
        self._last_observed_time: datetime | None = None

    # -- accessors ----------------------------------------------------------

    @property
    def store(self) -> RawPullStore:
        return self._store

    @property
    def results(self) -> tuple[AcquisitionResult, ...]:
        return tuple(self._results)

    @property
    def is_offline(self) -> bool:
        return self._client.is_offline

    @property
    def transport_implementation_identity(self) -> str | None:
        return self._client.transport_implementation_identity

    @property
    def transport_implementation_sha256(self) -> str | None:
        return self._client.transport_implementation_sha256

    @staticmethod
    def _open_windows_directory_handle(path: Path) -> int:
        return _open_windows_directory_handle(path)

    @staticmethod
    def _close_windows_handle(handle: int) -> None:
        _close_windows_handle(handle)

    @contextmanager
    def _raw_storage_publication_guard(
        self,
        *,
        function: str,
        symbol: str | None,
    ) -> Iterator[None]:
        """Hold every Windows directory without delete sharing through publish."""
        if os.name != "nt":
            yield
            return
        symbol_segment = symbol or "_"
        if (
            _RAW_STORAGE_COMPONENT_RE.fullmatch(function) is None
            or _RAW_STORAGE_COMPONENT_RE.fullmatch(symbol_segment) is None
        ):
            raise AcquisitionError("RAW_STORAGE_FAILURE")
        components = (
            self._layout.root,
            self._layout.raw,
            self._store.base_directory,
            self._store.base_directory / function,
            self._store.base_directory / function / symbol_segment,
        )
        handles: list[int] = []
        try:
            for component in components:
                component.mkdir(exist_ok=True)
                handles.append(self._open_windows_directory_handle(component))
        except OSError as exc:
            for handle in reversed(handles):
                self._close_windows_handle(handle)
            raise AcquisitionError("RAW_STORAGE_UNSUPPORTED") from exc
        try:
            yield
        finally:
            for handle in reversed(handles):
                self._close_windows_handle(handle)

    def observe_time(self) -> datetime:
        observed = self._clock()
        if observed.tzinfo is None:
            raise AcquisitionError("acquisition boundary clock must be timezone-aware")
        normalized = observed.astimezone(UTC)
        with self._clock_guard:
            if self._last_observed_time is not None and normalized < self._last_observed_time:
                raise ClockRegressionError(STATE_CLOCK_REGRESSION)
            self._last_observed_time = normalized
        return normalized

    def _last_trusted_time(self) -> datetime:
        with self._clock_guard:
            if self._last_observed_time is None:
                raise ClockRegressionError(STATE_CLOCK_REGRESSION)
            return self._last_observed_time

    def ledger_for(self, plan: ProviderPlan) -> QuotaLedger:
        ledger = self._ledgers.get(plan.plan_id)
        if ledger is None:
            ledger = QuotaLedger(plan, started_at=self.observe_time())
            self._ledgers[plan.plan_id] = ledger
        return ledger

    def _flight_lock(self, key: str) -> threading.Lock:
        with self._flight_guard:
            lock = self._flights.get(key)
            if lock is None:
                lock = threading.Lock()
                self._flights[key] = lock
            return lock

    def _parser_for(self, endpoint: str, override: Parser | None) -> Parser | None:
        if override is not None:
            return override
        return self._parsers.get(endpoint)

    def _active_redaction_secrets(self) -> tuple[str, ...]:
        try:
            return self._client._active_redaction_secrets()
        except CredentialError as exc:
            raise AcquisitionError("credential resolution failed") from exc

    def _reject_active_credential_material(self, value: object) -> None:
        _reject_attached_credential_material(
            value,
            secrets=self._active_redaction_secrets(),
        )

    @staticmethod
    def _validated_request_coordinates(
        request: AcquisitionRequest,
        *,
        expected_endpoint: str | None = None,
        expected_parameters: Mapping[str, str] | None = None,
        expected_key: str | None = None,
    ) -> tuple[str, dict[str, str], str]:
        try:
            request.validate_integrity()
            if not isinstance(request.parameters, MappingProxyType):
                raise AcquisitionError("request parameter mapping is not immutable")
            validated = AcquisitionRequest(
                endpoint=request.endpoint,
                parameters=request.parameters,
                purpose=request.purpose,
                requested_at=request.requested_at,
                symbol=request.symbol,
            )
            endpoint = validated.canonical_endpoint
            parameters = dict(validated.parameters)
            key = request_key(endpoint, parameters)
            if expected_endpoint is not None and endpoint != expected_endpoint:
                raise AcquisitionError("endpoint changed after validation")
            if expected_parameters is not None and parameters != dict(expected_parameters):
                raise AcquisitionError("parameters changed after validation")
            if expected_key is not None and key != expected_key:
                raise AcquisitionError("request key changed after validation")
        except Exception as exc:
            raise AcquisitionError("request coordinates failed revalidation") from exc
        return endpoint, parameters, key

    # -- the one entry point ------------------------------------------------

    def acquire(
        self,
        request: AcquisitionRequest,
        *,
        parser: Parser | None = None,
        allow_cache: bool = True,
    ) -> AcquisitionResult:
        """Acquire with a caller parser that can certify parsed data only."""
        return self._acquire(
            request,
            parser=parser,
            allow_cache=allow_cache,
        )

    def acquire_registered_normalized(
        self,
        request: AcquisitionRequest,
        *,
        allow_cache: bool = True,
    ) -> AcquisitionResult:
        """Acquire through the closed registered endpoint-normalizer path."""

        if (
            not self.is_offline
            and (
                self.transport_implementation_identity is None
                or self.transport_implementation_sha256 is None
            )
        ):
            raise AcquisitionError(
                "registered ingestion requires a verifiable transport implementation identity"
            )
        return self._acquire(
            request,
            parser=_registered_normalizer_for_request(request),
            allow_cache=allow_cache,
        )

    def _acquire(
        self,
        request: AcquisitionRequest,
        *,
        parser: Parser | None,
        allow_cache: bool,
    ) -> AcquisitionResult:
        self._reject_active_credential_material(
            (request.purpose, request.parameters, request.symbol)
        )
        endpoint, parameters, key = self._validated_request_coordinates(request)
        registered_normalizer = _matching_registered_normalizer(request, parser)
        with self._flight_lock(key):
            result = self._acquire_locked(
                request,
                endpoint=endpoint,
                parameters=parameters,
                key=key,
                parser=parser,
                allow_cache=allow_cache,
                registered_normalizer=registered_normalizer,
            )
        self._results.append(result)
        return result

    def _acquire_locked(
        self,
        request: AcquisitionRequest,
        *,
        endpoint: str,
        parameters: Mapping[str, str],
        key: str,
        parser: Parser | None,
        allow_cache: bool,
        registered_normalizer: Parser | None,
    ) -> AcquisitionResult:
        self._reject_active_credential_material(
            (request.purpose, request.parameters, request.symbol)
        )
        self._validated_request_coordinates(
            request,
            expected_endpoint=endpoint,
            expected_parameters=parameters,
            expected_key=key,
        )
        request_observed_at = self.observe_time()
        if allow_cache:
            try:
                entry = self._index.lookup(key)
            except RawPullStoreError as exc:
                raise CacheLineageError("CACHE_LINEAGE_INVALID") from exc
            if entry is not None:
                return self._from_cache(
                    request,
                    entry,
                    key,
                    request_observed_at,
                    registered_normalizer,
                )
        if self._client.is_offline:
            raise RawCacheMissError(
                f"no cached content for request key {key[:12]}... and the client is "
                "offline; a replay cannot reach the network"
            )

        grants: list[QuotaGrant] = []
        attempt_plans: dict[int, ProviderPlan] = {}
        transport_authorized_at: dict[int, datetime] = {}

        def _spend(attempt: int) -> None:
            self._validated_request_coordinates(
                request,
                expected_endpoint=endpoint,
                expected_parameters=parameters,
                expected_key=key,
            )
            observed_before_quota = self.observe_time()
            selected_plan = resolve_plan(
                observed_before_quota,
                plans=self._plans,
                plan_id=self._pinned_plan_id,
            )
            _require_registered_plan_authority(selected_plan)
            self._plans_used[selected_plan.plan_id] = selected_plan
            ledger = self.ledger_for(selected_plan)
            with self._quota_guard:
                grants.append(
                    ledger.acquire(
                        endpoint,
                        clock=self.observe_time,
                        sleep=self._sleep,
                        max_wait_seconds=self._max_quota_wait_seconds,
                    )
                )
            observed_after_quota = self.observe_time()
            effective_after_quota = resolve_plan(
                observed_after_quota,
                plans=self._plans,
                plan_id=self._pinned_plan_id,
            )
            _require_registered_plan_authority(effective_after_quota)
            if effective_after_quota != selected_plan:
                raise AcquisitionError(
                    "provider-plan authority changed while waiting for quota; "
                    "the attempt was not sent"
                )
            attempt_plans[attempt] = selected_plan

        def _authorize_transport(attempt: int) -> None:
            self._validated_request_coordinates(
                request,
                expected_endpoint=endpoint,
                expected_parameters=parameters,
                expected_key=key,
            )
            observed = self.observe_time()
            effective = resolve_plan(
                observed,
                plans=self._plans,
                plan_id=self._pinned_plan_id,
            )
            _require_registered_plan_authority(effective)
            granted_plan = attempt_plans.get(attempt)
            if granted_plan is None or effective != granted_plan:
                raise AcquisitionError(
                    "provider-plan authority changed before transport; the attempt was not sent"
                )
            transport_authorized_at[attempt] = observed

        try:
            outcome = self._client.fetch(
                endpoint,
                parameters,
                retry_policy=self._retry_policy,
                before_attempt=_spend,
                before_transport=_authorize_transport,
                clock=self.observe_time,
                require_observed_final_url=registered_normalizer is not None,
            )
        except ProviderAuthorityError as exc:
            raise AcquisitionError("registered provider authority changed") from exc
        if outcome.payload_state.state == STATE_CLOCK_REGRESSION:
            acquired_at = self._last_trusted_time()
            clock_state = STATE_CLOCK_REGRESSION
        else:
            try:
                acquired_at = self.observe_time()
                clock_state = None
            except ClockRegressionError:
                acquired_at = self._last_trusted_time()
                clock_state = STATE_CLOCK_REGRESSION
                outcome = replace(
                    outcome,
                    payload_state=PayloadState(
                        STATE_CLOCK_REGRESSION,
                        STATE_CLOCK_REGRESSION,
                        http_status=outcome.http_status,
                    ),
                    received_at=STATE_CLOCK_REGRESSION,
                )
        plan = attempt_plans[max(attempt_plans)]
        _require_registered_plan_authority(plan)
        if clock_state is None:
            try:
                effective_at_result = resolve_plan(
                    acquired_at,
                    plans=self._plans,
                    plan_id=self._pinned_plan_id,
                )
            except ProviderPlanError as exc:
                raise AcquisitionError(
                    "provider-plan authority changed before result acceptance; "
                    "the response was not published"
                ) from exc
            _require_registered_plan_authority(effective_at_result)
            if effective_at_result != plan:
                raise AcquisitionError(
                    "provider-plan authority changed before result acceptance; "
                    "the response was not published"
                )
        attempt_plan_authority = tuple(
            AttemptPlanAuthority(
                attempt=attempt,
                plan_id=attempt_plan.plan_id,
                plan_evidence_sha256=hashlib.sha256(
                    canonical_json_bytes(plan_evidence_dict(attempt_plan))
                ).hexdigest(),
            )
            for attempt, attempt_plan in sorted(attempt_plans.items())
        )
        if tuple(item.attempt for item in attempt_plan_authority) != tuple(
            range(1, outcome.attempts + 1)
        ):
            raise AcquisitionError("transport attempt plan authority is incomplete")
        authority_by_attempt = {
            item.attempt: item for item in attempt_plan_authority
        }
        retry_log = tuple(
            replace(
                event,
                plan_id=authority_by_attempt[event.attempt].plan_id,
                plan_evidence_sha256=authority_by_attempt[
                    event.attempt
                ].plan_evidence_sha256,
            )
            for event in outcome.retry_log
        )
        requested_at = transport_authorized_at.get(outcome.attempts, request_observed_at)

        # Credential precedence: screen the untrusted bytes *before* they can be
        # made durable, before they can be parsed, and before they can become a
        # reusable cache entry. Nothing below this point ever sees a body that
        # echoes the active credential.
        if body_contains_credential_material(
            outcome.body, secrets=outcome.redaction_secrets
        ):
            raise CredentialEvidenceError(
                byte_length=outcome.byte_length,
                body_sha256=outcome.sha256,
            )

        publication_stack = ExitStack()
        try:
            record: RawPullRecord | None = None
            if outcome.http_status is not None:
                self._validated_request_coordinates(
                    request,
                    expected_endpoint=endpoint,
                    expected_parameters=parameters,
                    expected_key=key,
                )
                # Durability first: bytes on disk, atomically published, before any
                # parser can see them.
                publication_stack.enter_context(
                    self._raw_storage_publication_guard(
                        function=endpoint,
                        symbol=parameters.get("symbol"),
                    )
                )
                try:
                    record = self._store.record(
                        outcome.to_raw_response(),
                        symbol=parameters.get("symbol"),
                        now=acquired_at,
                    )
                except (OSError, RawPullStoreError) as exc:
                    failure = str(exc)
                    if failure in {
                        "PUBLICATION_INDETERMINATE",
                        "RESPONSE_BODY_LIMIT_EXCEEDED",
                    }:
                        raise AcquisitionError(failure) from exc
                    raise AcquisitionError("RAW_STORAGE_FAILURE") from exc

            observed_available_at = (
                STATE_CLOCK_REGRESSION
                if clock_state is not None
                else acquired_at.astimezone(UTC).isoformat(timespec="microseconds")
            )
            observed_analysis_as_of = (
                None if clock_state is not None else observed_available_at
            )
            parse_status, parse_detail, digest, parsed = self._run_parser(
                request,
                endpoint,
                outcome.payload_state,
                outcome.body,
                parser,
                analysis_as_of=observed_analysis_as_of,
                available_at=observed_available_at,
                registered_normalizer=registered_normalizer,
                redaction_secrets=outcome.redaction_secrets,
            )
            chosen = self._parser_for(endpoint, parser)
            parser_identity_unsafe = chosen is not None and _contains_secret_material(
                (chosen.name, chosen.version),
                secrets=outcome.redaction_secrets,
            )
            safe_parser_name = (
                None
                if chosen is None
                else (
                    f"REDACTED_PARSER_IDENTITY:{chosen.implementation_sha256[:16]}"
                    if parser_identity_unsafe
                    else chosen.name
                )
            )
            safe_parser_version = (
                None
                if chosen is None
                else ("REDACTED" if parser_identity_unsafe else chosen.version)
            )
            cutoff_status = (
                str(parsed.get("cutoff_status"))
                if parsed is not None and parsed.get("cutoff_status") is not None
                else "REJECTED_NOT_NORMALIZED"
            )
            if record is not None and (
                outcome.payload_state.state != STATE_CLOCK_REGRESSION
                and (
                    not outcome.payload_state.is_data or parse_status == PARSER_STATUS_PARSED
                )
            ):
                # Raw evidence is always retained, but only parser-accepted DATA is
                # addressable as reusable cache content. A valid-JSON wrong-shape or
                # wrong-coordinate payload must not become a cache hit merely because
                # the generic HTTP classifier called it DATA.
                self._index.append(
                    record,
                    request_key=key,
                    provider_id=outcome.provider_id,
                    provider_version=outcome.provider_version,
                    canonical_parameters=outcome.canonical_parameters,
                    payload_state=outcome.payload_state.state,
                    acquisition_purpose=request.purpose,
                    plan_id=plan.plan_id,
                    parameters_redacted=outcome.parameters_redacted,
                    replay_lineage=_replay_lineage_for_publication(
                        outcome=outcome,
                        parser=chosen,
                        registered_normalizer=registered_normalizer,
                        parse_status=parse_status,
                        parse_hash_value=digest,
                        parsed=parsed,
                        requested_at=requested_at,
                        analysis_as_of=observed_analysis_as_of,
                        available_at=observed_available_at,
                        cutoff_status=cutoff_status,
                        source_plan_authority=attempt_plan_authority,
                        source_plan_observed_at=tuple(
                            transport_authorized_at[item.attempt]
                            .astimezone(UTC)
                            .isoformat(timespec="microseconds")
                            for item in attempt_plan_authority
                        ),
                    ),
                )
        finally:
            publication_stack.close()
        caller_requested_at_label = (
            None
            if request.requested_at is None
            else request.requested_at.astimezone(UTC).isoformat(timespec="microseconds")
        )
        result = AcquisitionResult(
            endpoint=endpoint,
            purpose=request.purpose,
            request_key=key,
            parameters_sha256=outcome.parameters_sha256,
            provider_id=outcome.provider_id,
            provider_version=outcome.provider_version,
            canonical_parameters=outcome.canonical_parameters,
            parameters_redacted=dict(outcome.parameters_redacted),
            public_url=outcome.public_url,
            observed_final_url=outcome.observed_final_url,
            plan_id=plan.plan_id,
            requested_at=requested_at.astimezone(UTC).isoformat(timespec="microseconds"),
            caller_requested_at_label=caller_requested_at_label,
            acquired_at=observed_available_at,
            analysis_as_of=observed_analysis_as_of,
            available_at=(
                observed_available_at
                if outcome.http_status is not None and clock_state is None
                else None
            ),
            cutoff_status=cutoff_status,
            http_status=outcome.http_status,
            content_type=outcome.content_type,
            http_headers=dict(outcome.http_headers),
            provider_metadata=outcome.provider_metadata,
            response_sha256=None if record is None else record.sha256,
            byte_length=outcome.byte_length,
            attempts=outcome.attempts,
            retry_log=retry_log,
            attempt_plan_authority=attempt_plan_authority,
            source_plan_authority=attempt_plan_authority,
            payload_state=outcome.payload_state,
            parser_name=safe_parser_name,
            parser_version=safe_parser_version,
            parser_implementation_sha256=(
                None if chosen is None else chosen.implementation_sha256
            ),
            parser_output_kind=(
                None
                if chosen is None
                else (
                    PARSER_OUTPUT_NORMALIZED_DATA
                    if registered_normalizer is chosen
                    else PARSER_OUTPUT_PARSED_DATA
                )
            ),
            parser_status=parse_status,
            parser_detail=parse_detail,
            parse_hash=digest,
            parse_result=parsed,
            raw_local_uri=None if record is None else record.body_logical_id,
            meta_local_uri=None if record is None else record.meta_logical_id,
            pull_id=None if record is None else record.pull_id,
            served_from_cache=False,
            quota_grant=grants[-1] if grants else None,
        )
        if (
            chosen is not None
            and registered_normalizer is chosen
            and parse_status == PARSER_STATUS_PARSED
        ):
            return _grant_normalized_result_authority(result, request, chosen)
        return result

    def _from_cache(
        self,
        request: AcquisitionRequest,
        entry: RequestKeyEntry,
        key: str,
        requested_at: datetime,
        parser: Parser | None,
    ) -> AcquisitionResult:
        """Replay one lineage-complete entry with no transport and no quota.

        The cached bytes are re-read and hash-verified, the *recorded* temporal
        cutoff coordinates are replayed rather than the replay-day clock (which
        would silently change what the normalizer accepts), and the recomputed
        parser identity, parse hash, and normalized-output digest must all equal
        the recorded ones. A replay that cannot reproduce every one of them is a
        typed failure, never a warning.
        """
        del requested_at
        lineage = entry.replay_lineage
        if not entry.is_lineage_complete or lineage is None:
            raise CacheLineageError("CACHE_LINEAGE_INVALID")
        if (
            not lineage.source_plan_authority
            or len(lineage.source_plan_authority)
            != len(lineage.source_plan_observed_at)
            or tuple(item[0] for item in lineage.source_plan_authority)
            != tuple(range(1, len(lineage.source_plan_authority) + 1))
        ):
            raise CacheLineageError("CACHE_LINEAGE_INVALID")
        for reference, observed_at_text in zip(
            lineage.source_plan_authority,
            lineage.source_plan_observed_at,
            strict=True,
        ):
            _attempt, source_plan_id, evidence_sha256 = reference
            try:
                observed_at = datetime.fromisoformat(observed_at_text)
                source_plan = resolve_plan(
                    observed_at,
                    plans=self._plans,
                    plan_id=source_plan_id,
                )
            except (TypeError, ValueError, ProviderPlanError) as exc:
                raise CacheLineageError("CACHE_LINEAGE_INVALID") from exc
            observed_digest = hashlib.sha256(
                canonical_json_bytes(plan_evidence_dict(source_plan))
            ).hexdigest()
            if observed_digest != evidence_sha256:
                raise CacheLineageError("CACHE_LINEAGE_INVALID")
            _require_registered_plan_authority(source_plan)
            self._plans_used[source_plan.plan_id] = source_plan
        registered = None if parser is None else _matching_registered_normalizer(request, parser)
        if registered is None:
            raise CacheLineageError(
                "cached lineage can only be replayed through a registered normalizer"
            )
        registered.validate_identity()
        endpoint = request.canonical_endpoint
        if (
            entry.endpoint != endpoint
            or entry.canonical_parameters != request.canonical_parameters
            or entry.request_key != key
            or entry.provider_id != PROVIDER_ID
            or entry.provider_version != PROVIDER_VERSION
            or lineage.parameters_sha256
            != parameters_hash_from_pairs(entry.canonical_parameters)
        ):
            raise CacheLineageError("cached entry does not answer this request coordinate")
        if (
            lineage.parser_name != registered.name
            or lineage.parser_version != registered.version
            or lineage.parser_implementation_sha256 != registered.implementation_sha256
            or lineage.parser_output_kind != PARSER_OUTPUT_NORMALIZED_DATA
        ):
            raise CacheLineageError("cached parser configuration is not the registered one")

        body = self._index.read_body(entry)
        if len(body) != entry.byte_length or hashlib.sha256(body).hexdigest() != entry.sha256:
            raise CacheLineageError("cached raw bytes do not reproduce their recorded digest")

        parse_status, parse_detail, digest, parsed = self._run_parser(
            request,
            endpoint,
            PayloadState(STATE_DATA, http_status=lineage.http_status),
            body,
            parser,
            analysis_as_of=lineage.analysis_as_of,
            available_at=lineage.available_at,
            registered_normalizer=registered,
            redaction_secrets=self._active_redaction_secrets(),
        )
        if parse_status != PARSER_STATUS_PARSED or parsed is None or digest is None:
            raise CacheLineageError(
                "cached bytes no longer reproduce a parsed result: "
                f"{parse_detail or parse_status}"
            )
        if digest != lineage.parse_hash:
            raise CacheLineageError("replayed parse output does not reproduce its recorded hash")
        if (
            hashlib.sha256(canonical_json_value_bytes(_deep_thaw(parsed))).hexdigest()
            != lineage.normalized_output_sha256
        ):
            raise CacheLineageError(
                "replayed normalized output does not reproduce its recorded hash"
            )
        cutoff_status = (
            str(parsed.get("cutoff_status"))
            if parsed.get("cutoff_status") is not None
            else "REJECTED_NOT_NORMALIZED"
        )
        if cutoff_status != lineage.cutoff_status:
            raise CacheLineageError("replayed cutoff status does not reproduce the recorded one")

        plan_id = entry.plan_id
        if plan_id is None:  # pragma: no cover - guarded by is_lineage_complete
            raise CacheLineageError("cached entry carries no plan authority")
        if plan_id != lineage.source_plan_authority[-1][1]:
            raise CacheLineageError("CACHE_LINEAGE_INVALID")
        caller_requested_at_label = (
            None
            if request.requested_at is None
            else request.requested_at.astimezone(UTC).isoformat(timespec="microseconds")
        )
        result = AcquisitionResult(
            endpoint=endpoint,
            purpose=request.purpose,
            request_key=key,
            parameters_sha256=lineage.parameters_sha256,
            provider_id=entry.provider_id,
            provider_version=entry.provider_version,
            canonical_parameters=entry.canonical_parameters,
            parameters_redacted=dict(entry.parameters_redacted),
            public_url=lineage.public_url,
            observed_final_url=lineage.observed_final_url,
            plan_id=plan_id,
            requested_at=lineage.requested_at,
            caller_requested_at_label=caller_requested_at_label,
            acquired_at=lineage.acquired_at,
            analysis_as_of=lineage.analysis_as_of,
            available_at=lineage.available_at,
            cutoff_status=cutoff_status,
            http_status=lineage.http_status,
            content_type=entry.content_type,
            http_headers=dict(lineage.http_headers),
            provider_metadata=dict(lineage.provider_metadata),
            response_sha256=entry.sha256,
            byte_length=entry.byte_length,
            # This replay issued no transport attempt, so it claims none: an
            # attempt count it did not make would be invented plan authority.
            attempts=0,
            retry_log=(),
            attempt_plan_authority=(),
            source_plan_authority=tuple(
                AttemptPlanAuthority(
                    attempt=attempt,
                    plan_id=plan_id,
                    plan_evidence_sha256=plan_evidence_sha256,
                )
                for attempt, plan_id, plan_evidence_sha256 in lineage.source_plan_authority
            ),
            payload_state=PayloadState(STATE_DATA, http_status=lineage.http_status),
            parser_name=registered.name,
            parser_version=registered.version,
            parser_implementation_sha256=registered.implementation_sha256,
            parser_output_kind=PARSER_OUTPUT_NORMALIZED_DATA,
            parser_status=parse_status,
            parser_detail=parse_detail,
            parse_hash=digest,
            parse_result=parsed,
            raw_local_uri=entry.body_logical_id,
            meta_local_uri=entry.meta_logical_id,
            pull_id=entry.pull_id,
            served_from_cache=True,
            quota_grant=None,
        )
        return _grant_normalized_result_authority(result, request, registered)

    def _run_parser(
        self,
        request: AcquisitionRequest,
        endpoint: str,
        state: PayloadState,
        body: bytes,
        parser: Parser | None,
        *,
        analysis_as_of: str | None,
        available_at: str,
        registered_normalizer: Parser | None,
        redaction_secrets: tuple[str, ...],
    ) -> tuple[str, str | None, str | None, Mapping[str, Any] | None]:
        if not state.is_data:
            detail = state.detail or state.state
            if state.state == STATE_TRANSPORT_FAILURE:
                return PARSER_STATUS_NOT_INVOKED, detail, None, None
            return PARSER_STATUS_SKIPPED_NON_DATA, detail, None, None
        if analysis_as_of is None:
            return PARSER_STATUS_ERROR, "INVALID_TEMPORAL_LINEAGE", None, None
        chosen = self._parser_for(endpoint, parser)
        if chosen is None:
            return PARSER_STATUS_NO_PARSER, f"no parser declared for {endpoint}", None, None
        if (
            registered_normalizer is not None
            and _matching_registered_normalizer(request, chosen) is not registered_normalizer
        ):
            return PARSER_STATUS_ERROR, "PARSER_IDENTITY_MISMATCH", None, None
        if _contains_secret_material(
            (chosen.name, chosen.version),
            secrets=redaction_secrets,
        ):
            return (
                PARSER_STATUS_ERROR,
                "PARSER_IDENTITY_CONTAINS_CREDENTIAL_MATERIAL",
                None,
                None,
            )
        try:
            parsed = chosen.execute(body, analysis_as_of, available_at)
        except AcquisitionError:
            return PARSER_STATUS_ERROR, "PARSER_IDENTITY_MISMATCH", None, None
        except Exception as exc:  # the bytes are already durably stored
            fixed_detail = _normalization_failure_detail(exc)
            return PARSER_STATUS_ERROR, fixed_detail or "PARSER_EXCEPTION", None, None
        try:
            chosen.validate_identity()
            if (
                registered_normalizer is not None
                and _matching_registered_normalizer(request, chosen)
                is not registered_normalizer
            ):
                raise AcquisitionError("registered parser authority changed")
        except AcquisitionError:
            return PARSER_STATUS_ERROR, "PARSER_IDENTITY_MISMATCH", None, None
        try:
            if registered_normalizer is chosen:
                frozen = _freeze_registered_normalized_output(
                    request,
                    chosen,
                    parsed,
                    analysis_as_of=analysis_as_of,
                    available_at=available_at,
                )
                contains_secret = frozen.contains_secret_material(
                    secrets=redaction_secrets
                )
            else:
                contains_secret = _contains_secret_material(
                    parsed,
                    secrets=redaction_secrets,
                )
                frozen = _deep_freeze(parsed)
            if contains_secret:
                return (
                    PARSER_STATUS_ERROR,
                    "PARSER_OUTPUT_CONTAINS_CREDENTIAL_MATERIAL",
                    None,
                    None,
                )
            if not isinstance(frozen, Mapping):
                raise TypeError("parser result is not a mapping")
            digest = parse_hash(
                chosen,
                frozen,
                output_kind=(
                    PARSER_OUTPUT_NORMALIZED_DATA
                    if registered_normalizer is chosen
                    else PARSER_OUTPUT_PARSED_DATA
                ),
            )
        except _TraversalLimitError:
            return PARSER_STATUS_ERROR, "INVALID_PARSER_OUTPUT:LIMIT_EXCEEDED", None, None
        except _CyclicContainerError:
            return PARSER_STATUS_ERROR, "INVALID_PARSER_OUTPUT:CYCLIC_CONTAINER", None, None
        except RecursionError:  # defense in depth for an unsupported recursive object
            return PARSER_STATUS_ERROR, "INVALID_PARSER_OUTPUT:CYCLIC_CONTAINER", None, None
        except (TypeError, ValueError):
            return PARSER_STATUS_ERROR, "INVALID_PARSER_OUTPUT", None, None
        return PARSER_STATUS_PARSED, None, digest, frozen

    # -- run manifest -------------------------------------------------------

    def build_run_manifest(
        self,
        *,
        run_id: str | None = None,
        run_id_domain: str = "av-acquisition",
        purpose: str,
        started_at: datetime,
        finished_at: datetime | None = None,
        results: Sequence[AcquisitionResult] | None = None,
        request_set: Sequence[Mapping[str, Any]] | None = None,
        run_configuration: Mapping[str, Any] | None = None,
        code_source_lineage: Mapping[str, str] | None = None,
        runtime_verifier: Callable[[], None] | None = None,
    ) -> AcquisitionRunManifest:
        _validate_registered_provider_authority()
        active_secrets = self._active_redaction_secrets()
        _reject_attached_credential_material(
            (run_id, run_id_domain, purpose),
            secrets=active_secrets,
        )
        if started_at.tzinfo is None:
            raise AcquisitionError("manifest started_at must be timezone-aware")
        end = finished_at or self.observe_time()
        if end.tzinfo is None:
            raise AcquisitionError("manifest finished_at must be timezone-aware")
        started_iso = started_at.astimezone(UTC).isoformat(timespec="microseconds")
        finished_iso = end.astimezone(UTC).isoformat(timespec="microseconds")
        selected_results = tuple(self._results if results is None else results)
        for result in selected_results:
            result.validate_parse_integrity()
            if (result.provider_id, result.provider_version) != _REGISTERED_PROVIDER_AUTHORITY:
                raise AcquisitionError(
                    "manifest result does not match registered provider authority"
                )
        source_references = tuple(
            authority
            for result in selected_results
            for authority in result.source_plan_authority
        )
        plan_ids = sorted({authority.plan_id for authority in source_references})
        selected_plans: list[ProviderPlan] = []
        for selected_plan_id in plan_ids:
            selected_plan = self._plans_used.get(selected_plan_id)
            if selected_plan is None:
                raise AcquisitionError(
                    f"run result cites plan {selected_plan_id!r} without attached provider-plan evidence"
                )
            selected_plans.append(selected_plan)
        plan_documents = tuple(plan_evidence_dict(plan) for plan in selected_plans)
        document_digests = {
            str(document["plan_id"]): hashlib.sha256(
                canonical_json_bytes(document)
            ).hexdigest()
            for document in plan_documents
        }
        for reference in source_references:
            if document_digests.get(reference.plan_id) != reference.plan_evidence_sha256:
                raise AcquisitionError("run source-plan evidence is incomplete or conflicting")
        _reject_attached_credential_material(
            plan_documents,
            secrets=active_secrets,
        )
        snapshots = tuple(
            self._ledgers[plan.plan_id].snapshot(end)
            for plan in selected_plans
            if plan.plan_id in self._ledgers
        )
        parser_versions: dict[str, str] = {}
        parser_implementations: dict[str, str] = {}
        for result in selected_results:
            if result.parser_version is None:
                continue
            if result.parser_name is None:
                raise AcquisitionError(
                    f"run parser for {result.endpoint} has no parser identity"
                )
            parser_identity = result.parser_name
            previous_version = parser_versions.get(parser_identity)
            if previous_version is not None and previous_version != result.parser_version:
                raise AcquisitionError(
                    f"run used conflicting versions for parser {parser_identity!r}: "
                    f"{previous_version!r} and {result.parser_version!r}"
                )
            parser_versions[parser_identity] = result.parser_version
            if result.parser_implementation_sha256 is None:
                raise AcquisitionError(
                    f"run parser for {result.endpoint} has no implementation digest"
                )
            previous_implementation = parser_implementations.get(parser_identity)
            if (
                previous_implementation is not None
                and previous_implementation != result.parser_implementation_sha256
            ):
                raise AcquisitionError(
                    f"run used conflicting implementations for parser {parser_identity!r}"
                )
            parser_implementations[parser_identity] = result.parser_implementation_sha256

        executed_request_documents = tuple(
            {
                "endpoint": result.endpoint,
                "canonical_parameters": [
                    list(pair) for pair in result.canonical_parameters
                ],
                "parser": result.parser_name,
                "parser_version": result.parser_version,
                "parser_implementation_sha256": result.parser_implementation_sha256,
                "parser_output_kind": result.parser_output_kind,
            }
            for result in selected_results
        )
        request_documents: tuple[Mapping[str, Any], ...]
        if request_set is None:
            request_documents = executed_request_documents
        else:
            copied_request_documents: list[Mapping[str, Any]] = []
            try:
                request_iterator = iter(request_set)
                for _index in range(_TRAVERSAL_MAX_ITEMS):
                    try:
                        item = next(request_iterator)
                    except StopIteration:
                        break
                    copied = _manifest_attached_copy(item)
                    if not isinstance(copied, Mapping) or any(
                        not isinstance(key, str) for key in copied
                    ):
                        raise AcquisitionError("manifest attached material is invalid")
                    _reject_attached_credential_material(
                        copied,
                        secrets=active_secrets,
                    )
                    copied_request_documents.append(cast(Mapping[str, Any], copied))
                else:
                    raise AcquisitionError("manifest attached material is invalid")
            except AcquisitionError:
                raise
            except (RecursionError, TypeError, ValueError) as exc:
                raise AcquisitionError("manifest attached material is invalid") from exc
            request_documents = tuple(copied_request_documents)
        if request_documents != executed_request_documents:
            raise AcquisitionError(
                "manifest request_set does not match executed result parser evidence one-to-one"
            )
        try:
            request_set_digest = hashlib.sha256(
                canonical_json_value_bytes(
                    [_deep_thaw(item) for item in request_documents]
                )
            ).hexdigest()
        except (_TraversalLimitError, RecursionError, TypeError, ValueError) as exc:
            raise AcquisitionError("manifest attached material is invalid") from exc
        configuration_document = _manifest_attached_copy(run_configuration or {})
        if not isinstance(configuration_document, dict):
            raise AcquisitionError("manifest attached material is invalid")
        _reject_attached_credential_material(
            configuration_document,
            secrets=active_secrets,
        )
        configuration_digest = hashlib.sha256(
            canonical_json_bytes(configuration_document)
        ).hexdigest()
        source_lineage_document = _manifest_attached_copy(code_source_lineage or {})
        if not isinstance(source_lineage_document, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in source_lineage_document.items()
        ):
            raise AcquisitionError("manifest attached material is invalid")
        _reject_attached_credential_material(
            source_lineage_document,
            secrets=active_secrets,
        )
        source_lineage = dict(sorted(source_lineage_document.items()))
        run_mode = "SHA256" if run_id is None else "EXPLICIT"
        run_domain = run_id_domain if run_id is None else None
        effective_counts: dict[str, int] = {}
        raw_payload_counts: dict[str, int] = {}
        parser_counts: dict[str, int] = {}
        for result in selected_results:
            effective_counts[result.effective_state] = (
                effective_counts.get(result.effective_state, 0) + 1
            )
            raw_payload_counts[result.payload_state.state] = (
                raw_payload_counts.get(result.payload_state.state, 0) + 1
            )
            parser_counts[result.parser_status] = parser_counts.get(result.parser_status, 0) + 1
        raw_hashes = {
            result.request_key: result.response_sha256
            for result in selected_results
            if result.response_sha256 is not None
        }
        claims = _default_manifest_claims()
        evidence_payload: dict[str, Any] = {
            "schema_version": ACQUISITION_RUN_SCHEMA_VERSION,
            "purpose": purpose,
            "started_at": started_iso,
            "finished_at": finished_iso,
            "provider": {
                "provider_id": _REGISTERED_PROVIDER_AUTHORITY[0],
                "provider_version": _REGISTERED_PROVIDER_AUTHORITY[1],
            },
            "plan_evidence": [_deep_thaw(item) for item in plan_documents],
            "retry_policy": self._retry_policy.to_json_dict(),
            "parser_versions": dict(sorted(parser_versions.items())),
            "parser_implementations": dict(sorted(parser_implementations.items())),
            "request_set": [_deep_thaw(item) for item in request_documents],
            "request_set_sha256": request_set_digest,
            "run_configuration": _deep_thaw(configuration_document),
            "configuration_sha256": configuration_digest,
            "code_source_lineage": source_lineage,
            "content_addressed_run_domain": run_domain,
            "content_addressed_run_mode": run_mode,
            "analysis_as_of_policy": "PER_REQUEST_OBSERVED_ACQUISITION_TIME",
            "counts": dict(sorted(effective_counts.items())),
            "raw_payload_counts": dict(sorted(raw_payload_counts.items())),
            "accepted_normalized_data_count": sum(
                result.accepted_normalized_data for result in selected_results
            ),
            "parser_counts": dict(sorted(parser_counts.items())),
            "raw_hashes": dict(sorted(raw_hashes.items())),
            "requests": [result._manifest_json_dict() for result in selected_results],
            "quota_snapshots": [snapshot.to_json_dict() for snapshot in snapshots],
            "claims": claims,
        }
        _reject_attached_credential_material(
            evidence_payload,
            secrets=active_secrets,
        )
        payload_digest = hashlib.sha256(canonical_json_bytes(evidence_payload)).hexdigest()
        actual_run_id = run_id or f"{run_id_domain}-{payload_digest}"
        evidence_document: dict[str, Any] = {
            **evidence_payload,
            "content_addressed_payload_sha256": payload_digest,
            "run_id": actual_run_id,
        }
        evidence_digest = hashlib.sha256(canonical_json_bytes(evidence_document)).hexdigest()
        publication_artifacts: list[tuple[str, str, int]] = []
        root = self._layout.root.resolve(strict=True)
        for result in selected_results:
            if result.raw_local_uri is not None:
                if result.response_sha256 is None:
                    raise AcquisitionError("stored raw evidence has no response digest")
                raw_path = (self._layout.root / result.raw_local_uri).resolve(strict=True)
                if not raw_path.is_relative_to(root):
                    raise AcquisitionError("stored raw evidence is outside the data root")
                publication_artifacts.append(
                    (str(raw_path), result.response_sha256, result.byte_length)
                )
            if result.meta_local_uri is not None:
                meta_path = (self._layout.root / result.meta_local_uri).resolve(strict=True)
                if not meta_path.is_relative_to(root):
                    raise AcquisitionError("stored metadata evidence is outside the data root")
                meta_bytes = meta_path.read_bytes()
                publication_artifacts.append(
                    (
                        str(meta_path),
                        hashlib.sha256(meta_bytes).hexdigest(),
                        len(meta_bytes),
                    )
                )
        publication_verifier = _write_bound_publication_verifier(
            tuple(publication_artifacts),
            runtime_verifier,
        )
        return AcquisitionRunManifest(
            run_id=actual_run_id,
            purpose=purpose,
            started_at=started_iso,
            finished_at=finished_iso,
            plan_evidence=plan_documents,
            retry_policy=self._retry_policy.to_json_dict(),
            parser_versions=parser_versions,
            results=selected_results,
            provider_id=_REGISTERED_PROVIDER_AUTHORITY[0],
            provider_version=_REGISTERED_PROVIDER_AUTHORITY[1],
            claims=claims,
            parser_implementations=parser_implementations,
            request_set=tuple(_deep_freeze(item) for item in request_documents),
            request_set_sha256=request_set_digest,
            run_configuration=_deep_freeze(configuration_document),
            configuration_sha256=configuration_digest,
            code_source_lineage=source_lineage,
            run_evidence=_deep_freeze(evidence_document),
            run_evidence_sha256=evidence_digest,
            content_addressed_run_domain=run_domain,
            content_addressed_run_mode=run_mode,
            content_addressed_payload_sha256=payload_digest,
            quota_snapshots=snapshots,
            _runtime_verifier=publication_verifier,
        )
