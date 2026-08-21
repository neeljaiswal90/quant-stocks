"""Fail-closed verifier for Specification Freeze V6 and export V5.

Freeze V6 is a new receipt-only successor.  It preserves every Freeze V5 byte,
binds the protected NEE-204 six-file candidate and its causally later review,
owner signoff, merge, and protected push CI, then removes exactly two
engineering-evidence blocker rows.  Ten blockers and every Freeze V5 claim
remain unchanged; no empirical, production, M0, or live-order claim is made.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from jsonschema import (  # type: ignore[import-untyped, unused-ignore]
    Draft202012Validator,
    FormatChecker,
)

__all__ = [
    "ACTIVE_BLOCKER_COUNT",
    "EXPORT_PATH",
    "EXPORT_SCHEMA_PATH",
    "MANIFEST_PATH",
    "POLICY_PATH",
    "POLICY_SCHEMA_PATH",
    "RESOLVED_TARGETS",
    "SpecificationFreezeV6Error",
    "VerifiedSpecificationFreezeV6",
    "serialize_specification_freeze_v6_export",
    "verify_specification_freeze_v6",
    "verify_specification_freeze_v6_manifest",
]

POLICY_PATH = Path("configs/governance/specification-freeze-policy-v6.json")
POLICY_SCHEMA_PATH = Path("schemas/governance/specification-freeze-policy-v6.schema.json")
EXPORT_PATH = Path("configs/governance/specification-freeze-export-v5.json")
EXPORT_SCHEMA_PATH = Path("schemas/governance/specification-freeze-export-v5.schema.json")
MANIFEST_PATH = Path("configs/governance/specification-freeze-v6.hashes.json")

POLICY_ID = "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V6"
EXPORT_ID = "NEE-110-SPECIFICATION-FREEZE-EXPORT-V5"
POLICY_STATUS = "BLOCKED_10_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
EXPORT_STATUS = "HASH_VERIFIED_BLOCKED_10_ACTIVE"
ACTIVE_BLOCKER_COUNT = 10
RESOLVED_TARGETS = (
    "NEE-122-CORRELATED-TRIAL-FIXTURE",
    "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE",
)

_POLICY_PATH = POLICY_PATH.as_posix()
_POLICY_SCHEMA_PATH = POLICY_SCHEMA_PATH.as_posix()
_EXPORT_PATH = EXPORT_PATH.as_posix()
_EXPORT_SCHEMA_PATH = EXPORT_SCHEMA_PATH.as_posix()
_MANIFEST_PATH = MANIFEST_PATH.as_posix()
_RUNTIME_PATH = "qme/governance/specification_freeze_v6.py"

# fmt: off
_EXPECTED_POLICY_SHA256 = "f28d2a90:7d5078a1:bdc90053:12ac3259:54c3e499:cb43a80c:f49ee70b:d6326668"
_EXPECTED_POLICY_SCHEMA_SHA256 = "afca0f66:444b3ec6:19b37e97:e1dc7cbf:0f82a6e3:9221cf12:6c1248a9:6a135f56"
_EXPECTED_EXPORT_SHA256 = "01d89c4a:4a28d859:b6bdf0cb:2a6a5e62:a7802e92:09f281b3:5e33e395:87d83ca1"
_EXPECTED_EXPORT_SCHEMA_SHA256 = "254cccd6:66e1d882:76f1db77:af7156e5:5be27bf8:38207b2c:ad950f02:69039cbc"
_EXPECTED_POLICY_SEMANTIC_SHA256 = "879d2107:1c5e8948:9fd6fed4:332027f1:8ebe9427:14503c84:a643c1b1:7d2e70ef"
_EXPECTED_DERIVED_EVIDENCE_SHA256 = "3d7d953e:ca62b35d:6de6c0ca:30fbf04a:fe1fab57:248b6eeb:2804ab2b:f48a5fc3"
_EXPECTED_SUPERSEDES_SHA256 = "3bcb5314:70efe9c7:f015370a:d0eee325:6f732e19:eda3b0cb:75f54107:00300876"
_EXPECTED_EFFECTIVE_TRIALS_EVIDENCE_SHA256 = "9ab4763d:dcc5c372:2ae51ea0:16439b8a:fe517beb:0c7b19dc:896e6633:4536d324"
_EXPECTED_RUNTIME_NORMALIZED_SHA256 = "35ec06d7:8f161620:8d5bfe58:0c83f4d8:244beb72:c3e0ef22:734d96ab:0a7e3111"
# fmt: on

_RECEIPT_DIR = "docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/"
_PROMPT_PATH = _RECEIPT_DIR + "DELTA-REVIEW-PROMPT.md"
_VERDICT_PATH = _RECEIPT_DIR + "DELTA-REVIEW-VERDICT.md"
_SIGNOFF_PATH = _RECEIPT_DIR + "OWNER-SIGNOFF.md"
_PUBLICATION_SNAPSHOT_PATH = _RECEIPT_DIR + "PROTECTED-PUBLICATION-RECEIPT.json"
_RECEIPT_PATH = _RECEIPT_DIR + "RECEIPT.md"

_EXPECTED_V5 = MappingProxyType(
    {
        "configs/governance/specification-freeze-policy-v5.json": "054270b6:d749e82e:38c9cd24:cba93a24:b56ec676:feed22cf:d9b6a211:cf37c840",
        "schemas/governance/specification-freeze-policy-v5.schema.json": "e30a678e:90e4a98e:39366d5d:0ad580c5:738cd7fa:c86707a3:a1da07db:118643fd",
        "configs/governance/specification-freeze-v5.hashes.json": "2eb7a5bd:b6117b71:b0b77836:eca6548a:3609141d:9db2c817:2c2f22b5:0489e548",
        "configs/governance/specification-freeze-export-v4.json": "de559315:30491c9f:a3a3a7de:81f7dcc2:302c2333:f6976091:101adc13:2e18b2be",
        "schemas/governance/specification-freeze-export-v4.schema.json": "6cc775fc:d320a37e:a5890e55:d8c812fe:efc361b1:cd9bccba:bdcade21:5628fb81",
        "qme/governance/specification_freeze_v5.py": "61c3012e:07b4cf80:042074c6:baafea66:0ab1ea49:03e2d8c8:d67fd9f7:6cc0f2cf",
        "tests/governance/test_specification_freeze_v5.py": "bac4be57:f5a7424d:598a8efd:33875225:780a7ea7:989b41f6:f5ac67b5:7cc87dba",
        "docs/governance/SPECIFICATION_FREEZE_V5.md": "00e6c3c8:cc95644f:3b16d511:59f44a53:1a7e74f0:a8406aff:47be293a:65bf8c6c",
    }
)

_EXPECTED_CANDIDATE = MappingProxyType(
    {
        "configs/governance/nee204-successor-freeze-candidate-v1.json": "5450c34d:ee31729c:533f6422:773fa69a:0e75b400:b4def0a1:f7c15495:fb031dc1",
        "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json": "27d74487:f9b29037:fcf08f2b:dce36b9a:ca98ab14:3850d4bc:cf32014e:c6ec152a",
        "docs/governance/NEE_204_SUCCESSOR_FREEZE_CANDIDATE_V1.md": "d23ad553:dec0ff0b:db86a9c9:ee864944:d8f9bb83:108d7fac:a4b774da:a3c66779",
        "qme/governance/nee204_successor_freeze_candidate.py": "4c714e8e:de3e5674:b7c8d979:25b0c082:37200c2a:966538e8:f8f17d1a:a3105658",
        "schemas/governance/nee204-successor-freeze-candidate-v1.schema.json": "6517d0b0:9b25fb36:5899a542:173a97e5:85360add:47733b76:018c9452:2edffeb7",
        "tests/governance/test_nee204_successor_freeze_candidate.py": "0462b78e:aedaf6ac:1245c452:f25a4208:1b8484fa:e5ad9ed5:cb140439:29356d36",
    }
)

_V5_MANIFEST_PATHS = (
    ".github/workflows/ci.yml",
    "configs/governance/nee120-successor-freeze-candidate-v1.hashes.json",
    "configs/governance/specification-freeze-v4.hashes.json",
    "configs/governance/specification-freeze-export-v4.json",
    "configs/governance/specification-freeze-policy-v5.json",
    "docs/governance/blocker-transition-receipts/nee120-inference-evidence/DELTA-REVIEW-PROMPT.md",
    "docs/governance/blocker-transition-receipts/nee120-inference-evidence/DELTA-REVIEW-VERDICT.md",
    "docs/governance/blocker-transition-receipts/nee120-inference-evidence/OWNER-SIGNOFF.md",
    "docs/governance/blocker-transition-receipts/nee120-inference-evidence/RECEIPT.md",
    "docs/governance/SPECIFICATION_FREEZE_V5.md",
    "qme/governance/specification_freeze_v5.py",
    "schemas/governance/specification-freeze-export-v4.schema.json",
    "schemas/governance/specification-freeze-policy-v5.schema.json",
    "tests/governance/test_specification_freeze_v5.py",
)

_CANDIDATE_MANIFEST_PATHS = (
    "configs/governance/nee204-successor-freeze-candidate-v1.json",
    "docs/governance/NEE_204_SUCCESSOR_FREEZE_CANDIDATE_V1.md",
    "qme/governance/nee204_successor_freeze_candidate.py",
    "schemas/governance/nee204-successor-freeze-candidate-v1.schema.json",
    "tests/governance/test_nee204_successor_freeze_candidate.py",
)

_EXPECTED_V5_ROWS = (
    (
        "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL",
        "NEE-110",
        "GOVERNANCE",
        "Final cross-contract semantic acceptance remains unavailable until every production evidence artifact exists and is reviewed.",
    ),
    (
        "NEE-116-ASYMMETRIC-COST-METHOD",
        "NEE-116",
        "ENGINEERING_EVIDENCE",
        "Dated SEC and FINRA sell-side fee logic and an independently checked ledger fixture are not implemented.",
    ),
    (
        "NEE-116-CAPACITY-SOLVER",
        "NEE-116",
        "ENGINEERING_EVIDENCE",
        "The authoritative greatest-capital discrete cost-aware solver remains unavailable.",
    ),
    (
        "NEE-116-CORPORATE-ACTION-EDGE-CASES",
        "NEE-116",
        "PRODUCTION_EVIDENCE",
        "Corporate-action targets lack immutable provider and independent-source receipts plus accepted ledger fixtures.",
    ),
    (
        "NEE-116-PRODUCTION-PIT-DATA",
        "NEE-116",
        "PRODUCTION_EVIDENCE",
        "Accepted point-in-time production data and receipts are unavailable.",
    ),
    (
        "NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE",
        "NEE-116",
        "ENGINEERING_EVIDENCE",
        "Tax-lot election, lot accounting, and within-account wash-sale logic lack implementation and fixtures.",
    ),
    (
        "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP",
        "NEE-119",
        "PRODUCTION_EVIDENCE",
        "No accepted point-in-time Nasdaq-100 membership snapshot is bound into the freeze candidate.",
    ),
    (
        "NEE-119-AV-PROXY-EVIDENCE",
        "NEE-119",
        "PRODUCTION_EVIDENCE",
        "No reviewed Alpha Vantage common-stock proxy snapshot is bound into the freeze candidate.",
    ),
    (
        "NEE-121-CALENDAR-SESSION-REGISTRATION",
        "NEE-121",
        "PRODUCTION_EVIDENCE",
        "XNAS identity is registered, but pinned generator package, lock, tzdata, immutable calendar/session-vector hashes, and closure/half-day evidence are unavailable.",
    ),
    (
        "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP",
        "NEE-121",
        "FINAL_DERIVED_EVIDENCE",
        "The final freeze anchor and receipt remain unavailable until every other blocker is accepted.",
    ),
    (
        "NEE-122-CORRELATED-TRIAL-FIXTURE",
        "NEE-122",
        "ENGINEERING_EVIDENCE",
        "Analytic participation-ratio and seeded Ledoit-Wolf end-to-end fixtures remain unavailable.",
    ),
    (
        "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE",
        "NEE-122",
        "ENGINEERING_EVIDENCE",
        "The registered estimator and bootstrap uncertainty procedure are not implemented.",
    ),
)
_EXPECTED_V6_ROWS = _EXPECTED_V5_ROWS[:-2]
_EXPECTED_RESOLVED_V5 = (
    "NEE-110-QME-CONFIG-V1-CONTRACT",
    "NEE-116-HUMAN-INDEPENDENT-REVIEW",
    "NEE-116-OFFICIAL-OPEN-FALLBACK",
    "NEE-116-TAX-LOT-METHOD",
    "NEE-117-EXACT-SHA-REMOTE-CI",
    "NEE-119-MINIMUM-ELIGIBLE-BREADTH",
    "NEE-119-PRODUCTION-SOURCE-FRESHNESS",
    "NEE-120-AUM-CAPACITY-LIMITS",
    "NEE-120-INFERENCE-POLICY",
    "NEE-120-PROMOTION-MANDATE",
    "NEE-120-PREREGISTRATION-APPROVAL",
    "NEE-121-HISTORICAL-ACCESS-PROVENANCE",
    "NEE-121-LABEL-ENDPOINT-REGISTRATIONS",
    "NEE-121-PROSPECTIVE-EVIDENCE-SUFFICIENCY",
    "NEE-122-DEPENDENCE-ESTIMATOR",
    "NEE-122-PRODUCTION-FAMILY-POLICY",
    "NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION",
    "NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE",
)

_EXPECTED_CLAIMS = MappingProxyType(
    {
        "operational_v2_contracts_materialized": True,
        "atomic_bundle_integrity_verified": True,
        "access_chain_engineering_evidence_accepted": True,
        "remaining_blockers_span_governance_evidence_and_engineering": True,
        "cross_contract_semantic_approval_complete": False,
        "production_access_event_corpus_available": False,
        "production_sample_access_evidence_available": False,
        "production_specification_accepted": False,
        "milestone_m0_complete": False,
        "data_spine_start_authorized": False,
        "production_ready": False,
        "empirical_performance_available": False,
        "alpha_proven": False,
        "inference_implementation_available": False,
        "effective_trials_computable": False,
        "dsr_computable": False,
        "portfolio_capacity_available": False,
        "production_calendar_available": False,
        "final_freeze_receipt_verified": False,
        "prospective_observations_consumable": False,
        "live_order_authority": False,
    }
)

_EXPECTED_SELECTION = MappingProxyType(
    {
        "owner_decision_accepted": True,
        "evidence_scope": "SYNTHETIC_DETERMINISTIC_KAT_ONLY",
        "index_stream_sha256": "e5f8ac97:7cbd6c5e:6de09048:c86b4d7d:c9351b89:8a2e6875:d9057860:f18a1640",
        "bootstrap_distribution_sha256": "e90ba0e3:da74fa34:bbeaddab:01e0d8a1:137702a1:8fc8de73:61f48b01:faf95bcf",
        "one_based_rank": 1950,
        "rank_value": "1.928085337475850467660159735112550709",
        "n_eff_used": 2,
        "valid_distribution_reason": "N_EFF_BOOTSTRAP_UPPER_QUANTILE",
        "invalid_distribution_fallback_reason_unchanged": "N_EFF_DISTRIBUTION_INVALID_CONSERVATIVE_M96",
        "predecessor_selection_009_accepted_fields_remain_false": True,
        "statistical_rule_changed": False,
        "production_n_eff_available": False,
    }
)

_EXPECTED_NONRUNTIME_LEAVES = MappingProxyType(
    {
        ".github/workflows/ci.yml": "a2f84258:c1b694cd:6e2761fd:5b4a07c2:c7306cf4:5368af1e:e5c5ff7a:c933992f",
        "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json": "27d74487:f9b29037:fcf08f2b:dce36b9a:ca98ab14:3850d4bc:cf32014e:c6ec152a",
        "configs/governance/specification-freeze-v5.hashes.json": "2eb7a5bd:b6117b71:b0b77836:eca6548a:3609141d:9db2c817:2c2f22b5:0489e548",
        _EXPORT_PATH: "01d89c4a:4a28d859:b6bdf0cb:2a6a5e62:a7802e92:09f281b3:5e33e395:87d83ca1",
        _POLICY_PATH: "f28d2a90:7d5078a1:bdc90053:12ac3259:54c3e499:cb43a80c:f49ee70b:d6326668",
        _PROMPT_PATH: "3d3a4d70:bfc50cea:7b416fc2:91886779:b5257b29:ae364ed9:05c8e6b1:d4efdcad",
        _VERDICT_PATH: "6abad804:f8e7969d:2cdaaf04:2dec823c:1a3a59f8:3601c367:b74bcda6:730d9805",
        _SIGNOFF_PATH: "0d386e92:03e32e04:2f0e3eee:2c21ae7d:1ef9ad3d:1aa631bb:7d350f6f:1f780700",
        _PUBLICATION_SNAPSHOT_PATH: "62a966a4:88500c1a:cd3dda76:3b013135:940c3550:a34b90ff:e02f3005:6c7b7527",
        _RECEIPT_PATH: "f29b5078:97743f47:8ed5d977:8c4dfe6c:9ffa5e55:a1dcf3ec:17ea4bcc:f8b44610",
        "docs/governance/SPECIFICATION_FREEZE_V6.md": "458b41a6:b42495a0:e205c635:29045137:3d4ba6ae:201a5d02:f89bbf11:6322805d",
        _EXPORT_SCHEMA_PATH: "254cccd6:66e1d882:76f1db77:af7156e5:5be27bf8:38207b2c:ad950f02:69039cbc",
        _POLICY_SCHEMA_PATH: "afca0f66:444b3ec6:19b37e97:e1dc7cbf:0f82a6e3:9221cf12:6c1248a9:6a135f56",
        "tests/governance/test_specification_freeze_v6.py": "5e0bc8aa:4a6a05c0:4cc403e5:820f28f7:8b6cbf18:5c4a871c:1def3f7d:40793b5e",
    }
)

_EXPECTED_SCHEMA_METADATA = MappingProxyType(
    {
        _POLICY_SCHEMA_PATH: (
            "https://json-schema.org/draft/2020-12/schema",
            "https://qme.local/schemas/governance/specification-freeze-policy-v6.schema.json",
            "QME specification freeze policy V6",
            "Exact reviewed 10-blocker policy instance with one bounded two-row effective-trials engineering-evidence transition.",
        ),
        _EXPORT_SCHEMA_PATH: (
            "https://json-schema.org/draft/2020-12/schema",
            "https://qme.local/schemas/governance/specification-freeze-export-v5.schema.json",
            "QME specification freeze export V5",
            "Exact deterministic blocked export for specification freeze policy V6.",
        ),
    }
)

_MANIFEST_PATHS = tuple(_EXPECTED_NONRUNTIME_LEAVES)[:5] + (
    _PROMPT_PATH,
    _VERDICT_PATH,
    _SIGNOFF_PATH,
    _PUBLICATION_SNAPSHOT_PATH,
    _RECEIPT_PATH,
    "docs/governance/SPECIFICATION_FREEZE_V6.md",
    _RUNTIME_PATH,
    _EXPORT_SCHEMA_PATH,
    _POLICY_SCHEMA_PATH,
    "tests/governance/test_specification_freeze_v6.py",
)


class SpecificationFreezeV6Error(ValueError):
    """Raised when any receipt, lineage, projection, or path check fails."""


class VerifiedSpecificationFreezeV6:
    """Sealed result produced only by the private repository verifier."""

    __slots__ = (
        "_status",
        "_policy_sha256",
        "_export_sha256",
        "_semantic_sha256",
        "_derived_evidence_sha256",
        "_active_blocker_codes",
        "_resolved_targets",
        "_selection_009_n_eff_used",
        "_receipt_protected_ci_required",
        "_milestone_m0_complete",
        "_repository_root",
    )

    def __new__(cls, *_args: object, **_kwargs: object) -> VerifiedSpecificationFreezeV6:
        raise TypeError("VerifiedSpecificationFreezeV6 is verifier-created only")

    @property
    def status(self) -> str:
        return cast(str, object.__getattribute__(self, "_status"))

    @property
    def active_blocker_codes(self) -> tuple[str, ...]:
        return cast(tuple[str, ...], object.__getattribute__(self, "_active_blocker_codes"))

    @property
    def active_blocker_count(self) -> int:
        return len(self.active_blocker_codes)

    @property
    def resolved_targets(self) -> tuple[str, ...]:
        return cast(tuple[str, ...], object.__getattribute__(self, "_resolved_targets"))

    @property
    def policy_sha256(self) -> str:
        return cast(str, object.__getattribute__(self, "_policy_sha256"))

    @property
    def export_sha256(self) -> str:
        return cast(str, object.__getattribute__(self, "_export_sha256"))


def _build_trusted_api(
    *,
    claims: Mapping[str, bool],
    datetime_type: type[datetime],
    error_type: type[SpecificationFreezeV6Error],
    expected_candidate: Mapping[str, str],
    expected_effective_trials_evidence_sha256: str,
    expected_nonruntime_leaves: Mapping[str, str],
    expected_schema_metadata: Mapping[str, tuple[str, str, str, str]],
    expected_selection: Mapping[str, Any],
    expected_supersedes_sha256: str,
    expected_v5: Mapping[str, str],
    format_checker_type: type[FormatChecker],
    hash_new: Any,
    json_dumps: Any,
    json_error_type: type[Exception],
    json_loads: Any,
    mapping_proxy_type: Any,
    os_module: Any,
    path_class: type[Path],
    re_module: Any,
    result_type: type[VerifiedSpecificationFreezeV6],
    stat_module: Any,
    type_builtin: type,
    validator_type: type[Draft202012Validator],
) -> tuple[Any, Any, Any]:
    path_type = type_builtin(path_class())
    object_new = object.__new__
    object_setattr = object.__setattr__
    object_getattribute = object.__getattribute__
    tuple_getitem = tuple.__getitem__
    any_builtin = any
    attribute_error_type = AttributeError
    bytes_type = bytes
    cast_fn = cast
    dict_type = dict
    getattr_builtin = getattr
    int_type = int
    len_builtin = len
    list_type = list
    mapping_type = Mapping
    set_type = set
    str_type = str
    tuple_type = tuple
    unicode_decode_error_type = UnicodeDecodeError
    value_error_type = ValueError
    zip_builtin = zip
    max_bytes = 67_108_864
    grouped_pattern = re_module.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}")
    grouped_commit_pattern = re_module.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){4}")
    runtime_pin_pattern = re_module.compile(
        rb'(_EXPECTED_RUNTIME_NORMALIZED_SHA256 = ")[0-9a-f:]{71}("\r?\n)'
    )
    expected_claims_json = json_dumps(dict(claims), sort_keys=True, separators=(",", ":"))
    expected_selection_json = json_dumps(
        dict(expected_selection), sort_keys=True, separators=(",", ":")
    )
    v5_pins = tuple(expected_v5.items())
    candidate_pins = tuple(expected_candidate.items())
    nonruntime_pins = tuple(expected_nonruntime_leaves.items())
    schema_metadata_pins = tuple(expected_schema_metadata.items())

    def fail(message: str) -> None:
        raise error_type(message)

    def normal(value: object, field: str) -> str:
        if type_builtin(value) is not str_type or grouped_pattern.fullmatch(value) is None:
            fail(f"{field} is not exact grouped SHA-256")
        return cast_fn(str_type, value).replace(":", "")

    def normal_commit(value: object, field: str) -> str:
        if type_builtin(value) is not str_type or grouped_commit_pattern.fullmatch(value) is None:
            fail(f"{field} is not exact grouped commit identity")
        return cast_fn(str_type, value).replace(":", "")

    def exact_dict(value: object, field: str) -> dict[str, Any]:
        if type_builtin(value) is not dict_type:
            fail(f"{field} must be an exact object")
        return cast_fn(dict_type, value)  # type: ignore[no-any-return]

    def exact_list(value: object, field: str) -> list[Any]:
        if type_builtin(value) is not list_type:
            fail(f"{field} must be an exact array")
        return cast_fn(list_type, value)

    def exact_str(value: object, field: str) -> str:
        if type_builtin(value) is not str_type:
            fail(f"{field} must be exact text")
        return cast_fn(str_type, value)

    def timestamp(value: object, field: str) -> datetime:
        text = exact_str(value, field)
        if (
            re_module.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})",
                text,
            )
            is None
        ):
            fail(f"{field} is not an exact offset timestamp")
        try:
            result = datetime_type.fromisoformat(text.replace("Z", "+00:00"))
        except value_error_type as exc:
            raise error_type(f"{field} is not a valid timestamp") from exc
        if result.utcoffset() is None:
            fail(f"{field} must be offset-aware")
        return result

    def pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                fail(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> None:
        fail(f"non-finite JSON number: {value}")

    def canonical(document: Mapping[str, Any]) -> bytes:
        return cast_fn(
            bytes_type,
            json_dumps(
                document,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n",
        )

    def semantic(document: Mapping[str, Any], key: str) -> str:
        clone = dict_type(document)
        clone.pop(key, None)
        return cast_fn(str_type, hash_new(canonical(clone)).hexdigest())

    def row_tuple(value: object) -> tuple[str, str, str, str]:
        row = exact_dict(value, "blocker row")
        if tuple_type(row) != ("blocker_code", "ticket_id", "category", "description"):
            fail("blocker row key order drift")
        values = tuple_type(row[key] for key in row)
        if any_builtin(type_builtin(item) is not str_type for item in values):
            fail("blocker row type drift")
        return cast_fn(tuple_type, values)  # type: ignore[return-value]

    def path_identity(path: Path) -> tuple[int, int, int, int, int]:
        info = path.lstat()
        attrs = int_type(getattr_builtin(info, "st_file_attributes", 0))
        reparse = int_type(getattr_builtin(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
        if stat_module.S_ISLNK(info.st_mode) or (reparse and attrs & reparse):
            fail(f"linked or reparse path rejected: {path}")
        return (
            int_type(info.st_dev),
            int_type(info.st_ino),
            int_type(info.st_mode),
            int_type(info.st_nlink),
            attrs,
        )

    def safe_parts(relative: str) -> tuple[str, ...]:
        if (
            type_builtin(relative) is not str_type
            or not relative
            or "\\" in relative
            or ":" in relative
        ):
            fail("artifact path is not canonical repository-relative POSIX text")
        parts = tuple_type(relative.split("/"))
        if any_builtin(part in ("", ".", "..") for part in parts):
            fail("artifact path contains an unsafe component")
        return parts

    def read_bytes(relative: str, root: Path) -> bytes:
        if type_builtin(root) is not path_type:
            fail("repository root must be an exact platform Path")
        parts = safe_parts(relative)
        root_resolved = root.resolve(strict=True)
        if not root_resolved.is_dir():
            fail("repository root is not a directory")
        target = root_resolved.joinpath(*parts)
        ancestors: list[Path] = [root_resolved]
        cursor = root_resolved
        for part in parts[:-1]:
            cursor = cursor / part
            ancestors.append(cursor)
        before = tuple_type((str_type(item), path_identity(item)) for item in ancestors)
        resolved = target.resolve(strict=True)
        try:
            resolved.relative_to(root_resolved)
        except value_error_type as exc:
            raise error_type("artifact escapes repository root") from exc
        before_target = path_identity(target)
        flags = os_module.O_RDONLY | int_type(getattr_builtin(os_module, "O_BINARY", 0))
        nofollow = int_type(getattr_builtin(os_module, "O_NOFOLLOW", 0))
        if nofollow:
            flags |= nofollow
        descriptor = os_module.open(target, flags)
        try:
            opened = os_module.fstat(descriptor)
            if (
                not stat_module.S_ISREG(opened.st_mode)
                or int_type(opened.st_nlink) != 1
                or (int_type(opened.st_dev), int_type(opened.st_ino))
                != (before_target[0], before_target[1])
            ):
                fail("artifact descriptor identity or type changed")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os_module.read(descriptor, 65_536)
                if not chunk:
                    break
                total += len_builtin(chunk)
                if total > max_bytes:
                    fail("artifact exceeds maximum size")
                chunks.append(chunk)
            after_open = os_module.fstat(descriptor)
            if (
                int_type(after_open.st_dev),
                int_type(after_open.st_ino),
                int_type(after_open.st_size),
            ) != (
                int_type(opened.st_dev),
                int_type(opened.st_ino),
                total,
            ):
                fail("artifact changed during same-handle read")
        finally:
            os_module.close(descriptor)
        if tuple_type((str_type(item), path_identity(item)) for item in ancestors) != before:
            fail("artifact ancestor identity changed during read")
        after_target = path_identity(target)
        if after_target != before_target:
            fail("artifact path identity changed during read")
        if target.resolve(strict=True) != resolved:
            fail("artifact resolved target changed during read")
        return b"".join(chunks)

    def load_json(relative: str, root: Path) -> tuple[dict[str, Any], bytes]:
        raw = read_bytes(relative, root)
        try:
            text = raw.decode("utf-8", errors="strict")
        except unicode_decode_error_type as exc:
            raise error_type(f"invalid UTF-8: {relative}") from exc
        try:
            value = json_loads(
                text,
                object_pairs_hook=pairs_object,
                parse_constant=reject_nonfinite,
            )
        except (json_error_type, error_type) as exc:
            raise error_type(f"invalid strict JSON: {relative}") from exc
        return exact_dict(value, relative), raw

    def sha(relative: str, root: Path) -> str:
        return cast_fn(str_type, hash_new(read_bytes(relative, root)).hexdigest())

    def exact_schema(schema_path: str, document: dict[str, Any], root: Path) -> None:
        schema, _ = load_json(schema_path, root)
        validator_type.check_schema(schema)
        expected_metadata: tuple[str, str, str, str] | None = None
        for expected_path, metadata in schema_metadata_pins:
            if schema_path == expected_path:
                expected_metadata = metadata
                break
        if expected_metadata is None:
            fail("schema path is not registered")
        if (
            tuple_type(schema) != ("$schema", "$id", "title", "description", "const")
            or (
                schema.get("$schema"),
                schema.get("$id"),
                schema.get("title"),
                schema.get("description"),
            )
            != expected_metadata
        ):
            fail("schema root inventory or metadata drift")
        if schema.get("const") != document:
            fail("schema const and document differ")
        errors = tuple_type(
            validator_type(schema, format_checker=format_checker_type()).iter_errors(document)
        )
        if errors:
            fail(f"schema validation failed: {errors[0].message}")

    def replay_manifest(
        relative: str,
        expected_raw: str,
        expected_id: str,
        expected_status: str,
        expected_paths: tuple[str, ...],
        root: Path,
    ) -> int:
        document, raw = load_json(relative, root)
        if hash_new(raw).hexdigest() != normal(expected_raw, f"{relative} hash"):
            fail(f"manifest bytes changed: {relative}")
        if (
            document.get("schema_version") != "qme.hash_manifest.v1"
            or document.get("artifact_id") != expected_id
        ):
            fail(f"manifest identity changed: {relative}")
        status = document.get("status", document.get("implementation_status"))
        if status != expected_status:
            fail(f"manifest status changed: {relative}")
        rows = exact_list(document.get("artifacts"), f"{relative} rows")
        if len_builtin(rows) != len_builtin(expected_paths):
            fail(f"manifest membership changed: {relative}")
        observed: list[str] = []
        for raw_row in rows:
            row = exact_dict(raw_row, "manifest row")
            if tuple_type(row) != ("path", "sha256"):
                fail("manifest row shape or order changed")
            member = exact_str(row["path"], "manifest path")
            if member in observed:
                fail("duplicate manifest path")
            observed.append(member)
            if sha(member, root) != normal(row["sha256"], f"{member} hash"):
                fail(f"manifest leaf mismatch: {member}")
        if tuple_type(observed) != expected_paths:
            fail(f"manifest path order changed: {relative}")
        return len_builtin(rows)

    def verify_predecessor(
        root: Path,
        *,
        _v5_manifest_paths: tuple[str, ...] = _V5_MANIFEST_PATHS,
        _v5_rows: tuple[tuple[str, str, str, str], ...] = _EXPECTED_V5_ROWS,
        _resolved_v5: tuple[str, ...] = _EXPECTED_RESOLVED_V5,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        for path, digest in v5_pins:
            if sha(path, root) != normal(digest, f"{path} pinned hash"):
                fail(f"Freeze V5 protected bytes changed: {path}")
        replay_manifest(
            "configs/governance/specification-freeze-v5.hashes.json",
            dict_type(v5_pins)["configs/governance/specification-freeze-v5.hashes.json"],
            "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V5",
            "BLOCKED_12_ACTIVE",
            _v5_manifest_paths,
            root,
        )
        policy, _ = load_json("configs/governance/specification-freeze-policy-v5.json", root)
        export, _ = load_json("configs/governance/specification-freeze-export-v4.json", root)
        if (
            policy.get("policy_id") != "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V5"
            or policy.get("policy_status")
            != "BLOCKED_12_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
            or semantic(policy, "semantic_sha256")
            != normal(policy.get("semantic_sha256"), "V5 semantic hash")
            or normal(policy.get("semantic_sha256"), "V5 semantic hash")
            != normal(
                "85f0e7d9:62992601:2a44217c:bf8133ca:2169855d:db1a0296:6a908ef5:9a650ef3",
                "expected V5 semantic hash",
            )
        ):
            fail("Freeze V5 identity or semantic digest changed")
        if (
            tuple_type(
                row_tuple(row)
                for row in exact_list(policy.get("unresolved_blockers"), "V5 blockers")
            )
            != _v5_rows
        ):
            fail("Freeze V5 blocker rows changed")
        if (
            tuple_type(
                exact_list(policy.get("resolved_or_superseded_blocker_codes"), "V5 resolved")
            )
            != _resolved_v5
        ):
            fail("Freeze V5 resolved lineage changed")
        if (
            json_dumps(policy.get("claims"), sort_keys=True, separators=(",", ":"))
            != expected_claims_json
        ):
            fail("Freeze V5 claims changed")
        if (
            export.get("export_status") != "HASH_VERIFIED_BLOCKED_12_ACTIVE"
            or tuple_type(exact_list(export.get("active_blocker_codes"), "V5 export blockers"))
            != tuple_type(row[0] for row in _v5_rows)
            or semantic(export, "derived_evidence_sha256")
            != normal(export.get("derived_evidence_sha256"), "V5 derived hash")
        ):
            fail("Freeze V5 export changed")
        return policy, export

    def verify_candidate(
        root: Path,
        *,
        _candidate_manifest_paths: tuple[str, ...] = _CANDIDATE_MANIFEST_PATHS,
        _v5_rows: tuple[tuple[str, str, str, str], ...] = _EXPECTED_V5_ROWS,
        _v6_rows: tuple[tuple[str, str, str, str], ...] = _EXPECTED_V6_ROWS,
        _resolved_targets: tuple[str, ...] = RESOLVED_TARGETS,
    ) -> dict[str, Any]:
        for path, digest in candidate_pins:
            if sha(path, root) != normal(digest, f"{path} pinned hash"):
                fail(f"protected NEE-204 candidate bytes changed: {path}")
        replay_manifest(
            "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json",
            dict_type(candidate_pins)[
                "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json"
            ],
            "NEE-204-SELECTION-009-SUCCESSOR-FREEZE-CANDIDATE-V1",
            "CANDIDATE_UNREVIEWED_FREEZE_V5_UNCHANGED_PENDING_DELTA_REVIEW_EXACT_BYTE_OWNER_SIGNOFF_AND_RECEIPT",
            _candidate_manifest_paths,
            root,
        )
        candidate, _ = load_json(
            "configs/governance/nee204-successor-freeze-candidate-v1.json", root
        )
        if (
            candidate.get("candidate_kind") != "BLOCKER_TRANSITION_CANDIDATE_NOT_BLOCKER_CLEARANCE"
            or semantic(candidate, "semantic_sha256")
            != normal(candidate.get("semantic_sha256"), "candidate semantic hash")
            or normal(candidate.get("semantic_sha256"), "candidate semantic hash")
            != normal(
                "eb441df6:cf49748e:0890e459:cca31445:931f9d6b:a73aff1d:909bc4d6:75c87871",
                "expected candidate semantic hash",
            )
        ):
            fail("protected candidate identity or semantic digest changed")
        target = exact_dict(candidate.get("target"), "candidate target")
        if (
            tuple_type(
                row_tuple(row)
                for row in exact_list(target.get("blocker_rows_verbatim"), "candidate target rows")
            )
            != _v5_rows[-2:]
        ):
            fail("candidate target rows changed")
        transition = exact_dict(candidate.get("proposed_transition"), "candidate transition")
        if (
            tuple_type(exact_list(transition.get("removes_exactly"), "removed codes"))
            != _resolved_targets
            or tuple_type(
                exact_list(
                    transition.get("retained_active_blocker_codes_in_order"),
                    "retained codes",
                )
            )
            != tuple_type(row[0] for row in _v6_rows)
            or transition.get("freeze_state_at_candidate")
            != {"active": 12, "historical_resolved_or_superseded": 18}
            or transition.get("freeze_state_after_receipt_if_separately_accepted")
            != {"active": 10, "historical_resolved_or_superseded": 20}
        ):
            fail("candidate transition changed")
        if (
            json_dumps(candidate.get("selection_009"), sort_keys=True, separators=(",", ":"))
            != expected_selection_json
        ):
            fail("candidate selection 009 changed")
        gates = exact_dict(candidate.get("required_next_gates"), "candidate gates")
        if len_builtin(gates) != 5 or set_type(gates.values()) != {"PENDING"}:
            fail("immutable candidate gate state changed")
        return candidate

    def verify_receipt_evidence(
        evidence: dict[str, Any],
        candidate: dict[str, Any],
        root: Path,
        *,
        _v5_rows: tuple[tuple[str, str, str, str], ...] = _EXPECTED_V5_ROWS,
        _resolved_targets: tuple[str, ...] = RESOLVED_TARGETS,
        _prompt_path: str = _PROMPT_PATH,
        _verdict_path: str = _VERDICT_PATH,
        _signoff_path: str = _SIGNOFF_PATH,
        _publication_snapshot_path: str = _PUBLICATION_SNAPSHOT_PATH,
        _receipt_path: str = _RECEIPT_PATH,
    ) -> None:
        if hash_new(canonical(evidence)).hexdigest() != normal(
            expected_effective_trials_evidence_sha256,
            "expected effective-trials evidence semantic inventory",
        ):
            fail("effective-trials evidence semantic inventory changed")
        if (
            evidence.get("scope")
            != "EFFECTIVE_TRIALS_ENGINEERING_EVIDENCE_ONLY_NO_EMPIRICAL_OR_PRODUCTION_INFERENCE_CLAIM"
            or evidence.get("source_ticket_id") != "NEE-204"
            or evidence.get("target_ticket_id") != "NEE-122"
            or tuple_type(
                exact_list(evidence.get("resolved_blocker_codes"), "resolved target codes")
            )
            != _resolved_targets
            or tuple_type(
                row_tuple(row)
                for row in exact_list(
                    evidence.get("original_v5_blocker_rows"), "original target rows"
                )
            )
            != _v5_rows[-2:]
        ):
            fail("effective-trials evidence identity changed")
        binding = exact_dict(evidence.get("candidate"), "candidate binding")
        if (
            binding.get("candidate_id") != "NEE-204-SELECTION-009-SUCCESSOR-FREEZE-CANDIDATE-V1"
            or binding.get("candidate_kind") != "BLOCKER_TRANSITION_CANDIDATE_NOT_BLOCKER_CLEARANCE"
            or binding.get("config_path")
            != "configs/governance/nee204-successor-freeze-candidate-v1.json"
            or binding.get("schema_path")
            != "schemas/governance/nee204-successor-freeze-candidate-v1.schema.json"
            or binding.get("manifest_path")
            != "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json"
            or binding.get("runtime_path") != "qme/governance/nee204_successor_freeze_candidate.py"
            or binding.get("tests_path")
            != "tests/governance/test_nee204_successor_freeze_candidate.py"
            or binding.get("doc_path") != "docs/governance/NEE_204_SUCCESSOR_FREEZE_CANDIDATE_V1.md"
            or normal(binding.get("semantic_sha256"), "candidate semantic receipt")
            != "eb441df6cf49748e0890e459cca31445931f9d6ba73aff1d909bc4d675c87871"  # pragma: allowlist secret
            or normal(
                binding.get("runtime_normalized_sha256"),
                "candidate normalized runtime receipt",
            )
            != "56d1914f7f30e4dd0c836fbd6fa0ead21c9dfd99ab10fdb5d499544c9c3a4abe"  # pragma: allowlist secret
        ):
            fail("candidate receipt identity or path changed")
        for key, expected in (
            (
                "config_sha256",
                dict_type(candidate_pins)[
                    "configs/governance/nee204-successor-freeze-candidate-v1.json"
                ],
            ),
            (
                "manifest_sha256",
                dict_type(candidate_pins)[
                    "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json"
                ],
            ),
            (
                "runtime_sha256",
                dict_type(candidate_pins)["qme/governance/nee204_successor_freeze_candidate.py"],
            ),
            (
                "schema_sha256",
                dict_type(candidate_pins)[
                    "schemas/governance/nee204-successor-freeze-candidate-v1.schema.json"
                ],
            ),
            (
                "tests_sha256",
                dict_type(candidate_pins)[
                    "tests/governance/test_nee204_successor_freeze_candidate.py"
                ],
            ),
            (
                "doc_sha256",
                dict_type(candidate_pins)[
                    "docs/governance/NEE_204_SUCCESSOR_FREEZE_CANDIDATE_V1.md"
                ],
            ),
        ):
            if normal(binding.get(key), key) != normal(expected, f"expected {key}"):
                fail(f"candidate receipt binding changed: {key}")
        pull_request = exact_dict(evidence.get("candidate_pull_request"), "candidate pull request")
        if (
            pull_request.get("number") != 55
            or normal_commit(pull_request.get("head_commit"), "head commit")
            != "aa41980791f3cdf008b91e274164efe3a9c4d37e"  # pragma: allowlist secret
            or normal_commit(pull_request.get("protected_main_commit"), "protected commit")
            != "2c314ffb80d5a43a9e1396248daaa494394848dc"  # pragma: allowlist secret
            or normal_commit(pull_request.get("protected_main_tree"), "protected tree")
            != "f85a35cdf86a4f5316957744adcacfab7a98b630"  # pragma: allowlist secret
            or normal_commit(pull_request.get("protected_main_parent"), "protected parent")
            != "a7ee2f5a75d58cbe6bc88cf4e5d177639b56aecd"  # pragma: allowlist secret
        ):
            fail("candidate pull request identity changed")
        checks = exact_list(pull_request.get("checks"), "candidate checks")
        if len_builtin(checks) != 2:
            fail("candidate check inventory changed")
        for expected_event, expected_run, expected_job, expected_head, raw_check in zip_builtin(
            ("pull_request", "push"),
            (32390978018, 32395355287),
            (96496726372, 96510774948),
            (
                "aa41980791f3cdf008b91e274164efe3a9c4d37e",  # pragma: allowlist secret
                "2c314ffb80d5a43a9e1396248daaa494394848dc",  # pragma: allowlist secret
            ),
            checks,
            strict=True,
        ):
            check = exact_dict(raw_check, "candidate check")
            if (
                check.get("workflow") != "qme-ci"
                or check.get("event") != expected_event
                or check.get("run_id") != expected_run
                or check.get("job_id") != expected_job
                or check.get("run_status") != "completed"
                or check.get("run_conclusion") != "success"
                or check.get("job_status") != "completed"
                or check.get("job_conclusion") != "success"
                or normal_commit(check.get("tested_commit"), "tested commit") != expected_head
            ):
                fail("candidate check identity or conclusion changed")
            created = timestamp(check.get("run_created_at"), "run created timestamp")
            started = timestamp(check.get("job_started_at"), "job started timestamp")
            completed = timestamp(check.get("job_completed_at"), "job completed timestamp")
            updated = timestamp(check.get("run_updated_at"), "run updated timestamp")
            if not created <= started <= completed <= updated:
                fail("candidate check timestamps are not causally ordered")
        review = exact_dict(evidence.get("candidate_delta_review"), "candidate delta review")
        if (
            review.get("source_comment_id") != "8f0a0e62-0544-4d59-8a95-9a03081bc572"
            or review.get("source_author_id") != "a2f77320-3e15-4fe3-acea-a276546a8274"
            or review.get("verdict_path") != _verdict_path
            or review.get("verdict_bytes") != 2962
            or sha(_verdict_path, root)
            != normal(review.get("verdict_sha256"), "review verdict hash")
            or review.get("disposition") != "SUFFICIENT_FOR_SEPARATE_EXACT_BYTE_OWNER_SIGNOFF"
            or any_builtin(review.get(name) != 0 for name in ("p0_count", "p1_count", "p2_count"))
            or sha(_prompt_path, root) != normal(review.get("prompt_sha256"), "review prompt hash")
        ):
            fail("candidate delta review changed")
        expected_six = {
            "config": dict_type(candidate_pins)[
                "configs/governance/nee204-successor-freeze-candidate-v1.json"
            ],
            "manifest": dict_type(candidate_pins)[
                "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json"
            ],
            "documentation": dict_type(candidate_pins)[
                "docs/governance/NEE_204_SUCCESSOR_FREEZE_CANDIDATE_V1.md"
            ],
            "runtime": dict_type(candidate_pins)[
                "qme/governance/nee204_successor_freeze_candidate.py"
            ],
            "schema": dict_type(candidate_pins)[
                "schemas/governance/nee204-successor-freeze-candidate-v1.schema.json"
            ],
            "tests": dict_type(candidate_pins)[
                "tests/governance/test_nee204_successor_freeze_candidate.py"
            ],
        }
        if exact_dict(review.get("reviewed_candidate_hashes"), "reviewed hashes") != expected_six:
            fail("delta review candidate hashes changed")
        if (
            normal_commit(review.get("reviewed_base_commit"), "reviewed base commit")
            != "a7ee2f5a75d58cbe6bc88cf4e5d177639b56aecd"  # pragma: allowlist secret
            or normal_commit(review.get("reviewed_base_tree"), "reviewed base tree")
            != "497e5702cd46ade49f4e7120eaf6f9feaab38bf3"  # pragma: allowlist secret
        ):
            fail("delta review base identity changed")
        signoff = exact_dict(evidence.get("owner_exact_byte_signoff"), "owner signoff")
        if (
            signoff.get("source_comment_id") != "df56674a-101e-4b8a-9594-7551a44afca0"
            or signoff.get("source_author_id") != "a2f77320-3e15-4fe3-acea-a276546a8274"
            or signoff.get("statement_path") != _signoff_path
            or signoff.get("statement_bytes") != 2008
            or sha(_signoff_path, root)
            != normal(signoff.get("statement_sha256"), "owner signoff hash")
            or signoff.get("disposition")
            != "EXACT_SIX_FILE_CANDIDATE_SIGNED_FOR_UNCHANGED_PUBLICATION"
        ):
            fail("owner exact-byte signoff changed")
        if exact_dict(signoff.get("signed_candidate_hashes"), "signed hashes") != expected_six:
            fail("owner signoff candidate hashes changed")
        if (
            normal_commit(signoff.get("signed_head_commit"), "signed head")
            != "aa41980791f3cdf008b91e274164efe3a9c4d37e"  # pragma: allowlist secret
            or normal_commit(signoff.get("signed_tree"), "signed tree")
            != "f85a35cdf86a4f5316957744adcacfab7a98b630"  # pragma: allowlist secret
        ):
            fail("owner signoff Git identity changed")
        publication = exact_dict(evidence.get("publication_receipt"), "publication receipt")
        if (
            publication.get("source_comment_id") != "2e9088af-e65b-4c12-b805-4f50dcf9f3ea"
            or normal(publication.get("source_body_sha256"), "publication body hash")
            != "9b8b26017e372ee4871bd1bc159f4156693f2f9f88152090a8d679e698bf347a"  # pragma: allowlist secret
            or publication.get("source_body_bytes") != 3586
            or publication.get("protected_ci_exact_head_success") is not True
            or publication.get("protected_test_count") != 1848
            or publication.get("nee204_status_after_correction") != "IN_PROGRESS"
            or publication.get("nee122_status") != "IN_PROGRESS"
        ):
            fail("protected publication receipt changed")
        publication_snapshot, _ = load_json(_publication_snapshot_path, root)
        expected_snapshot_keys = (
            "schema_version",
            "source_system",
            "source_issue_id",
            "source_comment_id",
            "source_created_at",
            "source_updated_at",
            "source_author_id",
            "source_author_name",
            "hash_convention",
            "source_body_bytes",
            "source_body_sha256",
            "body",
        )
        snapshot_body = exact_str(publication_snapshot.get("body"), "publication body")
        snapshot_body_bytes = snapshot_body.encode("utf-8")
        if (
            tuple_type(publication_snapshot) != expected_snapshot_keys
            or publication_snapshot.get("schema_version")
            != "qme.linear_comment_snapshot.v1"
            or publication_snapshot.get("source_system") != publication.get("source_system")
            or publication_snapshot.get("source_issue_id")
            != publication.get("source_issue_id")
            or publication_snapshot.get("source_comment_id")
            != publication.get("source_comment_id")
            or publication_snapshot.get("source_created_at")
            != publication.get("source_created_at")
            or publication_snapshot.get("source_updated_at")
            != publication.get("source_updated_at")
            or publication_snapshot.get("source_author_id")
            != publication.get("source_author_id")
            or publication_snapshot.get("source_author_name")
            != publication.get("source_author_name")
            or publication_snapshot.get("hash_convention")
            != "RAW_CONNECTOR_BODY_UTF8_NO_NORMALIZATION_NO_TRAILING_NEWLINE"
            or publication_snapshot.get("source_body_bytes")
            != publication.get("source_body_bytes")
            or normal(
                publication_snapshot.get("source_body_sha256"),
                "publication snapshot body hash",
            )
            != normal(publication.get("source_body_sha256"), "publication body hash")
            or len_builtin(snapshot_body_bytes) != publication.get("source_body_bytes")
            or hash_new(snapshot_body_bytes).hexdigest()
            != normal(publication.get("source_body_sha256"), "publication body hash")
        ):
            fail("protected publication receipt snapshot changed")
        review_at = timestamp(review.get("source_created_at"), "review created")
        signoff_at = timestamp(signoff.get("source_created_at"), "signoff created")
        commit_at = timestamp(pull_request.get("protected_main_committer_at"), "commit time")
        merged_at = timestamp(pull_request.get("merged_at"), "merge time")
        protected_updated = timestamp(checks[1]["run_updated_at"], "protected run updated")
        observed_at = timestamp(publication.get("observed_at"), "publication observation")
        if not review_at < signoff_at < commit_at <= merged_at < protected_updated < observed_at:
            fail("review, signoff, merge, CI, and publication timestamps are not causal")
        if (
            json_dumps(evidence.get("selection_009"), sort_keys=True, separators=(",", ":"))
            != expected_selection_json
        ):
            fail("accepted selection 009 projection changed")
        receipt = exact_dict(evidence.get("receipt"), "receipt")
        transition = exact_dict(candidate.get("proposed_transition"), "candidate transition")
        if (
            receipt.get("receipt_id")
            != "NEE-204-EFFECTIVE-TRIALS-EVIDENCE-BLOCKER-TRANSITION-RECEIPT-V1"
            or receipt.get("receipt_path") != _receipt_path
            or sha(_receipt_path, root) != normal(receipt.get("receipt_sha256"), "receipt hash")
            or receipt.get("statistical_rule_or_evidence_binding_changed") is not False
            or receipt.get("may_add_only") != transition.get("receipt_may_add_only")
            or receipt.get("may_not_change_without_new_review_and_signoff")
            != transition.get("receipt_may_not_change_without_new_delta_review_and_owner_signoff")
        ):
            fail("receipt identity or bytes changed")
        if timestamp(receipt.get("receipt_created_utc"), "receipt created") <= observed_at:
            fail("receipt timestamp does not follow protected publication observation")
        resolution = exact_dict(evidence.get("resolution"), "resolution")
        if (
            resolution.get("previous_active_blockers") != 12
            or resolution.get("new_active_blockers") != 10
            or resolution.get("newly_resolved_blockers") != 2
            or tuple_type(exact_list(resolution.get("removed_blocker_codes"), "resolved codes"))
            != _resolved_targets
            or resolution.get("all_other_active_rows") != "BYTE_IDENTICAL_SAME_ORDER"
            or resolution.get("claims_block_change") != "NONE_V5_CLAIMS_VERBATIM"
            or resolution.get("resolution_basis") != transition.get("resolution_basis")
            or resolution.get("linear_issue_nee204_complete_before_receipt_protected_ci")
            is not False
            or resolution.get("linear_issue_nee122_complete") is not False
            or resolution.get("empirical_n_eff_available") is not False
            or resolution.get("milestone_m0_complete") is not False
            or resolution.get("scope_expansion_authorized") is not False
        ):
            fail("resolution arithmetic or boundary changed")
        if candidate["selection_009"] != evidence["selection_009"]:
            fail("receipt changed candidate selection 009")

    def verify_repository(
        root: Path,
        *,
        _policy_path: str = _POLICY_PATH,
        _policy_schema_path: str = _POLICY_SCHEMA_PATH,
        _export_path: str = _EXPORT_PATH,
        _export_schema_path: str = _EXPORT_SCHEMA_PATH,
        _expected_policy_sha: str = _EXPECTED_POLICY_SHA256,
        _expected_policy_schema_sha: str = _EXPECTED_POLICY_SCHEMA_SHA256,
        _expected_export_sha: str = _EXPECTED_EXPORT_SHA256,
        _expected_export_schema_sha: str = _EXPECTED_EXPORT_SCHEMA_SHA256,
        _expected_semantic_sha: str = _EXPECTED_POLICY_SEMANTIC_SHA256,
        _expected_derived_sha: str = _EXPECTED_DERIVED_EVIDENCE_SHA256,
        _policy_id: str = POLICY_ID,
        _policy_status: str = POLICY_STATUS,
        _export_id: str = EXPORT_ID,
        _export_status: str = EXPORT_STATUS,
        _v5_rows: tuple[tuple[str, str, str, str], ...] = _EXPECTED_V5_ROWS,
        _v6_rows: tuple[tuple[str, str, str, str], ...] = _EXPECTED_V6_ROWS,
        _resolved_v5: tuple[str, ...] = _EXPECTED_RESOLVED_V5,
        _resolved_targets: tuple[str, ...] = RESOLVED_TARGETS,
    ) -> tuple[Any, ...]:
        v5_policy, v5_export = verify_predecessor(root)
        candidate = verify_candidate(root)
        policy, policy_raw = load_json(_policy_path, root)
        export, export_raw = load_json(_export_path, root)
        policy_hash = hash_new(policy_raw).hexdigest()
        export_hash = hash_new(export_raw).hexdigest()
        if policy_hash != normal(_expected_policy_sha, "expected V6 policy hash"):
            fail("V6 policy bytes differ from reviewed bytes")
        if export_hash != normal(_expected_export_sha, "expected V5 export hash"):
            fail("V5 export bytes differ from reviewed bytes")
        if sha(_policy_schema_path, root) != normal(
            _expected_policy_schema_sha, "expected V6 policy schema hash"
        ) or sha(_export_schema_path, root) != normal(
            _expected_export_schema_sha, "expected V5 export schema hash"
        ):
            fail("V6/V5 schema bytes differ from reviewed bytes")
        exact_schema(_policy_schema_path, policy, root)
        exact_schema(_export_schema_path, export, root)
        expected_policy_keys = (
            "$schema",
            "schema_version",
            "policy_id",
            "ticket_id",
            "policy_status",
            "canonicalization",
            "supersedes",
            "operational_bundle",
            "accepted_integrity_evidence",
            "accepted_access_chain_evidence",
            "accepted_inference_evidence",
            "accepted_effective_trials_evidence",
            "resolved_or_superseded_blocker_codes",
            "unresolved_blockers",
            "claims",
            "blocked_downstream_issue_ids",
            "semantic_sha256",
        )
        if tuple_type(policy) != expected_policy_keys or (
            policy.get("$schema")
            != "../../schemas/governance/specification-freeze-policy-v6.schema.json"
            or policy.get("schema_version") != "qme.specification_freeze_policy.v6"
            or policy.get("policy_id") != _policy_id
            or policy.get("ticket_id") != "NEE-110"
            or policy.get("policy_status") != _policy_status
            or policy.get("canonicalization") != "qme.foundation.canonical_json.v1"
        ):
            fail("V6 policy identity or key order changed")
        supersedes = exact_dict(policy.get("supersedes"), "V6 predecessor binding")
        if hash_new(canonical(supersedes)).hexdigest() != normal(
            expected_supersedes_sha256, "expected predecessor semantic inventory"
        ):
            fail("V6 predecessor semantic inventory changed")
        for path, digest in v5_pins:
            field_by_path = {
                "configs/governance/specification-freeze-policy-v5.json": "policy_sha256",
                "schemas/governance/specification-freeze-policy-v5.schema.json": "schema_sha256",
                "configs/governance/specification-freeze-v5.hashes.json": "manifest_sha256",
                "configs/governance/specification-freeze-export-v4.json": "export_sha256",
                "schemas/governance/specification-freeze-export-v4.schema.json": "export_schema_sha256",
                "qme/governance/specification_freeze_v5.py": "runtime_sha256",
                "tests/governance/test_specification_freeze_v5.py": "tests_sha256",
                "docs/governance/SPECIFICATION_FREEZE_V5.md": "documentation_sha256",
            }
            if normal(supersedes.get(field_by_path[path]), field_by_path[path]) != normal(
                digest, f"expected {path} hash"
            ):
                fail(f"V6 predecessor binding changed: {path}")
        for field in (
            "operational_bundle",
            "accepted_integrity_evidence",
            "accepted_access_chain_evidence",
            "accepted_inference_evidence",
            "blocked_downstream_issue_ids",
        ):
            if policy.get(field) != v5_policy.get(field):
                fail(f"V6 inherited V5 field changed: {field}")
        verify_receipt_evidence(
            exact_dict(
                policy.get("accepted_effective_trials_evidence"), "effective-trials evidence"
            ),
            candidate,
            root,
        )
        if (
            tuple_type(
                row_tuple(row)
                for row in exact_list(policy.get("unresolved_blockers"), "V6 blockers")
            )
            != _v6_rows
        ):
            fail("V6 must retain exactly the first ten V5 rows")
        if (
            tuple_type(
                exact_list(
                    policy.get("resolved_or_superseded_blocker_codes"),
                    "V6 resolved lineage",
                )
            )
            != _resolved_v5 + _resolved_targets
        ):
            fail("V6 resolved lineage must append exactly two target codes")
        if json_dumps(
            policy.get("claims"), sort_keys=True, separators=(",", ":")
        ) != expected_claims_json or policy.get("claims") != v5_policy.get("claims"):
            fail("V6 claims changed or were promoted")
        semantic_hash = semantic(policy, "semantic_sha256")
        if semantic_hash != normal(
            policy.get("semantic_sha256"), "V6 semantic hash"
        ) or semantic_hash != normal(_expected_semantic_sha, "expected V6 semantic hash"):
            fail("V6 policy semantic digest changed")

        expected_export_keys = (
            "$schema",
            "schema_version",
            "export_id",
            "export_status",
            "policy",
            "bundle",
            "accepted_access_chain_evidence",
            "accepted_inference_evidence",
            "accepted_effective_trials_evidence",
            "contract_projections",
            "verification_checks",
            "active_blocker_codes",
            "closure",
            "derived_evidence_sha256",
        )
        if tuple_type(export) != expected_export_keys or (
            export.get("$schema")
            != "../../schemas/governance/specification-freeze-export-v5.schema.json"
            or export.get("schema_version") != "qme.specification_freeze_export.v5"
            or export.get("export_id") != _export_id
            or export.get("export_status") != _export_status
        ):
            fail("V5 export identity or key order changed")
        policy_binding = exact_dict(export.get("policy"), "export policy binding")
        if (
            tuple_type(policy_binding) != ("policy_id", "path", "sha256", "semantic_sha256")
            or policy_binding.get("policy_id") != _policy_id
            or policy_binding.get("path") != _policy_path
            or normal(policy_binding.get("sha256"), "export policy hash") != policy_hash
            or normal(policy_binding.get("semantic_sha256"), "export semantic hash")
            != semantic_hash
        ):
            fail("export policy binding changed")
        for field in (
            "bundle",
            "accepted_access_chain_evidence",
            "accepted_inference_evidence",
            "contract_projections",
        ):
            if export.get(field) != v5_export.get(field):
                fail(f"export inherited V5 projection changed: {field}")
        effective_projection = exact_dict(
            export.get("accepted_effective_trials_evidence"),
            "effective-trials export projection",
        )
        if (
            tuple_type(
                exact_list(
                    effective_projection.get("resolved_blocker_codes"),
                    "export resolved codes",
                )
            )
            != _resolved_targets
            or tuple_type(
                row_tuple(row)
                for row in exact_list(
                    effective_projection.get("original_v5_blocker_rows"),
                    "export target rows",
                )
            )
            != _v5_rows[-2:]
            or effective_projection.get("selection_009_n_eff_used") != 2
            or effective_projection.get("disposition")
            != "SYNTHETIC_DETERMINISTIC_ENGINEERING_EVIDENCE_ONLY_NO_EMPIRICAL_OR_PRODUCTION_INFERENCE"
        ):
            fail("effective-trials export projection changed")
        evidence = exact_dict(
            policy.get("accepted_effective_trials_evidence"), "policy effective-trials evidence"
        )
        candidate_binding = exact_dict(evidence.get("candidate"), "policy candidate binding")
        pull_request = exact_dict(evidence.get("candidate_pull_request"), "policy pull request")
        review = exact_dict(evidence.get("candidate_delta_review"), "policy delta review")
        signoff = exact_dict(evidence.get("owner_exact_byte_signoff"), "policy signoff")
        publication = exact_dict(evidence.get("publication_receipt"), "policy publication")
        expected_effective_projection = {
            "candidate_id": candidate_binding["candidate_id"],
            "config_path": candidate_binding["config_path"],
            "config_sha256": candidate_binding["config_sha256"],
            "semantic_sha256": candidate_binding["semantic_sha256"],
            "manifest_path": candidate_binding["manifest_path"],
            "manifest_sha256": candidate_binding["manifest_sha256"],
            "candidate_protected_main_commit": pull_request["protected_main_commit"],
            "candidate_protected_main_tree": pull_request["protected_main_tree"],
            "delta_review_comment_id": review["source_comment_id"],
            "delta_review_verdict_sha256": review["verdict_sha256"],
            "owner_signoff_comment_id": signoff["source_comment_id"],
            "owner_signoff_body_sha256": signoff["statement_sha256"],
            "publication_receipt_comment_id": publication["source_comment_id"],
            "publication_receipt_body_sha256": publication["source_body_sha256"],
            "selection_009_n_eff_used": 2,
            "disposition": "SYNTHETIC_DETERMINISTIC_ENGINEERING_EVIDENCE_ONLY_NO_EMPIRICAL_OR_PRODUCTION_INFERENCE",
            "resolved_blocker_codes": list_type(_resolved_targets),
            "original_v5_blocker_rows": [
                {
                    "blocker_code": row[0],
                    "ticket_id": row[1],
                    "category": row[2],
                    "description": row[3],
                }
                for row in _v5_rows[-2:]
            ],
            "scope": evidence["scope"],
        }
        if effective_projection != expected_effective_projection:
            fail("effective-trials export and policy projection differ")
        expected_checks = (
            ("PREDECESSOR_FREEZE_V5_BYTES_AND_MANIFEST", "PASS"),
            ("PREDECESSOR_FREEZE_V5_NATIVE_TESTS_IN_SAME_CI", "PASS"),
            ("NEE204_CANDIDATE_BYTES_AND_MANIFEST", "PASS"),
            ("NEE204_CANDIDATE_PROTECTED_MAIN_EXACT_SHA_CI", "PASS"),
            ("FRESH_INDEPENDENT_CANDIDATE_DELTA_REVIEW", "PASS"),
            ("OWNER_EXACT_BYTE_CANDIDATE_SIGNOFF", "PASS"),
            ("SELECTION_009_SYNTHETIC_EVIDENCE_BINDING", "PASS"),
            ("EXACT_TWO_BLOCKER_RESOLUTION_DELTA", "PASS"),
            ("ACTIVE_BLOCKER_EXACT_EQUALITY", "PASS"),
            ("SCHEMA_CONFIG_PARITY", "PASS"),
            ("EMPIRICAL_OR_PRODUCTION_EFFECTIVE_TRIALS", "BLOCKED"),
            ("DSR_AND_HOLM_EXECUTION", "BLOCKED"),
            ("CROSS_CONTRACT_SEMANTIC_APPROVAL", "BLOCKED"),
            ("FINAL_FREEZE_ANCHOR_AND_RECEIPT", "BLOCKED"),
        )
        checks_projection = tuple_type(
            (
                check.get("check_id"),
                check.get("status"),
            )
            for item in exact_list(export.get("verification_checks"), "export checks")
            for check in (exact_dict(item, "export verification check"),)
            if tuple_type(check) == ("check_id", "status")
        )
        if (
            len_builtin(checks_projection) != len_builtin(expected_checks)
            or checks_projection != expected_checks
        ):
            fail("export verification checks changed")
        active_codes = tuple_type(row[0] for row in _v6_rows)
        if (
            tuple_type(exact_list(export.get("active_blocker_codes"), "export blocker codes"))
            != active_codes
        ):
            fail("export active blocker projection changed")
        closure = exact_dict(export.get("closure"), "export closure")
        expected_closure = dict_type(v5_export["closure"])
        expected_closure["overall_state"] = "BLOCKED_10_ACTIVE"
        if closure != expected_closure:
            fail("export closure changed or was promoted")
        derived = semantic(export, "derived_evidence_sha256")
        if derived != normal(
            export.get("derived_evidence_sha256"), "derived evidence hash"
        ) or derived != normal(_expected_derived_sha, "expected derived evidence hash"):
            fail("export derived evidence digest changed")
        return (
            _policy_status,
            policy_hash,
            export_hash,
            semantic_hash,
            derived,
            active_codes,
            _resolved_targets,
            2,
            True,
            False,
            root,
        )

    def make_result(state: tuple[Any, ...]) -> VerifiedSpecificationFreezeV6:
        value = object_new(result_type)
        for slot, item in zip_builtin(result_type.__slots__, state, strict=True):
            object_setattr(value, slot, item)
        return value

    def state_from_result(value: object) -> tuple[Any, ...]:
        if type_builtin(value) is not result_type:
            fail("verified freeze result must have exact type")
        try:
            return tuple_type(object_getattribute(value, slot) for slot in result_type.__slots__)
        except attribute_error_type as exc:
            raise error_type("verified freeze result is incomplete") from exc

    def verify(repository_root: Path | None = None) -> VerifiedSpecificationFreezeV6:
        root = (repository_root or path_class.cwd()).resolve(strict=True)
        verify_manifest(root)
        return make_result(verify_repository(root))

    def projection(state: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "status": tuple_getitem(state, 0),
            "policy_sha256": tuple_getitem(state, 1),
            "export_sha256": tuple_getitem(state, 2),
            "semantic_sha256": tuple_getitem(state, 3),
            "derived_evidence_sha256": tuple_getitem(state, 4),
            "active_blocker_codes": list_type(tuple_getitem(state, 5)),
            "active_blocker_count": len_builtin(tuple_getitem(state, 5)),
            "resolved_targets": list_type(tuple_getitem(state, 6)),
            "selection_009_n_eff_used": tuple_getitem(state, 7),
            "receipt_protected_ci_required": tuple_getitem(state, 8),
            "milestone_m0_complete": tuple_getitem(state, 9),
            "production_ready": False,
            "live_order_authority": False,
        }

    def serialize(
        value: VerifiedSpecificationFreezeV6, repository_root: Path | None = None
    ) -> Mapping[str, Any]:
        supplied = state_from_result(value)
        root = (repository_root or cast_fn(path_class, tuple_getitem(supplied, 10))).resolve(
            strict=True
        )
        verify_manifest(root)
        replayed = verify_repository(root)
        if supplied != replayed:
            fail("supplied verified freeze does not match fresh repository replay")
        return cast_fn(  # type: ignore[no-any-return]
            mapping_type, mapping_proxy_type(projection(replayed))
        )

    def normalized_runtime_sha(root: Path, *, _runtime_path: str = _RUNTIME_PATH) -> str:
        raw = read_bytes(_runtime_path, root)
        replaced, count = runtime_pin_pattern.subn(
            rb"\g<1>00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000\g<2>",
            raw,
        )
        if count != 1:
            fail("runtime normalized self-pin marker count drift")
        return cast_fn(str_type, hash_new(replaced).hexdigest())

    def verify_manifest(
        repository_root: Path | None = None,
        *,
        _manifest_path: str = _MANIFEST_PATH,
        _policy_id: str = POLICY_ID,
        _manifest_paths: tuple[str, ...] = _MANIFEST_PATHS,
        _runtime_path: str = _RUNTIME_PATH,
        _runtime_normalized_sha: str = _EXPECTED_RUNTIME_NORMALIZED_SHA256,
    ) -> Mapping[str, str]:
        root = (repository_root or path_class.cwd()).resolve(strict=True)
        document, _ = load_json(_manifest_path, root)
        if (
            document.get("schema_version") != "qme.hash_manifest.v1"
            or document.get("artifact_id") != _policy_id
            or document.get("status") != "BLOCKED_10_ACTIVE"
        ):
            fail("Freeze V6 manifest identity changed")
        rows = exact_list(document.get("artifacts"), "Freeze V6 manifest rows")
        if len_builtin(rows) != len_builtin(_manifest_paths):
            fail("Freeze V6 manifest membership changed")
        actual: dict[str, str] = {}
        for raw_row in rows:
            row = exact_dict(raw_row, "Freeze V6 manifest row")
            if tuple_type(row) != ("path", "sha256"):
                fail("Freeze V6 manifest row shape or order changed")
            member = exact_str(row.get("path"), "Freeze V6 manifest path")
            if member in actual:
                fail("Freeze V6 manifest path duplicated")
            digest = sha(member, root)
            if digest != normal(row.get("sha256"), f"{member} manifest hash"):
                fail(f"Freeze V6 manifest leaf mismatch: {member}")
            actual[member] = digest
        if tuple_type(actual) != _manifest_paths:
            fail("Freeze V6 manifest path order changed")
        for member, expected in nonruntime_pins:
            if actual.get(member) != normal(expected, f"{member} independent pin"):
                fail(f"Freeze V6 full-local-repin rejected: {member}")
        if normalized_runtime_sha(root) != normal(
            _runtime_normalized_sha, "runtime normalized self hash"
        ):
            fail("Freeze V6 runtime normalized self hash mismatch")
        return cast_fn(  # type: ignore[no-any-return]
            mapping_type, mapping_proxy_type(actual)
        )

    return verify, serialize, verify_manifest


(
    verify_specification_freeze_v6,
    serialize_specification_freeze_v6_export,
    verify_specification_freeze_v6_manifest,
) = _build_trusted_api(
    claims=_EXPECTED_CLAIMS,
    datetime_type=datetime,
    error_type=SpecificationFreezeV6Error,
    expected_candidate=_EXPECTED_CANDIDATE,
    expected_effective_trials_evidence_sha256=_EXPECTED_EFFECTIVE_TRIALS_EVIDENCE_SHA256,
    expected_nonruntime_leaves=_EXPECTED_NONRUNTIME_LEAVES,
    expected_schema_metadata=_EXPECTED_SCHEMA_METADATA,
    expected_selection=_EXPECTED_SELECTION,
    expected_supersedes_sha256=_EXPECTED_SUPERSEDES_SHA256,
    expected_v5=_EXPECTED_V5,
    format_checker_type=FormatChecker,
    hash_new=hashlib.sha256,
    json_dumps=json.dumps,
    json_error_type=json.JSONDecodeError,
    json_loads=json.loads,
    mapping_proxy_type=MappingProxyType,
    os_module=os,
    path_class=Path,
    re_module=re,
    result_type=VerifiedSpecificationFreezeV6,
    stat_module=stat,
    type_builtin=type,
    validator_type=Draft202012Validator,
)
del _build_trusted_api
