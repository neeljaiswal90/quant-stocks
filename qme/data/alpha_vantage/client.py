"""Paced, fail-closed Alpha Vantage HTTP client (stdlib only; no network in tests).

Design rules (from IMPLEMENTATION_PLAN non-negotiables and the 2026-08-07 audit):

* Alpha Vantage returns business errors and throttles as **HTTP 200** with a
  JSON body carrying ``Note``, ``Information``, or ``Error Message``. Every body
  is classified before it is trusted; ``classify_body`` is the single source of
  truth for that classification.
* Requests are **paced evenly** (default one per second, i.e. 60/min — under the
  75/min premium ceiling and spread out enough to avoid "burst pattern"
  responses) rather than fired in bursts.
* Retries are bounded, use exponential backoff, and happen **only** for
  idempotent-safe conditions: transport errors, HTTP 5xx/429, and the ``Note``
  throttle body. ``Information`` and ``Error Message`` are business outcomes
  and are returned to the caller unchanged, never retried into oblivion.
* The API key never appears in any recorded URL, exception text, or log line.
  ``RawResponse.public_url`` is built without it.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

BASE_URL = "https://www.alphavantage.co/query"
API_KEY_ENV = "ALPHA_VANTAGE_API_KEY"  # pragma: allowlist secret (env var name, not a value)
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MIN_INTERVAL_SECONDS = 1.0
DEFAULT_MAX_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = (2.0, 5.0, 12.0)

# Response classes returned by classify_body.
CLASS_OK = "OK"
CLASS_THROTTLE = "SOFT_ERROR_NOTE"  # per-minute/burst throttle -> retryable
CLASS_INFORMATION = "SOFT_ERROR_INFORMATION"  # tier/limit/entitlement message -> not retryable
CLASS_ERROR_MESSAGE = "SOFT_ERROR_ERROR_MESSAGE"  # invalid call/symbol -> not retryable
CLASS_MALFORMED = "MALFORMED"
CLASS_HTTP_ERROR = "HTTP_ERROR"

_SOFT_KEYS = {
    "Note": CLASS_THROTTLE,
    "Information": CLASS_INFORMATION,
    "Error Message": CLASS_ERROR_MESSAGE,
}
_CSV_HEADER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ]*(,[A-Za-z_][A-Za-z0-9_ ]*)+\r?$")


class AlphaVantageError(ValueError):
    """Raised for client-side misuse or an exhausted retry budget. Never carries the key."""


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


def classify_body(content_type: str, body: bytes) -> tuple[str, str | None]:
    """Classify a 200 response body. Returns (response_class, soft_message)."""
    ct = content_type.lower()
    if "json" in ct or body[:1] in (b"{", b"["):
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return CLASS_MALFORMED, "body is not valid JSON"
        if not isinstance(document, dict):
            return CLASS_MALFORMED, "JSON root is not an object"
        for key, klass in _SOFT_KEYS.items():
            if key in document:
                return klass, str(document[key])[:500]
        if not document:
            return CLASS_MALFORMED, "empty JSON object"
        return CLASS_OK, None
    if "csv" in ct or "text/plain" in ct or body[:1].isalpha():
        try:
            head = body.split(b"\n", 1)[0].decode("utf-8")
        except UnicodeDecodeError:
            return CLASS_MALFORMED, "CSV header is not UTF-8"
        if _CSV_HEADER_RE.match(head):
            return CLASS_OK, None
        # A soft error can also arrive as a plain-text/JSON body on a CSV endpoint.
        text = body[:2000].decode("utf-8", errors="replace")
        for key, klass in _SOFT_KEYS.items():
            if key in text:
                return klass, text[:500]
        return CLASS_MALFORMED, "CSV header does not look like a header row"
    return CLASS_MALFORMED, f"unrecognized content type {content_type!r}"


def load_api_key(
    *,
    environ: Mapping[str, str] | None = None,
    repository_root: Path | None = None,
) -> str:
    """Resolve the key from the environment, then ``<repository_root>/.env``.

    The value is returned to the caller and never logged. A ``.env`` line must
    be exactly ``ALPHA_VANTAGE_API_KEY=<value>``; python-dotenv is deliberately
    not used (it is not in the runtime lock).
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


Transport = Callable[[str, float], tuple[int, str, bytes]]
"""(url, timeout) -> (http_status, content_type, body). Injected in tests."""


def _urllib_transport(url: str, timeout: float) -> tuple[int, str, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "qme-av-ingest/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return (
                int(response.status),
                str(response.headers.get("Content-Type", "")),
                response.read(),
            )
    except urllib.error.HTTPError as exc:
        return int(exc.code), str(exc.headers.get("Content-Type", "")), exc.read()


class AlphaVantageClient:
    """Paced GET client.

    ``get`` returns a classified ``RawResponse`` for anything the server actually
    answered — including throttles and HTTP errors on the final attempt, so the
    caller can *record* what happened. It raises ``AlphaVantageError`` only for
    client misuse or when every attempt failed at the transport layer.
    """

    def __init__(
        self,
        api_key: str,
        *,
        transport: Transport | None = None,
        pacer: Pacer | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key or not api_key.strip():
            raise AlphaVantageError("api_key must be non-empty")
        if max_attempts < 1:
            raise AlphaVantageError("max_attempts must be >= 1")
        self._api_key = api_key.strip()
        self._transport = transport or _urllib_transport
        self._pacer = pacer or Pacer()
        self._max_attempts = max_attempts
        self._timeout = timeout_seconds
        self._sleep = sleep

    # -- URL construction ---------------------------------------------------

    @staticmethod
    def public_params(function: str, params: Mapping[str, str]) -> dict[str, str]:
        if "apikey" in params:
            raise AlphaVantageError("do not pass 'apikey' in params; the client injects it")
        merged = {"function": function, **{k: str(v) for k, v in params.items()}}
        return dict(sorted(merged.items()))

    @staticmethod
    def public_url(function: str, params: Mapping[str, str]) -> str:
        return BASE_URL + "?" + urllib.parse.urlencode(
            AlphaVantageClient.public_params(function, params)
        )

    def _private_url(self, function: str, params: Mapping[str, str]) -> str:
        public = AlphaVantageClient.public_params(function, params)
        return BASE_URL + "?" + urllib.parse.urlencode({**public, "apikey": self._api_key})

    # -- Request ------------------------------------------------------------

    def get(self, function: str, **params: str) -> RawResponse:
        public = self.public_params(function, params)
        public_url = self.public_url(function, params)
        private_url = self._private_url(function, params)
        attempts = 0
        last_reason = "no attempt made"
        while attempts < self._max_attempts:
            attempts += 1
            self._pacer.wait()
            requested_at = _now_iso()
            try:
                status, content_type, body = self._transport(private_url, self._timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_reason = f"transport error: {type(exc).__name__}"
                self._backoff(attempts)
                continue
            received_at = _now_iso()

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
