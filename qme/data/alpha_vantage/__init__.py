"""Alpha Vantage acquisition boundary: quota, immutable raw cache, schema gates.

Import map (the network edge is deliberate and asserted in
``tests/architecture/test_import_boundaries.py``)::

    plan_v1          provider-plan evidence: R, B, daily cap, source, effective date
    quota            token bucket + daily/endpoint caps, driven by an injected clock
    client           URLs, credential reference, request keys, typed payload states,
                     retry policy -- no sockets
    transport        the ONLY module that opens a network connection
    store            immutable raw pulls + request-key cache index
    validators       endpoint shape validators (parsers)
    acquisition      the boundary: quota -> fetch -> durable store -> parse -> manifest
    normalize        versioned, lossless canonical row normalizers (NEE-123, M1)
    endpoint_ingest  registered-scope M1 runner: boundary + normalizer, additive to M0
"""

from qme.data.alpha_vantage.acquisition import (
    AcquisitionBoundary,
    AcquisitionError,
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionRunManifest,
    Parser,
    expecting_parser,
    parse_hash,
)
from qme.data.alpha_vantage.client import (
    AlphaVantageClient,
    AlphaVantageError,
    CredentialError,
    CredentialRef,
    FetchOutcome,
    OfflineClientError,
    Pacer,
    PayloadState,
    RawResponse,
    RetryPolicy,
    TransportResponse,
    canonical_parameters,
    classify_body,
    classify_payload,
    load_api_key,
    parameters_hash,
    parameters_hash_from_pairs,
    redact_mapping,
    redact_url,
    request_key,
)
from qme.data.alpha_vantage.endpoint_ingest import (
    NORMALIZER_PARSERS,
    EndpointIngestPlan,
    EndpointIngestRun,
    EndpointIngestScopeError,
    normalizing_parser_for,
    run_registered_endpoint_ingest,
)
from qme.data.alpha_vantage.normalize import (
    NORMALIZER_VERSION,
    NormalizationError,
    NormalizedResult,
    normalize_dividends,
    normalize_listing_status,
    normalize_splits,
    normalize_time_series_daily,
)
from qme.data.alpha_vantage.plan_v1 import (
    REGISTERED_PLANS,
    ProviderPlan,
    ProviderPlanError,
    resolve_plan,
)
from qme.data.alpha_vantage.quota import (
    QuotaExhaustedError,
    QuotaLedger,
    QuotaUnavailableError,
    TokenBucket,
)
from qme.data.alpha_vantage.store import (
    RawCacheMissError,
    RawPullRecord,
    RawPullStore,
    RawPullStoreError,
    RequestKeyEntry,
    RequestKeyIndex,
)

__all__ = [
    "NORMALIZER_PARSERS",
    "NORMALIZER_VERSION",
    "REGISTERED_PLANS",
    "AcquisitionBoundary",
    "AcquisitionError",
    "AcquisitionRequest",
    "AcquisitionResult",
    "AcquisitionRunManifest",
    "AlphaVantageClient",
    "AlphaVantageError",
    "CredentialError",
    "CredentialRef",
    "EndpointIngestPlan",
    "EndpointIngestRun",
    "EndpointIngestScopeError",
    "FetchOutcome",
    "NormalizationError",
    "NormalizedResult",
    "OfflineClientError",
    "Pacer",
    "Parser",
    "PayloadState",
    "ProviderPlan",
    "ProviderPlanError",
    "QuotaExhaustedError",
    "QuotaLedger",
    "QuotaUnavailableError",
    "RawCacheMissError",
    "RawPullRecord",
    "RawPullStore",
    "RawPullStoreError",
    "RawResponse",
    "RequestKeyEntry",
    "RequestKeyIndex",
    "RetryPolicy",
    "TokenBucket",
    "TransportResponse",
    "canonical_parameters",
    "classify_body",
    "classify_payload",
    "expecting_parser",
    "load_api_key",
    "normalize_dividends",
    "normalize_listing_status",
    "normalize_splits",
    "normalize_time_series_daily",
    "normalizing_parser_for",
    "parameters_hash",
    "parameters_hash_from_pairs",
    "parse_hash",
    "redact_mapping",
    "redact_url",
    "request_key",
    "resolve_plan",
    "run_registered_endpoint_ingest",
]
