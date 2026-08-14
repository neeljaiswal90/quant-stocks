"""Bounded raw SEC Section 31 and FINRA TAF assessment candidate.

This module deliberately does not model broker/customer pass-through, ledger
rounding, or fill/order aggregation.  It computes only exact raw Decimal
amounts for an explicitly classified and pre-aggregated regulatory trade.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from datetime import date
from decimal import (
    ROUND_HALF_EVEN,
    Clamped,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    FloatOperation,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    Subnormal,
    Underflow,
    localcontext,
)
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator  # type: ignore[import-untyped, unused-ignore]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped, unused-ignore]

CONFIG_PATH: Final = Path("configs/governance/regulatory-fee-kernel-v1.json")
SCHEMA_PATH: Final = Path("schemas/governance/regulatory-fee-kernel-v1.schema.json")
SOURCE_FIXTURE_PATH: Final = Path(
    "tests/fixtures/governance/regulatory-fee-reviewed-sources-v1.json"
)
KAT_FIXTURE_PATH: Final = Path("tests/fixtures/quant/regulatory-fee-kernel-v1.cases.json")
MANIFEST_PATH: Final = Path("configs/governance/regulatory-fee-kernel-v1.hashes.json")

EXPECTED_CONFIG_SHA256: Final = "aa3758b0:69066e22:109f7898:c5554cff:224a1c87:fa4a64ca:9e57e69f:9aa45402"
EXPECTED_SCHEMA_SHA256: Final = "8e222dfc:321338bc:121022c7:cdd599b0:a4d168e6:04b9ed8a:3d10dd9d:be973344"
EXPECTED_SOURCE_FIXTURE_SHA256: Final = "6eddafd8:1e9bf654:1ccead90:bf351270:81a01123:37d008a5:bcf66fc1:f719b080"
EXPECTED_KAT_FIXTURE_SHA256: Final = "c5100c58:5eb2a0c4:512bdc13:722de276:96115506:bc95bcd3:80247b92:59219242"
EXPECTED_SEMANTIC_SHA256: Final = "b148ab04:e4d088f0:245a730c:f9da5540:232838dc:0355c3e0:61261964:32598238"

_MAX_JSON_BYTES: Final = 2 * 1024 * 1024
_DECIMAL_RE: Final = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z", re.ASCII)
_TRADE_ID_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z", re.ASCII)
_SEC_PER_MILLION_DENOMINATOR: Final = Decimal("1000000")
_FINRA_RATE: Final = Decimal("0.000195")
_FINRA_CAP: Final = Decimal("9.79")
_SEC_RATE_ZERO: Final = Decimal("0")
_SEC_RATE_ACTIVE: Final = Decimal("20.60")
_REVIEW_START: Final = date(2026, 1, 1)
_SEC_CHANGE: Final = date(2026, 4, 4)
_REVIEW_CUTOFF: Final = date(2026, 8, 14)

_IDENTITY: Final = {
    "$schema": "../../schemas/governance/regulatory-fee-kernel-v1.schema.json",
    "schema_version": "qme.regulatory_fee_kernel.v1",
    "artifact_id": "NEE-205-REGULATORY-FEE-KERNEL-V1",
    "ticket_id": "NEE-205",
    "status": "BOUNDED_2026_RAW_REGULATORY_ASSESSMENT_CANDIDATE_BLOCKERS_RETAINED",
}
_CLAIMS: Final = {
    "bounded_raw_decimal_kernel_available": True,
    "reviewed_2026_source_excerpts_available": True,
    "broker_customer_pass_through_available": False,
    "rounding_method_registered": False,
    "aggregation_method_registered": False,
    "complete_historical_schedule_available": False,
    "accounting_v1_integrated": False,
    "freeze_blocker_changed": False,
    "milestone_m0_complete": False,
    "production_ready": False,
    "empirical_evidence_available": False,
    "alpha_proven": False,
    "live_order_authority": False,
}
_NONCLAIMS: Final = (
    "NO_BROKER_OR_CUSTOMER_PASS_THROUGH_OR_WEBULL_DEBIT_CLAIM",
    "NO_REGULATOR_BROKER_OR_LEDGER_ROUNDING_CLAIM",
    "NO_FILL_ORDER_DAY_OR_REGULATORY_TRADE_AGGREGATION_CLAIM",
    "NO_CHARGE_DATE_INFERENCE_FROM_FILL_OR_TRADE_DATE",
    "NO_COMPLETE_2010_PLUS_HISTORICAL_OR_FUTURE_SCHEDULE",
    "NO_PRODUCTION_COST_CALIBRATION_EMPIRICAL_EVIDENCE_OR_BLOCKER_REMOVAL",
    "NO_M0_PRODUCTION_READINESS_ALPHA_OR_LIVE_ORDER_CLAIM",
)
_METHOD: Final = {
    "method_id": "qme-bounded-raw-regulatory-assessment-v1",
    "scope": "RAW_REGULATORY_ASSESSMENT_CANDIDATE_NOT_BROKER_OR_CUSTOMER_CHARGE",
    "reviewed_date_range": {"start": "2026-01-01", "end": "2026-08-14", "outside": "BLOCKED_NO_EXTRAPOLATION"},
    "required_inputs": ["side", "charge_date", "trade_date", "covered_sale_notional", "eligible_sold_shares", "execution_price_per_share", "coverage_classification", "regulatory_trade_id", "aggregation_status", "transaction_status", "pass_through_semantics", "rounding_semantics"],
    "buy_rule": "SEC31_RAW_0_AND_FINRA_TAF_RAW_0",
    "sell_identity": "covered_sale_notional == eligible_sold_shares * execution_price_per_share",
    "sec31": {"zero_rate_through": "2026-04-03", "active_from": "2026-04-04", "charge_date_through_2026_04_03_rate_per_million": "0", "charge_date_from_2026_04_04_rate_per_million": "20.60", "denominator": "1000000", "end_status": "BOUNDED_AT_REVIEW_CUTOFF_NOT_UNTIL_SUPERSEDED"},
    "finra_taf": {"trade_date_year": 2026, "effective_from": "2026-01-01", "effective_date_basis": "FINRA_UNLESS_SPECIFIED_OTHERWISE_FEE_INCREASES_EFFECTIVE_JANUARY_1_OF_STATED_YEAR", "rate_per_eligible_sold_share": "0.000195", "cap_per_regulatory_trade": "9.79", "strict_low_price_rule": "EXECUTION_PRICE_PER_SHARE_LT_RATE_EXCLUDED_EQUALITY_CHARGED", "aggregation": "CALLER_PREAGGREGATED_ONE_REGULATORY_TRADE_ONLY"},
    "transaction_status_rule": "FINAL_NOT_CANCELLED_OR_CORRECTED_ONLY_OTHERWISE_BLOCKED",
    "source_hierarchy": "2026_SCHEDULE_CONTROLS_RATE_AND_CAP_RULEBOOK_CONTROLS_APPLICABILITY_OPERATOR_FAQ_CONTROLS_DATE_AND_REPORTING_CONTEXT",
    "numeric": {"input": "CANONICAL_ASCII_DECIMAL_TEXT_NO_EXPONENT_NO_TRAILING_ZERO_MAX_80_CHARACTERS", "context": "PRECISION_256_ROUND_HALF_EVEN_EMIN_NEG999999_EMAX999999_CLAMP_0_TRAP_CLAMPED_INVALID_DIVZERO_FLOAT_INEXACT_OVERFLOW_ROUNDED_SUBNORMAL_UNDERFLOW", "context_spec": {"precision": 256, "rounding": "ROUND_HALF_EVEN", "emin": -999999, "emax": 999999, "capitals": 1, "clamp": 0, "traps": ["Clamped", "InvalidOperation", "DivisionByZero", "Inexact", "Overflow", "Rounded", "Subnormal", "Underflow", "FloatOperation"]}, "exactness_bound": "INPUT_AT_MOST_80_SIGNIFICANT_DIGITS_IDENTITY_PRODUCT_AT_MOST_160_FEE_PRODUCT_AT_MOST_86_TOTAL_AT_MOST_87_PRECISION_256", "output": "EXACT_RAW_DECIMAL_NO_QUANTIZATION_OR_ROUNDING"},
    "accounting_v1_integration": False,
}
_TYPED_BLOCKERS: Final = (
    ("CHARGE_DATE_MISSING_OR_AMBIGUOUS", "EXPLICIT_CANONICAL_CHARGE_DATE"),
    ("TRADE_DATE_MISSING_OR_AMBIGUOUS", "EXPLICIT_CANONICAL_TRADE_DATE"),
    ("DATE_OUTSIDE_REVIEWED_SCHEDULE", "VERSIONED_IMMUTABLE_DATED_SOURCE_RECEIPT"),
    ("COVERAGE_OR_EXEMPTION_UNRESOLVED", "EXPLICIT_COVERED_EQUITY_ELIGIBLE_NO_EXEMPTION_CLASSIFICATION"),
    ("AGGREGATION_NOT_PRECOMPUTED_EXPLICITLY", "EXPLICIT_PREAGGREGATED_SINGLE_REGULATORY_TRADE_INPUT"),
    ("CANCELLATION_OR_CORRECTION_STATUS_UNRESOLVED", "EXPLICIT_FINAL_NOT_CANCELLED_OR_CORRECTED_STATUS"),
    ("PASS_THROUGH_SEMANTICS_UNAUTHORIZED", "VERSIONED_BROKER_CUSTOMER_PASS_THROUGH_REGISTRATION"),
    ("ROUNDING_SEMANTICS_UNAUTHORIZED", "VERSIONED_REGULATOR_BROKER_AND_LEDGER_ROUNDING_REGISTRATION"),
    ("NOTIONAL_PRICE_SHARE_IDENTITY_MISMATCH", "EXACT_NOTIONAL_SHARE_PRICE_RECONCILIATION"),
    ("REGULATORY_TRADE_ID_INVALID", "CANONICAL_EXPLICIT_REGULATORY_TRADE_ID"),
)
_BLOCKERS: Final = (
    ("NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL", "NEE-110", "GOVERNANCE", "Final cross-contract semantic acceptance remains unavailable until every production evidence artifact exists and is reviewed."),
    ("NEE-116-ASYMMETRIC-COST-METHOD", "NEE-116", "ENGINEERING_EVIDENCE", "Dated SEC and FINRA sell-side fee logic and an independently checked ledger fixture are not implemented."),
    ("NEE-116-CAPACITY-SOLVER", "NEE-116", "ENGINEERING_EVIDENCE", "The authoritative greatest-capital discrete cost-aware solver remains unavailable."),
    ("NEE-116-CORPORATE-ACTION-EDGE-CASES", "NEE-116", "PRODUCTION_EVIDENCE", "Corporate-action targets lack immutable provider and independent-source receipts plus accepted ledger fixtures."),
    ("NEE-116-PRODUCTION-PIT-DATA", "NEE-116", "PRODUCTION_EVIDENCE", "Accepted point-in-time production data and receipts are unavailable."),
    ("NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE", "NEE-116", "ENGINEERING_EVIDENCE", "Tax-lot election, lot accounting, and within-account wash-sale logic lack implementation and fixtures."),
    ("NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP", "NEE-119", "PRODUCTION_EVIDENCE", "No accepted point-in-time Nasdaq-100 membership snapshot is bound into the freeze candidate."),
    ("NEE-119-AV-PROXY-EVIDENCE", "NEE-119", "PRODUCTION_EVIDENCE", "No reviewed Alpha Vantage common-stock proxy snapshot is bound into the freeze candidate."),
    ("NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE", "NEE-120", "ENGINEERING_EVIDENCE", "Registered bootstrap, block-selection, interval, and multiplicity methods lack executable conformance evidence."),
    ("NEE-121-CALENDAR-SESSION-REGISTRATION", "NEE-121", "PRODUCTION_EVIDENCE", "XNAS identity is registered, but pinned generator package, lock, tzdata, immutable calendar/session-vector hashes, and closure/half-day evidence are unavailable."),
    ("NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP", "NEE-121", "FINAL_DERIVED_EVIDENCE", "The final freeze anchor and receipt remain unavailable until every other blocker is accepted."),
    ("NEE-122-CORRELATED-TRIAL-FIXTURE", "NEE-122", "ENGINEERING_EVIDENCE", "Analytic participation-ratio and seeded Ledoit-Wolf end-to-end fixtures remain unavailable."),
    ("NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE", "NEE-122", "ENGINEERING_EVIDENCE", "The registered estimator and bootstrap uncertainty procedure are not implemented."),
)
_AUTHORITY_HASHES: Final = {
    "proposal": ("docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md", "5869d313:e179e442:e305704e:7cff5031:786d7452:73c0f864:bc910784:8685847c"),
    "m0_registration": ("configs/governance/m0-registration-v1.json", "fc61bb24:5e99c5a7:ac8de1ad:f909b785:2f3a651c:7925d0fb:63037db7:45946756"),
    "m0_manifest": ("configs/governance/m0-registration-v1.hashes.json", "b1375860:485bf393:df34d588:545bf1a2:738f5efd:c703825c:62c3430c:b427c6db"),
    "crosswalk_v3": ("configs/governance/s0a-contract-materialization-crosswalk-v3.json", "b2d1a7f2:afb6ea18:62d87bed:2323dd35:863f55b1:e53ca99b:c5d1168d:018606b5"),
    "crosswalk_v3_manifest": ("configs/governance/s0a-contract-materialization-crosswalk-v3.hashes.json", "5d57b7bf:7e42f138:c27f4879:1311f1e0:66cf508b:64bede71:c58c5ff0:e45b59e5"),
    "freeze_v4": ("configs/governance/specification-freeze-policy-v4.json", "adf2288b:32532669:cdd7fa9d:4876132b:222916d2:c754f006:6003a6cd:1a4fb458"),
    "freeze_v4_manifest": ("configs/governance/specification-freeze-v4.hashes.json", "a2c3bbfa:d15e7bd3:769142ad:69c291e7:885cd14d:6ca2d939:99c39df2:5360ea42"),
    "economic_promotion_v2": ("configs/quant/economic-promotion-decision-v2.json", "02d055b0:26d9352e:aa0979cd:c158d9df:26ed6aad:06259567:291970c9:0a9359a8"),
    "economic_promotion_v2_manifest": ("configs/quant/economic-promotion-decision-v2.hashes.json", "793caa4e:5a5b29ce:e746c050:96f25c98:f93aaf26:a2cce442:4de1c6a0:98f0ed87"),
    "accounting_equations_v1": ("configs/quant/accounting-equations-v1.json", "decb3d52:dea8b402:0f011554:848bb9a7:c6164827:cfe319be:36fc46c7:8b8c2e0c"),
    "accounting_equations_v1_manifest": ("tests/fixtures/quant/accounting-equations-v1.manifest.json", "d2f042da:9ecf1f28:d019af1a:8ae0d18d:56af0181:75dc3baf:a3699df5:5025f0ee"),
}
_BOUND_MANIFEST_INVENTORY: Final = {
    "configs/governance/m0-registration-v1.hashes.json": (
        {"schema_version": "qme.hash_manifest.v1", "manifest_id": "M0-OWNER-MANDATE-REGISTRATION-2026-08-12-V1", "status": "REGISTERED_DECISIONS_EVIDENCE_BLOCKERS_REMAIN"},
        ("configs/governance/experiment-family-registration-v1.json", "configs/governance/label-endpoint-session-offset-v1.json", "configs/governance/m0-registration-v1.json", "configs/quant/source-freshness-policy-v1.json", "docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md", "docs/governance/PRIOR_ACCESS_ATTESTATION_2019_2021.md", "qme/governance/m0_registration.py", "schemas/governance/experiment-family-registration-v1.schema.json", "schemas/governance/label-endpoint-session-offset-v1.schema.json", "schemas/governance/m0-registration-v1.schema.json", "schemas/quant/source-freshness-policy-v1.schema.json", "tests/governance/test_m0_registration.py"),
    ),
    "configs/governance/s0a-contract-materialization-crosswalk-v3.hashes.json": (
        {"schema_version": "qme.hash_manifest.v1", "artifact_id": "NEE-172-S0A-2-CONTRACT-MATERIALIZATION-CROSSWALK-V3", "implementation_status": "CROSSWALK_ONLY", "production_status": "BLOCKED_14_EVIDENCE_AND_ENGINEERING_ITEMS_REMAIN"},
        ("configs/governance/s0a-contract-materialization-crosswalk-v3.json", "docs/governance/S0A_CONTRACT_MATERIALIZATION_CROSSWALK_V3.md", "qme/governance/materialization_crosswalk_v3.py", "schemas/governance/s0a-contract-materialization-crosswalk-v3.schema.json", "tests/governance/test_materialization_crosswalk_v3.py"),
    ),
    "configs/governance/specification-freeze-v4.hashes.json": (
        {"schema_version": "qme.hash_manifest.v1", "artifact_id": "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V4", "status": "BLOCKED_13_ACTIVE"},
        (".github/workflows/ci.yml", "configs/governance/nee-172-operational-v2-bundle-v1.hashes.json", "configs/governance/sample-access-chain-v2.hashes.json", "configs/governance/specification-freeze-v3.hashes.json", "configs/governance/specification-freeze-export-v3.json", "configs/governance/specification-freeze-policy-v4.json", "docs/governance/SPECIFICATION_FREEZE_V4.md", "qme/governance/specification_freeze_v4.py", "schemas/governance/specification-freeze-export-v3.schema.json", "schemas/governance/specification-freeze-policy-v4.schema.json", "tests/governance/test_specification_freeze_v4.py"),
    ),
    "configs/quant/economic-promotion-decision-v2.hashes.json": (
        {"schema_version": "qme.hash_manifest.v1", "artifact_id": "NEE-120-QME-ECONOMIC-DECISION-V2", "implementation_status": "OWNER_REGISTERED_OPERATIONAL_CONTRACT_IMPLEMENTATION_AND_EVIDENCE_BLOCKED", "production_status": "BLOCKED_NO_PRODUCTION_EVIDENCE_OR_METHOD_IMPLEMENTATION"},
        ("configs/quant/economic-promotion-decision-v2.json", "docs/quant/ECONOMIC_PROMOTION_DECISION_V2.md", "qme/promotion/decision_v2.py", "schemas/quant/economic-promotion-decision-v2.schema.json", "tests/fixtures/promotion/economic-promotion-decision-v2.cases.json", "tests/promotion/test_decision_v2.py"),
    ),
    "tests/fixtures/quant/accounting-equations-v1.manifest.json": (
        {"schema_version": "qme.frozen_equation_manifest.v1", "equation_spec_id": "NEE-118-QME-ACCOUNTING-V1", "hash_algorithm": "sha256", "fixture_outputs_sha256": "e6e85f92:258e6d1c:97aa9527:c73cd98a:164d8823:ed006ddb:e852be8a:e47d6b38"},
        ("configs/quant/accounting-equations-v1.json", "docs/quant/QME_ACCOUNTING_EXECUTION_METRICS_SPEC.md", "qme/quant/__init__.py", "qme/quant/equations.py", "schemas/quant/accounting-equations-v1.schema.json", "tests/fixtures/quant/accounting-equations-v1.vectors.json"),
    ),
}
_SOURCE_EXPECTED: Final = (
    ("SEC31_2026_RATE_ADVISORY", "https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2", 625, "6a9af84b:f047d03b:faeaebac:8dfaf5ff:77924db4:821ad02c:348e1130:45f10169"),
    ("FINRA_2026_FEE_ADJUSTMENT_SCHEDULE", "https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule", 163, "1e2113e1:63bb73aa:bc5423d1:94f67630:4af4590e:965706c9:9d1ff9bc:266b34d1"),
    ("FINRA_MEMBER_REGULATORY_FEE_RULE", "https://www.finra.org/rules-guidance/rulebooks/corporate-organization/section-1-member-regulatory-fees", 664, "0e23e205:e6436d05:129001fb:dc52b3df:7ea74205:6f70d04f:9963a0d4:0497c2d9"),
    ("FINRA_TAF_FAQ", "https://www.finra.org/rules-guidance/guidance/faqs/trading-activity-fee", 347, "343fa4a1:3bce8571:d7f9c2f3:da5e1bf1:bb72d13f:9ed3b35b:0c35907c:9a4e9ffd"),
    ("SEC31_PASS_THROUGH_EXPLANATION", "https://www.sec.gov/rules-regulations/fee-rate-advisories/section-31-transaction-fees-basic-information-firms", 258, "750a0c7d:41a1a55f:1d5099dc:0d42596c:c5b3d6f7:c46dea9a:45c6720b:7f8eba7a"),
)
_SOURCE_METADATA: Final = (
    ("U.S. Securities and Exchange Commission", "2026-02-27", ("SEC31_RATE_0_USD_PER_MILLION_THROUGH_CHARGE_DATE_2026_04_03", "SEC31_RATE_20_60_USD_PER_MILLION_FROM_CHARGE_DATE_2026_04_04_UNTIL_SUPERSEDED")),
    ("Financial Industry Regulatory Authority", None, ("FINRA_UNLESS_SPECIFIED_OTHERWISE_FEE_INCREASES_EFFECTIVE_JANUARY_1_OF_STATED_YEAR", "FINRA_2026_COVERED_EQUITY_RATE_0_000195_USD_PER_SHARE", "FINRA_2026_COVERED_EQUITY_CAP_9_79_USD_PER_REGULATORY_TRADE")),
    ("Financial Industry Regulatory Authority", None, ("TAF_APPLIES_TO_MEMBER_SALES_OF_COVERED_SECURITIES", "COVERED_EQUITY_TAF_IS_PER_SHARE", "REPORTING_IS_AGGREGATE", "STRICTLY_BELOW_APPLICABLE_PER_UNIT_RATE_IS_EXCLUDED_EQUALITY_IS_NOT_EXCLUDED")),
    ("Financial Industry Regulatory Authority", None, ("TAF_DATE_COORDINATE_IS_TRADE_DATE", "TAF_HAS_NO_PER_TRADE_ROUNDING_RULE_BECAUSE_REPORTING_IS_AGGREGATE", "CANCELLED_UNREPLACED_EXCLUDED_CORRECTED_INCLUDED")),
    ("U.S. Securities and Exchange Commission", "2026-03-02", ("SECTION31_DOES_NOT_DEFINE_BROKER_OR_CUSTOMER_PASS_THROUGH", "BROKER_CUSTOMER_CHARGE_SEMANTICS_REMAIN_UNREGISTERED")),
)
_SOURCE_LIMITATIONS: Final = (
    "EXCERPTS_NOT_COMPLETE_WEB_PAGES",
    "URL_CONTENT_MAY_CHANGE_AFTER_RETRIEVAL_DATE",
    "NO_BROKER_OR_CUSTOMER_PASS_THROUGH_AUTHORITY",
    "NO_BROKER_LEDGER_ROUNDING_OR_CALLER_PREAGGREGATION_METHOD_AUTHORITY",
    "NO_COMPLETE_HISTORICAL_OR_FUTURE_SCHEDULE",
)
_KAT_CASE_HASHES: Final = (
    ("FINRA_2025_12_31_OUTSIDE_REVIEWED_SCHEDULE", "31fc64ed:cdd2c9d3:386e4967:c60dadd5:7aa9b3d8:f0539697:5c408260:a121bd42"),
    ("FINRA_2026_01_01_EFFECTIVE", "1c8cc2c4:9b0a4180:03c508b5:483ad964:afb667d1:bd46d376:335ed33e:65a62a7e"),
    ("BUY_COMPONENTS_EXACT_ZERO", "d9db11d2:33977e85:bae4bc8e:36007ac3:6346245c:c5d37de9:3eb75e57:001543fa"),
    ("DAY_BEFORE_SEC_CHANGE", "bf0f9f8d:78e7fdf2:3fc59965:2cd24047:48950ca1:17f1219a:79d789f3:9f41c3b5"),
    ("DAY_OF_SEC_CHANGE", "8d81a72e:274dc7f9:a408f5f3:82d99aa8:818588fc:68f656ce:719c26e7:25b1815d"),
    ("FINRA_CAP_BELOW_FIRST_CAPPED_INTEGER", "194d7e07:b811cb8a:a51de582:2d56fd21:9bebda43:5cd2c3b9:9c8feba9:086e0be7"),
    ("FINRA_CAP_FIRST_INTEGER_RAW_EXCEEDS_ASSESSED_EQUALS_CAP", "c3053c5c:345a8ad9:5dd65030:d6e8532e:2be7430e:284cd697:7f755023:67455a6b"),
    ("FINRA_CAP_ABOVE", "a407ee65:9bc01a6b:e8951727:329c784c:291f5e14:ff4a7c02:9649bf1a:1aa77d4f"),
    ("FINRA_LOW_PRICE_STRICTLY_BELOW_EXCLUDED", "4b020e61:18ca3c1b:3ab39998:96952c82:86e63826:b40a62c5:1486e6c7:83929432"),
    ("FINRA_LOW_PRICE_EQUALITY_CHARGED", "47ddc09a:8389d3dd:8a749219:5c4af476:0436cfc1:5be31a2c:239378a9:0ac7a579"),
)
_MANIFEST_PATHS: Final = (
    ".github/workflows/regulatory-fees-linux.yml",
    "configs/governance/regulatory-fee-kernel-v1.json",
    "docs/quant/REGULATORY_FEE_KERNEL_V1.md",
    "qme/quant/regulatory_fees.py",
    "schemas/governance/regulatory-fee-kernel-v1.schema.json",
    "tests/fixtures/governance/regulatory-fee-reviewed-sources-v1.json",
    "tests/fixtures/quant/regulatory-fee-kernel-v1.cases.json",
    "tests/quant/test_regulatory_fees.py",
)

type _Request = tuple[str, str, str, str, str, str, str, str, str, str, str, str]
type _ParameterState = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    int,
    str,
    int,
    int,
    int,
    int,
    tuple[str, ...],
]


class RegulatoryFeeInputError(ValueError):
    """Raised for malformed or noncanonical caller inputs."""


class RegulatoryFeeEvidenceError(RuntimeError):
    """Raised when the immutable evidence packet fails verification."""


class RegulatoryFeeAssessment:
    """Immutable calculation result; serialization independently recomputes it."""

    _finra_cap_applied: bool
    _finra_low_price_exclusion_applied: bool
    _finra_taf_raw: str | None
    _parameters: _ParameterState
    _reason_code: str | None
    _request: _Request
    _sec31_raw: str | None
    _status: str
    _total_raw: str | None

    __slots__ = (
        "_finra_cap_applied",
        "_finra_low_price_exclusion_applied",
        "_finra_taf_raw",
        "_parameters",
        "_reason_code",
        "_request",
        "_sec31_raw",
        "_status",
        "_total_raw",
    )

    def __new__(cls, *_args: object, **_kwargs: object) -> RegulatoryFeeAssessment:
        raise TypeError("RegulatoryFeeAssessment is created only by calculation")

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("RegulatoryFeeAssessment cannot be subclassed")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("RegulatoryFeeAssessment is immutable")

    @property
    def status(self) -> str:
        return self._status

    @property
    def reason_code(self) -> str | None:
        return self._reason_code

    @property
    def sec31_raw(self) -> str | None:
        return self._sec31_raw

    @property
    def finra_taf_raw(self) -> str | None:
        return self._finra_taf_raw

    @property
    def total_raw(self) -> str | None:
        return self._total_raw

    @property
    def finra_cap_applied(self) -> bool:
        return self._finra_cap_applied

    @property
    def finra_low_price_exclusion_applied(self) -> bool:
        return self._finra_low_price_exclusion_applied


class VerifiedRegulatoryFeeEvidence:
    """Immutable projection whose serializer replays the repository evidence."""

    _active_blocker_codes: tuple[str, ...]
    _config_sha256: str
    _parameters: _ParameterState
    _semantic_sha256: str
    _source_ids: tuple[str, ...]
    _status: str

    __slots__ = (
        "_active_blocker_codes",
        "_config_sha256",
        "_parameters",
        "_semantic_sha256",
        "_source_ids",
        "_status",
    )

    def __new__(cls, *_args: object, **_kwargs: object) -> VerifiedRegulatoryFeeEvidence:
        raise TypeError("VerifiedRegulatoryFeeEvidence is created only by repository verification")

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("VerifiedRegulatoryFeeEvidence cannot be subclassed")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("VerifiedRegulatoryFeeEvidence is immutable")

    @property
    def status(self) -> str:
        return self._status

    @property
    def config_sha256(self) -> str:
        return self._config_sha256

    @property
    def semantic_sha256(self) -> str:
        return self._semantic_sha256

    @property
    def active_blocker_codes(self) -> tuple[str, ...]:
        return self._active_blocker_codes

    @property
    def source_ids(self) -> tuple[str, ...]:
        return self._source_ids


def _decimal_context() -> Context:
    context = Context(
        prec=256,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
    )
    for signal in (
        Clamped,
        InvalidOperation,
        DivisionByZero,
        Inexact,
        Overflow,
        Rounded,
        Subnormal,
        Underflow,
        FloatOperation,
    ):
        context.traps[signal] = True
    return context


def _parameters_from_method(method: object) -> _ParameterState:
    if type(method) is not dict:
        raise RegulatoryFeeEvidenceError("bounded method is not an object")
    reviewed = method.get("reviewed_date_range")
    sec31 = method.get("sec31")
    finra = method.get("finra_taf")
    numeric = method.get("numeric")
    if (
        type(reviewed) is not dict
        or type(sec31) is not dict
        or type(finra) is not dict
        or type(numeric) is not dict
    ):
        raise RegulatoryFeeEvidenceError("bounded method parameter objects are invalid")
    context_spec = numeric.get("context_spec")
    if type(context_spec) is not dict:
        raise RegulatoryFeeEvidenceError("bounded Decimal context is invalid")
    denominator = sec31.get("denominator")
    finra_rate = finra.get("rate_per_eligible_sold_share")
    finra_cap = finra.get("cap_per_regulatory_trade")
    sec_zero = sec31.get("charge_date_through_2026_04_03_rate_per_million")
    sec_active = sec31.get("charge_date_from_2026_04_04_rate_per_million")
    review_start = reviewed.get("start")
    sec_active_from = sec31.get("active_from")
    review_end = reviewed.get("end")
    precision = context_spec.get("precision")
    rounding = context_spec.get("rounding")
    emin = context_spec.get("emin")
    emax = context_spec.get("emax")
    capitals = context_spec.get("capitals")
    clamp = context_spec.get("clamp")
    traps = context_spec.get("traps")
    if (
        type(denominator) is not str
        or type(finra_rate) is not str
        or type(finra_cap) is not str
        or type(sec_zero) is not str
        or type(sec_active) is not str
        or type(review_start) is not str
        or type(sec_active_from) is not str
        or type(review_end) is not str
        or type(precision) is not int
        or type(rounding) is not str
        or type(emin) is not int
        or type(emax) is not int
        or type(capitals) is not int
        or type(clamp) is not int
        or type(traps) is not list
        or any(type(value) is not str for value in traps)
    ):
        raise RegulatoryFeeEvidenceError("bounded method parameter types are invalid")
    return (
        denominator,
        finra_rate,
        finra_cap,
        sec_zero,
        sec_active,
        review_start,
        sec_active_from,
        review_end,
        precision,
        rounding,
        emin,
        emax,
        capitals,
        clamp,
        tuple(traps),
    )


def _canonical_decimal(value: object, label: str, *, positive: bool = True) -> Decimal:
    if type(value) is not str or _DECIMAL_RE.fullmatch(value) is None or len(value) > 80:
        raise RegulatoryFeeInputError(f"{label} must be canonical ASCII base-10 text")
    try:
        parsed = Decimal(value)
    except DecimalException as exc:
        raise RegulatoryFeeInputError(f"{label} is invalid") from exc
    if not parsed.is_finite() or (positive and parsed <= 0) or (not positive and parsed < 0):
        comparator = "positive" if positive else "non-negative"
        raise RegulatoryFeeInputError(f"{label} must be finite and {comparator}")
    return parsed


def _format_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _parse_date(value: str) -> date | None:
    if type(value) is not str or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _new_assessment(
    request: _Request,
    parameters: _ParameterState,
    status: str,
    reason_code: str | None,
    sec31_raw: str | None,
    finra_taf_raw: str | None,
    total_raw: str | None,
    finra_cap_applied: bool,
    finra_low_price_exclusion_applied: bool,
) -> RegulatoryFeeAssessment:
    result = object.__new__(RegulatoryFeeAssessment)
    object.__setattr__(result, "_request", request)
    object.__setattr__(result, "_parameters", parameters)
    object.__setattr__(result, "_status", status)
    object.__setattr__(result, "_reason_code", reason_code)
    object.__setattr__(result, "_sec31_raw", sec31_raw)
    object.__setattr__(result, "_finra_taf_raw", finra_taf_raw)
    object.__setattr__(result, "_total_raw", total_raw)
    object.__setattr__(result, "_finra_cap_applied", finra_cap_applied)
    object.__setattr__(
        result,
        "_finra_low_price_exclusion_applied",
        finra_low_price_exclusion_applied,
    )
    return result


def _blocked(
    request: _Request,
    parameters: _ParameterState,
    reason: str,
) -> RegulatoryFeeAssessment:
    return _new_assessment(
        request,
        parameters,
        "BLOCKED",
        reason,
        None,
        None,
        None,
        False,
        False,
    )


def _calculate_with_dependencies(
    parameters: _ParameterState,
    decimal_type: Callable[[object], Decimal],
    decimal_exception_type: type[DecimalException],
    input_error_type: type[RegulatoryFeeInputError],
    date_type: type[date],
    context_type: Callable[..., Context],
    rounding_mode: str,
    signal_bindings: tuple[tuple[str, type[DecimalException]], ...],
    decimal_pattern: re.Pattern[str],
    trade_id_pattern: re.Pattern[str],
    result_type: type[RegulatoryFeeAssessment],
    local_context: Callable[[Context], Any],
    *,
    side: object,
    charge_date: object,
    trade_date: object,
    covered_sale_notional: object,
    eligible_sold_shares: object,
    execution_price_per_share: object,
    coverage_classification: object,
    regulatory_trade_id: object,
    aggregation_status: object,
    transaction_status: object,
    pass_through_semantics: object,
    rounding_semantics: object,
) -> RegulatoryFeeAssessment:
    """Compute a bounded raw assessment or a typed semantic blocker."""

    def parse_decimal(value: object, label: str) -> Decimal:
        if (
            type(value) is not str
            or decimal_pattern.fullmatch(value) is None
            or len(value) > 80
        ):
            raise input_error_type(f"{label} must be canonical ASCII base-10 text")
        try:
            parsed = decimal_type(value)
        except decimal_exception_type as exc:
            raise input_error_type(f"{label} is invalid") from exc
        if not parsed.is_finite() or parsed <= 0:
            raise input_error_type(f"{label} must be finite and positive")
        return parsed

    def parse_date(value: str) -> date | None:
        if type(value) is not str or len(value) != 10:
            return None
        try:
            parsed = date_type.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.isoformat() == value else None

    def make_assessment(
        request: _Request,
        status: str,
        reason_code: str | None,
        sec31_raw: str | None,
        finra_taf_raw: str | None,
        total_raw: str | None,
        finra_cap_applied: bool,
        finra_low_price_exclusion_applied: bool,
    ) -> RegulatoryFeeAssessment:
        result = object.__new__(result_type)
        object.__setattr__(result, "_request", request)
        object.__setattr__(result, "_parameters", parameters)
        object.__setattr__(result, "_status", status)
        object.__setattr__(result, "_reason_code", reason_code)
        object.__setattr__(result, "_sec31_raw", sec31_raw)
        object.__setattr__(result, "_finra_taf_raw", finra_taf_raw)
        object.__setattr__(result, "_total_raw", total_raw)
        object.__setattr__(result, "_finra_cap_applied", finra_cap_applied)
        object.__setattr__(
            result,
            "_finra_low_price_exclusion_applied",
            finra_low_price_exclusion_applied,
        )
        return result

    def blocked(request: _Request, reason: str) -> RegulatoryFeeAssessment:
        return make_assessment(
            request,
            "BLOCKED",
            reason,
            None,
            None,
            None,
            False,
            False,
        )

    def format_decimal(value: Decimal) -> str:
        if value == 0:
            return "0"
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered

    (
        sec_denominator_text,
        finra_rate_text,
        finra_cap_text,
        sec_zero_text,
        sec_active_text,
        review_start_text,
        sec_active_from_text,
        review_cutoff_text,
        precision,
        rounding,
        emin,
        emax,
        capitals,
        clamp,
        trap_names,
    ) = parameters
    try:
        sec_denominator = decimal_type(sec_denominator_text)
        finra_rate = decimal_type(finra_rate_text)
        finra_cap = decimal_type(finra_cap_text)
        sec_zero = decimal_type(sec_zero_text)
        sec_active = decimal_type(sec_active_text)
    except decimal_exception_type as exc:
        raise input_error_type("verified economic Decimal parameter is invalid") from exc
    review_start = parse_date(review_start_text)
    sec_active_from = parse_date(sec_active_from_text)
    review_cutoff = parse_date(review_cutoff_text)
    if review_start is None or sec_active_from is None or review_cutoff is None:
        raise input_error_type("verified economic date parameter is invalid")
    if rounding != "ROUND_HALF_EVEN":
        raise input_error_type("unsupported verified Decimal rounding mode")
    context = context_type(
        prec=precision,
        rounding=rounding_mode,
        Emin=emin,
        Emax=emax,
        capitals=capitals,
        clamp=clamp,
    )
    if tuple(name for name, _signal in signal_bindings) != trap_names:
        raise input_error_type("verified Decimal trap inventory drift")
    for _name, signal in signal_bindings:
        context.traps[signal] = True

    text_values = (
        side,
        charge_date,
        trade_date,
        covered_sale_notional,
        eligible_sold_shares,
        execution_price_per_share,
        coverage_classification,
        regulatory_trade_id,
        aggregation_status,
        transaction_status,
        pass_through_semantics,
        rounding_semantics,
    )
    if any(type(value) is not str for value in text_values):
        raise input_error_type("every request field must be exact str")
    request: _Request = text_values  # type: ignore[assignment]
    if side not in {"BUY", "SELL"}:
        raise input_error_type("side must be BUY or SELL")
    notional = parse_decimal(covered_sale_notional, "covered_sale_notional")
    shares = parse_decimal(eligible_sold_shares, "eligible_sold_shares")
    price = parse_decimal(execution_price_per_share, "execution_price_per_share")
    try:
        with local_context(context):
            if notional != shares * price:
                return blocked(request, "NOTIONAL_PRICE_SHARE_IDENTITY_MISMATCH")
    except decimal_exception_type as exc:
        raise input_error_type("exact notional identity arithmetic failed") from exc
    parsed_charge_date = parse_date(str(charge_date))
    parsed_trade_date = parse_date(str(trade_date))
    if parsed_charge_date is None:
        return blocked(request, "CHARGE_DATE_MISSING_OR_AMBIGUOUS")
    if parsed_trade_date is None:
        return blocked(request, "TRADE_DATE_MISSING_OR_AMBIGUOUS")
    if not trade_id_pattern.fullmatch(str(regulatory_trade_id)):
        return blocked(request, "REGULATORY_TRADE_ID_INVALID")
    if coverage_classification != "COVERED_EQUITY_ELIGIBLE_NO_EXEMPTION":
        return blocked(request, "COVERAGE_OR_EXEMPTION_UNRESOLVED")
    if aggregation_status != "PRE_AGGREGATED_SINGLE_REGULATORY_TRADE":
        return blocked(request, "AGGREGATION_NOT_PRECOMPUTED_EXPLICITLY")
    if transaction_status != "FINAL_NOT_CANCELLED_OR_CORRECTED":
        return blocked(request, "CANCELLATION_OR_CORRECTION_STATUS_UNRESOLVED")
    if pass_through_semantics != "NOT_APPLIED":
        return blocked(request, "PASS_THROUGH_SEMANTICS_UNAUTHORIZED")
    if rounding_semantics != "RAW_EXACT_DECIMAL_NO_ROUNDING":
        return blocked(request, "ROUNDING_SEMANTICS_UNAUTHORIZED")
    if not (
        review_start <= parsed_charge_date <= review_cutoff
        and review_start <= parsed_trade_date <= review_cutoff
    ):
        return blocked(request, "DATE_OUTSIDE_REVIEWED_SCHEDULE")
    if side == "BUY":
        return make_assessment(
            request,
            "CALCULATED_RAW_REGULATORY_ASSESSMENT_ONLY",
            None,
            "0",
            "0",
            "0",
            False,
            False,
        )
    sec_rate = sec_zero if parsed_charge_date < sec_active_from else sec_active
    try:
        with local_context(context):
            sec31 = notional * sec_rate / sec_denominator
            low_price = price < finra_rate
            taf_before_cap = finra_rate * shares
            cap_applied = not low_price and taf_before_cap > finra_cap
            taf = decimal_type(0) if low_price else min(taf_before_cap, finra_cap)
            total = sec31 + taf
    except decimal_exception_type as exc:
        raise input_error_type("exact regulatory fee arithmetic failed") from exc
    return make_assessment(
        request,
        "CALCULATED_RAW_REGULATORY_ASSESSMENT_ONLY",
        None,
        format_decimal(sec31),
        format_decimal(taf),
        format_decimal(total),
        cap_applied,
        low_price,
    )


def _make_calculator(
    implementation: Callable[..., RegulatoryFeeAssessment],
) -> Callable[..., RegulatoryFeeAssessment]:
    decimal_type = Decimal
    decimal_exception_type = DecimalException
    input_error_type = RegulatoryFeeInputError
    date_type = date
    context_type = Context
    rounding_mode = ROUND_HALF_EVEN
    decimal_pattern = _DECIMAL_RE
    trade_id_pattern = _TRADE_ID_RE
    result_type = RegulatoryFeeAssessment
    local_context = localcontext
    signal_bindings = (
        ("Clamped", Clamped),
        ("InvalidOperation", InvalidOperation),
        ("DivisionByZero", DivisionByZero),
        ("Inexact", Inexact),
        ("Overflow", Overflow),
        ("Rounded", Rounded),
        ("Subnormal", Subnormal),
        ("Underflow", Underflow),
        ("FloatOperation", FloatOperation),
    )

    def calculator(parameters: _ParameterState, **request: object) -> RegulatoryFeeAssessment:
        return implementation(
            parameters,
            decimal_type,
            decimal_exception_type,
            input_error_type,
            date_type,
            context_type,
            rounding_mode,
            signal_bindings,
            decimal_pattern,
            trade_id_pattern,
            result_type,
            local_context,
            **request,
        )

    return calculator


_calculate_with_parameters = _make_calculator(_calculate_with_dependencies)
del _make_calculator


def _assess_with_worker(
    worker: Callable[..., RegulatoryFeeAssessment],
    *,
    side: object,
    charge_date: object,
    trade_date: object,
    covered_sale_notional: object,
    eligible_sold_shares: object,
    execution_price_per_share: object,
    coverage_classification: object,
    regulatory_trade_id: object,
    aggregation_status: object,
    transaction_status: object,
    pass_through_semantics: object,
    rounding_semantics: object,
) -> RegulatoryFeeAssessment:
    """Compute with immutable literals; authoritative emission replays artifacts."""

    parameters: _ParameterState = (
        "1000000",
        "0.000195",
        "9.79",
        "0",
        "20.60",
        "2026-01-01",
        "2026-04-04",
        "2026-08-14",
        256,
        "ROUND_HALF_EVEN",
        -999999,
        999999,
        1,
        0,
        (
            "Clamped",
            "InvalidOperation",
            "DivisionByZero",
            "Inexact",
            "Overflow",
            "Rounded",
            "Subnormal",
            "Underflow",
            "FloatOperation",
        ),
    )
    return worker(
        parameters,
        side=side,
        charge_date=charge_date,
        trade_date=trade_date,
        covered_sale_notional=covered_sale_notional,
        eligible_sold_shares=eligible_sold_shares,
        execution_price_per_share=execution_price_per_share,
        coverage_classification=coverage_classification,
        regulatory_trade_id=regulatory_trade_id,
        aggregation_status=aggregation_status,
        transaction_status=transaction_status,
        pass_through_semantics=pass_through_semantics,
        rounding_semantics=rounding_semantics,
    )


def _make_public_assessor(
    implementation: Callable[..., RegulatoryFeeAssessment],
    worker: Callable[..., RegulatoryFeeAssessment],
) -> Callable[..., RegulatoryFeeAssessment]:
    def assessor(**request: object) -> RegulatoryFeeAssessment:
        return implementation(worker, **request)

    return assessor


assess_regulatory_fees = _make_public_assessor(
    _assess_with_worker,
    _calculate_with_parameters,
)
del _make_public_assessor


def _assessment_projection(result: RegulatoryFeeAssessment) -> dict[str, object]:
    return {
        "status": result._status,
        "reason_code": result._reason_code,
        "sec31_raw": result._sec31_raw,
        "finra_taf_raw": result._finra_taf_raw,
        "total_raw": result._total_raw,
        "finra_cap_applied": result._finra_cap_applied,
        "finra_low_price_exclusion_applied": result._finra_low_price_exclusion_applied,
    }


def _serialize_regulatory_fee_assessment(
    result: RegulatoryFeeAssessment,
    repository_root: str | Path,
    worker: Callable[..., RegulatoryFeeAssessment],
    projector: Callable[[RegulatoryFeeAssessment], dict[str, object]],
    verifier: Callable[[str | Path], VerifiedRegulatoryFeeEvidence],
    result_type: type[RegulatoryFeeAssessment],
    error_type: type[RegulatoryFeeInputError],
) -> Mapping[str, object]:
    """Reverify artifacts and recalculate before emitting authoritative fields."""

    if type(result) is not result_type:
        raise error_type("result must be exact RegulatoryFeeAssessment")
    request = result._request
    if type(request) is not tuple or len(request) != 12 or any(type(item) is not str for item in request):
        raise error_type("result request commitment is invalid")
    parameters = result._parameters
    if type(parameters) is not tuple or len(parameters) != 15:
        raise error_type("result parameter commitment is invalid")
    verified = verifier(repository_root)
    if parameters != verified._parameters:
        raise error_type("result parameters differ from verified artifacts")
    replay = worker(
        verified._parameters,
        side=request[0],
        charge_date=request[1],
        trade_date=request[2],
        covered_sale_notional=request[3],
        eligible_sold_shares=request[4],
        execution_price_per_share=request[5],
        coverage_classification=request[6],
        regulatory_trade_id=request[7],
        aggregation_status=request[8],
        transaction_status=request[9],
        pass_through_semantics=request[10],
        rounding_semantics=request[11],
    )
    expected = projector(replay)
    if projector(result) != expected:
        raise error_type("result differs from independent recalculation")
    return expected


def _make_assessment_serializer(
    implementation: Callable[
        [
            RegulatoryFeeAssessment,
            str | Path,
            Callable[..., RegulatoryFeeAssessment],
            Callable[[RegulatoryFeeAssessment], dict[str, object]],
            Callable[[str | Path], VerifiedRegulatoryFeeEvidence],
            type[RegulatoryFeeAssessment],
            type[RegulatoryFeeInputError],
        ],
        Mapping[str, object],
    ],
    worker: Callable[..., RegulatoryFeeAssessment],
    projector: Callable[[RegulatoryFeeAssessment], dict[str, object]],
    verifier: Callable[[str | Path], VerifiedRegulatoryFeeEvidence],
    result_type: type[RegulatoryFeeAssessment],
    error_type: type[RegulatoryFeeInputError],
) -> Callable[[RegulatoryFeeAssessment, str | Path], Mapping[str, object]]:
    def serializer(
        result: RegulatoryFeeAssessment,
        repository_root: str | Path,
    ) -> Mapping[str, object]:
        return implementation(
            result,
            repository_root,
            worker,
            projector,
            verifier,
            result_type,
            error_type,
        )

    return serializer


def _normalize_grouped(value: object, label: str) -> str:
    if type(value) is not str:
        raise RegulatoryFeeEvidenceError(f"{label} must be grouped SHA-256")
    groups = value.split(":")
    if len(groups) != 8 or any(
        len(group) != 8 or any(char not in "0123456789abcdef" for char in group)
        for group in groups
    ):
        raise RegulatoryFeeEvidenceError(f"{label} must be exactly 8 groups of lowercase hex")
    return "".join(groups)


def _normalize_manifest_digest(value: object, label: str) -> str:
    if type(value) is not str:
        raise RegulatoryFeeEvidenceError(f"{label} must be SHA-256")
    if len(value) == 64 and all(char in "0123456789abcdef" for char in value):
        return value
    return _normalize_grouped(value, label)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RegulatoryFeeEvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _nonfinite(token: str) -> None:
    raise RegulatoryFeeEvidenceError(f"non-finite JSON token: {token}")


def _path_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        getattr(info, "st_file_attributes", 0),
    )


def _confined_bytes(
    relative: Path,
    root: Path,
    *,
    limit: int = _MAX_JSON_BYTES,
    _interleave_hook: Callable[[Path, Path], None] | None = None,
) -> bytes:
    if relative.is_absolute() or ".." in relative.parts:
        raise RegulatoryFeeEvidenceError("path is not repository relative")
    resolved_root = root.resolve(strict=True)
    target = resolved_root.joinpath(relative)
    snapshots: list[tuple[Path, tuple[int, int, int, int, int]]] = []
    root_info = resolved_root.lstat()
    snapshots.append((resolved_root, _path_identity(root_info)))
    cursor = resolved_root
    for index, part in enumerate(relative.parts):
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise RegulatoryFeeEvidenceError(f"path is missing: {relative.as_posix()}") from exc
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400):
            raise RegulatoryFeeEvidenceError(f"linked/reparse path forbidden: {relative.as_posix()}")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise RegulatoryFeeEvidenceError(f"ancestor is not a directory: {relative.as_posix()}")
        snapshots.append((cursor, _path_identity(info)))
    try:
        relative_target = target.resolve(strict=True).relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise RegulatoryFeeEvidenceError(f"path escapes or is missing: {relative.as_posix()}") from exc
    if relative_target != relative:
        raise RegulatoryFeeEvidenceError(f"noncanonical path forbidden: {relative.as_posix()}")
    if _interleave_hook is not None:
        _interleave_hook(resolved_root, target)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        raise RegulatoryFeeEvidenceError(f"file open failed: {relative.as_posix()}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise RegulatoryFeeEvidenceError(f"nonregular or oversized file: {relative.as_posix()}")
        raw = b""
        while len(raw) <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - len(raw)))
            if not chunk:
                break
            raw += chunk
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    for component, expected_identity in snapshots:
        try:
            component_info = component.lstat()
        except OSError as exc:
            raise RegulatoryFeeEvidenceError(
                f"path changed during read: {relative.as_posix()}"
            ) from exc
        attributes = getattr(component_info, "st_file_attributes", 0)
        if (
            stat.S_ISLNK(component_info.st_mode)
            or bool(attributes & 0x400)
            or _path_identity(component_info) != expected_identity
        ):
            raise RegulatoryFeeEvidenceError(f"path changed during read: {relative.as_posix()}")
    try:
        final_resolved = target.resolve(strict=True)
        final_relative = final_resolved.relative_to(resolved_root)
        final = target.stat()
    except (OSError, ValueError) as exc:
        raise RegulatoryFeeEvidenceError(f"path changed during read: {relative.as_posix()}") from exc
    if final_relative != relative:
        raise RegulatoryFeeEvidenceError(f"path changed during read: {relative.as_posix()}")
    before_id = (before.st_dev, before.st_ino, before.st_nlink, before.st_size, before.st_mtime_ns)
    after_id = (after.st_dev, after.st_ino, after.st_nlink, after.st_size, after.st_mtime_ns)
    final_id = (final.st_dev, final.st_ino, final.st_nlink, final.st_size, final.st_mtime_ns)
    if len(raw) > limit or before_id != after_id or after_id != final_id:
        raise RegulatoryFeeEvidenceError(f"file changed during read: {relative.as_posix()}")
    return raw


def _load(relative: Path, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _confined_bytes(relative, root).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegulatoryFeeEvidenceError(f"invalid strict JSON: {relative.as_posix()}") from exc
    if type(value) is not dict:
        raise RegulatoryFeeEvidenceError(f"JSON root must be object: {relative.as_posix()}")
    return value


def _raw_hash(relative: Path, root: Path) -> str:
    return hashlib.sha256(_confined_bytes(relative, root)).hexdigest()


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _semantic_hash(config: Mapping[str, Any]) -> str:
    projection = dict(config)
    projection.pop("semantic_sha256", None)
    return hashlib.sha256(_canonical_json_bytes(projection)).hexdigest()


def _replay_bound_manifest(relative: Path, root: Path) -> None:
    manifest = _load(relative, root)
    inventory = _BOUND_MANIFEST_INVENTORY.get(relative.as_posix())
    if inventory is None:
        raise RegulatoryFeeEvidenceError(f"unregistered bound manifest: {relative.as_posix()}")
    expected_identity, expected_paths = inventory
    expected_root_keys = {*expected_identity, "artifacts"}
    if set(manifest) != expected_root_keys:
        raise RegulatoryFeeEvidenceError(f"manifest root key drift: {relative.as_posix()}")
    for key, expected in expected_identity.items():
        actual = manifest.get(key)
        if key == "fixture_outputs_sha256":
            if _normalize_manifest_digest(actual, key) != _normalize_grouped(expected, key):
                raise RegulatoryFeeEvidenceError(f"manifest identity drift: {relative.as_posix()}")
        elif actual != expected:
            raise RegulatoryFeeEvidenceError(f"manifest identity drift: {relative.as_posix()}")
    artifacts = manifest.get("artifacts")
    rows: list[tuple[str, object]] = []
    if type(artifacts) is dict:
        for path, digest in artifacts.items():
            if type(path) is not str:
                raise RegulatoryFeeEvidenceError(
                    f"manifest path type drift: {relative.as_posix()}"
                )
            rows.append((path, digest))
    elif type(artifacts) is list:
        for row in artifacts:
            if type(row) is not dict or set(row) != {"path", "sha256"}:
                raise RegulatoryFeeEvidenceError(
                    f"manifest row shape drift: {relative.as_posix()}"
                )
            path = row.get("path")
            if type(path) is not str:
                raise RegulatoryFeeEvidenceError(
                    f"manifest path type drift: {relative.as_posix()}"
                )
            rows.append((path, row.get("sha256")))
    else:
        raise RegulatoryFeeEvidenceError(
            f"manifest artifacts missing: {relative.as_posix()}"
        )
    paths = tuple(path for path, _digest in rows)
    if len(paths) != len(set(paths)) or paths != expected_paths:
        raise RegulatoryFeeEvidenceError(
            f"manifest ordered path inventory drift: {relative.as_posix()}"
        )
    for path, digest in rows:
        expected = _normalize_manifest_digest(digest, f"{relative.as_posix()}:{path}")
        if _raw_hash(Path(path), root) != expected:
            raise RegulatoryFeeEvidenceError(f"manifest leaf drift: {path}")


def _verify_authority(root: Path, config: Mapping[str, Any]) -> None:
    authority = config.get("authority")
    expected_keys = {
        "protected_main_commit",
        "protected_main_tree",
        "economic_promotion_v2_semantic_sha256",
        *_AUTHORITY_HASHES.keys(),
    }
    if (
        type(authority) is not dict
        or set(authority) != expected_keys
        or authority.get("protected_main_commit")
        != "6485223e:a0349cb6:a66cca9e:2151159f:d46c2c88"
        or authority.get("protected_main_tree")
        != "d2008404:9ca626f8:d7e8ac49:c23264bb:bcebd041"
        or authority.get("economic_promotion_v2_semantic_sha256")
        != "3b35015f:fa528926:3a9b125d:913fd99f:25027c1f:c7ebea82:7c962654:e1406ff4"
    ):
        raise RegulatoryFeeEvidenceError("protected authority identity drift")
    for key, (path, grouped_hash) in _AUTHORITY_HASHES.items():
        row = authority.get(key)
        if row != {"path": path, "sha256": grouped_hash}:
            raise RegulatoryFeeEvidenceError(f"authority binding drift: {key}")
        if _raw_hash(Path(path), root) != _normalize_grouped(grouped_hash, key):
            raise RegulatoryFeeEvidenceError(f"authority bytes drift: {key}")
    for manifest_key in (
        "m0_manifest",
        "crosswalk_v3_manifest",
        "freeze_v4_manifest",
        "economic_promotion_v2_manifest",
        "accounting_equations_v1_manifest",
    ):
        _replay_bound_manifest(Path(_AUTHORITY_HASHES[manifest_key][0]), root)
    crosswalk = _load(Path(_AUTHORITY_HASHES["crosswalk_v3"][0]), root)
    rows = crosswalk.get("entries")
    if type(rows) is not list:
        raise RegulatoryFeeEvidenceError("crosswalk entries missing")
    expected_rows = {
        "S0A1-120-048": ("SEC_SECTION_31_DATED_RATE", "/risk_and_capacity_mandate/sell_fee_sources/0"),
        "S0A1-120-049": ("FINRA_TAF_DATED_RATE", "/risk_and_capacity_mandate/sell_fee_sources/1"),
    }
    selected: dict[str, Any] = {
        row["id"]: row
        for row in rows
        if type(row) is dict and row.get("id") in expected_rows
    }
    for row_id, (value, destination) in expected_rows.items():
        row = selected.get(row_id)
        if row is None or row.get("value") != value or row.get("disposition") != "RETAIN_TYPED_BLOCKER" or row.get("status") != "TYPED_BLOCKER" or row.get("destination_json_pointers") != [destination] or row.get("reason") != "Dated fee-rate receipts and implementation evidence are pending.":
            raise RegulatoryFeeEvidenceError(f"crosswalk row drift: {row_id}")
    economic = _load(Path(_AUTHORITY_HASHES["economic_promotion_v2"][0]), root)
    if economic.get("semantic_sha256") != authority.get(
        "economic_promotion_v2_semantic_sha256"
    ):
        raise RegulatoryFeeEvidenceError("economic-promotion semantic binding drift")
    risk = economic.get("risk_and_capacity_mandate")
    if type(risk) is not dict or risk.get("sell_fee_sources") != {
        "0": "SEC_SECTION_31_DATED_RATE",
        "1": "FINRA_TAF_DATED_RATE",
    }:
        raise RegulatoryFeeEvidenceError("economic-promotion sell-fee source drift")
    freeze = _load(Path(_AUTHORITY_HASHES["freeze_v4"][0]), root)
    rows = freeze.get("unresolved_blockers")
    actual = tuple(
        (row.get("blocker_code"), row.get("ticket_id"), row.get("category"), row.get("description"))
        for row in rows
    ) if type(rows) is list else ()
    if actual != _BLOCKERS:
        raise RegulatoryFeeEvidenceError("Freeze V4 13-blocker inventory drift")


def _verify_sources(source: Mapping[str, Any]) -> tuple[str, ...]:
    expected_root = {
        "schema_version",
        "artifact_id",
        "ticket_id",
        "status",
        "retrieval_date",
        "retrieved_at",
        "retrieval_time_status",
        "reviewed_bytes_convention",
        "receipts",
        "limitations",
        "source_hierarchy",
    }
    if set(source) != expected_root or (
        source.get("schema_version"),
        source.get("artifact_id"),
        source.get("ticket_id"),
        source.get("status"),
        source.get("retrieval_date"),
        source.get("retrieved_at"),
        source.get("retrieval_time_status"),
        source.get("reviewed_bytes_convention"),
    ) != (
        "qme.regulatory_fee_reviewed_sources.v1",
        "NEE-205-REGULATORY-FEE-REVIEWED-SOURCES-V1",
        "NEE-205",
        "IMMUTABLE_REVIEWED_PRIMARY_SOURCE_EXCERPTS_BOUNDED_TO_2026_08_14",
        "2026-08-14",
        None,
        "EXACT_TIMESTAMP_UNAVAILABLE_NOT_INFERRED",
        "RENDERED_PRIMARY_SOURCE_TEXT_UTF8_NO_NORMALIZATION_NO_TRAILING_LF",
    ):
        raise RegulatoryFeeEvidenceError("source fixture identity drift")
    receipts = source.get("receipts")
    if type(receipts) is not list or len(receipts) != len(_SOURCE_EXPECTED):
        raise RegulatoryFeeEvidenceError("source receipt inventory drift")
    ids: list[str] = []
    receipt_keys = {"source_id", "url", "publisher", "reviewed_at_page_date", "reviewed_excerpt_utf8", "reviewed_excerpt_size_bytes", "reviewed_excerpt_sha256", "registered_facts"}
    for row, expected, metadata in zip(receipts, _SOURCE_EXPECTED, _SOURCE_METADATA, strict=True):
        if type(row) is not dict:
            raise RegulatoryFeeEvidenceError("source receipt must be object")
        if set(row) != receipt_keys:
            raise RegulatoryFeeEvidenceError("source receipt key inventory drift")
        source_id, url, size, grouped_hash = expected
        publisher, page_date, facts = metadata
        if row.get("source_id") != source_id or row.get("url") != url or row.get("reviewed_excerpt_size_bytes") != size or row.get("reviewed_excerpt_sha256") != grouped_hash:
            raise RegulatoryFeeEvidenceError(f"source receipt metadata drift: {source_id}")
        if row.get("publisher") != publisher or row.get("reviewed_at_page_date") != page_date or tuple(row.get("registered_facts", ())) != facts:
            raise RegulatoryFeeEvidenceError(f"source receipt semantics drift: {source_id}")
        excerpt = row.get("reviewed_excerpt_utf8")
        if type(excerpt) is not str:
            raise RegulatoryFeeEvidenceError(f"source excerpt missing: {source_id}")
        raw = excerpt.encode("utf-8")
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != _normalize_grouped(grouped_hash, source_id):
            raise RegulatoryFeeEvidenceError(f"source excerpt bytes drift: {source_id}")
        ids.append(source_id)
    if source.get("source_hierarchy") != {
        "rate_and_cap_for_2026": "FINRA_2026_FEE_ADJUSTMENT_SCHEDULE",
        "coverage_exemptions_and_strict_low_price_operator": "FINRA_MEMBER_REGULATORY_FEE_RULE",
        "trade_date_cancellation_aggregation_and_no_rounding_context": "FINRA_TAF_FAQ",
        "stale_rulebook_numeric_rates_are_not_used": True,
    }:
        raise RegulatoryFeeEvidenceError("source hierarchy drift")
    if tuple(source.get("limitations", ())) != _SOURCE_LIMITATIONS:
        raise RegulatoryFeeEvidenceError("source limitations drift")
    return tuple(ids)


def _verify_kats_with_worker(
    kats: Mapping[str, Any],
    parameters: _ParameterState,
    worker: Callable[..., RegulatoryFeeAssessment],
) -> None:
    if set(kats) != {"schema_version", "artifact_id", "method_id", "cases"} or (
        kats.get("schema_version"), kats.get("artifact_id"), kats.get("method_id")
    ) != (
        "qme.regulatory_fee_kernel_cases.v1",
        "NEE-205-REGULATORY-FEE-KERNEL-KAT-V1",
        "qme-bounded-raw-regulatory-assessment-v1",
    ):
        raise RegulatoryFeeEvidenceError("KAT identity drift")
    cases = kats.get("cases")
    required_inputs = _METHOD["required_inputs"]
    if type(cases) is not list or len(cases) != len(_KAT_CASE_HASHES):
        raise RegulatoryFeeEvidenceError("KAT inventory drift")
    if type(required_inputs) is not list or any(
        type(value) is not str for value in required_inputs
    ):
        raise RegulatoryFeeEvidenceError("KAT required-input inventory drift")
    seen: set[str] = set()
    for row, (expected_case_id, grouped_case_hash) in zip(cases, _KAT_CASE_HASHES, strict=True):
        if type(row) is not dict or set(row) != {"case_id", "input", "expected"}:
            raise RegulatoryFeeEvidenceError("KAT row shape drift")
        case_id = row.get("case_id")
        inputs = row.get("input")
        expected = row.get("expected")
        if type(case_id) is not str or case_id != expected_case_id or case_id in seen or type(inputs) is not dict or type(expected) is not dict:
            raise RegulatoryFeeEvidenceError("KAT row identity drift")
        if set(inputs) != set(required_inputs) or set(expected) != {
            "status",
            "reason_code",
            "sec31_raw",
            "finra_taf_raw",
            "total_raw",
            "finra_cap_applied",
            "finra_low_price_exclusion_applied",
        }:
            raise RegulatoryFeeEvidenceError(f"KAT field inventory drift: {case_id}")
        case_hash = hashlib.sha256(_canonical_json_bytes(row)).hexdigest()
        if case_hash != _normalize_grouped(grouped_case_hash, case_id):
            raise RegulatoryFeeEvidenceError(f"KAT exact bytes drift: {case_id}")
        seen.add(case_id)
        try:
            result = worker(parameters, **inputs)
        except (RegulatoryFeeInputError, TypeError) as exc:
            raise RegulatoryFeeEvidenceError(f"KAT input invalid: {case_id}") from exc
        if _assessment_projection(result) != expected:
            raise RegulatoryFeeEvidenceError(f"KAT output drift: {case_id}")


def _make_kat_verifier(
    implementation: Callable[
        [
            Mapping[str, Any],
            _ParameterState,
            Callable[..., RegulatoryFeeAssessment],
        ],
        None,
    ],
    worker: Callable[..., RegulatoryFeeAssessment],
) -> Callable[[Mapping[str, Any], _ParameterState], None]:
    def verifier(kats: Mapping[str, Any], parameters: _ParameterState) -> None:
        implementation(kats, parameters, worker)

    return verifier


_verify_kats = _make_kat_verifier(_verify_kats_with_worker, _calculate_with_parameters)
del _make_kat_verifier


def verify_regulatory_fee_manifest(repository_root: str | Path) -> None:
    root = Path(repository_root)
    manifest = _load(MANIFEST_PATH, root)
    if set(manifest) != {"schema_version", "artifact_id", "ticket_id", "status", "artifacts", "limitations"} or (
        manifest.get("schema_version"), manifest.get("artifact_id"), manifest.get("ticket_id"), manifest.get("status")
    ) != (
        "qme.regulatory_fee_kernel_manifest.v1",
        "NEE-205-REGULATORY-FEE-KERNEL-MANIFEST-V1",
        "NEE-205",
        "REVIEWED_BOUNDED_CANDIDATE_ZERO_BLOCKERS_RESOLVED",
    ):
        raise RegulatoryFeeEvidenceError("outer manifest identity drift")
    rows = manifest.get("artifacts")
    if type(rows) is not list or tuple(row.get("path") for row in rows if type(row) is dict) != _MANIFEST_PATHS:
        raise RegulatoryFeeEvidenceError("outer manifest path inventory drift")
    for row in rows:
        if type(row) is not dict or set(row) != {"path", "sha256"}:
            raise RegulatoryFeeEvidenceError("outer manifest row shape drift")
        path = row.get("path")
        grouped_hash = row.get("sha256")
        if type(path) is not str or _raw_hash(Path(path), root) != _normalize_grouped(grouped_hash, path):
            raise RegulatoryFeeEvidenceError(f"outer manifest artifact drift: {path}")
    if manifest.get("limitations") != list(_NONCLAIMS):
        raise RegulatoryFeeEvidenceError("outer manifest limitations drift")


def _new_verified(
    config_sha256: str,
    semantic_sha256: str,
    active_blocker_codes: tuple[str, ...],
    source_ids: tuple[str, ...],
    parameters: _ParameterState,
    status: str,
) -> VerifiedRegulatoryFeeEvidence:
    result = object.__new__(VerifiedRegulatoryFeeEvidence)
    object.__setattr__(result, "_config_sha256", config_sha256)
    object.__setattr__(result, "_semantic_sha256", semantic_sha256)
    object.__setattr__(result, "_active_blocker_codes", active_blocker_codes)
    object.__setattr__(result, "_source_ids", source_ids)
    object.__setattr__(result, "_parameters", parameters)
    object.__setattr__(result, "_status", status)
    return result


def verify_regulatory_fee_evidence(repository_root: str | Path) -> VerifiedRegulatoryFeeEvidence:
    root = Path(repository_root)
    expected_hashes = (
        (CONFIG_PATH, EXPECTED_CONFIG_SHA256),
        (SCHEMA_PATH, EXPECTED_SCHEMA_SHA256),
        (SOURCE_FIXTURE_PATH, EXPECTED_SOURCE_FIXTURE_SHA256),
        (KAT_FIXTURE_PATH, EXPECTED_KAT_FIXTURE_SHA256),
    )
    for path, grouped_hash in expected_hashes:
        if _raw_hash(path, root) != _normalize_grouped(grouped_hash, path.as_posix()):
            raise RegulatoryFeeEvidenceError(f"pinned artifact drift: {path.as_posix()}")
    config = _load(CONFIG_PATH, root)
    schema = _load(SCHEMA_PATH, root)
    source = _load(SOURCE_FIXTURE_PATH, root)
    kats = _load(KAT_FIXTURE_PATH, root)
    if set(schema) != {"$schema", "$id", "title", "const"} or schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema" or schema.get("$id") != "qme.regulatory_fee_kernel.v1" or schema.get("title") != "Exact NEE-205 bounded regulatory-fee authority and kernel candidate" or schema.get("const") != config:
        raise RegulatoryFeeEvidenceError("schema exact-const parity drift")
    try:
        Draft202012Validator.check_schema(schema)
        validation_errors = list(Draft202012Validator(schema).iter_errors(config))
    except SchemaError as exc:
        raise RegulatoryFeeEvidenceError("schema is invalid") from exc
    if validation_errors:
        raise RegulatoryFeeEvidenceError("config fails exact schema")
    if {key: config.get(key) for key in _IDENTITY} != _IDENTITY or set(config) != {
        "$schema", "schema_version", "artifact_id", "ticket_id", "status", "semantic_sha256", "authority", "source_register", "bounded_method", "typed_blockers", "active_freeze_v4_blockers", "nonclaims", "claims"
    }:
        raise RegulatoryFeeEvidenceError("config identity or root inventory drift")
    semantic = _semantic_hash(config)
    if semantic != _normalize_grouped(EXPECTED_SEMANTIC_SHA256, "semantic") or config.get("semantic_sha256") != EXPECTED_SEMANTIC_SHA256:
        raise RegulatoryFeeEvidenceError("config semantic hash drift")
    if config.get("claims") != _CLAIMS or tuple(config.get("nonclaims", ())) != _NONCLAIMS:
        raise RegulatoryFeeEvidenceError("claims/nonclaims drift")
    blocker_rows = config.get("active_freeze_v4_blockers")
    actual_blockers = tuple(
        (row.get("blocker_code"), row.get("ticket_id"), row.get("category"), row.get("description"))
        for row in blocker_rows
    ) if type(blocker_rows) is list else ()
    if actual_blockers != _BLOCKERS:
        raise RegulatoryFeeEvidenceError("config blocker inventory drift")
    _verify_authority(root, config)
    source_ids = _verify_sources(source)
    source_binding = config.get("source_register")
    if source_binding != {
        "path": SOURCE_FIXTURE_PATH.as_posix(),
        "sha256": EXPECTED_SOURCE_FIXTURE_SHA256,
        "reviewed_through_date": "2026-08-14",
        "complete_web_pages_captured": False,
    }:
        raise RegulatoryFeeEvidenceError("source register binding drift")
    method = config.get("bounded_method")
    if method != _METHOD:
        raise RegulatoryFeeEvidenceError("bounded method semantics drift")
    parameters = _parameters_from_method(method)
    typed_rows = config.get("typed_blockers")
    actual_typed = tuple(
        (row.get("code"), row.get("clear_condition")) for row in typed_rows
    ) if type(typed_rows) is list else ()
    if actual_typed != _TYPED_BLOCKERS:
        raise RegulatoryFeeEvidenceError("typed blocker inventory drift")
    _verify_kats(kats, parameters)
    verify_regulatory_fee_manifest(root)
    status = config.get("status")
    if type(status) is not str:
        raise RegulatoryFeeEvidenceError("config status type drift")
    return _new_verified(
        _raw_hash(CONFIG_PATH, root),
        semantic,
        tuple(row[0] for row in _BLOCKERS),
        source_ids,
        parameters,
        status,
    )


serialize_regulatory_fee_assessment = _make_assessment_serializer(
    _serialize_regulatory_fee_assessment,
    _calculate_with_parameters,
    _assessment_projection,
    verify_regulatory_fee_evidence,
    RegulatoryFeeAssessment,
    RegulatoryFeeInputError,
)
del _make_assessment_serializer


def _verified_evidence_projection(
    value: VerifiedRegulatoryFeeEvidence,
) -> dict[str, object]:
    return {
        "status": value._status,
        "config_sha256": value._config_sha256,
        "semantic_sha256": value._semantic_sha256,
        "active_blocker_codes": list(value._active_blocker_codes),
        "source_ids": list(value._source_ids),
    }


def _serialize_verified_regulatory_fee_evidence(
    value: VerifiedRegulatoryFeeEvidence,
    repository_root: str | Path,
    verifier: Callable[[str | Path], VerifiedRegulatoryFeeEvidence],
    projector: Callable[[VerifiedRegulatoryFeeEvidence], dict[str, object]],
    evidence_type: type[VerifiedRegulatoryFeeEvidence],
    error_type: type[RegulatoryFeeEvidenceError],
) -> Mapping[str, object]:
    """Replay repository artifacts and emit only the replay-derived projection."""

    if type(value) is not evidence_type:
        raise error_type("value must be exact VerifiedRegulatoryFeeEvidence")
    replay = verifier(repository_root)
    projection = projector(replay)
    candidate = projector(value)
    if candidate != projection or value._parameters != replay._parameters:
        raise error_type("verified value differs from repository replay")
    return projection


def _make_verified_evidence_serializer(
    implementation: Callable[
        [
            VerifiedRegulatoryFeeEvidence,
            str | Path,
            Callable[[str | Path], VerifiedRegulatoryFeeEvidence],
            Callable[[VerifiedRegulatoryFeeEvidence], dict[str, object]],
            type[VerifiedRegulatoryFeeEvidence],
            type[RegulatoryFeeEvidenceError],
        ],
        Mapping[str, object],
    ],
    verifier: Callable[[str | Path], VerifiedRegulatoryFeeEvidence],
    projector: Callable[[VerifiedRegulatoryFeeEvidence], dict[str, object]],
    evidence_type: type[VerifiedRegulatoryFeeEvidence],
    error_type: type[RegulatoryFeeEvidenceError],
) -> Callable[[VerifiedRegulatoryFeeEvidence, str | Path], Mapping[str, object]]:
    def serializer(
        value: VerifiedRegulatoryFeeEvidence,
        repository_root: str | Path,
    ) -> Mapping[str, object]:
        return implementation(
            value,
            repository_root,
            verifier,
            projector,
            evidence_type,
            error_type,
        )

    return serializer


serialize_verified_regulatory_fee_evidence = _make_verified_evidence_serializer(
    _serialize_verified_regulatory_fee_evidence,
    verify_regulatory_fee_evidence,
    _verified_evidence_projection,
    VerifiedRegulatoryFeeEvidence,
    RegulatoryFeeEvidenceError,
)
del _make_verified_evidence_serializer
