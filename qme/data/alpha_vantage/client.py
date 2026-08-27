"""Paced, fail-closed Alpha Vantage HTTP client (stdlib only; no network in tests).

Design rules (from IMPLEMENTATION_PLAN non-negotiables, the 2026-08-07 audit,
and NEE-123):

* Alpha Vantage returns business errors and throttles as **HTTP 200** with a
  JSON body carrying ``Note``, ``Information``, or ``Error Message``. Every body
  is classified before it is trusted; :func:`classify_payload` is the single
  source of truth for that classification and returns a **typed non-data
  state** — throttle, information, error message, malformed JSON, malformed CSV,
  unexpected media type, truncated, empty, HTTP error, transport failure.
* Requests are **paced evenly** (default one per second) rather than fired in
  bursts. Plan-bound token-bucket quota lives in :mod:`.quota`; pacing here is
  the older, weaker smoothing that the legacy :meth:`AlphaVantageClient.get`
  path still uses.
* Retries are bounded, use exponential backoff, and happen **only** for declared
  idempotent reads in declared transient classes (:class:`RetryPolicy`).
  ``Information`` and ``Error Message`` are business outcomes and are returned
  to the caller unchanged, never retried into oblivion.
* The credential is a **reference** (:class:`CredentialRef` — an environment
  variable *name*) resolved from ``os.environ`` at call time. It never appears
  in a recorded URL, a canonical parameter list, a request key, an exception, or
  a log line: :func:`redact_url` and :func:`redact_mapping` enforce that for
  anything persisted.
* This module performs **no network I/O**. The only module that opens a socket
  is :mod:`qme.data.alpha_vantage.transport`, which imports the transport
  contract from here — so the acquisition boundary is a module edge that
  ``tests/architecture/test_import_boundaries.py`` can assert on.
"""

from __future__ import annotations

import dis
import hashlib
import hmac
import inspect
import json
import math
import os
import re
import secrets
import sys
import time
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from types import (
    CodeType,
    ModuleType,
)
from typing import Any, cast

from qme.data.alpha_vantage.plan_v1 import PROVIDER_ID, PROVIDER_VERSION
from qme.foundation.lineage import canonical_json_bytes

_CLOSED_PROVIDER_AUTHORITY = (PROVIDER_ID, PROVIDER_VERSION)
_PROVIDER_AUTHORITY_SEAL_KEY = secrets.token_bytes(32)
_PROVIDER_AUTHORITY_SEAL = hmac.new(
    _PROVIDER_AUTHORITY_SEAL_KEY,
    "\x00".join(_CLOSED_PROVIDER_AUTHORITY).encode("utf-8"),
    hashlib.sha256,
).hexdigest()

BASE_URL = "https://www.alphavantage.co/query"
API_KEY_ENV = "ALPHA_VANTAGE_API_KEY"  # pragma: allowlist secret (env var name, not a value)
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MIN_INTERVAL_SECONDS = 1.0
DEFAULT_MAX_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = (2.0, 5.0, 12.0)

# Single owner-approved authority for every Alpha Vantage response body path.
MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES = 2_097_152

#: What a redacted credential looks like anywhere it would otherwise be printed.
REDACTED = "REDACTED"

#: Parameter names that may carry a credential. They are stripped from canonical
#: parameters, excluded from the request key, and redacted in anything persisted.
CREDENTIAL_PARAM_NAMES: frozenset[str] = frozenset(
    {"apikey", "api_key", "key", "token", "access_token", "secret", "password"}
)

#: Strict, case-insensitive response metadata allowlist. Unknown headers are
#: omitted rather than persisted because provider/CDN responses are untrusted.
RESPONSE_EVIDENCE_HEADER_NAMES: frozenset[str] = frozenset(
    {
        "content-type",
        "content-length",
        "date",
        "etag",
        "last-modified",
        "retry-after",
    }
)
REVIEWED_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "application/csv",
        "application/json",
        "application/octet-stream",
        "application/x-download",
        "text/csv",
        "text/json",
        "text/plain",
    }
)
_MEDIA_TYPE_RE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*")
_REPR_ADDRESS_RE = re.compile(r"\bat 0x[0-9A-Fa-f]+\b")

# Response classes returned by classify_body (legacy, kept for stored records).
CLASS_OK = "OK"
CLASS_THROTTLE = "SOFT_ERROR_NOTE"  # per-minute/burst throttle -> retryable
CLASS_INFORMATION = "SOFT_ERROR_INFORMATION"  # tier/limit/entitlement message -> not retryable
CLASS_ERROR_MESSAGE = "SOFT_ERROR_ERROR_MESSAGE"  # invalid call/symbol -> not retryable
CLASS_MALFORMED = "MALFORMED"
CLASS_HTTP_ERROR = "HTTP_ERROR"

# Typed payload states. Exactly one of these describes any response, including
# an HTTP 200 that carries no data.
STATE_DATA = "DATA"
STATE_THROTTLE_NOTE = "NON_DATA_THROTTLE_NOTE"
STATE_INFORMATION = "NON_DATA_INFORMATION"
STATE_ERROR_MESSAGE = "NON_DATA_ERROR_MESSAGE"
STATE_MALFORMED_JSON = "NON_DATA_MALFORMED_JSON"
STATE_MALFORMED_CSV = "NON_DATA_MALFORMED_CSV"
STATE_UNEXPECTED_MEDIA_TYPE = "NON_DATA_UNEXPECTED_MEDIA_TYPE"
STATE_TRUNCATED = "NON_DATA_TRUNCATED"
STATE_EMPTY = "NON_DATA_EMPTY_BODY"
STATE_HTTP_ERROR = "NON_DATA_HTTP_ERROR"
STATE_TRANSPORT_FAILURE = "NON_DATA_TRANSPORT_FAILURE"
STATE_CLOCK_REGRESSION = "CLOCK_REGRESSION"

NON_DATA_STATES: frozenset[str] = frozenset(
    {
        STATE_THROTTLE_NOTE,
        STATE_INFORMATION,
        STATE_ERROR_MESSAGE,
        STATE_MALFORMED_JSON,
        STATE_MALFORMED_CSV,
        STATE_UNEXPECTED_MEDIA_TYPE,
        STATE_TRUNCATED,
        STATE_EMPTY,
        STATE_HTTP_ERROR,
        STATE_TRANSPORT_FAILURE,
        STATE_CLOCK_REGRESSION,
    }
)

_LEGACY_CLASS_BY_STATE: Mapping[str, str] = {
    STATE_DATA: CLASS_OK,
    STATE_THROTTLE_NOTE: CLASS_THROTTLE,
    STATE_INFORMATION: CLASS_INFORMATION,
    STATE_ERROR_MESSAGE: CLASS_ERROR_MESSAGE,
    STATE_MALFORMED_JSON: CLASS_MALFORMED,
    STATE_MALFORMED_CSV: CLASS_MALFORMED,
    STATE_UNEXPECTED_MEDIA_TYPE: CLASS_MALFORMED,
    STATE_TRUNCATED: CLASS_MALFORMED,
    STATE_EMPTY: CLASS_MALFORMED,
    STATE_HTTP_ERROR: CLASS_HTTP_ERROR,
    STATE_TRANSPORT_FAILURE: CLASS_HTTP_ERROR,
    STATE_CLOCK_REGRESSION: CLASS_HTTP_ERROR,
}

_SOFT_KEYS = {
    "Note": STATE_THROTTLE_NOTE,
    "Information": STATE_INFORMATION,
    "Error Message": STATE_ERROR_MESSAGE,
}
_SOFT_DETAILS = {
    "Note": "PROVIDER_NOTE",
    "Information": "PROVIDER_INFORMATION",
    "Error Message": "PROVIDER_ERROR_MESSAGE",
}
_CSV_HEADER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ]*(,[A-Za-z_][A-Za-z0-9_ ]*)+\r?$")

#: Endpoints declared idempotent reads. Only these are ever retried.
DECLARED_IDEMPOTENT_ENDPOINTS: frozenset[str] = frozenset(
    {
        "TIME_SERIES_DAILY",
        "TIME_SERIES_DAILY_ADJUSTED",
        "TIME_SERIES_WEEKLY",
        "TIME_SERIES_MONTHLY",
        "DIVIDENDS",
        "SPLITS",
        "LISTING_STATUS",
    }
)

#: Declared transient classes. Everything else is a terminal outcome.
DECLARED_TRANSIENT_STATES: frozenset[str] = frozenset(
    {STATE_THROTTLE_NOTE, STATE_HTTP_ERROR, STATE_TRANSPORT_FAILURE, STATE_TRUNCATED}
)


class AlphaVantageError(ValueError):
    """Raised for client-side misuse or an exhausted retry budget. Never carries the key."""


class ProviderAuthorityError(AlphaVantageError):
    """The import-time registered provider authority no longer validates."""


def _validate_closed_provider_authority() -> None:
    current = (PROVIDER_ID, PROVIDER_VERSION)
    expected_seal = hmac.new(
        _PROVIDER_AUTHORITY_SEAL_KEY,
        "\x00".join(_CLOSED_PROVIDER_AUTHORITY).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if (
        current != _CLOSED_PROVIDER_AUTHORITY
        or not hmac.compare_digest(expected_seal, _PROVIDER_AUTHORITY_SEAL)
    ):
        raise ProviderAuthorityError("registered provider authority changed")


class CredentialError(AlphaVantageError):
    """Raised when a credential reference cannot be resolved. Never carries a value."""


class ClockRegressionError(AlphaVantageError):
    """The injected evidence clock moved backwards."""


class OfflineClientError(AlphaVantageError):
    """Raised when an offline client (no injected transport) is asked to send."""


class TransportError(OSError):
    """Network-layer failure raised by a transport. Never carries the credential."""


class ResponseBodyLimitError(TransportError):
    """Raised before an over-limit response can become complete evidence."""


class ResponseHeaderError(TransportError):
    """Raised for malformed response framing metadata."""


class UndeclaredTransportError(OSError):
    """An injected transport violated the declared exception contract."""


class TransportTimeoutError(TransportError, TimeoutError):
    """A transport timed out. Retryable for declared idempotent reads."""


class TransportProvenanceError(AlphaVantageError):
    """The observed final transport destination contradicts the intended request."""


class ImplementationIdentityError(AlphaVantageError):
    """A callable has unsupported, cyclic, or over-broad behavior state."""


_IDENTITY_MAX_DEPTH = 128
_IDENTITY_MAX_ITEMS = 50_000
_EXECUTION_SEAL_KEY = secrets.token_bytes(32)
_EXECUTION_MEMO_MISSING = object()
_EXECUTION_FREEZE_IN_PROGRESS = object()


@dataclass
class _IdentityBudget:
    items: int = 0
    seen_callables: set[int] = field(default_factory=set)
    seen_classes: set[int] = field(default_factory=set)
    loaded_dependency_depth: int = 0

    def enter(self, depth: int) -> None:
        if depth > _IDENTITY_MAX_DEPTH:
            raise ImplementationIdentityError("implementation identity depth limit exceeded")
        self.items += 1
        if self.items > _IDENTITY_MAX_ITEMS:
            raise ImplementationIdentityError("implementation identity item limit exceeded")


def _bounded_identity_mapping_items(
    value: Mapping[object, object],
    *,
    budget: _IdentityBudget,
) -> list[tuple[object, object]]:
    remaining = _IDENTITY_MAX_ITEMS - budget.items
    if remaining < 0 or len(value) > remaining:
        raise ImplementationIdentityError("implementation identity item limit exceeded")
    environment_data = getattr(os.environ, "_data", None)
    omit_hash_seed = budget.loaded_dependency_depth > 0 and (
        value is os.environ or value is environment_data
    )
    items: list[tuple[object, object]] = []
    for item in value.items():
        key = item[0]
        if omit_hash_seed and key in {"PYTHONHASHSEED", b"PYTHONHASHSEED"}:
            # The interpreter's randomized-order test coordinate must not enter
            # executable identity. All behavior-bearing proxy/environment
            # values remain bound.
            continue
        if len(items) >= remaining:
            raise ImplementationIdentityError("implementation identity item limit exceeded")
        items.append(item)
    return items


def _code_constant_material(
    value: object,
    *,
    active: set[int],
    budget: _IdentityBudget,
    depth: int,
) -> object:
    budget.enter(depth)
    if value is None or value is Ellipsis or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ImplementationIdentityError("non-finite code constant")
        return {"finite_float_hex": value.hex()}
    if isinstance(value, complex):
        if not math.isfinite(value.real) or not math.isfinite(value.imag):
            raise ImplementationIdentityError("non-finite complex code constant")
        return {"complex_hex": [value.real.hex(), value.imag.hex()]}
    if isinstance(value, CodeType):
        return _code_material(value, active=active, budget=budget, depth=depth + 1)
    if isinstance(value, tuple):
        identity = id(value)
        if identity in active:
            raise ImplementationIdentityError("cyclic code constant")
        active.add(identity)
        try:
            return {
                "tuple": [
                    _code_constant_material(
                        item,
                        active=active,
                        budget=budget,
                        depth=depth + 1,
                    )
                    for item in value
                ]
            }
        finally:
            active.remove(identity)
    if isinstance(value, frozenset):
        encoded = [
            _code_constant_material(
                item,
                active=active,
                budget=budget,
                depth=depth + 1,
            )
            for item in value
        ]
        return {
            "frozenset": sorted(
                encoded,
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        }
    raise ImplementationIdentityError("unsupported code constant")


def _code_material(
    code: CodeType,
    *,
    active: set[int],
    budget: _IdentityBudget,
    depth: int,
) -> dict[str, object]:
    budget.enter(depth)
    return {
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "nlocals": code.co_nlocals,
        "stacksize": code.co_stacksize,
        "flags": code.co_flags,
        "bytecode_hex": code.co_code.hex(),
        "constants": [
            _code_constant_material(
                item,
                active=active,
                budget=budget,
                depth=depth + 1,
            )
            for item in code.co_consts
        ],
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars),
        "cellvars": list(code.co_cellvars),
    }


def _callable_source_material(target: Callable[..., object] | type[object]) -> dict[str, object]:
    try:
        source: str | None = inspect.getsource(target).replace("\r\n", "\n")
    except (OSError, TypeError):
        source = None
    code = getattr(target, "__code__", None)
    return {
        "module": getattr(target, "__module__", type(target).__module__),
        "qualname": getattr(target, "__qualname__", type(target).__qualname__),
        "source": source,
        "code": (
            None
            if not isinstance(code, CodeType)
            else _code_material(
                code,
                active=set(),
                budget=_IdentityBudget(),
                depth=0,
            )
        ),
    }


def _loaded_callable_configuration_identity(
    value: object,
    *,
    active: set[int],
    callable_active: set[int],
    budget: _IdentityBudget,
    depth: int,
) -> object:
    if type(value) is object:
        return {"opaque_sentinel": "builtins.object"}
    if isinstance(value, (tuple, list)):
        return {
            "sequence": [
                _loaded_callable_configuration_identity(
                    item,
                    active=active,
                    callable_active=callable_active,
                    budget=budget,
                    depth=depth + 1,
                )
                for item in value
            ]
        }
    if isinstance(value, Mapping):
        items = _bounded_identity_mapping_items(value, budget=budget)
        encoded = [
            [
                _loaded_callable_configuration_identity(
                    key,
                    active=active,
                    callable_active=callable_active,
                    budget=budget,
                    depth=depth + 1,
                ),
                _loaded_callable_configuration_identity(
                    item,
                    active=active,
                    callable_active=callable_active,
                    budget=budget,
                    depth=depth + 1,
                ),
            ]
            for key, item in items
        ]
        return {
            "mapping_entries": sorted(
                encoded,
                key=lambda pair: json.dumps(
                    pair[0],
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        }
    if isinstance(value, (set, frozenset)):
        set_encoded: list[object] = [
            _loaded_callable_configuration_identity(
                item,
                active=active,
                callable_active=callable_active,
                budget=budget,
                depth=depth + 1,
            )
            for item in value
        ]
        return {
            "set": sorted(
                set_encoded,
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        }
    return _identity_value(
        value,
        active=active,
        callable_active=callable_active,
        budget=budget,
        depth=depth + 1,
    )


def _class_loaded_member_names(value: type[object]) -> frozenset[str]:
    names: set[str] = set()
    for member in vars(value).values():
        function: object = member
        if isinstance(member, (staticmethod, classmethod)):
            function = member.__func__
        elif isinstance(member, property):
            for accessor in (member.fget, member.fset, member.fdel):
                if accessor is not None:
                    names.update(
                        str(instruction.argval)
                        for instruction in dis.get_instructions(accessor)
                        if instruction.opname in {"LOAD_ATTR", "LOAD_METHOD"}
                    )
            continue
        if inspect.isfunction(function):
            names.update(
                str(instruction.argval)
                for instruction in dis.get_instructions(function)
                if instruction.opname in {"LOAD_ATTR", "LOAD_METHOD"}
            )
    return frozenset(names)


def _class_identity_material(
    value: type[object],
    *,
    active: set[int],
    callable_active: set[int],
    budget: _IdentityBudget,
    depth: int,
) -> dict[str, object]:
    """Bind deterministic class behavior without traversing unrelated modules."""

    budget.enter(depth)
    identity = id(value)
    if identity in callable_active or identity in budget.seen_classes:
        return {"class_reference": _callable_source_material(value)}
    budget.seen_classes.add(identity)
    callable_active.add(identity)
    try:
        members: dict[str, object] = {}
        loaded_member_names = _class_loaded_member_names(value)
        for name, member in sorted(vars(value).items()):
            budget.enter(depth + 1)
            if inspect.isfunction(member) or inspect.ismethod(member):
                members[name] = _callable_identity_material(
                    cast(Callable[..., object], member),
                    active=active,
                    callable_active=callable_active,
                    budget=budget,
                    depth=depth + 1,
                )
            elif isinstance(member, (staticmethod, classmethod)):
                members[name] = {
                    type(member).__name__: _callable_identity_material(
                        member.__func__,
                        active=active,
                        callable_active=callable_active,
                        budget=budget,
                        depth=depth + 1,
                    )
                }
            elif isinstance(member, property):
                members[name] = {
                    "property": {
                        label: (
                            None
                            if function is None
                            else _callable_identity_material(
                                function,
                                active=active,
                                callable_active=callable_active,
                                budget=budget,
                                depth=depth + 1,
                            )
                        )
                        for label, function in (
                            ("get", member.fget),
                            ("set", member.fset),
                            ("delete", member.fdel),
                        )
                    }
                }
            elif name.startswith("__"):
                # Interpreter/dataclass bookkeeping is not a loaded class
                # dependency. Dunder methods and descriptors were handled above.
                continue
            elif member is None or isinstance(
                member,
                (
                    str,
                    bool,
                    int,
                    bytes,
                    float,
                    date,
                    datetime,
                    tzinfo,
                    re.Pattern,
                ),
            ):
                members[name] = _identity_value(
                    member,
                    active=active,
                    callable_active=callable_active,
                    budget=budget,
                    depth=depth + 1,
                )
            elif isinstance(member, (tuple, list, set, frozenset, Mapping)):
                if name not in loaded_member_names:
                    continue
                members[name] = _identity_value(
                    member,
                    active=active,
                    callable_active=callable_active,
                    budget=budget,
                    depth=depth + 1,
                )
            else:
                members[name] = {
                    "descriptor_type": (
                        f"{type(member).__module__}.{type(member).__qualname__}"
                    )
                }
        return {
            "class": _callable_source_material(value),
            "bases": [f"{base.__module__}.{base.__qualname__}" for base in value.__bases__],
            "members": members,
        }
    finally:
        callable_active.remove(identity)


def _loaded_attribute_identity(
    value: object,
    *,
    active: set[int],
    callable_active: set[int],
    budget: _IdentityBudget,
    depth: int,
) -> object:
    """Recursively bind the exact loaded dependency graph under one budget."""

    budget.loaded_dependency_depth += 1
    try:
        if callable(value):
            module_root = getattr(value, "__module__", "").partition(".")[0]
            if budget.loaded_dependency_depth > 2 and module_root in sys.stdlib_module_names:
                return {"stdlib_dependency": _callable_source_material(value)}
            return _callable_identity_material(
                cast(Callable[..., object], value),
                active=active,
                callable_active=callable_active,
                budget=budget,
                depth=depth + 1,
            )
        return _identity_value(
            value,
            active=active,
            callable_active=callable_active,
            budget=budget,
            depth=depth + 1,
        )
    finally:
        budget.loaded_dependency_depth -= 1


def _loaded_module_attribute_material(
    target: Callable[..., object],
    referenced_globals: Mapping[str, object],
    *,
    active: set[int],
    callable_active: set[int],
    budget: _IdentityBudget,
    depth: int,
) -> dict[str, object]:
    """Bind every loaded module-attribute chain the callable can execute."""

    code = getattr(target, "__code__", None)
    if not isinstance(code, CodeType):
        return {}
    instructions = tuple(dis.get_instructions(code))
    chains: set[tuple[str, ...]] = set()
    for index, instruction in enumerate(instructions):
        if instruction.opname not in {"LOAD_GLOBAL", "LOAD_NAME"}:
            continue
        global_name = instruction.argval
        if not isinstance(global_name, str) or not isinstance(
            referenced_globals.get(global_name), ModuleType
        ):
            continue
        chain = [global_name]
        for following in instructions[index + 1 :]:
            if following.opname not in {"LOAD_ATTR", "LOAD_METHOD"}:
                break
            attribute = following.argval
            if not isinstance(attribute, str):
                break
            chain.append(attribute)
        if len(chain) > 1:
            chains.add(tuple(chain))

    material: dict[str, object] = {}
    for observed_chain in sorted(chains):
        current = referenced_globals[observed_chain[0]]
        try:
            for attribute in observed_chain[1:]:
                current = getattr(current, attribute)
        except AttributeError as exc:
            raise ImplementationIdentityError(
                "loaded module attribute is unavailable"
            ) from exc
        material[".".join(observed_chain)] = _loaded_attribute_identity(
            current,
            active=active,
            callable_active=callable_active,
            budget=budget,
            depth=depth + 1,
        )
    return material


def _identity_value(
    value: object,
    *,
    active: set[int],
    callable_active: set[int],
    budget: _IdentityBudget,
    depth: int,
) -> object:
    budget.enter(depth)
    if type(value) is object and budget.loaded_dependency_depth:
        return {"opaque_sentinel": "builtins.object"}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            if not budget.loaded_dependency_depth:
                raise ImplementationIdentityError("naive implementation identity datetime")
            return {"naive_datetime": value.isoformat(timespec="microseconds")}
        return {"datetime": value.astimezone(UTC).isoformat(timespec="microseconds")}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, tzinfo):
        return {"timezone": str(value)}
    if isinstance(value, BaseException):
        return {
            "exception_class": _callable_source_material(type(value)),
            "args": _identity_value(
                value.args,
                active=active,
                callable_active=callable_active,
                budget=budget,
                depth=depth + 1,
            ),
        }
    if isinstance(value, Path):
        return {"path": value.as_posix()}
    if isinstance(value, float):
        if not math.isfinite(value):
            if not budget.loaded_dependency_depth:
                raise ImplementationIdentityError("non-finite implementation identity value")
            return {"non_finite_float": repr(value)}
        return {"finite_float_hex": value.hex()}
    if isinstance(value, ModuleType):
        return {"module": value.__name__}
    if isinstance(value, re.Pattern):
        pattern: object = value.pattern
        if isinstance(pattern, bytes):
            pattern = {
                "bytes_sha256": hashlib.sha256(pattern).hexdigest(),
                "length": len(pattern),
            }
        return {"regex": pattern, "flags": value.flags}
    if isinstance(value, type):
        return _class_identity_material(
            value,
            active=active,
            callable_active=callable_active,
            budget=budget,
            depth=depth + 1,
        )
    if callable(value):
        return _callable_identity_material(
            value,
            active=active,
            callable_active=callable_active,
            budget=budget,
            depth=depth + 1,
        )
    if is_dataclass(value) and not isinstance(value, type):
        identity = id(value)
        if identity in active:
            if budget.loaded_dependency_depth:
                return {"cycle_reference": f"{type(value).__module__}.{type(value).__qualname__}"}
            raise ImplementationIdentityError("cyclic implementation identity value")
        active.add(identity)
        try:
            return {
                "dataclass": f"{type(value).__module__}.{type(value).__qualname__}",
                "fields": {
                    item.name: _identity_value(
                        getattr(value, item.name),
                        active=active,
                        callable_active=callable_active,
                        budget=budget,
                        depth=depth + 1,
                    )
                    for item in fields(value)
                },
            }
        finally:
            active.remove(identity)
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            if budget.loaded_dependency_depth:
                return {"cycle_reference": f"{type(value).__module__}.{type(value).__qualname__}"}
            raise ImplementationIdentityError("cyclic implementation identity value")
        items = _bounded_identity_mapping_items(value, budget=budget)
        if (
            any(not isinstance(key, str) for key, _item in items)
            and not budget.loaded_dependency_depth
        ):
            raise ImplementationIdentityError("implementation identity mapping key is not text")
        active.add(identity)
        try:
            if any(not isinstance(key, str) for key, _item in items):
                encoded_items = [
                    [
                        _identity_value(
                            key,
                            active=active,
                            callable_active=callable_active,
                            budget=budget,
                            depth=depth + 1,
                        ),
                        _identity_value(
                            item,
                            active=active,
                            callable_active=callable_active,
                            budget=budget,
                            depth=depth + 1,
                        ),
                    ]
                    for key, item in items
                ]
                return {
                    "mapping_entries": sorted(
                        encoded_items,
                        key=lambda pair: json.dumps(
                            pair[0],
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                }
            return {
                "mapping": {
                    key: _identity_value(
                        item,
                        active=active,
                        callable_active=callable_active,
                        budget=budget,
                        depth=depth + 1,
                    )
                    for key, item in sorted(items)
                }
            }
        finally:
            active.remove(identity)
    if isinstance(value, (tuple, list)):
        identity = id(value)
        if identity in active:
            if budget.loaded_dependency_depth:
                return {"cycle_reference": f"{type(value).__module__}.{type(value).__qualname__}"}
            raise ImplementationIdentityError("cyclic implementation identity value")
        active.add(identity)
        try:
            return {
                "sequence": [
                    _identity_value(
                        item,
                        active=active,
                        callable_active=callable_active,
                        budget=budget,
                        depth=depth + 1,
                    )
                    for item in value
                ]
            }
        finally:
            active.remove(identity)
    if isinstance(value, (set, frozenset)):
        identity = id(value)
        if identity in active:
            if budget.loaded_dependency_depth:
                return {"cycle_reference": f"{type(value).__module__}.{type(value).__qualname__}"}
            raise ImplementationIdentityError("cyclic implementation identity value")
        active.add(identity)
        try:
            encoded = [
                _identity_value(
                    item,
                    active=active,
                    callable_active=callable_active,
                    budget=budget,
                    depth=depth + 1,
                )
                for item in value
            ]
            return {
                "set": sorted(
                    encoded,
                    key=lambda item: json.dumps(
                        item,
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            }
        finally:
            active.remove(identity)
    instance_dictionary = getattr(value, "__dict__", None)
    if isinstance(instance_dictionary, Mapping):
        identity = id(value)
        if identity in active:
            if budget.loaded_dependency_depth:
                return {"cycle_reference": f"{type(value).__module__}.{type(value).__qualname__}"}
            raise ImplementationIdentityError("cyclic implementation identity value")
        active.add(identity)
        try:
            return {
                "object": f"{type(value).__module__}.{type(value).__qualname__}",
                "class": _callable_source_material(type(value)),
                "state": _identity_value(
                    instance_dictionary,
                    active=active,
                    callable_active=callable_active,
                    budget=budget,
                    depth=depth + 1,
                ),
            }
        finally:
            active.remove(identity)
    if budget.loaded_dependency_depth:
        rendered = repr(value)
        if _REPR_ADDRESS_RE.search(rendered) is None:
            return {
                "loaded_opaque": f"{type(value).__module__}.{type(value).__qualname__}",
                "representation": rendered,
            }
    raise ImplementationIdentityError(
        "unsupported implementation identity value: "
        f"{type(value).__module__}.{type(value).__qualname__}"
    )


def _callable_identity_material(
    function: Callable[..., object],
    *,
    active: set[int],
    callable_active: set[int],
    budget: _IdentityBudget,
    depth: int,
) -> dict[str, object]:
    budget.enter(depth)
    if isinstance(function, type):
        return _class_identity_material(
            function,
            active=active,
            callable_active=callable_active,
            budget=budget,
            depth=depth + 1,
        )
    target = function.__func__ if inspect.ismethod(function) else (
        function if inspect.isfunction(function) or inspect.isbuiltin(function)
        else type(function).__call__
    )
    identity = id(target) if inspect.ismethod(function) else id(function)
    if identity in callable_active or identity in budget.seen_callables:
        return {"callable_reference": _callable_source_material(target)}
    budget.seen_callables.add(identity)
    callable_active.add(identity)
    try:
        if inspect.ismethod(function):
            target = function.__func__
            bound_self = function.__self__
            instance_state: object = getattr(bound_self, "__dict__", None)
        elif inspect.isfunction(function) or inspect.isbuiltin(function):
            target = function
            instance_state = None
        else:
            target = type(function).__call__
            identity_state = getattr(function, "__qme_identity_state__", None)
            if callable(identity_state):
                instance_state = identity_state()
                declared_identity_state = _callable_source_material(identity_state)
            else:
                instance_state = getattr(function, "__dict__", None)
                declared_identity_state = None
        if "declared_identity_state" not in locals():
            declared_identity_state = None
        try:
            closure = inspect.getclosurevars(target)
            nonlocals = closure.nonlocals
            referenced_globals = {
                name: value
                for name, value in closure.globals.items()
                if not name.startswith(("@py_", "@pytest_"))
            }
        except TypeError:
            nonlocals = {}
            referenced_globals = {}
        return {
            "callable": _callable_source_material(target),
            "declared_identity_state": declared_identity_state,
            "defaults": _loaded_callable_configuration_identity(
                getattr(target, "__defaults__", None),
                active=active,
                callable_active=callable_active,
                budget=budget,
                depth=depth + 1,
            ),
            "keyword_defaults": _loaded_callable_configuration_identity(
                getattr(target, "__kwdefaults__", None),
                active=active,
                callable_active=callable_active,
                budget=budget,
                depth=depth + 1,
            ),
            "function_state": _identity_value(
                getattr(target, "__dict__", None) if inspect.isfunction(target) else None,
                active=active,
                callable_active=callable_active,
                budget=budget,
                depth=depth + 1,
            ),
            "nonlocals": {
                name: _identity_value(
                    value,
                    active=active,
                    callable_active=callable_active,
                    budget=budget,
                    depth=depth + 1,
                )
                for name, value in sorted(nonlocals.items())
            },
            "referenced_globals": {
                name: _loaded_attribute_identity(
                    value,
                    active=active,
                    callable_active=callable_active,
                    budget=budget,
                    depth=depth + 1,
                )
                for name, value in sorted(referenced_globals.items())
            },
            "loaded_module_attributes": _loaded_module_attribute_material(
                target,
                referenced_globals,
                active=active,
                callable_active=callable_active,
                budget=budget,
                depth=depth + 1,
            ),
            "instance_state": _identity_value(
                instance_state,
                active=active,
                callable_active=callable_active,
                budget=budget,
                depth=depth + 1,
            ),
        }
    finally:
        callable_active.remove(identity)


def callable_implementation_identity(
    function: Callable[..., object],
) -> tuple[str, str]:
    """Return cross-process-stable callable/configuration identity and SHA-256."""
    material = _callable_identity_material(
        function,
        active=set(),
        callable_active=set(),
        budget=_IdentityBudget(),
        depth=0,
    )
    target = function if inspect.isfunction(function) else type(function).__call__
    module = getattr(target, "__module__", type(function).__module__)
    qualname = getattr(target, "__qualname__", type(function).__qualname__)
    return (
        f"{module}.{qualname}",
        hashlib.sha256(canonical_json_bytes(material)).hexdigest(),
    )


# Credential reference (never a value on disk, never a .env read)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CredentialRef:
    """A reference to a credential: the *name* of an environment variable.

    ``resolve`` reads ``os.environ`` at call time. No file — and specifically no
    ``.env`` — is ever read here, and the resolved value is never stored on the
    reference, logged, hashed, or included in a request key.
    """

    env_var: str = API_KEY_ENV

    def __post_init__(self) -> None:
        if not self.env_var or self.env_var != self.env_var.strip():
            raise CredentialError("credential env var name must be a non-empty bare name")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.env_var):
            raise CredentialError(
                f"credential env var name {self.env_var!r} must be UPPER_SNAKE_CASE"
            )

    def resolve(self, environ: Mapping[str, str] | None = None) -> str:
        values = os.environ if environ is None else environ
        value = (values.get(self.env_var) or "").strip()
        if not value:
            raise CredentialError(
                f"environment variable {self.env_var} is not set; export it for this "
                "process (no .env file is read)"
            )
        return value

    def is_available(self, environ: Mapping[str, str] | None = None) -> bool:
        values = os.environ if environ is None else environ
        return bool((values.get(self.env_var) or "").strip())

    def to_json_dict(self) -> dict[str, str]:
        return {"credential_kind": "ENVIRONMENT_VARIABLE_NAME", "env_var": self.env_var}


def redact_mapping(params: Mapping[str, Any]) -> dict[str, str]:
    """Copy ``params`` with any credential-bearing value replaced by ``REDACTED``."""
    return {
        str(key): (REDACTED if str(key).lower() in CREDENTIAL_PARAM_NAMES else str(value))
        for key, value in params.items()
    }


_REDACTION_MAX_PERCENT_PASSES = 3
_REDACTION_MAX_NESTING = 4
_REDACTION_MAX_QUERY_CHARS = 65_536
_REDACTION_MAX_EVIDENCE_CHARS = 65_536
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)(^|[?&#;\s])(" + "|".join(
        re.escape(name)
        for name in sorted(CREDENTIAL_PARAM_NAMES, key=lambda item: (-len(item), item))
    ) + r")=([^&#;\s]*)"
)


def _bounded_unquote(value: str) -> tuple[str, bool]:
    decoded = value
    for _ in range(_REDACTION_MAX_PERCENT_PASSES):
        next_value = urllib.parse.unquote(decoded)
        if next_value == decoded:
            return decoded, False
        decoded = next_value
    return decoded, "%" in decoded


def _evidence_value_contains_encoded_secret(
    value: str,
    *,
    secrets: tuple[str, ...],
    max_chars: int = _REDACTION_MAX_EVIDENCE_CHARS,
) -> bool:
    if len(value) > max_chars:
        return True
    frontier = {value}
    for pass_number in range(_REDACTION_MAX_PERCENT_PASSES + 1):
        if any(secret and secret in candidate for candidate in frontier for secret in secrets):
            return True
        next_frontier = {
            transformed
            for candidate in frontier
            for transformed in (
                urllib.parse.unquote(candidate),
                urllib.parse.unquote_plus(candidate),
            )
        }
        if next_frontier == frontier:
            return False
        if pass_number == _REDACTION_MAX_PERCENT_PASSES:
            return True
        frontier = next_frontier
    return True


def body_contains_credential_material(body: bytes, *, secrets: tuple[str, ...]) -> bool:
    """Bounded search for an active credential inside an untrusted response body.

    Screens the literal UTF-8 credential and the same bounded repeated
    percent-encoding already approved for response-header evidence. Work is at
    most ``_REDACTION_MAX_PERCENT_PASSES + 1`` linear passes over the body, so a
    hostile body cannot turn this check into the denial of service it prevents.
    A body still re-decoding after the last pass is treated as bearing
    credential material, because at that point the check can no longer prove it
    does not.
    """
    active = tuple(secret for secret in secrets if secret)
    if not active:
        return False
    if any(secret.encode("utf-8") in body for secret in active):
        return True
    return _evidence_value_contains_encoded_secret(
        body.decode("utf-8", errors="replace"),
        secrets=active,
        max_chars=MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES,
    )


def _redact_embedded_assignments(value: str) -> str:
    return _CREDENTIAL_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}={REDACTED}",
        value,
    )


def _redact_url_inner(url: str, *, depth: int) -> str:
    if depth > _REDACTION_MAX_NESTING:
        return REDACTED
    decoded_url, over_encoded = _bounded_unquote(url)
    if over_encoded:
        return REDACTED
    split = urllib.parse.urlsplit(decoded_url)
    if not split.query:
        return _redact_embedded_assignments(decoded_url)
    if len(split.query) > _REDACTION_MAX_QUERY_CHARS:
        return urllib.parse.urlunsplit(
            (split.scheme, split.netloc, split.path, f"redaction={REDACTED}", "")
        )
    redacted_pairs: list[tuple[str, str]] = []
    for raw_key, raw_value in urllib.parse.parse_qsl(split.query, keep_blank_values=True):
        key, key_over_encoded = _bounded_unquote(raw_key)
        if key_over_encoded:
            redacted_pairs.append(("redaction", REDACTED))
            continue
        if "=" in key:
            embedded_key, _embedded_value = key.split("=", 1)
            if embedded_key.strip().lower() in CREDENTIAL_PARAM_NAMES:
                redacted_pairs.append((embedded_key, REDACTED))
                continue
        if key.strip().lower() in CREDENTIAL_PARAM_NAMES:
            redacted_pairs.append((key, REDACTED))
            continue
        value, value_over_encoded = _bounded_unquote(raw_value)
        if value_over_encoded:
            redacted_pairs.append((key, REDACTED))
            continue
        if _CREDENTIAL_ASSIGNMENT_RE.search(value):
            value = _redact_url_inner(value, depth=depth + 1)
        redacted_pairs.append((key, _redact_embedded_assignments(value)))
    return urllib.parse.urlunsplit(
        (
            split.scheme,
            split.netloc,
            split.path,
            urllib.parse.urlencode(redacted_pairs),
            _redact_embedded_assignments(split.fragment),
        )
    )


def redact_url(url: str) -> str:
    """Redact direct, encoded, and nested credential-bearing URL material."""
    return _redact_url_inner(url, depth=0)


def normalize_media_type(value: object) -> str:
    """Return a reviewed lowercase media-type token, never untrusted parameters."""
    token = str(value).split(";", 1)[0].strip().lower()
    if _MEDIA_TYPE_RE.fullmatch(token) is None or token not in REVIEWED_MEDIA_TYPES:
        return ""
    return token


def _transport_implementation_identity(transport: Transport | None) -> tuple[str, str] | None:
    if transport is None:
        return None
    try:
        return callable_implementation_identity(transport)
    except (ImplementationIdentityError, RecursionError) as exc:
        raise ImplementationIdentityError("transport identity is unsupported") from exc


def _observed_final_url(
    final_url: str | None,
    *,
    intended_public_url: str,
    credential_value: str,
) -> str | None:
    if final_url is None:
        return None
    decoded_final_url, over_encoded = _bounded_unquote(final_url)
    if over_encoded:
        raise TransportProvenanceError(
            "observed final destination exceeds the percent-decoding limit"
        )
    try:
        decoded_destination = urllib.parse.urlsplit(decoded_final_url)
    except ValueError as exc:
        raise TransportProvenanceError(
            "observed final destination is not a valid URL"
        ) from exc
    if (
        decoded_destination.username is not None
        or decoded_destination.password is not None
        or "#" in decoded_final_url
    ):
        raise TransportProvenanceError(
            "observed final destination contains forbidden userinfo or fragment material"
        )
    if credential_value and credential_value in decoded_final_url:
        query_values = {
            key.strip().lower(): value
            for key, value in urllib.parse.parse_qsl(
                decoded_destination.query, keep_blank_values=True
            )
        }
        if not any(
            key in CREDENTIAL_PARAM_NAMES and value == credential_value
            for key, value in query_values.items()
        ):
            raise TransportProvenanceError(
                "observed final destination contains active credential material"
            )
    redacted = redact_url(decoded_final_url)
    decoded_redacted, redacted_over_encoded = _bounded_unquote(redacted)
    if (
        redacted_over_encoded
        or (credential_value and credential_value in redacted)
        or (credential_value and credential_value in decoded_redacted)
    ):
        raise TransportProvenanceError(
            "observed final destination contains active credential material"
        )
    observed = urllib.parse.urlsplit(redacted)
    intended = urllib.parse.urlsplit(intended_public_url)
    if (
        observed.scheme.lower() != intended.scheme.lower()
        or observed.hostname != intended.hostname
        or observed.port != intended.port
        or observed.path != intended.path
        or observed.fragment
    ):
        raise TransportProvenanceError(
            "observed final destination contradicts the registered Alpha Vantage origin"
        )
    observed_pairs = sorted(
        (key, value)
        for key, value in urllib.parse.parse_qsl(observed.query, keep_blank_values=True)
        if key.strip().lower() not in CREDENTIAL_PARAM_NAMES
    )
    intended_pairs = sorted(
        urllib.parse.parse_qsl(intended.query, keep_blank_values=True)
    )
    if observed_pairs != intended_pairs:
        raise TransportProvenanceError(
            "observed final destination contradicts the intended request coordinates"
        )
    return redacted


# ---------------------------------------------------------------------------
# Cache identity: request_key = SHA256(provider_version || endpoint || params)
# ---------------------------------------------------------------------------


def canonical_endpoint(endpoint: str) -> str:
    """Uppercase, whitespace-free endpoint name."""
    value = str(endpoint).strip().upper()
    if not value or not re.fullmatch(r"[A-Z][A-Z0-9_]*", value):
        raise AlphaVantageError(f"endpoint {endpoint!r} is not a canonical Alpha Vantage function")
    return value


def canonical_parameters(params: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Normalize request parameters for the cache identity.

    Credential-bearing keys and the redundant ``function`` key are dropped, keys
    are lowercased and de-spaced, values are stringified and stripped, empty
    values are dropped, and the result is sorted — so the same logical request
    always produces the same key.
    """
    normalized: dict[str, str] = {}
    for raw_key, raw_value in params.items():
        key = str(raw_key).strip().lower()
        if not key or key in CREDENTIAL_PARAM_NAMES or key == "function":
            continue
        value = str(raw_value).strip()
        if not value:
            continue
        if key in normalized and normalized[key] != value:
            raise AlphaVantageError(f"parameter {key!r} was supplied twice with different values")
        normalized[key] = value
    return tuple(sorted(normalized.items()))


def request_key_material(
    endpoint: str,
    params: Mapping[str, Any],
    *,
    provider_version: str = PROVIDER_VERSION,
) -> bytes:
    """The exact bytes hashed into a request key (canonical JSON, unambiguous)."""
    return canonical_json_bytes(
        {
            "provider_version": provider_version,
            "endpoint": canonical_endpoint(endpoint),
            "canonical_parameters": [list(pair) for pair in canonical_parameters(params)],
        }
    )


def request_key(
    endpoint: str,
    params: Mapping[str, Any],
    *,
    provider_version: str = PROVIDER_VERSION,
) -> str:
    """``SHA256(provider_version || endpoint || canonical_parameters)``.

    The credential is excluded by construction: :func:`canonical_parameters`
    drops every credential-bearing key before the material is built.
    """
    return hashlib.sha256(
        request_key_material(endpoint, params, provider_version=provider_version)
    ).hexdigest()


def parameters_hash(params: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical parameters alone, credential excluded.

    Narrower than :func:`request_key`: it identifies *what was asked for*
    independently of the endpoint and the provider surface version, which is
    what a run record needs when comparing two requests' inputs.
    """
    return hashlib.sha256(
        canonical_json_bytes(
            {"canonical_parameters": [list(pair) for pair in canonical_parameters(params)]}
        )
    ).hexdigest()


def parameters_hash_from_pairs(pairs: Iterable[tuple[str, str]]) -> str:
    """The same digest computed from already-canonical parameter pairs."""
    return hashlib.sha256(
        canonical_json_bytes({"canonical_parameters": [list(pair) for pair in pairs]})
    ).hexdigest()


# ---------------------------------------------------------------------------
# Transport contract (implemented by qme.data.alpha_vantage.transport)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransportResponse:
    """One HTTP exchange as the transport saw it, before any interpretation."""

    status: int
    content_type: str
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)
    declared_length: int | None = None
    final_url: str | None = None

    def redacted_headers(self, *, secret_values: Iterable[str] = ()) -> dict[str, str]:
        secrets = tuple(secret for secret in secret_values if secret)
        evidence: dict[str, str] = {}
        missing = object()
        for name in sorted(RESPONSE_EVIDENCE_HEADER_NAMES):
            canonical = "-".join(part.capitalize() for part in name.split("-"))
            if name == "etag":
                canonical = "ETag"
            query_names = tuple(dict.fromkeys((name, canonical, name.upper())))
            values: list[str] = []
            try:
                for query_name in query_names:
                    raw_value = self.headers.get(query_name, missing)
                    if raw_value is missing:
                        continue
                    value = str(raw_value)
                    if name == "content-type":
                        value = normalize_media_type(value)
                        if not value:
                            continue
                    elif (
                        "\r" in value
                        or "\n" in value
                        or _evidence_value_contains_encoded_secret(value, secrets=secrets)
                    ):
                        value = REDACTED
                    values.append(value)
            except Exception as exc:
                raise AlphaVantageError("REMOTE_EVIDENCE_INVALID") from exc
            if values:
                evidence[name] = values[0] if all(value == values[0] for value in values) else REDACTED
        return dict(sorted(evidence.items()))


Transport = Callable[[str, float], TransportResponse | tuple[int, str, bytes]]
"""(url, timeout) -> TransportResponse (or the legacy 3-tuple). Injected in tests."""


def normalize_transport_result(
    result: TransportResponse | tuple[int, str, bytes],
) -> TransportResponse:
    """Accept either the rich response or the legacy ``(status, ct, body)`` tuple."""
    if isinstance(result, TransportResponse):
        response = result
    else:
        status, content_type, body = result
        response = TransportResponse(
            status=int(status),
            content_type=str(content_type),
            body=bytes(body),
        )
    if (
        response.declared_length is not None
        and response.declared_length > MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES
    ) or len(response.body) > MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES:
        raise ResponseBodyLimitError("RESPONSE_BODY_LIMIT_EXCEEDED")
    return response


# ---------------------------------------------------------------------------
# Typed payload classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PayloadState:
    """The typed verdict on one response body."""

    state: str
    detail: str | None = None
    http_status: int | None = None

    @property
    def is_data(self) -> bool:
        return self.state == STATE_DATA

    @property
    def legacy_class(self) -> str:
        return _LEGACY_CLASS_BY_STATE[self.state]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "http_status": self.http_status,
            "response_class": self.legacy_class,
            "is_data": self.is_data,
        }


def _classify_json(body: bytes) -> PayloadState:
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return PayloadState(STATE_MALFORMED_JSON, "MALFORMED_JSON")
    except RecursionError:
        # A remote body nested deeply enough to exhaust the C stack is a
        # malformed payload, not an interpreter failure the caller must handle.
        # Classification runs before any parser, so letting this escape would
        # abort acquisition with a raw exception instead of typed evidence.
        return PayloadState(STATE_MALFORMED_JSON, "MALFORMED_JSON_TOO_DEEP")
    except MemoryError:
        return PayloadState(STATE_MALFORMED_JSON, "MALFORMED_JSON_EXHAUSTED_MEMORY")
    if not isinstance(document, dict):
        return PayloadState(STATE_MALFORMED_JSON, "JSON root is not an object")
    for key, state in _SOFT_KEYS.items():
        if key in document:
            return PayloadState(state, _SOFT_DETAILS[key])
    if not document:
        return PayloadState(STATE_MALFORMED_JSON, "empty JSON object")
    return PayloadState(STATE_DATA)


def _classify_csv(body: bytes) -> PayloadState:
    try:
        head = body.split(b"\n", 1)[0].decode("utf-8")
    except UnicodeDecodeError:
        return PayloadState(STATE_MALFORMED_CSV, "CSV header is not UTF-8")
    if _CSV_HEADER_RE.match(head):
        return PayloadState(STATE_DATA)
    # A soft error can also arrive as a plain-text/JSON body on a CSV endpoint.
    text = body[:2000].decode("utf-8", errors="replace")
    for key, state in _SOFT_KEYS.items():
        if key in text:
            return PayloadState(state, _SOFT_DETAILS[key])
    return PayloadState(STATE_MALFORMED_CSV, "CSV header does not look like a header row")


def classify_payload(
    content_type: str,
    body: bytes,
    *,
    http_status: int = 200,
    declared_length: int | None = None,
) -> PayloadState:
    """Classify one response into exactly one typed state.

    ``declared_length`` is the transport's ``Content-Length``; a body shorter
    than it is a partial/incomplete read, not data, even on HTTP 200.
    """
    if http_status != 200:
        return PayloadState(STATE_HTTP_ERROR, f"HTTP {http_status}", http_status=http_status)
    if declared_length is not None and len(body) < declared_length:
        return PayloadState(
            STATE_TRUNCATED,
            f"body is {len(body)} byte(s); Content-Length declared {declared_length}",
            http_status=http_status,
        )
    if not body:
        return PayloadState(STATE_EMPTY, "response body is empty", http_status=http_status)
    ct = content_type.lower()
    if "json" in ct or body[:1] in (b"{", b"["):
        state = _classify_json(body)
    elif "csv" in ct or "text/plain" in ct or body[:1].isalpha():
        state = _classify_csv(body)
    else:
        state = PayloadState(STATE_UNEXPECTED_MEDIA_TYPE, "UNREVIEWED_MEDIA_TYPE")
    return PayloadState(state.state, state.detail, http_status=http_status)


def classify_body(content_type: str, body: bytes) -> tuple[str, str | None]:
    """Legacy classification of a 200 body. Returns ``(response_class, message)``.

    Kept byte-for-byte compatible with the pre-NEE-123 contract that
    ``RawPullRecord.response_class`` and the M0 fixture receipts depend on; new
    code should call :func:`classify_payload` for the typed state.
    """
    state = classify_payload(content_type, body)
    return state.legacy_class, state.detail


# ---------------------------------------------------------------------------
# Retry policy: declared idempotent reads, declared transient classes
# ---------------------------------------------------------------------------


def _http_status_is_transient(status: int | None) -> bool:
    return status is not None and (status == 429 or 500 <= status < 600)


@dataclass(frozen=True)
class RetryPolicy:
    """Which outcomes may be retried, how often, and how long to back off."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff_seconds: tuple[float, ...] = _RETRY_BACKOFF_SECONDS
    idempotent_endpoints: frozenset[str] = DECLARED_IDEMPOTENT_ENDPOINTS
    transient_states: frozenset[str] = DECLARED_TRANSIENT_STATES
    enforce_idempotency: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise AlphaVantageError("max_attempts must be >= 1")
        if not self.backoff_seconds:
            raise AlphaVantageError("backoff_seconds must not be empty")

    def is_idempotent(self, endpoint: str) -> bool:
        if not self.enforce_idempotency:
            return True
        return canonical_endpoint(endpoint) in self.idempotent_endpoints

    def is_transient(self, state: str, http_status: int | None) -> bool:
        if state not in self.transient_states:
            return False
        if state == STATE_HTTP_ERROR:
            return _http_status_is_transient(http_status)
        return True

    def may_retry(self, endpoint: str, state: str, http_status: int | None, attempt: int) -> bool:
        if attempt >= self.max_attempts:
            return False
        if not self.is_idempotent(endpoint):
            return False
        return self.is_transient(state, http_status)

    def backoff_for(self, attempt: int) -> float:
        index = min(max(attempt, 1) - 1, len(self.backoff_seconds) - 1)
        return self.backoff_seconds[index]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "backoff_seconds": list(self.backoff_seconds),
            "idempotent_endpoints": sorted(self.idempotent_endpoints),
            "transient_states": sorted(self.transient_states),
            "enforce_idempotency": self.enforce_idempotency,
        }


#: The pre-NEE-123 behaviour, kept for :meth:`AlphaVantageClient.get`: retry any
#: endpoint on a throttle Note, a 429/5xx, or a transport failure.
LEGACY_RETRY_POLICY = RetryPolicy(
    transient_states=frozenset({STATE_THROTTLE_NOTE, STATE_HTTP_ERROR, STATE_TRANSPORT_FAILURE}),
    enforce_idempotency=False,
)


@dataclass(frozen=True)
class RetryEvent:
    """One recorded attempt that did not terminate the request."""

    attempt: int
    outcome_state: str
    http_status: int | None
    detail: str | None
    backoff_seconds: float
    observed_at: str
    plan_id: str | None = None
    plan_evidence_sha256: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "outcome_state": self.outcome_state,
            "http_status": self.http_status,
            "detail": self.detail,
            "backoff_seconds": self.backoff_seconds,
            "observed_at": self.observed_at,
            "plan_id": self.plan_id,
            "plan_evidence_sha256": self.plan_evidence_sha256,
        }


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawResponse:
    """One HTTP exchange, exactly as received. ``body`` is the untouched payload."""

    function: str
    params_public: Mapping[str, str]  # request parameters WITHOUT the api key
    public_url: str  # URL WITHOUT the api key
    http_status: int
    content_type: str
    body: bytes
    requested_at: str  # ISO-8601 UTC
    received_at: str  # ISO-8601 UTC
    attempts: int
    response_class: str
    soft_message: str | None = None


@dataclass(frozen=True)
class FetchOutcome:
    """Everything NEE-123 asks the acquisition boundary to be able to record.

    This is the richer sibling of :class:`RawResponse`: it adds the cache
    identity, the HTTP metadata, the retry/throttle log, the typed payload
    state, and the provider metadata. It carries **no** credential.
    """

    endpoint: str
    request_key: str
    parameters_sha256: str
    provider_id: str
    provider_version: str
    canonical_parameters: tuple[tuple[str, str], ...]
    parameters_redacted: Mapping[str, str]
    public_url: str
    observed_final_url: str | None
    redaction_secrets: tuple[str, ...] = field(repr=False, compare=False)
    http_status: int | None
    content_type: str
    http_headers: Mapping[str, str]
    body: bytes
    sha256: str
    byte_length: int
    requested_at: str
    received_at: str
    attempts: int
    payload_state: PayloadState
    retry_log: tuple[RetryEvent, ...]
    transport_failure: str | None = None

    @property
    def is_data(self) -> bool:
        return self.payload_state.is_data

    @property
    def provider_metadata(self) -> dict[str, str]:
        metadata = {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "endpoint": self.endpoint,
        }
        for header in ("Date", "Server", "Content-Type", "Content-Length"):
            for key, value in self.http_headers.items():
                if key.lower() == header.lower():
                    metadata[f"http_{header.lower().replace('-', '_')}"] = str(value)
        return metadata

    def to_raw_response(self) -> RawResponse:
        """Adapt to the legacy record shape the immutable store persists."""
        return RawResponse(
            function=self.endpoint,
            params_public=dict(self.parameters_redacted),
            public_url=self.observed_final_url or self.public_url,
            http_status=self.http_status if self.http_status is not None else 0,
            content_type=self.content_type,
            body=self.body,
            requested_at=self.requested_at,
            received_at=self.received_at,
            attempts=self.attempts,
            response_class=self.payload_state.legacy_class,
            soft_message=self.payload_state.detail,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "request_key": self.request_key,
            "parameters_sha256": self.parameters_sha256,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "canonical_parameters": [list(pair) for pair in self.canonical_parameters],
            "parameters_redacted": dict(sorted(self.parameters_redacted.items())),
            "public_url": self.public_url,
            "observed_final_url": self.observed_final_url,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "http_headers": {str(k): str(v) for k, v in sorted(self.http_headers.items())},
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "requested_at": self.requested_at,
            "received_at": self.received_at,
            "attempts": self.attempts,
            "payload_state": self.payload_state.to_json_dict(),
            "retry_log": [event.to_json_dict() for event in self.retry_log],
            "transport_failure": self.transport_failure,
            "provider_metadata": self.provider_metadata,
        }


@dataclass
class Pacer:
    """Even request pacing: at least ``min_interval_seconds`` between sends."""

    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS
    _last_sent: float | None = field(default=None, repr=False)
    _sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def wait(self) -> float:
        """Block until the next send is allowed; return the seconds slept."""
        now = self._clock()
        slept = 0.0
        if self._last_sent is not None:
            due = self._last_sent + self.min_interval_seconds
            if now < due:
                slept = due - now
                self._sleep(slept)
                now = self._clock()
        self._last_sent = now
        return slept


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _observed_iso(clock: Callable[[], datetime]) -> str:
    observed = clock()
    if observed.tzinfo is None:
        raise AlphaVantageError("request clock must be timezone-aware")
    return observed.astimezone(UTC).isoformat(timespec="microseconds")


def load_api_key(
    *,
    environ: Mapping[str, str] | None = None,
    repository_root: Path | None = None,
) -> str:
    """Legacy key loader: environment first, then ``<repository_root>/.env``.

    .. deprecated:: NEE-123
       New code must use :class:`CredentialRef`, which resolves an environment
       variable *name* through ``os.environ`` at call time and never reads a
       file. This function is retained only because the pre-NEE-123 M0 fixture
       CLI path and its test depend on the ``.env`` fallback; nothing in the
       acquisition boundary calls it.
    """
    values = os.environ if environ is None else environ
    key = (values.get(API_KEY_ENV) or "").strip()
    if key:
        return key
    if repository_root is not None:
        env_path = repository_root / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith(API_KEY_ENV + "="):
                    candidate = line.split("=", 1)[1].strip().strip("'\"")
                    if candidate:
                        return candidate
    raise AlphaVantageError(
        f"{API_KEY_ENV} is not set in the environment or in <repository_root>/.env"
    )


class AlphaVantageClient:
    """Paced GET client with an injected transport.

    ``transport`` is **required for any network access**: with ``transport=None``
    the client is offline and every request path raises before a socket could be
    opened. The real transport lives in
    :mod:`qme.data.alpha_vantage.transport`, which this module deliberately does
    not import.

    ``get`` returns a classified :class:`RawResponse` for anything the server
    actually answered — including throttles and HTTP errors on the final
    attempt, so the caller can *record* what happened. It raises
    ``AlphaVantageError`` only for client misuse or when every attempt failed at
    the transport layer. ``fetch`` is the NEE-123 path: it never raises for a
    server outcome, returning a typed :class:`FetchOutcome` instead.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        credential: CredentialRef | None = None,
        environ: Mapping[str, str] | None = None,
        transport: Transport | None = None,
        pacer: Pacer | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        if credential is not None and api_key is not None:
            raise AlphaVantageError("pass either api_key or credential, not both")
        if credential is None:
            if api_key is None or not api_key.strip():
                raise AlphaVantageError("api_key must be non-empty")
            self._api_key: str | None = api_key.strip()
        else:
            self._api_key = None
        if max_attempts < 1:
            raise AlphaVantageError("max_attempts must be >= 1")
        self._credential = credential
        self._environ = environ
        self._transport = transport
        self._transport_instance = transport
        self._transport_identity = _transport_implementation_identity(transport)
        self._transport_execution = transport
        self._transport_execution_seal = self._seal_transport_execution(
            self._transport_execution
        )
        self._pacer = pacer or Pacer()
        self._max_attempts = max_attempts
        self._timeout = timeout_seconds
        self._sleep = sleep
        self._retry_policy = retry_policy or RetryPolicy(max_attempts=max_attempts)

    # -- credential ---------------------------------------------------------

    @property
    def credential_ref(self) -> CredentialRef | None:
        return self._credential

    @property
    def is_offline(self) -> bool:
        return self._transport is None

    @property
    def transport_implementation_identity(self) -> str | None:
        current = self.validate_transport_identity()
        return None if current is None else current[0]

    @property
    def transport_implementation_sha256(self) -> str | None:
        current = self.validate_transport_identity()
        return None if current is None else current[1]

    def validate_transport_identity(
        self,
        transport: Transport | None | object = Ellipsis,
    ) -> tuple[str, str] | None:
        """Recompute and compare the exact transport about to execute."""
        observed = self._transport if transport is Ellipsis else transport
        if observed is not self._transport_instance or self._transport is not observed:
            raise TransportProvenanceError("transport identity changed after client construction")
        try:
            current = _transport_implementation_identity(observed)
        except ImplementationIdentityError as exc:
            raise TransportProvenanceError("transport identity changed or became unsupported") from exc
        if current != self._transport_identity:
            raise TransportProvenanceError("transport identity changed after client construction")
        return current

    def _seal_transport_execution(self, execution: object) -> str:
        material = canonical_json_bytes(
            {
                "execution_object": None if execution is None else id(execution),
                "transport_identity": self._transport_identity,
            }
        )
        return hmac.new(_EXECUTION_SEAL_KEY, material, hashlib.sha256).hexdigest()

    def _credential_value(self) -> str:
        """Resolve the credential for exactly one request. Never cached, never logged."""
        if self._credential is not None:
            return self._credential.resolve(self._environ)
        if self._api_key is None:  # pragma: no cover - guarded in __init__
            raise CredentialError("no credential is configured")
        return self._api_key

    def _active_redaction_secrets(self) -> tuple[str, ...]:
        """Return request-local screening material without persisting or logging it.

        An offline client holds no wire: it never builds a private URL, never
        sends a credential, and never publishes provider bytes. Resolving a
        credential *reference* there would create a credential exposure that the
        work itself does not need, and would make replay impossible on a machine
        that legitimately holds no credential. So an offline client with a
        reference screens against nothing. A credential passed directly to the
        constructor is already resident in this process, so it is still screened.
        """
        if self._credential is not None and self.is_offline:
            return ()
        return (self._credential_value(),)

    # -- URL construction ---------------------------------------------------

    @staticmethod
    def public_params(function: str, params: Mapping[str, str]) -> dict[str, str]:
        normalized_keys = {str(key).strip().lower() for key in params}
        if normalized_keys & CREDENTIAL_PARAM_NAMES:
            raise AlphaVantageError("do not pass 'apikey' in params; the client injects it")
        if "function" in normalized_keys:
            raise AlphaVantageError(
                "function parameter is forbidden; the endpoint argument is authoritative"
            )
        merged = {"function": function, **{k: str(v) for k, v in params.items()}}
        return dict(sorted(merged.items()))

    @staticmethod
    def public_url(function: str, params: Mapping[str, str]) -> str:
        return BASE_URL + "?" + urllib.parse.urlencode(
            AlphaVantageClient.public_params(function, params)
        )

    def _private_url(
        self,
        function: str,
        params: Mapping[str, str],
        *,
        credential_value: str | None = None,
    ) -> str:
        public = AlphaVantageClient.public_params(function, params)
        secret = self._credential_value() if credential_value is None else credential_value
        return BASE_URL + "?" + urllib.parse.urlencode(
            {**public, "apikey": secret}
        )

    def _send(self, private_url: str) -> TransportResponse:
        transport = self._transport
        execution = self._transport_execution
        if transport is None:
            raise OfflineClientError(
                "this Alpha Vantage client is offline: no transport was injected, so no "
                "network request can be made (replay from the immutable raw cache instead)"
            )
        if execution is None or not hmac.compare_digest(
            self._transport_execution_seal,
            self._seal_transport_execution(execution),
        ):
            raise TransportProvenanceError(
                "transport closed execution changed after client construction"
            )
        self.validate_transport_identity(transport)
        try:
            result = execution(private_url, self._timeout)
        except (OSError, AlphaVantageError):
            raise
        except Exception as exc:
            raise UndeclaredTransportError("UNDECLARED_TRANSPORT_FAILURE") from exc
        self.validate_transport_identity(transport)
        return normalize_transport_result(result)

    # -- Legacy request path ------------------------------------------------

    def get(self, function: str, **params: str) -> RawResponse:
        public = self.public_params(function, params)
        public_url = self.public_url(function, params)
        attempts = 0
        last_reason = "no attempt made"
        while attempts < self._max_attempts:
            attempts += 1
            self._pacer.wait()
            requested_at = _now_iso()
            try:
                self.validate_transport_identity()
                private_url = self._private_url(function, params)
                response = self._send(private_url)
            except (TimeoutError, OSError):
                last_reason = "TRANSPORT_EXCEPTION"
                self._backoff(attempts)
                continue
            received_at = _now_iso()
            status, content_type, body = response.status, response.content_type, response.body

            if status == 200:
                klass, message = classify_body(content_type, body)
            elif status in (429,) or 500 <= status < 600:
                klass, message = CLASS_HTTP_ERROR, f"HTTP {status}"
            else:
                klass, message = CLASS_HTTP_ERROR, f"HTTP {status}"
                return RawResponse(
                    function, public, public_url, status, content_type, body,
                    requested_at, received_at, attempts, klass, message,
                )

            if klass in (CLASS_THROTTLE, CLASS_HTTP_ERROR) and attempts < self._max_attempts:
                last_reason = message or klass
                self._backoff(attempts)
                continue
            return RawResponse(
                function, public, public_url, status, content_type, body,
                requested_at, received_at, attempts, klass, message,
            )
        raise AlphaVantageError(
            f"retry budget exhausted after {attempts} attempt(s) for {public_url}: {last_reason}"
        )

    def _backoff(self, attempt_number: int) -> None:
        index = min(attempt_number - 1, len(_RETRY_BACKOFF_SECONDS) - 1)
        self._sleep(_RETRY_BACKOFF_SECONDS[index])

    # -- NEE-123 request path -----------------------------------------------

    def fetch(
        self,
        endpoint: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        retry_policy: RetryPolicy | None = None,
        before_attempt: Callable[[int], None] | None = None,
        before_transport: Callable[[int], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        require_observed_final_url: bool = False,
    ) -> FetchOutcome:
        """Perform one logical request and return a typed outcome.

        Never raises for anything the provider did: a throttle, a business
        error, a malformed body, a truncated read, and an exhausted retry budget
        after transport failures all come back as non-data states with a full
        retry log. ``before_attempt`` is called with the 1-based attempt number
        before pacing/quota work. ``before_transport`` is called after pacing and
        immediately before credential resolution and transport, so an authority
        boundary can fail closed without sending.
        """
        _validate_closed_provider_authority()
        canonical = canonical_endpoint(endpoint)
        params = dict(parameters or {})
        policy = retry_policy or self._retry_policy
        public = self.public_params(canonical, params)
        public_url = self.public_url(canonical, params)
        key = request_key(canonical, params)
        canonical_params = canonical_parameters(params)
        redacted = redact_mapping(public)
        retry_log: list[RetryEvent] = []
        attempts = 0
        observed_clock = clock or (lambda: datetime.now(UTC))

        while True:
            attempts += 1
            if before_attempt is not None:
                before_attempt(attempts)
            self._pacer.wait()
            if before_transport is not None:
                before_transport(attempts)
            self.validate_transport_identity()
            requested_at = _observed_iso(observed_clock)
            try:
                credential_value = self._credential_value()
                private_url = self._private_url(
                    canonical, params, credential_value=credential_value
                )
                response = self._send(private_url)
            except ResponseBodyLimitError:
                received_at = _observed_iso(observed_clock)
                detail = "RESPONSE_BODY_LIMIT_EXCEEDED"
                state = PayloadState(STATE_TRANSPORT_FAILURE, detail)
                return self._outcome(
                    canonical,
                    key,
                    canonical_params,
                    redacted,
                    public_url,
                    None,
                    (credential_value,),
                    None,
                    "",
                    {},
                    b"",
                    requested_at,
                    received_at,
                    attempts,
                    state,
                    tuple(retry_log),
                    detail,
                )
            except (TimeoutError, OSError) as exc:
                received_at = _observed_iso(observed_clock)
                detail = (
                    "UNDECLARED_TRANSPORT_FAILURE"
                    if isinstance(exc, UndeclaredTransportError)
                    else "TRANSPORT_EXCEPTION"
                )
                state = PayloadState(STATE_TRANSPORT_FAILURE, detail)
                if policy.may_retry(canonical, state.state, None, attempts):
                    backoff = policy.backoff_for(attempts)
                    retry_log.append(
                        RetryEvent(attempts, state.state, None, detail, backoff, received_at)
                    )
                    self._sleep(backoff)
                    continue
                return self._outcome(
                    canonical, key, canonical_params, redacted, public_url,
                    None, (credential_value,), None, "", {}, b"", requested_at,
                    received_at, attempts, state,
                    tuple(retry_log), detail,
                )
            try:
                received_at = _observed_iso(observed_clock)
            except ClockRegressionError:
                state = PayloadState(STATE_CLOCK_REGRESSION, STATE_CLOCK_REGRESSION)
                content_type = normalize_media_type(response.content_type)
                return self._outcome(
                    canonical,
                    key,
                    canonical_params,
                    redacted,
                    public_url,
                    None,
                    (credential_value,),
                    response.status,
                    content_type,
                    response.redacted_headers(secret_values=(credential_value,)),
                    response.body,
                    requested_at,
                    STATE_CLOCK_REGRESSION,
                    attempts,
                    state,
                    tuple(retry_log),
                    None,
                )
            observed_final_url = _observed_final_url(
                response.final_url,
                intended_public_url=public_url,
                credential_value=credential_value,
            )
            if require_observed_final_url and observed_final_url is None:
                raise TransportProvenanceError(
                    "observed final destination is required for registered ingestion"
                )
            content_type = normalize_media_type(response.content_type)
            state = classify_payload(
                content_type,
                response.body,
                http_status=response.status,
                declared_length=response.declared_length,
            )
            if policy.may_retry(canonical, state.state, response.status, attempts):
                backoff = policy.backoff_for(attempts)
                retry_log.append(
                    RetryEvent(
                        attempts, state.state, response.status, state.detail, backoff, received_at
                    )
                )
                self._sleep(backoff)
                continue
            return self._outcome(
                canonical, key, canonical_params, redacted, public_url,
                observed_final_url,
                (credential_value,),
                response.status,
                content_type,
                response.redacted_headers(secret_values=(credential_value,)),
                response.body, requested_at, received_at, attempts, state,
                tuple(retry_log), None,
            )

    def _outcome(
        self,
        endpoint: str,
        key: str,
        canonical_params: tuple[tuple[str, str], ...],
        redacted: Mapping[str, str],
        public_url: str,
        observed_final_url: str | None,
        redaction_secrets: tuple[str, ...],
        http_status: int | None,
        content_type: str,
        headers: Mapping[str, str],
        body: bytes,
        requested_at: str,
        received_at: str,
        attempts: int,
        state: PayloadState,
        retry_log: tuple[RetryEvent, ...],
        transport_failure: str | None,
    ) -> FetchOutcome:
        _validate_closed_provider_authority()
        return FetchOutcome(
            endpoint=endpoint,
            request_key=key,
            parameters_sha256=parameters_hash_from_pairs(canonical_params),
            provider_id=PROVIDER_ID,
            provider_version=PROVIDER_VERSION,
            canonical_parameters=canonical_params,
            parameters_redacted=dict(redacted),
            public_url=public_url,
            observed_final_url=observed_final_url,
            redaction_secrets=redaction_secrets,
            http_status=http_status,
            content_type=content_type,
            http_headers=dict(headers),
            body=body,
            sha256=hashlib.sha256(body).hexdigest(),
            byte_length=len(body),
            requested_at=requested_at,
            received_at=received_at,
            attempts=attempts,
            payload_state=state,
            retry_log=retry_log,
            transport_failure=transport_failure,
        )


def non_data_states(outcomes: Iterable[FetchOutcome]) -> tuple[str, ...]:
    """The typed non-data states present in ``outcomes``, sorted and de-duplicated."""
    return tuple(sorted({o.payload_state.state for o in outcomes if not o.is_data}))
