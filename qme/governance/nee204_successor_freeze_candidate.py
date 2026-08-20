"""Fail-closed verifier for the NEE-204 successor-freeze candidate.

The candidate is deliberately incapable of changing Freeze V5.  It binds the
exact owner selection-009 decision and proposes a two-row transition that may
only be enacted by a later, separately reviewed receipt and successor freeze.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from jsonschema import (  # type: ignore[import-untyped, unused-ignore]
    Draft202012Validator,
    FormatChecker,
)

__all__ = [
    "Nee204SuccessorFreezeCandidateError",
    "VerifiedNee204SuccessorFreezeCandidate",
    "normalize_grouped_sha256",
    "serialize_verified_nee204_successor_freeze_candidate",
    "verify_nee204_successor_freeze_candidate",
    "verify_nee204_successor_freeze_candidate_manifest",
]


_CONFIG_PATH = "configs/governance/nee204-successor-freeze-candidate-v1.json"
_SCHEMA_PATH = "schemas/governance/nee204-successor-freeze-candidate-v1.schema.json"
_MANIFEST_PATH = "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json"
_RUNTIME_PATH = "qme/governance/nee204_successor_freeze_candidate.py"

_EXPECTED_CONFIG_SHA256 = "5450c34d:ee31729c:533f6422:773fa69a:0e75b400:b4def0a1:f7c15495:fb031dc1"
_EXPECTED_CONFIG_SEMANTIC_SHA256 = (
    "eb441df6:cf49748e:0890e459:cca31445:931f9d6b:a73aff1d:909bc4d6:75c87871"
)
_EXPECTED_SCHEMA_SHA256 = "6517d0b0:9b25fb36:5899a542:173a97e5:85360add:47733b76:018c9452:2edffeb7"
# fmt: off
_EXPECTED_RUNTIME_NORMALIZED_SHA256 = "56d1914f:7f30e4dd:0c836fbd:6fa0ead2:1c9dfd99:ab10fdb5:d499544c:9c3a4abe"
# fmt: on

_EXPECTED_OWNED_NONRUNTIME_LEAVES = MappingProxyType(
    {
        _CONFIG_PATH: _EXPECTED_CONFIG_SHA256,
        "docs/governance/NEE_204_SUCCESSOR_FREEZE_CANDIDATE_V1.md": "d23ad553:dec0ff0b:db86a9c9:ee864944:d8f9bb83:108d7fac:a4b774da:a3c66779",
        _SCHEMA_PATH: _EXPECTED_SCHEMA_SHA256,
        "tests/governance/test_nee204_successor_freeze_candidate.py": "0462b78e:aedaf6ac:1245c452:f25a4208:1b8484fa:e5ad9ed5:cb140439:29356d36",
    }
)

_EXPECTED_LINEAGE = (
    (
        "configs/governance/specification-freeze-v4.hashes.json",
        "a2c3bbfa:d15e7bd3:769142ad:69c291e7:885cd14d:6ca2d939:99c39df2:5360ea42",
        "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V4",
        "IMMUTABLE_PREDECESSOR_FREEZE",
    ),
    (
        "configs/governance/specification-freeze-v5.hashes.json",
        "2eb7a5bd:b6117b71:b0b77836:eca6548a:3609141d:9db2c817:2c2f22b5:0489e548",
        "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V5",
        "CURRENT_ACTIVE_FREEZE",
    ),
    (
        "configs/governance/ppw-bootstrap-uncertainty-authority-v1.hashes.json",
        "464066ca:0595defa:64036b7b:53047d25:fce6ab1c:ee2a30fb:c6c5742f:45c7d12b",
        "QME-PPW-BOOTSTRAP-UNCERTAINTY-AUTHORITY-V1",
        "SOURCE_EQUATION_AUTHORITY",
    ),
    (
        "configs/governance/ppw-bootstrap-owner-selections-v1.hashes.json",
        "e325adbc:2f4d8f8e:4e8034c0:f74ca022:d4f2e2cc:54ca0226:d9e31cdc:b38de58f",
        "QME-PPW-BOOTSTRAP-OWNER-SELECTIONS-V1",
        "OWNER_SELECTIONS_001_THROUGH_008",
    ),
    (
        "configs/governance/effective-trials-point-evidence-v1.hashes.json",
        "121db413:12c69c5a:30ac31ce:cb116c75:83e68636:afed78f7:91c34269:f995edd5",
        "NEE-175-EFFECTIVE-TRIALS-POINT-EVIDENCE-V1",
        "DETERMINISTIC_POINT_KERNEL_AND_ANALYTIC_FIXTURES",
    ),
    (
        "configs/governance/effective-trials-uncertainty-v1.hashes.json",
        "01faf91e:6601b8c7:085aaadd:7d4decc3:7adcca5b:a78bf34a:a1a6536d:1a387864",
        "QME-EFFECTIVE-TRIALS-UNCERTAINTY-V1",
        "SEEDED_2000_REPLICATE_IMPLEMENTATION_AND_KAT",
    ),
    (
        "configs/governance/ppw-independent-vector-kats-v1.hashes.json",
        "f1a86a1e:7fc7f1a5:6a161d8b:49977253:9c8503cc:5a5e2416:3fff09c6:36fa6acd",
        "QME-PPW-INDEPENDENT-VECTOR-KATS-V1",
        "INDEPENDENT_NUMERIC_SELECTION_009_PACKET",
    ),
)

_EXPECTED_ACTIVE_ROWS = (
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

_EXPECTED_TARGET_CODES = (
    "NEE-122-CORRELATED-TRIAL-FIXTURE",
    "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE",
)
_EXPECTED_RETAINED_CODES = tuple(row[0] for row in _EXPECTED_ACTIVE_ROWS[:-2])
_EXPECTED_RESOLVED_CODES = (
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

_EXPECTED_FREEZE_CLAIMS = MappingProxyType(
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

_EXPECTED_CANDIDATE_CLAIMS = MappingProxyType(
    {
        "successor_freeze_candidate_registered": True,
        "independent_review_receipt_bound": True,
        "owner_selection_009_decision_bound": True,
        "selection_009_synthetic_evidence_accepted_by_owner": True,
        "lineage_manifests_replayed": True,
        "fresh_candidate_delta_review_required": True,
        "owner_exact_byte_signoff_required": True,
        "separate_receipt_required": True,
        "target_blockers_cleared": False,
        "any_freeze_v5_blocker_cleared": False,
        "successor_freeze_published": False,
        "receipt_published": False,
        "candidate_delta_review_satisfied": False,
        "owner_candidate_signoff_recorded": False,
        "nee204_linear_issue_complete": False,
        "nee122_linear_issue_complete": False,
        "production_n_eff_available": False,
        "dsr_computable": False,
        "holm_execution_accepted": False,
        "empirical_performance_available": False,
        "alpha_proven": False,
        "production_ready": False,
        "milestone_m0_complete": False,
        "live_order_authority": False,
    }
)

_EXPECTED_NONCLAIMS = (
    "THIS_CANDIDATE_PROPOSES_BUT_DOES_NOT_PERFORM_A_TWO_ROW_BLOCKER_TRANSITION",
    "FREEZE_V5_REMAINS_BYTE_IDENTICAL_WITH_12_ACTIVE_BLOCKERS",
    "OWNER_SELECTION_009_ACCEPTANCE_IS_LIMITED_TO_THE_EXACT_SYNTHETIC_REVIEWED_EVIDENCE",
    "PREDECESSOR_SELECTION_009_ACCEPTED_FALSE_FIELDS_REMAIN_IMMUTABLE_HISTORICAL_FACTS",
    "NO_DSR_OR_HOLM_ACCEPTANCE",
    "NO_EMPIRICAL_OR_PRODUCTION_EFFECTIVE_TRIAL_RESULT",
    "NO_ALPHA_OR_PRODUCTION_READINESS_CLAIM",
    "NO_M0_COMPLETION_OR_FINAL_FREEZE_CLAIM",
    "NO_LIVE_ORDER_AUTHORITY",
    "NEE204_AND_NEE122_REMAIN_IN_PROGRESS_PENDING_THE_FULL_SUCCESSOR_SEQUENCE",
)


class Nee204SuccessorFreezeCandidateError(ValueError):
    """Raised when any candidate, lineage, path, or claim check fails."""


class VerifiedNee204SuccessorFreezeCandidate:
    """Opaque verified result. Construction outside the private verifier is invalid."""

    __slots__ = (
        "_status",
        "_config_sha256",
        "_semantic_sha256",
        "_active_blocker_count",
        "_proposed_active_blocker_count",
        "_historical_resolved_count",
        "_proposed_historical_resolved_count",
        "_removed_codes",
        "_review_sha256",
        "_owner_decision_sha256",
        "_lineage_leaf_count",
    )

    def __new__(cls, *_args: object, **_kwargs: object) -> VerifiedNee204SuccessorFreezeCandidate:
        raise Nee204SuccessorFreezeCandidateError("verified values are verifier-created only")

    @property
    def status(self) -> str:
        return cast(str, object.__getattribute__(self, "_status"))

    @property
    def active_blocker_count(self) -> int:
        return cast(int, object.__getattribute__(self, "_active_blocker_count"))

    @property
    def proposed_active_blocker_count(self) -> int:
        return cast(int, object.__getattribute__(self, "_proposed_active_blocker_count"))

    @property
    def removed_codes(self) -> tuple[str, ...]:
        return cast(tuple[str, ...], object.__getattribute__(self, "_removed_codes"))

    @property
    def config_sha256(self) -> str:
        return cast(str, object.__getattribute__(self, "_config_sha256"))

    @property
    def semantic_sha256(self) -> str:
        return cast(str, object.__getattribute__(self, "_semantic_sha256"))


def normalize_grouped_sha256(value: object, field: str) -> str:
    """Normalize an exact grouped SHA-256 string to 64 lowercase hex digits."""

    if type(value) is not str:
        raise Nee204SuccessorFreezeCandidateError(f"{field} must be a string")
    if re.fullmatch(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}", value) is None:
        raise Nee204SuccessorFreezeCandidateError(f"{field} is not grouped SHA-256")
    return value.replace(":", "")


def _build_trusted_api(
    *,
    candidate_claims: Mapping[str, bool],
    cast_function: Any,
    config_path: str,
    config_sha: str,
    config_semantic_sha: str,
    error_type: type[Nee204SuccessorFreezeCandidateError],
    expected_active_rows: tuple[tuple[str, str, str, str], ...],
    expected_freeze_claims: Mapping[str, bool],
    expected_lineage: tuple[tuple[str, str, str, str], ...],
    expected_nonclaims: tuple[str, ...],
    expected_owned_leaves: Mapping[str, str],
    expected_resolved_codes: tuple[str, ...],
    expected_retained_codes: tuple[str, ...],
    expected_target_codes: tuple[str, ...],
    format_checker_type: type[FormatChecker],
    hash_new: Any,
    json_dumps: Any,
    json_error_type: type[Exception],
    json_loads: Any,
    manifest_path: str,
    mapping_proxy_type: Any,
    os_module: Any,
    path_class: type[Path],
    re_module: Any,
    result_type: type[VerifiedNee204SuccessorFreezeCandidate],
    runtime_path: str,
    runtime_normalized_sha: str,
    schema_path: str,
    schema_sha: str,
    stat_module: Any,
    type_builtin: type,
    validator_type: type[Draft202012Validator],
) -> tuple[Any, Any, Any]:
    path_type = type_builtin(path_class())
    object_new = object.__new__
    object_setattr = object.__setattr__
    object_getattribute = object.__getattribute__
    cast_value = cast_function
    tuple_getitem = tuple.__getitem__
    max_bytes = 4 * 1024 * 1024
    grouped_pattern = re_module.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}")
    runtime_pin_pattern = re_module.compile(
        rb'(_EXPECTED_RUNTIME_NORMALIZED_SHA256 = ")[0-9a-f:]{71}("\r?\n)'
    )

    def fail(message: str) -> None:
        raise error_type(message)

    def normal(value: object, field: str) -> str:
        if type_builtin(value) is not str or grouped_pattern.fullmatch(value) is None:
            fail(f"{field} is not exact grouped SHA-256")
        return cast_value(str, value).replace(":", "")  # type: ignore[no-any-return]

    def exact_dict(value: object, field: str) -> dict[str, Any]:
        if type_builtin(value) is not dict:
            fail(f"{field} must be an exact object")
        return cast_value(dict[str, Any], value)  # type: ignore[no-any-return]

    def exact_list(value: object, field: str) -> list[Any]:
        if type_builtin(value) is not list:
            fail(f"{field} must be an exact array")
        return cast_value(list[Any], value)  # type: ignore[no-any-return]

    def exact_str(value: object, field: str) -> str:
        if type_builtin(value) is not str:
            fail(f"{field} must be exact text")
        return cast_value(str, value)  # type: ignore[no-any-return]

    def row_tuple(value: object) -> tuple[str, str, str, str]:
        row = exact_dict(value, "blocker row")
        if tuple(row.keys()) != (
            "blocker_code",
            "ticket_id",
            "category",
            "description",
        ):
            fail("blocker row key order or type drift")
        fields = tuple(row[key] for key in ("blocker_code", "ticket_id", "category", "description"))
        if any(type_builtin(item) is not str for item in fields):
            fail("blocker row field type drift")
        return cast_value(tuple[str, str, str, str], fields)  # type: ignore[no-any-return]

    def pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                fail(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> None:
        fail(f"non-finite JSON number: {value}")

    def path_identity(path: Path) -> tuple[int, int, int, int, int]:
        info = path.lstat()
        attrs = int(getattr(info, "st_file_attributes", 0))
        reparse = int(getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
        if stat_module.S_ISLNK(info.st_mode) or (reparse and attrs & reparse):
            fail(f"linked or reparse path rejected: {path}")
        return (int(info.st_dev), int(info.st_ino), int(info.st_mode), int(info.st_nlink), attrs)

    def safe_parts(relative: str) -> tuple[str, ...]:
        if type_builtin(relative) is not str or not relative or "\\" in relative or ":" in relative:
            fail("artifact path is not canonical repository-relative POSIX text")
        parts = tuple(relative.split("/"))
        if any(part in ("", ".", "..") for part in parts):
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
        ancestor_list = [root_resolved]
        cursor = root_resolved
        for part in parts[:-1]:
            cursor = cursor / part
            ancestor_list.append(cursor)
        ancestors = tuple(ancestor_list)
        before = tuple((str(path), path_identity(path)) for path in ancestors)
        resolved = target.resolve(strict=True)
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            fail("artifact escapes repository root")
        target_before = path_identity(target)
        if not stat_module.S_ISREG(target_before[2]) or target_before[3] != 1:
            fail("artifact must be a single-link regular file")
        flags = (
            os_module.O_RDONLY
            | int(getattr(os_module, "O_BINARY", 0))
            | int(getattr(os_module, "O_NOFOLLOW", 0))
        )
        descriptor = os_module.open(target, flags)
        try:
            opened = os_module.fstat(descriptor)
            opened_identity = (
                int(opened.st_dev),
                int(opened.st_ino),
                int(opened.st_mode),
                int(opened.st_nlink),
                int(getattr(opened, "st_file_attributes", 0)),
            )
            if (
                opened_identity != target_before
                or not stat_module.S_ISREG(opened.st_mode)
                or int(opened.st_nlink) != 1
            ):
                fail("artifact changed before same-handle read")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = os_module.read(descriptor, 65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    fail("artifact exceeds bounded verifier size")
                chunks.append(chunk)
            after_open = os_module.fstat(descriptor)
            after_open_identity = (
                int(after_open.st_dev),
                int(after_open.st_ino),
                int(after_open.st_mode),
                int(after_open.st_nlink),
                int(getattr(after_open, "st_file_attributes", 0)),
            )
            if after_open_identity != opened_identity:
                fail("artifact changed during same-handle read")
            raw = b"".join(chunks)
        finally:
            os_module.close(descriptor)
        if path_identity(target) != target_before or target.resolve(strict=True) != resolved:
            fail("artifact path changed after same-handle read")
        after = tuple((str(path), path_identity(path)) for path in ancestors)
        if after != before:
            fail("artifact ancestor identity changed during read")
        return raw

    def load_json(relative: str, root: Path) -> tuple[dict[str, Any], bytes]:
        raw = read_bytes(relative, root)
        try:
            text = raw.decode("utf-8", errors="strict")
            value = json_loads(
                text, object_pairs_hook=pairs_object, parse_constant=reject_nonfinite
            )
        except (UnicodeDecodeError, json_error_type) as exc:
            raise error_type(f"artifact is not strict UTF-8 JSON: {relative}") from exc
        if type_builtin(value) is not dict:
            fail(f"JSON root must be an object: {relative}")
        return cast_value(dict[str, Any], value), raw

    def semantic_digest(document: dict[str, Any]) -> str:
        projected = dict(document)
        projected.pop("semantic_sha256", None)
        raw = (
            json_dumps(
                projected,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        return cast_value(str, hash_new(raw).hexdigest())  # type: ignore[no-any-return]

    def verify_manifest(relative: str, expected_sha: str, expected_id: str, root: Path) -> int:
        document, raw = load_json(relative, root)
        if hash_new(raw).hexdigest() != normal(expected_sha, f"{relative} hash"):
            fail(f"lineage manifest hash mismatch: {relative}")
        if document.get("artifact_id") != expected_id:
            fail(f"lineage manifest artifact identity mismatch: {relative}")
        artifacts = exact_list(document.get("artifacts"), f"{relative} artifacts")
        if not artifacts:
            fail(f"lineage manifest artifacts invalid: {relative}")
        seen: set[str] = set()
        for row in artifacts:
            manifest_row = exact_dict(row, "manifest artifact row")
            path = exact_str(manifest_row.get("path"), "manifest artifact path")
            digest = manifest_row.get("sha256")
            if path in seen:
                fail("manifest artifact path missing or duplicated")
            seen.add(path)
            if hash_new(read_bytes(path, root)).hexdigest() != normal(digest, f"{path} hash"):
                fail(f"lineage artifact hash mismatch: {path}")
        return len(artifacts)

    def verify_receipt(
        receipt: object, expected_id: str, expected_bytes: int, expected_sha: str
    ) -> None:
        value = exact_dict(receipt, "decision receipt")
        if value.get("source_comment_id") != expected_id:
            fail("decision receipt comment identity mismatch")
        body = exact_str(value.get("source_body"), "decision receipt body")
        raw = body.encode("utf-8")
        if len(raw) != expected_bytes or hash_new(raw).hexdigest() != normal(
            expected_sha, "decision receipt body hash"
        ):
            fail("decision receipt body bytes mismatch")

    def verify_repository(root: Path) -> tuple[Any, ...]:
        config, config_raw = load_json(config_path, root)
        config_digest = hash_new(config_raw).hexdigest()
        if config_digest != normal(config_sha, "candidate config hash"):
            fail("candidate config raw hash mismatch")
        if semantic_digest(config) != normal(
            config_semantic_sha, "candidate semantic hash"
        ) or normal(config.get("semantic_sha256"), "candidate semantic field") != normal(
            config_semantic_sha, "candidate semantic hash"
        ):
            fail("candidate semantic hash mismatch")

        schema, schema_raw = load_json(schema_path, root)
        if hash_new(schema_raw).hexdigest() != normal(schema_sha, "candidate schema hash"):
            fail("candidate schema raw hash mismatch")
        validator_type.check_schema(schema)
        errors = tuple(
            validator_type(schema, format_checker=format_checker_type()).iter_errors(config)
        )
        if errors:
            fail(f"candidate schema validation failed: {errors[0].message}")

        if (
            config.get("candidate_kind") != "BLOCKER_TRANSITION_CANDIDATE_NOT_BLOCKER_CLEARANCE"
            or config.get("status")
            != "CANDIDATE_UNREVIEWED_FREEZE_V5_UNCHANGED_PENDING_DELTA_REVIEW_EXACT_BYTE_OWNER_SIGNOFF_AND_RECEIPT"
        ):
            fail("candidate identity or status drift")
        incapability = exact_dict(config.get("candidate_incapability"), "candidate incapability")
        if (
            incapability.get("can_change_active_freeze") is not False
            or len(exact_list(incapability.get("transition_ladder"), "transition ladder")) != 5
        ):
            fail("candidate incapability contract drift")

        authority = exact_dict(config.get("authority"), "candidate authority")
        verify_receipt(
            authority.get("independent_review_receipt"),
            "e306099c-8dc0-4699-915c-1fd3ca9e5d29",
            3113,
            "81ff408a:dd01c3ea:fdc91b0c:c03b177f:92f13d3f:6523a29e:b7d1baf2:4162a359",
        )
        verify_receipt(
            authority.get("owner_selection_009_decision_receipt"),
            "261dee73-a885-4297-922a-3bd67a9e55fb",
            2633,
            "65ea4e97:a1c626ab:a677d9bc:f82fcecf:3d8f57f6:9d4fa3ef:d6a6e024:3f09b05e",
        )
        protected = exact_dict(authority.get("protected_main"), "protected-main authority")
        if protected != {
            "commit": "a7ee2f5a:75d58cbe:6bc88cf4:e5d17763:9b56aecd",
            "tree": "497e5702:cd46ade4:9f4e7120:eaf6f9fe:aab38bf3",
            "qme_ci_workflow_path": ".github/workflows/ci.yml",
            "qme_ci_workflow_sha256": "a2f84258:c1b694cd:6e2761fd:5b4a07c2:c7306cf4:5368af1e:e5c5ff7a:c933992f",
            "push_run": "32346828082",
            "push_job": "96357230515",
            "event": "push",
            "conclusion": "success",
        }:
            fail("protected-main authority drift")
        if hash_new(read_bytes(".github/workflows/ci.yml", root)).hexdigest() != normal(
            protected["qme_ci_workflow_sha256"], "qme-ci workflow hash"
        ):
            fail("qme-ci workflow hash mismatch")

        lineage = exact_list(config.get("lineage_manifests"), "lineage manifest inventory")
        projected_lineage = tuple(
            (
                exact_dict(row, "lineage manifest row").get("path"),
                exact_dict(row, "lineage manifest row").get("sha256"),
                exact_dict(row, "lineage manifest row").get("artifact_id"),
                exact_dict(row, "lineage manifest row").get("role"),
            )
            for row in lineage
        )
        if projected_lineage != expected_lineage:
            fail("lineage manifest inventory drift")
        leaf_count = 1
        for path, digest, artifact_id, _role in expected_lineage:
            leaf_count += verify_manifest(path, digest, artifact_id, root)
        if leaf_count != 58:
            fail("expected 58 direct authority leaves")

        freeze, freeze_raw = load_json(
            "configs/governance/specification-freeze-policy-v5.json", root
        )
        if hash_new(freeze_raw).hexdigest() != normal(
            "054270b6:d749e82e:38c9cd24:cba93a24:b56ec676:feed22cf:d9b6a211:cf37c840",
            "Freeze V5 policy hash",
        ):
            fail("Freeze V5 policy bytes changed")
        if semantic_digest(freeze) != normal(
            "85f0e7d9:62992601:2a44217c:bf8133ca:2169855d:db1a0296:6a908ef5:9a650ef3",
            "Freeze V5 semantic hash",
        ):
            fail("Freeze V5 semantic hash changed")
        rows = exact_list(freeze.get("unresolved_blockers"), "Freeze V5 unresolved blockers")
        if tuple(row_tuple(row) for row in rows) != expected_active_rows:
            fail("Freeze V5 blocker rows changed")
        resolved = exact_list(
            freeze.get("resolved_or_superseded_blocker_codes"), "Freeze V5 resolved lineage"
        )
        if tuple(resolved) != expected_resolved_codes:
            fail("Freeze V5 resolved lineage changed")
        if (
            freeze.get("policy_id") != "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V5"
            or freeze.get("policy_status")
            != "BLOCKED_12_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
            or freeze.get("claims") != dict(expected_freeze_claims)
        ):
            fail("Freeze V5 policy identity, status, or claims changed")
        export, _ = load_json("configs/governance/specification-freeze-export-v4.json", root)
        active_codes = tuple(row[0] for row in expected_active_rows)
        if (
            tuple(exact_list(export.get("active_blocker_codes"), "Freeze V5 export blocker codes"))
            != active_codes
            or exact_dict(export.get("closure"), "Freeze V5 export closure").get("overall_state")
            != "BLOCKED_12_ACTIVE"
        ):
            fail("Freeze V5 export projection changed")

        target = exact_dict(config.get("target"), "candidate target")
        if (
            tuple(
                row_tuple(row)
                for row in exact_list(target.get("blocker_rows_verbatim"), "candidate target rows")
            )
            != expected_active_rows[-2:]
            or target.get("transition_count") != 2
            or target.get("transition_performed_by_this_candidate") is not False
        ):
            fail("candidate target row contract drift")
        transition = exact_dict(config.get("proposed_transition"), "candidate proposed transition")
        if (
            tuple(exact_list(transition.get("removes_exactly"), "candidate removed codes"))
            != expected_target_codes
            or tuple(
                exact_list(
                    transition.get("retained_active_blocker_codes_in_order"),
                    "candidate retained codes",
                )
            )
            != expected_retained_codes
        ):
            fail("candidate proposed transition set or order drift")
        if transition.get("freeze_state_at_candidate") != {
            "active": 12,
            "historical_resolved_or_superseded": 18,
        } or transition.get("freeze_state_after_receipt_if_separately_accepted") != {
            "active": 10,
            "historical_resolved_or_superseded": 20,
        }:
            fail("candidate transition arithmetic drift")
        if (
            transition.get("successor_freeze_claims_block")
            != "NO_CHANGE_PROPOSED_BY_THIS_CANDIDATE"
            or transition.get("milestone_m0_complete_after_transition") is not False
            or transition.get("production_ready_after_transition") is not False
            or transition.get("empirical_performance_available_after_transition") is not False
        ):
            fail("candidate transition overclaim")

        selection = exact_dict(config.get("selection_009"), "selection 009 projection")
        if selection != {
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
        }:
            fail("selection 009 decision projection drift")

        uncertainty, _ = load_json("configs/governance/effective-trials-uncertainty-v1.json", root)
        candidate_kat = exact_dict(uncertainty.get("candidate_kat"), "uncertainty candidate KAT")
        uncertainty_claims = exact_dict(uncertainty.get("claims"), "uncertainty claims")
        if (
            candidate_kat.get("index_stream_sha256") != selection["index_stream_sha256"]
            or candidate_kat.get("bootstrap_distribution_sha256")
            != selection["bootstrap_distribution_sha256"]
            or candidate_kat.get("order_statistic_1950") != selection["rank_value"]
            or candidate_kat.get("n_eff_used") != 2
            or candidate_kat.get("selection_009_accepted") is not False
            or uncertainty_claims.get("selection_009_accepted") is not False
        ):
            fail("seeded uncertainty evidence or immutable predecessor claim drift")
        vector, _ = load_json("configs/governance/ppw-independent-vector-kats-v1.json", root)
        vector_claims = exact_dict(vector.get("claims"), "vector evidence claims")
        if (
            vector.get("vector_classes")
            != [
                "short",
                "constant",
                "IID",
                "negatively_correlated",
                "zero_negative_intermediate",
                "floor",
                "cap",
                "integer_boundary",
                "96_column_aggregation",
            ]
            or vector_claims.get("selection_009_accepted") is not False
            or vector_claims.get("freeze_blocker_changed") is not False
        ):
            fail("independent vector evidence or immutable predecessor claim drift")

        claims = config.get("claims")
        if (
            claims != dict(candidate_claims)
            or tuple(exact_list(config.get("nonclaims"), "candidate nonclaims"))
            != expected_nonclaims
        ):
            fail("candidate claims or nonclaims drift")
        issue_boundary = exact_dict(config.get("issue_state_boundary"), "issue-state boundary")
        if (
            issue_boundary.get("nee204_status") != "IN_PROGRESS"
            or issue_boundary.get("nee122_status") != "IN_PROGRESS"
            or issue_boundary.get("candidate_may_change_issue_status_or_relations") is not False
        ):
            fail("issue-state fail-closed boundary drift")
        gates = exact_dict(config.get("required_next_gates"), "required successor gates")
        if set(gates.values()) != {"PENDING"} or len(gates) != 5:
            fail("required successor gates drift")

        return (
            config["status"],
            config_digest,
            normal(config["semantic_sha256"], "candidate semantic field"),
            12,
            10,
            18,
            20,
            expected_target_codes,
            normal(
                exact_dict(authority["independent_review_receipt"], "independent review receipt")[
                    "source_body_sha256"
                ],
                "review hash",
            ),
            normal(
                exact_dict(
                    authority["owner_selection_009_decision_receipt"], "owner decision receipt"
                )["source_body_sha256"],
                "owner decision hash",
            ),
            leaf_count,
        )

    def make_result(state: tuple[Any, ...]) -> VerifiedNee204SuccessorFreezeCandidate:
        value = object_new(result_type)
        for slot, item in zip(result_type.__slots__, state, strict=True):
            object_setattr(value, slot, item)
        return value

    def state_from_result(value: object) -> tuple[Any, ...]:
        if type_builtin(value) is not result_type:
            fail("verified candidate result must have exact type")
        try:
            return tuple(object_getattribute(value, slot) for slot in result_type.__slots__)
        except AttributeError as exc:
            raise error_type("verified candidate result is incomplete") from exc

    def verify(root: Path) -> VerifiedNee204SuccessorFreezeCandidate:
        return make_result(verify_repository(root))

    def projection(state: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "status": tuple_getitem(state, 0),
            "config_sha256": tuple_getitem(state, 1),
            "semantic_sha256": tuple_getitem(state, 2),
            "active_blocker_count": tuple_getitem(state, 3),
            "proposed_active_blocker_count": tuple_getitem(state, 4),
            "historical_resolved_or_superseded_count": tuple_getitem(state, 5),
            "proposed_historical_resolved_or_superseded_count": tuple_getitem(state, 6),
            "proposed_removed_codes": list(tuple_getitem(state, 7)),
            "independent_review_body_sha256": tuple_getitem(state, 8),
            "owner_decision_body_sha256": tuple_getitem(state, 9),
            "verified_direct_authority_leaf_count": tuple_getitem(state, 10),
            "transition_performed": False,
            "freeze_v5_unchanged": True,
            "milestone_m0_complete": False,
            "production_ready": False,
            "live_order_authority": False,
        }

    def serialize(value: VerifiedNee204SuccessorFreezeCandidate, root: Path) -> Mapping[str, Any]:
        supplied = state_from_result(value)
        replayed = verify_repository(root)
        if supplied != replayed:
            fail("supplied verified result does not match fresh repository replay")
        return cast_value(  # type: ignore[no-any-return]
            Mapping[str, Any], mapping_proxy_type(projection(replayed))
        )

    def normalized_runtime_sha(root: Path) -> str:
        raw = read_bytes(runtime_path, root)
        replaced, count = runtime_pin_pattern.subn(
            rb"\g<1>00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000\g<2>",
            raw,
        )
        if count != 1:
            fail("runtime normalized self-pin marker count drift")
        return cast_value(str, hash_new(replaced).hexdigest())  # type: ignore[no-any-return]

    def verify_outer_manifest(root: Path) -> Mapping[str, str]:
        document, _ = load_json(manifest_path, root)
        if (
            document.get("schema_version") != "qme.hash_manifest.v1"
            or document.get("artifact_id") != "NEE-204-SELECTION-009-SUCCESSOR-FREEZE-CANDIDATE-V1"
            or document.get("status")
            != "CANDIDATE_UNREVIEWED_FREEZE_V5_UNCHANGED_PENDING_DELTA_REVIEW_EXACT_BYTE_OWNER_SIGNOFF_AND_RECEIPT"
        ):
            fail("candidate manifest identity or status drift")
        artifacts = exact_list(document.get("artifacts"), "candidate manifest artifacts")
        if len(artifacts) != 5:
            fail("candidate manifest must contain exactly five leaves")
        actual: dict[str, str] = {}
        for row in artifacts:
            manifest_row = exact_dict(row, "candidate manifest row")
            if tuple(manifest_row.keys()) != ("path", "sha256"):
                fail("candidate manifest row shape drift")
            path = exact_str(manifest_row["path"], "candidate manifest path")
            if path in actual:
                fail("candidate manifest path missing or duplicated")
            digest = hash_new(read_bytes(path, root)).hexdigest()
            if digest != normal(manifest_row["sha256"], f"{path} manifest hash"):
                fail(f"candidate manifest leaf mismatch: {path}")
            actual[path] = digest
        if tuple(actual) != (
            config_path,
            "docs/governance/NEE_204_SUCCESSOR_FREEZE_CANDIDATE_V1.md",
            runtime_path,
            schema_path,
            "tests/governance/test_nee204_successor_freeze_candidate.py",
        ):
            fail("candidate manifest path order drift")
        for path, expected in expected_owned_leaves.items():
            if actual.get(path) != normal(expected, f"{path} independently pinned hash"):
                fail(f"candidate manifest full-local-repin rejected: {path}")
        if normalized_runtime_sha(root) != normal(
            runtime_normalized_sha, "runtime normalized self hash"
        ):
            fail("candidate runtime normalized self hash mismatch")
        return cast_value(Mapping[str, str], mapping_proxy_type(actual))  # type: ignore[no-any-return]

    return verify, serialize, verify_outer_manifest


(
    verify_nee204_successor_freeze_candidate,
    serialize_verified_nee204_successor_freeze_candidate,
    verify_nee204_successor_freeze_candidate_manifest,
) = _build_trusted_api(
    candidate_claims=_EXPECTED_CANDIDATE_CLAIMS,
    cast_function=cast,
    config_path=_CONFIG_PATH,
    config_sha=_EXPECTED_CONFIG_SHA256,
    config_semantic_sha=_EXPECTED_CONFIG_SEMANTIC_SHA256,
    error_type=Nee204SuccessorFreezeCandidateError,
    expected_active_rows=_EXPECTED_ACTIVE_ROWS,
    expected_freeze_claims=_EXPECTED_FREEZE_CLAIMS,
    expected_lineage=_EXPECTED_LINEAGE,
    expected_nonclaims=_EXPECTED_NONCLAIMS,
    expected_owned_leaves=_EXPECTED_OWNED_NONRUNTIME_LEAVES,
    expected_resolved_codes=_EXPECTED_RESOLVED_CODES,
    expected_retained_codes=_EXPECTED_RETAINED_CODES,
    expected_target_codes=_EXPECTED_TARGET_CODES,
    format_checker_type=FormatChecker,
    hash_new=hashlib.sha256,
    json_dumps=json.dumps,
    json_error_type=json.JSONDecodeError,
    json_loads=json.loads,
    manifest_path=_MANIFEST_PATH,
    mapping_proxy_type=MappingProxyType,
    os_module=os,
    path_class=Path,
    re_module=re,
    result_type=VerifiedNee204SuccessorFreezeCandidate,
    runtime_path=_RUNTIME_PATH,
    runtime_normalized_sha=_EXPECTED_RUNTIME_NORMALIZED_SHA256,
    schema_path=_SCHEMA_PATH,
    schema_sha=_EXPECTED_SCHEMA_SHA256,
    stat_module=stat,
    type_builtin=type,
    validator_type=Draft202012Validator,
)
del _build_trusted_api
