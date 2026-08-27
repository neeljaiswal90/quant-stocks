"""The only Alpha Vantage module that opens a network connection.

Keeping ``urllib.request`` in exactly one small module makes the acquisition
boundary a **module edge** rather than a comment: research code can be asserted
never to reach this module, and the assertion is enforced by
``tests/architecture/test_import_boundaries.py``.

Nothing here interprets a payload — classification lives in
:mod:`qme.data.alpha_vantage.client`. Nothing here logs: the URL handed to
``urllib`` carries the credential, so it is never printed, never stored, and
never attached to an exception (:func:`_redacted` is used for every message).
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import cast

from qme.data.alpha_vantage.client import (
    MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES,
    ResponseBodyLimitError,
    ResponseHeaderError,
    Transport,
    TransportError,
    TransportResponse,
    TransportTimeoutError,
    redact_url,
)

DEFAULT_USER_AGENT = "qme-av-ingest/0.2"

#: Response headers worth keeping as HTTP metadata on a stored pull. Everything
#: else is dropped so no cookie or auth echo can reach an artifact.
RECORDED_HEADERS: tuple[str, ...] = (
    "Content-Type",
    "Content-Length",
    "Date",
    "Server",
    "Transfer-Encoding",
    "Content-Encoding",
    "Retry-After",
)


def _redacted(url: str) -> str:
    return redact_url(url)


def _selected_headers(headers: Mapping[str, str]) -> dict[str, str]:
    selected: dict[str, str] = {}
    for name in RECORDED_HEADERS:
        value = headers.get(name)
        if value is None:
            value = headers.get(name.lower())
        if value is not None:
            selected[name] = str(value)
    return selected


def _declared_length(headers: Mapping[str, str]) -> int | None:
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        values = list(get_all("Content-Length") or ())
    else:
        value = headers.get("Content-Length")
        if value is None:
            value = headers.get("content-length")
        values = [] if value is None else [value]
    if not values:
        return None
    if len(values) != 1:
        raise ResponseHeaderError("INVALID_CONTENT_LENGTH")
    text = str(values[0])
    if not text.isascii() or not text.isdecimal() or (len(text) > 1 and text.startswith("0")):
        raise ResponseHeaderError("INVALID_CONTENT_LENGTH")
    declared = int(text)
    if declared > MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES:
        raise ResponseBodyLimitError("RESPONSE_BODY_LIMIT_EXCEEDED")
    return declared


def _read_bounded(stream: object, *, declared_length: int | None) -> bytes:
    if (
        declared_length is not None
        and declared_length > MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES
    ):
        raise ResponseBodyLimitError("RESPONSE_BODY_LIMIT_EXCEEDED")
    read = getattr(stream, "read", None)
    if not callable(read):
        raise TransportError("TRANSPORT_STREAM_INVALID")
    remaining = MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES + 1
    chunks: list[bytes] = []
    while remaining:
        requested = min(64 * 1024, remaining)
        chunk = read(requested)
        if not isinstance(chunk, bytes):
            raise TransportError("TRANSPORT_STREAM_INVALID")
        if not chunk:
            break
        if len(chunk) > requested:
            chunk = chunk[:remaining]
        chunks.append(chunk)
        remaining -= len(chunk)
    body = b"".join(chunks)
    if len(body) > MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES:
        raise ResponseBodyLimitError("RESPONSE_BODY_LIMIT_EXCEEDED")
    return body


def make_urllib_transport(*, user_agent: str = DEFAULT_USER_AGENT) -> Transport:
    """Build a stdlib transport. The returned callable is the network egress point."""

    def _transport(url: str, timeout: float) -> TransportResponse:
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                headers = _selected_headers(response.headers)
                declared_length = _declared_length(response.headers)
                body = _read_bounded(response, declared_length=declared_length)
                return TransportResponse(
                    status=int(response.status),
                    content_type=headers.get("Content-Type", ""),
                    body=body,
                    headers=headers,
                    declared_length=declared_length,
                    final_url=_redacted(str(response.geturl())),
                )
        except urllib.error.HTTPError as exc:
            source_headers: Mapping[str, str] = (
                {} if exc.headers is None else cast(Mapping[str, str], exc.headers)
            )
            headers = _selected_headers(source_headers)
            declared_length = _declared_length(source_headers)
            body = _read_bounded(exc, declared_length=declared_length)
            return TransportResponse(
                status=int(exc.code),
                content_type=headers.get("Content-Type", ""),
                body=body,
                headers=headers,
                declared_length=declared_length,
                final_url=_redacted(url),
            )
        except (ResponseBodyLimitError, ResponseHeaderError):
            raise
        except TimeoutError as exc:
            raise TransportTimeoutError(
                f"timed out after {timeout}s for {_redacted(url)}: {type(exc).__name__}"
            ) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise TransportTimeoutError(
                    f"timed out after {timeout}s for {_redacted(url)}"
                ) from exc
            raise TransportError(
                f"transport failure for {_redacted(url)}: {type(exc).__name__}"
            ) from exc
        except OSError as exc:
            raise TransportError(
                f"transport failure for {_redacted(url)}: {type(exc).__name__}"
            ) from exc

    return _transport


#: A ready-made transport with the default user agent.
urllib_transport: Transport = make_urllib_transport()
