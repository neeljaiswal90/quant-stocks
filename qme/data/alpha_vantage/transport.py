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

from qme.data.alpha_vantage.client import (
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
    wanted = {name.lower() for name in RECORDED_HEADERS}
    return {
        str(key): str(value) for key, value in headers.items() if str(key).lower() in wanted
    }


def _declared_length(headers: Mapping[str, str]) -> int | None:
    for key, value in headers.items():
        if str(key).lower() == "content-length":
            try:
                return int(str(value).strip())
            except (TypeError, ValueError):
                return None
    return None


def make_urllib_transport(*, user_agent: str = DEFAULT_USER_AGENT) -> Transport:
    """Build a stdlib transport. The returned callable is the network egress point."""

    def _transport(url: str, timeout: float) -> TransportResponse:
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                headers = dict(response.headers.items())
                body = response.read()
                return TransportResponse(
                    status=int(response.status),
                    content_type=str(response.headers.get("Content-Type", "")),
                    body=body,
                    headers=_selected_headers(headers),
                    declared_length=_declared_length(headers),
                    final_url=_redacted(str(response.geturl())),
                )
        except urllib.error.HTTPError as exc:
            headers = dict(exc.headers.items()) if exc.headers is not None else {}
            body = exc.read()
            return TransportResponse(
                status=int(exc.code),
                content_type=str(headers.get("Content-Type", "")),
                body=body,
                headers=_selected_headers(headers),
                declared_length=_declared_length(headers),
                final_url=_redacted(url),
            )
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
