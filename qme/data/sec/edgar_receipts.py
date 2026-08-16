"""Cross-source SEC EDGAR receipts for the registered corporate-action fixture events.

Scope is exactly the fixture set registered in
``docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md`` §5.1 (taxonomy §5.3).
§5.1 requires, per fixture, **one independent cross-source** — an issuer press
release or SEC filing — hash-bound alongside the Alpha Vantage raw pulls. This
module acquires that second source from EDGAR, stores it immutably, and records
per event whether a receipt was located.

Fair access
-----------

SEC's Fair Access policy is honoured mechanically, not by convention:

* every request declares a contact ``User-Agent``
  (:data:`USER_AGENT`); :class:`EdgarClient` refuses a user agent without a
  contact address, so an anonymous pull cannot be made by accident;
* requests are paced evenly (default one per second — an order of magnitude
  under the published 10 requests/second ceiling) rather than fired in bursts;
* ``403``/``429`` are treated as "you are going too fast": the client backs off
  for at least :data:`MIN_THROTTLE_BACKOFF_SECONDS` and retries at most twice,
  then hands the caller a classified response so the run records
  :data:`STATUS_PULL_UNAVAILABLE` instead of guessing;
* only the documented JSON/Archives endpoints are used —
  ``https://data.sec.gov/submissions/CIK##########.json`` for the filing index,
  ``https://www.sec.gov/Archives/edgar/data/<cik>/<accession>/<document>`` for
  documents, and the Archives ``-index-headers.html`` SGML header of a single
  filing to resolve exhibit types. No HTML search page is scraped.

Storage
-------

    raw/sec_edgar/<10-digit CIK>/<accession-with-dashes>/<document>
    raw/sec_edgar/<10-digit CIK>/<accession-with-dashes>/<document>.meta.json
    raw/sec_edgar/_audit.jsonl                       (append-only, one line per stored document)
    derived/corporate-actions/receipts/<run_id>/receipts-index.json

Bodies are written with ``O_EXCL`` and never overwritten. A document already
stored under the same logical id is re-verified against its recorded sha256 and
**reused without a network request**, so a re-run is idempotent and cheap; a
stored body whose bytes no longer match its sidecar is a fail-closed error.
Logical ids are root-relative — no absolute path ever enters an artifact.

Non-claims
----------

* A ``CORROBORATED`` status means the registered SEC filing was located and its
  bytes are stored and hashed. It does **not** mean anyone has read, reviewed, or
  reconciled the document against the pack: ``cross_source_receipts_reviewed`` is
  ``false`` in every index this module writes.
* Nothing here builds a golden oracle fixture, records an independent review, or
  changes a freeze blocker.
* Accession numbers are never constructed or guessed. A target whose filing
  cannot be identified from the submissions index by (form type, filing-date
  window, 8-K item) is recorded as ``RECEIPT_NOT_LOCATED`` together with the
  candidate rows that were seen.
* Quoted sentences are extracted mechanically from the stored bytes and are
  reading aids bound to a sha256 — they are not a parsed, typed reading of the
  filing, and the pack corrections they bear on are reported, never applied.
"""

from __future__ import annotations

import contextlib
import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from qme.data.alpha_vantage.client import Pacer
from qme.foundation.data_root import DataRootLayout

RECEIPTS_SCHEMA_VERSION = "qme.sec_cross_source_receipts.v1"
DOCUMENT_SCHEMA_VERSION = "qme.sec_edgar_document.v1"
RUN_KIND = "sec-receipts"
SOURCE_ID = "sec_edgar"

#: SEC Fair Access requires a declared contact. Form: ``<tool>/<version> (<email>)``.
USER_AGENT = "qme-research/0.1 (neeljaiswal90@gmail.com)"

SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

DEFAULT_MIN_INTERVAL_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_ATTEMPTS = 3
#: Backoff floor after a 403/429. SEC asks for a pause, not a tighter loop.
MIN_THROTTLE_BACKOFF_SECONDS = 10.0
THROTTLE_BACKOFF_SECONDS: tuple[float, ...] = (10.0, 20.0)
#: Refuse to store anything larger than this; an unexpectedly huge body is a bug, not evidence.
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024

CLASS_OK = "OK"
CLASS_THROTTLED = "THROTTLED"
CLASS_HTTP_ERROR = "HTTP_ERROR"
CLASS_TRANSPORT_ERROR = "TRANSPORT_ERROR"
CLASS_OVERSIZE = "OVERSIZE"

STATUS_CORROBORATED = "CORROBORATED"
STATUS_PARTIAL = "PARTIAL"
STATUS_NOT_LOCATED = "RECEIPT_NOT_LOCATED"
STATUS_PULL_UNAVAILABLE = "PULL_UNAVAILABLE"

STATUS_ORDER: tuple[str, ...] = (
    STATUS_CORROBORATED,
    STATUS_PARTIAL,
    STATUS_NOT_LOCATED,
    STATUS_PULL_UNAVAILABLE,
)

PACK_REFERENCE = "docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md#5.1"
PACK_TAXONOMY_REFERENCE = "docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md#5.3"

#: Days either side of a target window kept when reporting the candidates that were seen.
CANDIDATE_WINDOW_DAYS = 45
MAX_CANDIDATES_REPORTED = 12

_CIK_RE = re.compile(r"^\d{1,10}$")
_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_USER_AGENT_RE = re.compile(r"^\S+/\S+\s+\(\S+@\S+\)$")


class EdgarError(ValueError):
    """Client-side misuse, an unsafe path, or a body that is not what EDGAR documents."""


class EdgarSchemaError(EdgarError):
    """A submissions or filing-index body does not have its documented shape."""


class EdgarUnavailableError(EdgarError):
    """EDGAR did not serve a required index after the bounded retry budget.

    The caller records :data:`STATUS_PULL_UNAVAILABLE`; it never substitutes a
    cached, guessed, or partial index.
    """


class SecReceiptStoreError(ValueError):
    """Raised on any attempt to violate immutability or the path contract."""


# ---------------------------------------------------------------------------
# URLs and identifiers
# ---------------------------------------------------------------------------


def normalize_cik(value: str | int) -> str:
    """Return the 10-digit zero-padded CIK EDGAR keys submissions by."""

    text = str(value).strip().upper()
    if text.startswith("CIK"):
        text = text[3:]
    if not _CIK_RE.match(text):
        raise EdgarError(f"{value!r} is not a CIK")
    return text.zfill(10)


def _numeric_cik(cik: str) -> str:
    """Archives paths use the unpadded CIK."""

    return normalize_cik(cik).lstrip("0") or "0"


def _validated_accession(accession_number: str) -> str:
    text = accession_number.strip()
    if not _ACCESSION_RE.match(text):
        raise EdgarError(f"{accession_number!r} is not an accession number (##########-##-######)")
    return text


def accession_no_dashes(accession_number: str) -> str:
    return _validated_accession(accession_number).replace("-", "")


def _validated_document_name(document_name: str) -> str:
    text = document_name.strip().replace("\\", "/")
    if not text or text.startswith("/") or text.endswith("/"):
        raise EdgarError(f"{document_name!r} is not a relative document name")
    segments = text.split("/")
    if len(segments) > 3:
        raise EdgarError(f"{document_name!r} is nested too deeply for an EDGAR document")
    for segment in segments:
        if not _SAFE_SEGMENT_RE.match(segment):
            raise EdgarError(f"{document_name!r} contains an unsafe path segment {segment!r}")
    return text


def submissions_url(cik: str | int) -> str:
    return f"{SUBMISSIONS_BASE}/CIK{normalize_cik(cik)}.json"


def archive_submissions_url(name: str) -> str:
    """URL of an older submissions page named by ``filings.files[].name``."""

    if not _SAFE_SEGMENT_RE.match(name) or not name.endswith(".json"):
        raise EdgarError(f"{name!r} is not a submissions archive file name")
    return f"{SUBMISSIONS_BASE}/{name}"


def document_url(cik: str | int, accession_number: str, document_name: str) -> str:
    return (
        f"{ARCHIVES_BASE}/{_numeric_cik(str(cik))}/"
        f"{accession_no_dashes(accession_number)}/{_validated_document_name(document_name)}"
    )


def filing_index_url(cik: str | int, accession_number: str) -> str:
    """SGML header page of one filing; it maps every document to its EDGAR ``TYPE``."""

    accession = _validated_accession(accession_number)
    return (
        f"{ARCHIVES_BASE}/{_numeric_cik(str(cik))}/"
        f"{accession_no_dashes(accession)}/{accession}-index-headers.html"
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EdgarResponse:
    """One HTTP exchange, exactly as received. ``body`` is the untouched payload."""

    url: str
    http_status: int
    content_type: str
    body: bytes
    requested_at: str
    received_at: str
    attempts: int
    response_class: str
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.response_class == CLASS_OK


Transport = Callable[[str, str, float], tuple[int, str, bytes]]
"""(url, user_agent, timeout) -> (http_status, content_type, body). Injected in tests."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _urllib_transport(url: str, user_agent: str, timeout: float) -> tuple[int, str, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return (
                int(response.status),
                str(response.headers.get("Content-Type", "")),
                response.read(),
            )
    except urllib.error.HTTPError as exc:
        return int(exc.code), str(exc.headers.get("Content-Type", "")), exc.read()


class EdgarClient:
    """Paced GET client for EDGAR that always declares a contact user agent.

    ``get`` never raises for something the server actually answered: a throttle,
    an HTTP error, or an exhausted transport budget comes back as a classified
    :class:`EdgarResponse` so the caller can *record* what happened rather than
    infer a filing. It raises :class:`EdgarError` only for client misuse.
    """

    def __init__(
        self,
        *,
        user_agent: str = USER_AGENT,
        transport: Transport | None = None,
        pacer: Pacer | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        max_bytes: int = MAX_DOCUMENT_BYTES,
    ) -> None:
        if not _USER_AGENT_RE.match(user_agent.strip()):
            raise EdgarError(
                "SEC fair access requires a user agent of the form "
                "'<tool>/<version> (<contact email>)'"
            )
        if max_attempts < 1:
            raise EdgarError("max_attempts must be >= 1")
        if max_bytes < 1:
            raise EdgarError("max_bytes must be >= 1")
        self._user_agent = user_agent.strip()
        self._transport = transport or _urllib_transport
        self._pacer = pacer or Pacer(min_interval_seconds=DEFAULT_MIN_INTERVAL_SECONDS)
        self._max_attempts = max_attempts
        self._timeout = timeout_seconds
        self._sleep = sleep
        self._max_bytes = max_bytes
        self._requests_made = 0

    @property
    def user_agent(self) -> str:
        return self._user_agent

    @property
    def requests_made(self) -> int:
        """Every send that reached the transport, retries included."""

        return self._requests_made

    def get(self, url: str) -> EdgarResponse:
        if not url.startswith("https://"):
            raise EdgarError(f"refusing a non-https EDGAR url: {url!r}")
        attempts = 0
        detail = "no attempt made"
        while attempts < self._max_attempts:
            attempts += 1
            self._pacer.wait()
            requested_at = _now_iso()
            self._requests_made += 1
            try:
                status, content_type, body = self._transport(url, self._user_agent, self._timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                detail = f"transport error: {type(exc).__name__}"
                if attempts < self._max_attempts:
                    self._backoff(attempts)
                    continue
                return EdgarResponse(
                    url, 0, "", b"", requested_at, _now_iso(),
                    attempts, CLASS_TRANSPORT_ERROR, detail,
                )
            received_at = _now_iso()

            if status == 200:
                if len(body) > self._max_bytes:
                    return EdgarResponse(
                        url, status, content_type, b"", requested_at, received_at,
                        attempts, CLASS_OVERSIZE,
                        f"body is {len(body)} bytes, over the {self._max_bytes} byte ceiling",
                    )
                return EdgarResponse(
                    url, status, content_type, body, requested_at, received_at,
                    attempts, CLASS_OK, None,
                )

            retryable = status in (403, 429) or 500 <= status < 600
            klass = CLASS_THROTTLED if status in (403, 429) else CLASS_HTTP_ERROR
            detail = f"HTTP {status}"
            if retryable and attempts < self._max_attempts:
                self._backoff(attempts)
                continue
            return EdgarResponse(
                url, status, content_type, body, requested_at, received_at,
                attempts, klass, detail,
            )
        raise EdgarError(f"retry loop ended without a response for {url}: {detail}")

    def _backoff(self, attempt_number: int) -> None:
        index = min(attempt_number - 1, len(THROTTLE_BACKOFF_SECONDS) - 1)
        self._sleep(max(THROTTLE_BACKOFF_SECONDS[index], MIN_THROTTLE_BACKOFF_SECONDS))


# ---------------------------------------------------------------------------
# Submissions index
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilingRow:
    """One row of the EDGAR submissions index, as EDGAR serves it."""

    cik: str
    form: str
    filing_date: str
    accession_number: str
    primary_document: str
    accepted_at: str | None
    report_date: str | None
    items: tuple[str, ...]
    primary_doc_description: str | None

    def to_json_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["items"] = list(self.items)
        return payload


@dataclass(frozen=True)
class ArchiveFile:
    """An older submissions page referenced by ``filings.files``."""

    name: str
    filing_from: str
    filing_to: str

    def covers(self, window: tuple[str, str]) -> bool:
        return self.filing_from <= window[1] and self.filing_to >= window[0]


_COLUMNS = (
    "accessionNumber",
    "filingDate",
    "form",
    "primaryDocument",
)


def _column(block: dict[str, Any], name: str, rows: int) -> list[Any]:
    value = block.get(name)
    if value is None:
        return [None] * rows
    if not isinstance(value, list) or len(value) != rows:
        raise EdgarSchemaError(f"submissions column {name!r} is not a list of {rows} values")
    return list(value)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_submissions(body: bytes, *, cik: str) -> tuple[tuple[FilingRow, ...], tuple[ArchiveFile, ...]]:
    """Parse a submissions body into rows plus the archive pages it points at.

    Accepts both shapes EDGAR serves: the primary ``CIK##########.json``
    (``filings.recent`` plus ``filings.files``) and an older
    ``CIK##########-submissions-NNN.json`` page, which is the bare columnar block.
    """

    padded = normalize_cik(cik)
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EdgarSchemaError(f"submissions body is not valid JSON: {type(exc).__name__}") from exc
    if not isinstance(document, dict):
        raise EdgarSchemaError("submissions JSON root is not an object")

    archives: tuple[ArchiveFile, ...] = ()
    filings = document.get("filings")
    if isinstance(filings, dict):
        block = filings.get("recent")
        if not isinstance(block, dict):
            raise EdgarSchemaError("submissions filings.recent is not an object")
        archives = _parse_archive_files(filings.get("files"))
    else:
        block = document

    for name in _COLUMNS:
        if not isinstance(block.get(name), list):
            raise EdgarSchemaError(f"submissions block has no {name!r} column")
    rows = len(block["accessionNumber"])
    accessions = _column(block, "accessionNumber", rows)
    forms = _column(block, "form", rows)
    dates = _column(block, "filingDate", rows)
    documents = _column(block, "primaryDocument", rows)
    accepted = _column(block, "acceptanceDateTime", rows)
    reports = _column(block, "reportDate", rows)
    items = _column(block, "items", rows)
    descriptions = _column(block, "primaryDocDescription", rows)

    parsed: list[FilingRow] = []
    for index in range(rows):
        filing_date = str(dates[index] or "").strip()
        if not _DATE_RE.match(filing_date):
            raise EdgarSchemaError(f"submissions row {index} has filingDate {dates[index]!r}")
        raw_items = str(items[index] or "")
        parsed.append(
            FilingRow(
                cik=padded,
                form=str(forms[index] or "").strip(),
                filing_date=filing_date,
                accession_number=str(accessions[index] or "").strip(),
                primary_document=str(documents[index] or "").strip(),
                accepted_at=_text(accepted[index]),
                report_date=_text(reports[index]),
                items=tuple(part.strip() for part in raw_items.split(",") if part.strip()),
                primary_doc_description=_text(descriptions[index]),
            )
        )
    return tuple(parsed), archives


def _parse_archive_files(value: Any) -> tuple[ArchiveFile, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise EdgarSchemaError("submissions filings.files is not a list")
    out: list[ArchiveFile] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise EdgarSchemaError("submissions filings.files entry is not an object")
        name = str(entry.get("name", "")).strip()
        start = str(entry.get("filingFrom", "")).strip()
        end = str(entry.get("filingTo", "")).strip()
        if not name or not _DATE_RE.match(start) or not _DATE_RE.match(end):
            raise EdgarSchemaError(f"submissions filings.files entry {name!r} is malformed")
        out.append(ArchiveFile(name=name, filing_from=start, filing_to=end))
    return tuple(out)


def list_filings(
    client: EdgarClient,
    cik: str | int,
    *,
    date_window: tuple[str, str] | None = None,
) -> tuple[FilingRow, ...]:
    """Return the submissions rows for ``cik``.

    ``filings.recent`` holds the most recent ~1000 filings only. When
    ``date_window`` reaches further back, the archive pages whose declared range
    overlaps the window are fetched as well — and only those, so an old event
    costs one extra request rather than the issuer's whole history.
    """

    padded = normalize_cik(cik)
    window = _validated_window(date_window) if date_window else None
    response = client.get(submissions_url(padded))
    if not response.ok:
        raise EdgarUnavailableError(
            f"submissions index for CIK {padded} unavailable: "
            f"{response.response_class} {response.detail or ''}".strip()
        )
    rows, archives = parse_submissions(response.body, cik=padded)
    if window is None:
        return rows
    collected = list(rows)
    for archive in archives:
        if not archive.covers(window):
            continue
        page = client.get(archive_submissions_url(archive.name))
        if not page.ok:
            raise EdgarUnavailableError(
                f"submissions archive {archive.name} unavailable: "
                f"{page.response_class} {page.detail or ''}".strip()
            )
        older, _ = parse_submissions(page.body, cik=padded)
        collected.extend(older)
    return tuple(collected)


def _validated_window(window: tuple[str, str]) -> tuple[str, str]:
    start, end = window
    if not _DATE_RE.match(start) or not _DATE_RE.match(end):
        raise EdgarError("date_window bounds must be YYYY-MM-DD")
    if start > end:
        raise EdgarError("date_window start must not be after its end")
    return start, end


def select_filings(
    rows: Sequence[FilingRow],
    form_types: Sequence[str],
    date_window: tuple[str, str],
    *,
    items: Sequence[str] = (),
    limit: int | None = None,
) -> tuple[FilingRow, ...]:
    """Rows whose form, inclusive filing-date window, and 8-K items all match.

    ``items`` is an any-of filter over the row's ``items`` list (an 8-K carries
    several); an empty ``items`` applies no item filter. The result is ordered by
    ``(filing_date, accession_number)`` so selection is deterministic regardless
    of the order EDGAR served.
    """

    start, end = _validated_window(date_window)
    wanted_forms = {form.strip().upper() for form in form_types}
    wanted_items = {item.strip() for item in items if item.strip()}
    matched = [
        row
        for row in rows
        if row.form.upper() in wanted_forms
        and start <= row.filing_date <= end
        and (not wanted_items or wanted_items & set(row.items))
    ]
    matched.sort(key=lambda row: (row.filing_date, row.accession_number))
    if limit is not None:
        if limit < 0:
            raise EdgarError("limit must not be negative")
        matched = matched[:limit]
    return tuple(matched)


# ---------------------------------------------------------------------------
# Filing document index (exhibit resolution)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilingDocument:
    """One document inside a filing, with the EDGAR ``TYPE`` that names it."""

    document_type: str
    sequence: str
    filename: str
    description: str | None


_DOCUMENT_BLOCK_RE = re.compile(
    r"&lt;DOCUMENT&gt;\s*"
    r"&lt;TYPE&gt;(?P<type>[^\r\n]*)\s*"
    r"&lt;SEQUENCE&gt;(?P<sequence>[^\r\n]*)\s*"
    r"&lt;FILENAME&gt;(?P<filename>[^\r\n]*)\s*"
    r"(?:&lt;DESCRIPTION&gt;(?P<description>[^\r\n]*)\s*)?"
)


def parse_filing_documents(body: bytes) -> tuple[FilingDocument, ...]:
    """Parse the Archives ``-index-headers.html`` SGML header of one filing.

    The header lists ``<DOCUMENT><TYPE>…<SEQUENCE>…<FILENAME>…`` for every
    document, HTML-escaped inside a ``<PRE>`` block. That mapping is the only
    place EDGAR publishes an exhibit's *type* in machine-readable form —
    ``index.json`` carries icon names, not types.
    """

    text = body.decode("utf-8", errors="replace")
    out: list[FilingDocument] = []
    for match in _DOCUMENT_BLOCK_RE.finditer(text):
        filename = html.unescape(match.group("filename") or "").strip()
        if not filename:
            continue
        description = html.unescape(match.group("description") or "").strip()
        out.append(
            FilingDocument(
                document_type=html.unescape(match.group("type") or "").strip(),
                sequence=html.unescape(match.group("sequence") or "").strip(),
                filename=filename,
                description=description or None,
            )
        )
    if not out:
        raise EdgarSchemaError("filing index header lists no documents")
    return tuple(out)


def list_filing_documents(
    client: EdgarClient,
    cik: str | int,
    accession_number: str,
) -> tuple[FilingDocument, ...]:
    response = client.get(filing_index_url(cik, accession_number))
    if not response.ok:
        raise EdgarUnavailableError(
            f"filing index for {accession_number} unavailable: "
            f"{response.response_class} {response.detail or ''}".strip()
        )
    return parse_filing_documents(response.body)


def select_documents(
    documents: Sequence[FilingDocument],
    document_types: Sequence[str],
) -> tuple[FilingDocument, ...]:
    """Documents whose EDGAR ``TYPE`` matches, in filing sequence order."""

    wanted = {value.strip().upper() for value in document_types}
    matched = [doc for doc in documents if doc.document_type.upper() in wanted]
    matched.sort(key=lambda doc: (len(doc.sequence), doc.sequence, doc.filename))
    return tuple(matched)


# ---------------------------------------------------------------------------
# Immutable document store
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecDocumentRecord:
    """Everything needed to cite one stored EDGAR document, with no absolute paths."""

    schema_version: str
    source_id: str
    cik: str
    accession_number: str
    document_name: str
    document_url: str
    http_status: int
    content_type: str
    byte_length: int
    sha256: str
    retrieved_at: str
    stored_at: str
    attempts: int
    body_logical_id: str
    meta_logical_id: str

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        handle_fd = os.open(path, flags, 0o644)
    except FileExistsError as exc:
        raise SecReceiptStoreError(f"refusing to overwrite existing artifact: {path.name}") from exc
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()
        raise


class SecReceiptStore:
    """Append-only EDGAR document storage bound to a validated data root."""

    def __init__(self, layout: DataRootLayout) -> None:
        if type(layout) is not DataRootLayout:
            raise SecReceiptStoreError("layout must be a DataRootLayout")
        self._layout = layout
        self._base = layout.raw / SOURCE_ID
        self._audit_path = self._base / "_audit.jsonl"

    @property
    def base_directory(self) -> Path:
        return self._base

    @property
    def audit_path(self) -> Path:
        return self._audit_path

    def _body_path(self, cik: str, accession_number: str, document_name: str) -> Path:
        padded = normalize_cik(cik)
        accession = _validated_accession(accession_number)
        relative = _validated_document_name(document_name)
        return self._base.joinpath(padded, accession, *relative.split("/"))

    def existing(
        self,
        *,
        cik: str,
        accession_number: str,
        document_name: str,
    ) -> SecDocumentRecord | None:
        """Return the record of an already-stored document, or ``None``.

        The stored bytes are re-hashed against the sidecar, so a reused document
        is verified rather than trusted.
        """

        body_path = self._body_path(cik, accession_number, document_name)
        meta_path = body_path.with_name(body_path.name + ".meta.json")
        if not body_path.is_file() or not meta_path.is_file():
            return None
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise SecReceiptStoreError(f"meta sidecar is not an object: {meta_path.name}")
        record = SecDocumentRecord(**payload)
        if hashlib.sha256(body_path.read_bytes()).hexdigest() != record.sha256:
            raise SecReceiptStoreError(
                f"stored body no longer matches its recorded sha256: {record.body_logical_id}"
            )
        return record

    def record(
        self,
        response: EdgarResponse,
        *,
        cik: str,
        accession_number: str,
        document_name: str,
        now: datetime | None = None,
    ) -> SecDocumentRecord:
        if not response.ok:
            raise SecReceiptStoreError(
                f"refusing to store a {response.response_class} response for {document_name}"
            )
        stored_at = now or datetime.now(UTC)
        if stored_at.tzinfo is None:
            raise SecReceiptStoreError("now must be timezone-aware")
        padded = normalize_cik(cik)
        accession = _validated_accession(accession_number)
        relative = _validated_document_name(document_name)

        body_path = self._body_path(padded, accession, relative)
        meta_path = body_path.with_name(body_path.name + ".meta.json")
        body_path.parent.mkdir(parents=True, exist_ok=True)

        document = SecDocumentRecord(
            schema_version=DOCUMENT_SCHEMA_VERSION,
            source_id=SOURCE_ID,
            cik=padded,
            accession_number=accession,
            document_name=relative,
            document_url=response.url,
            http_status=response.http_status,
            content_type=response.content_type,
            byte_length=len(response.body),
            sha256=hashlib.sha256(response.body).hexdigest(),
            retrieved_at=response.received_at,
            stored_at=stored_at.astimezone(UTC).isoformat(timespec="microseconds"),
            attempts=response.attempts,
            body_logical_id=self._layout.logical_artifact_id(body_path),
            meta_logical_id=self._layout.logical_artifact_id(meta_path),
        )
        meta_bytes = (
            json.dumps(document.to_json_dict(), sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")

        _write_exclusive(body_path, response.body)
        try:
            _write_exclusive(meta_path, meta_bytes)
        except BaseException:
            body_path.unlink(missing_ok=True)
            raise
        self._append_audit(document)
        return document

    def _append_audit(self, document: SecDocumentRecord) -> None:
        line = json.dumps(document.to_json_dict(), sort_keys=True, ensure_ascii=False) + "\n"
        self._base.mkdir(parents=True, exist_ok=True)
        with self._audit_path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def read_body(self, record: SecDocumentRecord) -> bytes:
        data = (self._layout.root / record.body_logical_id).read_bytes()
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise SecReceiptStoreError(
                f"stored body no longer matches its recorded sha256: {record.body_logical_id}"
            )
        return data

    def audit_records(self) -> list[dict[str, Any]]:
        if not self._audit_path.is_file():
            return []
        out: list[dict[str, Any]] = []
        for line in self._audit_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    out.append(parsed)
        return out


def fetch_document(
    client: EdgarClient,
    store: SecReceiptStore,
    *,
    cik: str | int,
    accession_number: str,
    document_name: str,
    now: datetime | None = None,
) -> tuple[SecDocumentRecord | None, EdgarResponse | None]:
    """Store one EDGAR document immutably; reuse it verbatim when already stored.

    Returns ``(record, response)``. ``response`` is ``None`` when the document was
    already present (no request was made). ``record`` is ``None`` when EDGAR did
    not serve the document after the bounded retry budget — the caller records
    :data:`STATUS_PULL_UNAVAILABLE` and never invents bytes.
    """

    padded = normalize_cik(cik)
    accession = _validated_accession(accession_number)
    relative = _validated_document_name(document_name)
    cached = store.existing(cik=padded, accession_number=accession, document_name=relative)
    if cached is not None:
        return cached, None
    response = client.get(document_url(padded, accession, relative))
    if not response.ok:
        return None, response
    record = store.record(
        response,
        cik=padded,
        accession_number=accession,
        document_name=relative,
        now=now,
    )
    return record, response


# ---------------------------------------------------------------------------
# Text helpers (reading aids bound to a stored sha256)
# ---------------------------------------------------------------------------

_SCRIPT_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def extract_text(body: bytes) -> str:
    """Collapse an HTML/XML EDGAR document to a single normalized text run.

    Deliberately dumb: strip script/style, strip tags, unescape entities, collapse
    whitespace. Good enough to quote a sentence back to a reviewer, and never used
    to derive a typed value.
    """

    text = body.decode("utf-8", errors="replace")
    text = _SCRIPT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace(" ", " ").replace("’", "'").replace("—", "-")
    return _WHITESPACE_RE.sub(" ", text).strip()


def find_quotes(
    text: str,
    term_groups: Sequence[Sequence[str]],
    *,
    max_quotes: int = 4,
    max_chars: int = 600,
) -> tuple[str, ...]:
    """Sentences containing every term of at least one group, in document order."""

    if not term_groups:
        return ()
    lowered_groups = [
        [term.lower() for term in group if term.strip()] for group in term_groups
    ]
    lowered_groups = [group for group in lowered_groups if group]
    out: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        candidate = sentence.strip()
        if not candidate:
            continue
        haystack = candidate.lower()
        if any(all(term in haystack for term in group) for group in lowered_groups):
            trimmed = candidate[:max_chars]
            if trimmed not in out:
                out.append(trimmed)
            if len(out) >= max_quotes:
                break
    return tuple(out)


# ---------------------------------------------------------------------------
# Registered receipt targets (pack §5.1 / §5.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReceiptTarget:
    """One filing to locate for an event, described by *criteria*, never by accession.

    Criteria are (form types, inclusive filing-date window, optional any-of 8-K
    item filter). If they do not resolve to a filing, the run records
    ``RECEIPT_NOT_LOCATED`` with the candidates it saw.
    """

    target_id: str
    purpose: str
    form_types: tuple[str, ...]
    date_window: tuple[str, str]
    required_items: tuple[str, ...] = ()
    exhibit_types: tuple[str, ...] = ()
    quote_terms: tuple[tuple[str, ...], ...] = ()
    max_filings: int = 1

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "purpose": self.purpose,
            "form_types": list(self.form_types),
            "date_window": list(self.date_window),
            "required_items": list(self.required_items),
            "exhibit_types": list(self.exhibit_types),
            "max_filings": self.max_filings,
        }


@dataclass(frozen=True)
class ReceiptEvent:
    """One registered fixture event and the SEC filings that would corroborate it."""

    event_id: str
    event_class: str
    symbol: str
    cik: str
    company: str
    pack_expectation: str
    targets: tuple[ReceiptTarget, ...]
    note: str | None = None

    @property
    def span(self) -> tuple[str, str]:
        starts = [target.date_window[0] for target in self.targets]
        ends = [target.date_window[1] for target in self.targets]
        return min(starts), max(ends)


REGISTERED_RECEIPT_EVENTS: tuple[ReceiptEvent, ...] = (
    ReceiptEvent(
        event_id="AAPL-SPLIT-DIVIDEND-2020",
        event_class="ORDINARY_SPLIT_AND_DIVIDEND",
        symbol="AAPL",
        cik="0000320193",
        company="Apple Inc.",
        pack_expectation="AAPL 4:1 split 2020-08-31; AAPL dividend ex 2020-08-07",
        targets=(
            ReceiptTarget(
                target_id="aapl-q3-2020-earnings-8k",
                purpose=(
                    "Q3 FY2020 earnings 8-K; its EX-99.1 press release announces both the "
                    "four-for-one split and the quarterly dividend with their dates"
                ),
                form_types=("8-K",),
                date_window=("2020-07-28", "2020-08-05"),
                required_items=("2.02",),
                exhibit_types=("EX-99.1",),
                quote_terms=(
                    ("four-for-one",),
                    ("split-adjusted",),
                    ("dividend", "payable"),
                ),
            ),
        ),
        note=(
            "The pack cites 'item 8.01'; Apple filed the split announcement inside the "
            "item 2.02 earnings 8-K, so the item filter follows the filing, not the pack."
        ),
    ),
    ReceiptEvent(
        event_id="NVDA-SPLIT-2024",
        event_class="LARGE_MODERN_SPLIT",
        symbol="NVDA",
        cik="0001045810",
        company="NVIDIA Corporation",
        pack_expectation="NVDA 10:1 split 2024-06-10",
        targets=(
            ReceiptTarget(
                target_id="nvda-ten-for-one-split-8k",
                purpose="8-K (item 8.01) announcing the ten-for-one split, with its EX-99.1 release",
                form_types=("8-K",),
                date_window=("2024-05-20", "2024-05-31"),
                required_items=("8.01",),
                exhibit_types=("EX-99.1",),
                quote_terms=(
                    ("ten-for-one",),
                    ("split-adjusted",),
                    ("record holder", "additional shares"),
                ),
            ),
        ),
    ),
    ReceiptEvent(
        event_id="MSFT-DIVIDEND-2026Q3",
        event_class="ORDINARY_DIVIDEND",
        symbol="MSFT",
        cik="0000789019",
        company="Microsoft Corporation",
        pack_expectation="MSFT $0.91 dividend, ex 2026-02-19, payable 2026-03-12",
        targets=(
            ReceiptTarget(
                target_id="msft-q2-fy2026-10q",
                purpose=(
                    "10-Q for the quarter ended 2025-12-31; its stockholders' equity note "
                    "tabulates the declaration, record and payment dates and the per-share amount"
                ),
                form_types=("10-Q",),
                date_window=("2026-01-01", "2026-02-28"),
                quote_terms=(
                    ("declaration date", "record date", "payment date"),
                    ("board of directors declared", "dividends"),
                ),
            ),
        ),
        note=(
            "Microsoft announces the quarterly dividend by press release rather than by 8-K; "
            "the 10-Q is the SEC-filed primary source for the same declaration."
        ),
    ),
    ReceiptEvent(
        event_id="COST-SPECIAL-DIVIDEND-2024",
        event_class="SPECIAL_DIVIDEND",
        symbol="COST",
        cik="0000909832",
        company="Costco Wholesale Corporation",
        pack_expectation="COST $15.00 special dividend, pack ex-date 2024-01-11",
        targets=(
            ReceiptTarget(
                target_id="cost-special-dividend-8k",
                purpose=(
                    "8-K (item 8.01) declaring the special cash dividend; its EX-99.1 release "
                    "carries the record and payable dates that settle the pack's ex-date"
                ),
                form_types=("8-K",),
                date_window=("2023-12-08", "2023-12-22"),
                required_items=("8.01",),
                exhibit_types=("EX-99.1",),
                quote_terms=(
                    ("special cash dividend", "payable", "record"),
                ),
            ),
        ),
        note=(
            "This is the receipt the COST ex-date correction turns on: the Alpha Vantage pull "
            "shows ex 2023-12-27 / payable 2024-01-12 against a registered ex-date of 2024-01-11."
        ),
    ),
    ReceiptEvent(
        event_id="ATVI-CASH-MERGER-DELISTING-2023",
        event_class="CASH_MERGER_DELISTING",
        symbol="ATVI",
        cik="0000718877",
        company="Activision Blizzard, Inc.",
        pack_expectation="ATVI acquired by Microsoft for USD 95 cash per share; delisted 2023-10-13",
        targets=(
            ReceiptTarget(
                target_id="atvi-merger-completion-8k",
                purpose="Merger-completion 8-K (items 2.01 / 3.01) stating the cash consideration",
                form_types=("8-K",),
                date_window=("2023-10-10", "2023-10-20"),
                required_items=("2.01",),
                quote_terms=(
                    ("merger consideration", "in cash"),
                    ("form 25", "no longer listed"),
                ),
            ),
            ReceiptTarget(
                target_id="atvi-form-25",
                purpose="Exchange notification of removal from listing (Form 25 / 25-NSE)",
                form_types=("25-NSE", "25"),
                date_window=("2023-10-01", "2023-11-30"),
            ),
        ),
    ),
    ReceiptEvent(
        event_id="BBBY-ADVERSE-DELISTING-2023",
        event_class="ADVERSE_DELISTING",
        symbol="BBBY",
        cik="0000886158",
        company="Bed Bath & Beyond Inc. (now 20230930-DK-Butterfly-1, Inc.)",
        pack_expectation="BBBY NASDAQ delisting to OTC, 2023-05-03",
        targets=(
            ReceiptTarget(
                target_id="bbby-delisting-notice-8k",
                purpose="8-K item 3.01 — notice of delisting / failure to satisfy a listing rule",
                form_types=("8-K",),
                date_window=("2023-04-20", "2023-05-31"),
                required_items=("3.01",),
                quote_terms=(
                    ("suspended", "opening of business"),
                    ("will be delisted from nasdaq",),
                ),
            ),
            ReceiptTarget(
                target_id="bbby-form-25",
                purpose="Exchange notification of removal from listing (Form 25 / 25-NSE)",
                form_types=("25-NSE", "25"),
                date_window=("2023-04-20", "2023-08-31"),
            ),
            ReceiptTarget(
                target_id="bbby-plan-effectiveness-8k",
                purpose="8-K item 3.03 around 2023-09-29 — plan effective, equity cancelled",
                form_types=("8-K",),
                date_window=("2023-09-20", "2023-10-10"),
                required_items=("3.03",),
                quote_terms=(
                    ("confirmed plan became effective",),
                    ("cancelled on the effective date",),
                ),
            ),
        ),
        note=(
            "Both coordinates are recorded and neither is adjudicated here: the exchange "
            "delisting (~2023-05-03) and the plan effectiveness / share cancellation "
            "(2023-09-29, the last BBBYQ print in the Alpha Vantage pull)."
        ),
    ),
    ReceiptEvent(
        event_id="FB-META-IDENTITY-2022",
        event_class="IDENTITY_TICKER_CHANGE",
        symbol="FB",
        cik="0001326801",
        company="Meta Platforms, Inc.",
        pack_expectation="FB -> META ticker change, 2022-06-09",
        targets=(
            ReceiptTarget(
                target_id="meta-ticker-change-8k",
                purpose="8-K item 8.01 announcing the FB -> META ticker change and its effective date",
                form_types=("8-K",),
                date_window=("2022-05-25", "2022-06-15"),
                required_items=("8.01",),
                quote_terms=(("new ticker symbol",),),
            ),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@dataclass
class Receipt:
    """One stored document, bound to the event it corroborates."""

    target_id: str
    accession_number: str
    form: str
    filing_date: str
    accepted_at: str | None
    document_type: str
    document_name: str
    url: str
    sha256: str
    byte_length: int
    retrieved_at: str
    logical_id: str
    meta_logical_id: str
    reused_existing: bool
    quoted_sentences: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "accession_number": self.accession_number,
            "form": self.form,
            "filing_date": self.filing_date,
            "accepted_at": self.accepted_at,
            "document_type": self.document_type,
            "document_name": self.document_name,
            "url": self.url,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "retrieved_at": self.retrieved_at,
            "logical_id": self.logical_id,
            "meta_logical_id": self.meta_logical_id,
            "reused_existing": self.reused_existing,
            "quoted_sentences": list(self.quoted_sentences),
        }


@dataclass
class TargetOutcome:
    target: ReceiptTarget
    status: str
    receipts: list[Receipt] = field(default_factory=list)
    candidates: list[FilingRow] = field(default_factory=list)
    detail: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            **self.target.to_json_dict(),
            "status": self.status,
            "detail": self.detail,
            "receipts": [receipt.to_json_dict() for receipt in self.receipts],
            "candidates_seen": [row.to_json_dict() for row in self.candidates],
        }


@dataclass
class EventReceipts:
    event: ReceiptEvent
    status: str
    targets: list[TargetOutcome] = field(default_factory=list)
    detail: str | None = None

    @property
    def receipts(self) -> list[Receipt]:
        return [receipt for outcome in self.targets for receipt in outcome.receipts]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event.event_id,
            "event_class": self.event.event_class,
            "symbol": self.event.symbol,
            "cik": self.event.cik,
            "company": self.event.company,
            "pack_expectation": self.event.pack_expectation,
            "note": self.event.note,
            "status": self.status,
            "detail": self.detail,
            "receipt_count": len(self.receipts),
            "targets": [outcome.to_json_dict() for outcome in self.targets],
        }


@dataclass
class ReceiptsIndex:
    run_id: str
    started_at: str
    user_agent: str
    finished_at: str | None = None
    events: list[EventReceipts] = field(default_factory=list)
    requests_made: int = 0
    index_logical_id: str | None = None
    index_sha256: str | None = None

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for event in self.events:
            out[event.status] = out.get(event.status, 0) + 1
        return {status: out[status] for status in STATUS_ORDER if status in out}

    @property
    def all_corroborated(self) -> bool:
        return bool(self.events) and all(
            event.status == STATUS_CORROBORATED for event in self.events
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RECEIPTS_SCHEMA_VERSION,
            "run_kind": RUN_KIND,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "user_agent": self.user_agent,
            "pack_reference": PACK_REFERENCE,
            "pack_taxonomy_reference": PACK_TAXONOMY_REFERENCE,
            "requests_made": self.requests_made,
            "receipt_count": sum(len(event.receipts) for event in self.events),
            "counts": self.counts,
            "all_corroborated": self.all_corroborated,
            "events": [event.to_json_dict() for event in self.events],
            "claims": {
                "cross_source_receipts_stored_immutably": True,
                "cross_source_receipts_reviewed": False,
                "oracle_fixture_built": False,
                "freeze_blocker_changed": False,
            },
        }


def _widened(window: tuple[str, str], days: int) -> tuple[str, str]:
    start = date.fromisoformat(window[0]) - timedelta(days=days)
    end = date.fromisoformat(window[1]) + timedelta(days=days)
    return start.isoformat(), end.isoformat()


def _candidates(rows: Sequence[FilingRow], target: ReceiptTarget) -> list[FilingRow]:
    """What was actually on offer near a target that did not resolve."""

    widened = _widened(target.date_window, CANDIDATE_WINDOW_DAYS)
    same_form = select_filings(rows, target.form_types, widened)
    if same_form:
        return list(same_form[:MAX_CANDIDATES_REPORTED])
    start, end = widened
    nearby = [row for row in rows if start <= row.filing_date <= end]
    nearby.sort(key=lambda row: (row.filing_date, row.accession_number))
    return nearby[:MAX_CANDIDATES_REPORTED]


def _documents_for(
    client: EdgarClient,
    row: FilingRow,
    target: ReceiptTarget,
) -> list[tuple[str, str]]:
    """(document_type, document_name) pairs to fetch for one selected filing."""

    wanted: list[tuple[str, str]] = [(row.form, row.primary_document)]
    if not target.exhibit_types:
        return wanted
    documents = list_filing_documents(client, row.cik, row.accession_number)
    for document in select_documents(documents, target.exhibit_types):
        pair = (document.document_type, document.filename)
        if pair not in wanted and document.filename != row.primary_document:
            wanted.append(pair)
    return wanted


def _run_target(
    client: EdgarClient,
    store: SecReceiptStore,
    event: ReceiptEvent,
    target: ReceiptTarget,
    rows: Sequence[FilingRow],
    say: Callable[[str], None],
) -> TargetOutcome:
    selected = select_filings(
        rows,
        target.form_types,
        target.date_window,
        items=target.required_items,
        limit=target.max_filings,
    )
    if not selected:
        return TargetOutcome(
            target=target,
            status=STATUS_NOT_LOCATED,
            candidates=_candidates(rows, target),
            detail=(
                f"no {'/'.join(target.form_types)} filing in "
                f"{target.date_window[0]}..{target.date_window[1]}"
                + (f" with item(s) {','.join(target.required_items)}" if target.required_items else "")
            ),
        )

    outcome = TargetOutcome(target=target, status=STATUS_CORROBORATED)
    failures: list[str] = []
    for row in selected:
        try:
            wanted = _documents_for(client, row, target)
        except EdgarUnavailableError as exc:
            failures.append(str(exc))
            continue
        for document_type, document_name in wanted:
            say(f"{event.event_id} {target.target_id} {row.accession_number} {document_name}")
            record, response = fetch_document(
                client,
                store,
                cik=row.cik,
                accession_number=row.accession_number,
                document_name=document_name,
            )
            if record is None:
                detail = response.detail if response else "no response"
                failures.append(f"{document_name}: {response.response_class if response else '?'} {detail or ''}".strip())
                continue
            quotes: tuple[str, ...] = ()
            if target.quote_terms:
                quotes = find_quotes(extract_text(store.read_body(record)), target.quote_terms)
            outcome.receipts.append(
                Receipt(
                    target_id=target.target_id,
                    accession_number=row.accession_number,
                    form=row.form,
                    filing_date=row.filing_date,
                    accepted_at=row.accepted_at,
                    document_type=document_type,
                    document_name=document_name,
                    url=record.document_url,
                    sha256=record.sha256,
                    byte_length=record.byte_length,
                    retrieved_at=record.retrieved_at,
                    logical_id=record.body_logical_id,
                    meta_logical_id=record.meta_logical_id,
                    reused_existing=response is None,
                    quoted_sentences=list(quotes),
                )
            )
    if failures:
        outcome.detail = "; ".join(failures)
        outcome.status = STATUS_PARTIAL if outcome.receipts else STATUS_PULL_UNAVAILABLE
    return outcome


def _event_status(outcomes: Sequence[TargetOutcome]) -> str:
    statuses = {outcome.status for outcome in outcomes}
    if statuses == {STATUS_CORROBORATED}:
        return STATUS_CORROBORATED
    if any(outcome.receipts for outcome in outcomes):
        return STATUS_PARTIAL
    if STATUS_PULL_UNAVAILABLE in statuses:
        return STATUS_PULL_UNAVAILABLE
    return STATUS_NOT_LOCATED


def build_receipts_index(
    client: EdgarClient,
    layout: DataRootLayout,
    *,
    events: Sequence[ReceiptEvent] = REGISTERED_RECEIPT_EVENTS,
    now: datetime | None = None,
    progress: Callable[[str], None] | None = None,
) -> ReceiptsIndex:
    """Acquire, hash and store the registered cross-source receipts; write the index.

    Every event is attempted; nothing aborts the run. An event whose filings
    cannot be identified is ``RECEIPT_NOT_LOCATED`` with candidates attached, and
    an event EDGAR would not serve is ``PULL_UNAVAILABLE``.
    """

    started = (now or datetime.now(UTC)).astimezone(UTC)
    index = ReceiptsIndex(
        run_id=started.strftime("%Y%m%dT%H%M%SZ") + "-" + RUN_KIND,
        started_at=started.isoformat(timespec="seconds"),
        user_agent=client.user_agent,
    )
    store = SecReceiptStore(layout)
    say = progress or (lambda _message: None)

    for event in events:
        say(f"submissions CIK{event.cik} ({event.symbol})")
        try:
            rows = list_filings(client, event.cik, date_window=event.span)
        except EdgarUnavailableError as exc:
            index.events.append(
                EventReceipts(event=event, status=STATUS_PULL_UNAVAILABLE, detail=str(exc))
            )
            continue
        outcomes = [
            _run_target(client, store, event, target, rows, say) for target in event.targets
        ]
        index.events.append(
            EventReceipts(event=event, status=_event_status(outcomes), targets=outcomes)
        )

    index.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
    index.requests_made = client.requests_made
    _write_index(layout, index)
    return index


def receipts_directory(layout: DataRootLayout, run_id: str) -> Path:
    return layout.derived / "corporate-actions" / "receipts" / run_id


def _write_index(layout: DataRootLayout, index: ReceiptsIndex) -> None:
    directory = receipts_directory(layout, index.run_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "receipts-index.json"
    payload = (
        json.dumps(index.to_json_dict(), sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    _write_exclusive(path, payload)
    index.index_logical_id = layout.logical_artifact_id(path)
    index.index_sha256 = hashlib.sha256(payload).hexdigest()
