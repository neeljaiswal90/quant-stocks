"""Versioned M1 data-spine stores: trading calendar, prices, vintage risk-free.

Three read-only stores over immutable inputs (NEE-126 prebuild, T2 engineering
stream: PR + CI, no per-slice governance):

``calendar_v1``
    The **accepted** XNAS session calendar, bound by grouped digest and verified
    on load, with the exact session algebra: sessions, half-days, month-end
    sessions, exact signed session offsets, and an explicitly named
    next-eligible-session mapping. Also the package's base layer for grouped
    digests, canonical dataset hashing, and the shared typed error.

``prices_v1``
    Raw, split-adjusted, and total-return coordinates as **separately named**
    tables whose value-field names are pairwise disjoint and drawn from the
    NEE-125 kernel's own series names, so a raw coordinate cannot be read as a
    derived one. Arithmetic is the kernel's; this store partitions and attaches
    lineage.

``riskfree_v1``
    Vintage-stamped risk-free observations with declared quote unit, compounding
    convention, and day-count basis, converted to period returns by dispatching
    on the declared convention. Its source registry ships **empty** pending the
    owner's vintage-source decision, so real-source resolution fails closed.

Nothing in this package imports a transport module, opens a socket, or reads a
credential; the architecture test asserts the first of those as a module edge.
"""

from qme.data.stores.calendar_v1 import (
    ACCEPTED_CALENDAR_AUTHORITY,
    CALENDAR_FAIL_CLOSED_STATES,
    CALENDAR_ID,
    BoundArtifact,
    MarketStoreError,
    SessionRow,
    TradingCalendar,
    TradingCalendarError,
    canonical_dataset_digest,
    grouped_sha256_bytes,
    grouped_sha256_file,
    load_calendar,
    store_binding_digest,
    verify_bound_artifacts,
)
from qme.data.stores.prices_v1 import (
    COORDINATE_KEY_FIELDS,
    COORDINATE_SYSTEMS,
    COORDINATE_VALUE_FIELDS,
    PRICE_STORE_FAIL_CLOSED_STATES,
    PriceStore,
    PriceStoreError,
    RawPriceRow,
    SplitAdjustedPriceRow,
    TotalReturnRow,
    assert_coordinates_non_joinable,
    build_price_store,
    join_coordinates,
)
from qme.data.stores.riskfree_v1 import (
    COMPOUNDING_EFFECTIVE_ANNUAL,
    COMPOUNDING_SIMPLE_ANNUAL,
    DAY_COUNT_BASES,
    REGISTERED_SOURCES,
    RISK_FREE_FAIL_CLOSED_STATES,
    PeriodReturn,
    RiskFreeObservation,
    RiskFreeSource,
    RiskFreeStore,
    RiskFreeStoreError,
    build_risk_free_store,
    day_fraction,
    period_return,
    resolve_observation,
    resolve_source,
    visible_observations,
)

__all__ = [
    "ACCEPTED_CALENDAR_AUTHORITY",
    "CALENDAR_FAIL_CLOSED_STATES",
    "CALENDAR_ID",
    "COMPOUNDING_EFFECTIVE_ANNUAL",
    "COMPOUNDING_SIMPLE_ANNUAL",
    "COORDINATE_KEY_FIELDS",
    "COORDINATE_SYSTEMS",
    "COORDINATE_VALUE_FIELDS",
    "DAY_COUNT_BASES",
    "PRICE_STORE_FAIL_CLOSED_STATES",
    "REGISTERED_SOURCES",
    "RISK_FREE_FAIL_CLOSED_STATES",
    "BoundArtifact",
    "MarketStoreError",
    "PeriodReturn",
    "PriceStore",
    "PriceStoreError",
    "RawPriceRow",
    "RiskFreeObservation",
    "RiskFreeSource",
    "RiskFreeStore",
    "RiskFreeStoreError",
    "SessionRow",
    "SplitAdjustedPriceRow",
    "TotalReturnRow",
    "TradingCalendar",
    "TradingCalendarError",
    "assert_coordinates_non_joinable",
    "build_price_store",
    "build_risk_free_store",
    "canonical_dataset_digest",
    "day_fraction",
    "grouped_sha256_bytes",
    "grouped_sha256_file",
    "join_coordinates",
    "load_calendar",
    "period_return",
    "resolve_observation",
    "resolve_source",
    "store_binding_digest",
    "verify_bound_artifacts",
    "visible_observations",
]
