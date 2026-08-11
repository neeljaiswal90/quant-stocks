"""Fail-closed assembly of the NEE-110 specification-freeze candidate."""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from qme.foundation import canonical_json_bytes

POLICY_SCHEMA_VERSION = "qme.specification_freeze_policy.v1"
EXPORT_SCHEMA_VERSION = "qme.specification_freeze_export.v1"
POLICY_ID = "NEE-110A-SPECIFICATION-FREEZE-CANDIDATE-V1"
CANONICALIZATION_ID = "qme.foundation.canonical_json.v1"
POLICY_DOCUMENT_SHA256 = "f11546e4f9f0b4d7066d4d68c002edbe26d1451e7ffd5540b2b37fb11be50719"
ARTIFACT_INDEX_SHA256 = "90cded90e5cb9b1b35461de3484d41e1ceaa6e6c654b8e4182c4201dfd60b44b"
DERIVED_EVIDENCE_SHA256 = "be290d1bfaa64d0d842261f13309aa62d61c51160534cb618fb7af9642d9ba49"
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024

_EXPECTED_ARTIFACT_SET_IDS = frozenset(
    {
        "NEE-116A-GOLDEN-FIXTURE",
        "NEE-118-ACCOUNTING",
        "NEE-119-QUANT-CONTRACT",
        "NEE-120-PROMOTION",
        "NEE-121-HOLDOUT",
        "NEE-122-EXPERIMENT-REGISTRY",
    }
)

_ARTIFACT_SET_CONTRACTS: dict[str, tuple[str, str, int, str]] = {
    "NEE-116A-GOLDEN-FIXTURE": (
        "NEE-116",
        "tests/fixtures/quant/golden-two-rebalance-v1.manifest.json",
        8,
        "2e1e880f6f7ef244d82fe3dd2baa30dfe464fb6e7545bd18ca55ab2a9e2d143c",
    ),
    "NEE-118-ACCOUNTING": (
        "NEE-118",
        "tests/fixtures/quant/accounting-equations-v1.manifest.json",
        6,
        "d2f042da9ecf1f28d019af1a8ae0d18d56af018175dc3bafa3699df55025f0ee",
    ),
    "NEE-119-QUANT-CONTRACT": (
        "NEE-119",
        "configs/quant/qme-v0.1-contract.hashes.json",
        5,
        "84970ccb3cf8d1d0a63efae90f3a32e07ce973b4f37ef000a1702980188e6be3",
    ),
    "NEE-120-PROMOTION": (
        "NEE-120",
        "tests/fixtures/quant/economic-promotion-decision-v1.manifest.json",
        7,
        "e087cd29f1bbe6f7d066c32631fe6f6586da84c0eb7b33045cec7162d7c4a36a",
    ),
    "NEE-121-HOLDOUT": (
        "NEE-121",
        "configs/governance/sample-holdout-v1.hashes.json",
        9,
        "e28864a8067f8e233ea880f0082299916d986ed68614a6782616aa93f96f91a2",
    ),
    "NEE-122-EXPERIMENT-REGISTRY": (
        "NEE-122",
        "configs/governance/experiment-registry-v1.hashes.json",
        16,
        "aebdca44dc207f9d87b5349b0c9fe19af5ac77abfcfdc53d0662a9a198dd728f",
    ),
}

_EXPECTED_BLOCKER_CODES = frozenset(
    {
        "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL",
        "NEE-110-QME-CONFIG-V1-CONTRACT",
        "NEE-116-ASYMMETRIC-COST-METHOD",
        "NEE-116-CAPACITY-SOLVER",
        "NEE-116-CORPORATE-ACTION-EDGE-CASES",
        "NEE-116-HUMAN-INDEPENDENT-REVIEW",
        "NEE-116-OFFICIAL-OPEN-FALLBACK",
        "NEE-116-PRODUCTION-PIT-DATA",
        "NEE-116-TAX-LOT-METHOD",
        "NEE-117-EXACT-SHA-REMOTE-CI",
        "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP",
        "NEE-119-AV-PROXY-EVIDENCE",
        "NEE-119-MINIMUM-ELIGIBLE-BREADTH",
        "NEE-119-PRODUCTION-SOURCE-FRESHNESS",
        "NEE-120-AUM-CAPACITY-LIMITS",
        "NEE-120-INFERENCE-POLICY",
        "NEE-120-PROMOTION-MANDATE",
        "NEE-120-PREREGISTRATION-APPROVAL",
        "NEE-121-CALENDAR-SESSION-REGISTRATION",
        "NEE-121-HISTORICAL-ACCESS-PROVENANCE",
        "NEE-121-LABEL-ENDPOINT-REGISTRATIONS",
        "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP",
        "NEE-121-PROSPECTIVE-EVIDENCE-SUFFICIENCY",
        "NEE-122-CORRELATED-TRIAL-FIXTURE",
        "NEE-122-DEPENDENCE-ESTIMATOR",
        "NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION",
        "NEE-122-PRODUCTION-FAMILY-POLICY",
    }
)

_CLAIM_RULES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "LOCAL_QUANT_MECHANICS",
        "SUPPORTED_BOUNDED",
        ("NEE-118-ACCOUNTING", "NEE-119-QUANT-CONTRACT"),
        ("LOCAL_AND_SYNTHETIC_ONLY",),
    ),
    (
        "SYNTHETIC_ARITHMETIC_CONFORMANCE",
        "SUPPORTED_BOUNDED",
        ("NEE-116A-GOLDEN-FIXTURE", "NEE-118-ACCOUNTING"),
        ("NOT_EMPIRICAL_EVIDENCE",),
    ),
    (
        "AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY",
        "BLOCKED",
        (),
        ("NEE-119-AV-PROXY-EVIDENCE",),
    ),
    (
        "PRODUCTION_SPECIFICATION_ACCEPTED",
        "BLOCKED",
        (),
        tuple(sorted(_EXPECTED_BLOCKER_CODES)),
    ),
    (
        "AUTHORITATIVE_POINT_IN_TIME_NDX_MEMBERSHIP",
        "BLOCKED",
        (),
        ("NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP",),
    ),
    (
        "PRODUCTION_DATA_COVERAGE_AND_FRESHNESS",
        "BLOCKED",
        (),
        (
            "NEE-116-PRODUCTION-PIT-DATA",
            "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP",
            "NEE-119-MINIMUM-ELIGIBLE-BREADTH",
            "NEE-119-PRODUCTION-SOURCE-FRESHNESS",
            "NEE-121-CALENDAR-SESSION-REGISTRATION",
        ),
    ),
    ("EMPIRICAL_PERFORMANCE", "FORBIDDEN", (), ("NO_EMPIRICAL_INPUTS",)),
    (
        "PROSPECTIVE_HOLDOUT",
        "FORBIDDEN",
        (),
        (
            "NEE-121-CALENDAR-SESSION-REGISTRATION",
            "NEE-121-HISTORICAL-ACCESS-PROVENANCE",
            "NEE-121-LABEL-ENDPOINT-REGISTRATIONS",
            "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP",
            "NEE-121-PROSPECTIVE-EVIDENCE-SUFFICIENCY",
        ),
    ),
    (
        "EFFECTIVE_TRIALS_N_EFF",
        "FORBIDDEN",
        (),
        (
            "NEE-122-DEPENDENCE-ESTIMATOR",
            "NEE-122-CORRELATED-TRIAL-FIXTURE",
            "NEE-122-PRODUCTION-FAMILY-POLICY",
            "NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION",
        ),
    ),
    (
        "DEFLATED_SHARPE_RATIO",
        "FORBIDDEN",
        (),
        (
            "NEE-120-INFERENCE-POLICY",
            "NEE-122-DEPENDENCE-ESTIMATOR",
            "NEE-122-CORRELATED-TRIAL-FIXTURE",
            "NEE-122-PRODUCTION-FAMILY-POLICY",
            "NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION",
        ),
    ),
    (
        "PORTFOLIO_CAPACITY",
        "FORBIDDEN",
        (),
        ("NEE-116-CAPACITY-SOLVER", "NEE-120-AUM-CAPACITY-LIMITS"),
    ),
    ("EXACT_SHA_REMOTE_CI", "BLOCKED", (), ("NEE-117-EXACT-SHA-REMOTE-CI",)),
    (
        "NASDAQ_100_READY",
        "BLOCKED",
        (),
        (
            "NEE-110-QME-CONFIG-V1-CONTRACT",
            "NEE-116-PRODUCTION-PIT-DATA",
            "NEE-117-EXACT-SHA-REMOTE-CI",
            "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP",
            "NEE-119-MINIMUM-ELIGIBLE-BREADTH",
            "NEE-119-PRODUCTION-SOURCE-FRESHNESS",
            "NEE-121-CALENDAR-SESSION-REGISTRATION",
        ),
    ),
    (
        "DATA_SPINE_START_AUTHORIZED",
        "BLOCKED",
        (),
        tuple(sorted(_EXPECTED_BLOCKER_CODES)),
    ),
)

_EFFECTIVE_TRIALS_FIELDS = (
    "semantic_target",
    "estimator_version_hash",
    "family_and_m_binding",
    "return_matrix_artifact_hash",
    "return_frequency_and_benchmark_basis",
    "common_calendar_and_missingness_policy",
    "null_centering_and_standardization",
    "dependence_or_resampling_method",
    "block_rule_seed_and_replicates",
    "psd_regularization_and_solver_bounds",
    "negative_correlation_and_single_trial_rules",
    "monte_carlo_interval_and_tolerance",
    "independent_fixture_hash",
    "sharpe_estimator_and_annualization",
    "cross_trial_sharpe_variance_estimator",
    "eligible_trial_inclusion_rule",
    "sample_length_convention",
    "skewness_and_kurtosis_convention",
    "selected_trial_mapping",
    "single_trial_dsr_benchmark",
)

_BLOCKED_DOWNSTREAM_ISSUES = (
    "NEE-114",
    "NEE-123",
    "NEE-124",
    "NEE-125",
    "NEE-126",
    "NEE-127",
    "NEE-128",
)


class SpecificationFreezeError(ValueError):
    """Raised when freeze inputs cannot be verified without assumptions."""


_EXPORT_CONSTRUCTION_TOKEN = object()


class SpecificationFreezeExport(Mapping[str, object]):
    """Immutable validated export; callers receive copies, never the sealed state."""

    _bytes: bytes
    _document: dict[str, object]
    _sealed: bool
    _sha256: str
    __slots__ = ("_bytes", "_document", "_sealed", "_sha256")

    def __init__(self, document: Mapping[str, object], token: object) -> None:
        if token is not _EXPORT_CONSTRUCTION_TOKEN:
            raise SpecificationFreezeError("specification-freeze exports must be builder-created")
        copied = deepcopy(dict(document))
        _validate_export_invariants(copied)
        payload = canonical_json_bytes(copied)
        object.__setattr__(self, "_document", copied)
        object.__setattr__(self, "_bytes", payload)
        object.__setattr__(self, "_sha256", hashlib.sha256(payload).hexdigest())
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("specification-freeze export is immutable")
        object.__setattr__(self, name, value)

    def _verified_snapshot(self) -> tuple[bytes, str]:
        document = deepcopy(self._document)
        _validate_export_invariants(document)
        payload = canonical_json_bytes(document)
        digest = hashlib.sha256(payload).hexdigest()
        if payload != self._bytes or digest != self._sha256:
            raise SpecificationFreezeError("sealed export storage differs from validated content")
        return payload, digest

    def __getitem__(self, key: str) -> object:
        return deepcopy(self._document[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._document)

    def __len__(self) -> int:
        return len(self._document)

    def to_document(self) -> dict[str, object]:
        return deepcopy(self._document)

    @property
    def canonical_bytes(self) -> bytes:
        return self._verified_snapshot()[0]

    @property
    def sha256(self) -> str:
        return self._verified_snapshot()[1]


def _require_exact_keys(document: Mapping[str, object], expected: set[str], label: str) -> None:
    observed = set(document)
    if observed != expected:
        raise SpecificationFreezeError(
            f"{label} fields differ: missing={sorted(expected - observed)!r}, "
            f"extra={sorted(observed - expected)!r}"
        )


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise SpecificationFreezeError(f"{label} must be one non-blank canonical string")
    if unicodedata.normalize("NFC", value) != value:
        raise SpecificationFreezeError(f"{label} must use NFC Unicode")
    return value


def _required_sha256(value: object, label: str) -> str:
    digest = _required_string(value, label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise SpecificationFreezeError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _required_positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SpecificationFreezeError(f"{label} must be a positive integer")
    return value


def _as_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise SpecificationFreezeError(f"{label} must be one JSON object")
    return cast(dict[str, object], value)


def _as_array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise SpecificationFreezeError(f"{label} must be one JSON array")
    return cast(list[object], value)


def _strict_json_object(payload: bytes, label: str) -> dict[str, object]:
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise SpecificationFreezeError(f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise SpecificationFreezeError(f"{label} contains non-finite number {value}")

    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpecificationFreezeError(f"{label} is not strict UTF-8 JSON") from exc
    return _as_object(decoded, label)


def _read_bounded(path: Path, label: str) -> bytes:
    size = path.stat().st_size
    if size > MAX_ARTIFACT_BYTES:
        raise SpecificationFreezeError(f"{label} exceeds {MAX_ARTIFACT_BYTES} bytes")
    with path.open("rb") as handle:
        payload = handle.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) != size or len(payload) > MAX_ARTIFACT_BYTES:
        raise SpecificationFreezeError(f"{label} changed while being read or is oversized")
    return payload


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & 0x400)


def _safe_repository_file(root: Path, raw_path: object, label: str) -> tuple[str, Path]:
    normalized = _required_string(raw_path, label)
    if "\\" in normalized or ":" in normalized:
        raise SpecificationFreezeError(f"{label} must be a portable repository-relative path")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or pure.as_posix() != normalized or any(
        part in {"", ".", ".."} for part in pure.parts
    ):
        raise SpecificationFreezeError(f"{label} must be a normalized repository-relative path")
    resolved_root = root.resolve(strict=True)
    candidate = root.joinpath(*pure.parts)
    if not candidate.exists():
        raise SpecificationFreezeError(f"{label} is missing: {normalized}")
    current = resolved_root
    for part in pure.parts:
        current = current / part
        if _is_reparse(current):
            raise SpecificationFreezeError(f"{label} crosses a reparse or symbolic-link component")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise SpecificationFreezeError(f"{label} escapes the repository root") from exc
    if not resolved.is_file():
        raise SpecificationFreezeError(f"{label} is not a regular file")
    return normalized, resolved


def _register_path_identity(registry: dict[str, str], path: str, label: str) -> None:
    folded = unicodedata.normalize("NFC", path).casefold()
    prior = registry.get(folded)
    if prior is not None:
        raise SpecificationFreezeError(f"{label} path collision: {prior!r} and {path!r}")
    registry[folded] = path


@dataclass(frozen=True)
class ArtifactSetPolicy:
    artifact_set_id: str
    ticket_id: str
    manifest_path: str
    manifest_sha256: str
    required_leaf_count: int
    evidence_state: str

    @classmethod
    def from_document(cls, value: object, index: int) -> ArtifactSetPolicy:
        document = _as_object(value, f"artifact_sets[{index}]")
        _require_exact_keys(
            document,
            {
                "artifact_set_id",
                "ticket_id",
                "manifest_path",
                "manifest_sha256",
                "required_leaf_count",
                "evidence_state",
            },
            f"artifact_sets[{index}]",
        )
        evidence_state = _required_string(
            document["evidence_state"], f"artifact_sets[{index}].evidence_state"
        )
        if evidence_state != "COMMITTED_UNVERIFIED":
            raise SpecificationFreezeError(
                "policy v1 accepts only COMMITTED_UNVERIFIED source evidence"
            )
        return cls(
            artifact_set_id=_required_string(
                document["artifact_set_id"], f"artifact_sets[{index}].artifact_set_id"
            ),
            ticket_id=_required_string(
                document["ticket_id"], f"artifact_sets[{index}].ticket_id"
            ),
            manifest_path=_required_string(
                document["manifest_path"], f"artifact_sets[{index}].manifest_path"
            ),
            manifest_sha256=_required_sha256(
                document["manifest_sha256"], f"artifact_sets[{index}].manifest_sha256"
            ),
            required_leaf_count=_required_positive_int(
                document["required_leaf_count"],
                f"artifact_sets[{index}].required_leaf_count",
            ),
            evidence_state=evidence_state,
        )

    def to_document(self) -> dict[str, object]:
        return {
            "artifact_set_id": self.artifact_set_id,
            "ticket_id": self.ticket_id,
            "manifest_path": self.manifest_path,
            "manifest_sha256": self.manifest_sha256,
            "required_leaf_count": self.required_leaf_count,
            "evidence_state": self.evidence_state,
        }


@dataclass(frozen=True)
class UnresolvedBlocker:
    blocker_code: str
    ticket_id: str
    category: str
    description: str

    @classmethod
    def from_document(cls, value: object, index: int) -> UnresolvedBlocker:
        document = _as_object(value, f"unresolved_blockers[{index}]")
        _require_exact_keys(
            document,
            {"blocker_code", "ticket_id", "category", "description"},
            f"unresolved_blockers[{index}]",
        )
        return cls(
            blocker_code=_required_string(
                document["blocker_code"], f"unresolved_blockers[{index}].blocker_code"
            ),
            ticket_id=_required_string(
                document["ticket_id"], f"unresolved_blockers[{index}].ticket_id"
            ),
            category=_required_string(
                document["category"], f"unresolved_blockers[{index}].category"
            ),
            description=_required_string(
                document["description"], f"unresolved_blockers[{index}].description"
            ),
        )

    def to_document(self) -> dict[str, object]:
        return {
            "blocker_code": self.blocker_code,
            "ticket_id": self.ticket_id,
            "category": self.category,
            "description": self.description,
            "status": "UNRESOLVED_BLOCKER",
        }


@dataclass(frozen=True)
class FreezePolicy:
    artifact_sets: tuple[ArtifactSetPolicy, ...]
    unresolved_blockers: tuple[UnresolvedBlocker, ...]

    @classmethod
    def from_document(cls, value: Mapping[str, object]) -> FreezePolicy:
        document = dict(value)
        _require_exact_keys(
            document,
            {
                "schema_version",
                "policy_id",
                "ticket_id",
                "canonicalization",
                "artifact_sets",
                "unresolved_blockers",
            },
            "policy",
        )
        if document["schema_version"] != POLICY_SCHEMA_VERSION:
            raise SpecificationFreezeError("unsupported specification-freeze policy schema")
        if document["policy_id"] != POLICY_ID or document["ticket_id"] != "NEE-110":
            raise SpecificationFreezeError("policy identity differs from the bounded NEE-110A contract")
        if document["canonicalization"] != CANONICALIZATION_ID:
            raise SpecificationFreezeError("unsupported canonicalization identity")
        artifact_sets = tuple(
            ArtifactSetPolicy.from_document(item, index)
            for index, item in enumerate(_as_array(document["artifact_sets"], "artifact_sets"))
        )
        blockers = tuple(
            UnresolvedBlocker.from_document(item, index)
            for index, item in enumerate(
                _as_array(document["unresolved_blockers"], "unresolved_blockers")
            )
        )
        artifact_ids = [item.artifact_set_id for item in artifact_sets]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise SpecificationFreezeError("artifact_set_id must be unique")
        if frozenset(artifact_ids) != _EXPECTED_ARTIFACT_SET_IDS:
            raise SpecificationFreezeError("policy must contain the exact bounded M0 artifact sets")
        for item in artifact_sets:
            expected_ticket, expected_path, expected_count, expected_digest = _ARTIFACT_SET_CONTRACTS[
                item.artifact_set_id
            ]
            if (
                item.ticket_id,
                item.manifest_path,
                item.required_leaf_count,
                item.manifest_sha256,
            ) != (
                expected_ticket,
                expected_path,
                expected_count,
                expected_digest,
            ):
                raise SpecificationFreezeError(
                    f"{item.artifact_set_id} identity differs from the bounded source contract"
                )
        blocker_codes = [item.blocker_code for item in blockers]
        if len(blocker_codes) != len(set(blocker_codes)):
            raise SpecificationFreezeError("blocker_code must be unique")
        if frozenset(blocker_codes) != _EXPECTED_BLOCKER_CODES:
            raise SpecificationFreezeError("policy must retain every registered unresolved blocker")
        policy = cls(
            artifact_sets=tuple(sorted(artifact_sets, key=lambda item: item.artifact_set_id)),
            unresolved_blockers=tuple(sorted(blockers, key=lambda item: item.blocker_code)),
        )
        if hashlib.sha256(canonical_json_bytes(policy.to_document())).hexdigest() != (
            POLICY_DOCUMENT_SHA256
        ):
            raise SpecificationFreezeError(
                "policy semantics differ from the immutable NEE-110A v1 registration"
            )
        return policy

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "policy_id": POLICY_ID,
            "ticket_id": "NEE-110",
            "canonicalization": CANONICALIZATION_ID,
            "artifact_sets": [item.to_document() for item in self.artifact_sets],
            "unresolved_blockers": [
                {
                    "blocker_code": item.blocker_code,
                    "ticket_id": item.ticket_id,
                    "category": item.category,
                    "description": item.description,
                }
                for item in self.unresolved_blockers
            ],
        }


def load_freeze_policy(path: Path) -> FreezePolicy:
    return FreezePolicy.from_document(_strict_json_object(_read_bounded(path, "policy"), "policy"))


def _manifest_artifacts(
    document: Mapping[str, object], manifest_path: str
) -> list[tuple[str, str]]:
    raw_artifacts = document.get("artifacts")
    entries: list[tuple[str, str]] = []
    if isinstance(raw_artifacts, dict):
        for raw_path, raw_digest in raw_artifacts.items():
            if not isinstance(raw_path, str):
                raise SpecificationFreezeError(f"{manifest_path} has a non-string artifact path")
            entries.append((raw_path, _required_sha256(raw_digest, f"{manifest_path}:{raw_path}")))
    elif isinstance(raw_artifacts, list):
        for index, raw_entry in enumerate(raw_artifacts):
            entry = _as_object(raw_entry, f"{manifest_path}.artifacts[{index}]")
            _require_exact_keys(entry, {"path", "sha256"}, f"{manifest_path}.artifacts[{index}]")
            entries.append(
                (
                    _required_string(entry["path"], f"{manifest_path}.artifacts[{index}].path"),
                    _required_sha256(
                        entry["sha256"], f"{manifest_path}.artifacts[{index}].sha256"
                    ),
                )
            )
    else:
        raise SpecificationFreezeError(f"{manifest_path} has no supported artifact index")
    if not entries:
        raise SpecificationFreezeError(f"{manifest_path} has an empty artifact index")
    return entries


def _ci_result(ci_evidence: Mapping[str, object] | None, repository_commit: str) -> dict[str, object]:
    if ci_evidence is None:
        return {"status": "MISSING", "tested_commit": None, "required_checks_passed": False}
    document = dict(ci_evidence)
    _require_exact_keys(
        document,
        {"tested_commit", "workflow", "run_url", "required_checks_passed", "artifact_sha256"},
        "ci_evidence",
    )
    tested_commit = _required_string(document["tested_commit"], "ci_evidence.tested_commit")
    workflow = _required_string(document["workflow"], "ci_evidence.workflow")
    run_url = _required_string(document["run_url"], "ci_evidence.run_url")
    artifact_sha256 = _required_sha256(
        document["artifact_sha256"], "ci_evidence.artifact_sha256"
    )
    checks = document["required_checks_passed"]
    if not isinstance(checks, bool):
        raise SpecificationFreezeError("ci_evidence.required_checks_passed must be boolean")
    if tested_commit != repository_commit:
        status = "SHA_MISMATCH"
    elif not checks:
        status = "FAILED_REQUIRED_CHECKS"
    else:
        status = "CALLER_ASSERTED_UNVERIFIED"
    return {
        "status": status,
        "tested_commit": tested_commit,
        "workflow": workflow,
        "run_url": run_url,
        "required_checks_passed": checks,
        "artifact_sha256": artifact_sha256,
    }


def build_specification_freeze(
    *,
    policy: FreezePolicy,
    repository_root: Path,
    repository_commit: str,
    dirty_worktree: bool,
    ci_evidence: Mapping[str, object] | None = None,
) -> SpecificationFreezeExport:
    """Verify reviewed M0 artifacts and emit a candidate that cannot authorize closure."""

    if hashlib.sha256(canonical_json_bytes(policy.to_document())).hexdigest() != (
        POLICY_DOCUMENT_SHA256
    ):
        raise SpecificationFreezeError(
            "policy object differs from the immutable NEE-110A v1 registration"
        )
    commit = _required_string(repository_commit, "repository_commit")
    if commit != "UNCOMMITTED" and (
        len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit)
    ):
        raise SpecificationFreezeError("repository_commit must be UNCOMMITTED or a full lowercase SHA")
    if not isinstance(dirty_worktree, bool):
        raise SpecificationFreezeError("dirty_worktree must be boolean")

    root = repository_root.resolve(strict=True)
    observed_manifest_paths: dict[str, str] = {}
    global_artifacts: dict[str, dict[str, object]] = {}
    artifact_set_results: list[dict[str, object]] = []
    total_leaf_references = 0

    for source in policy.artifact_sets:
        canonical_manifest_path, manifest_file = _safe_repository_file(
            root, source.manifest_path, f"{source.artifact_set_id}.manifest_path"
        )
        _register_path_identity(
            observed_manifest_paths, canonical_manifest_path, "master manifest"
        )
        manifest_bytes = _read_bounded(manifest_file, canonical_manifest_path)
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        if manifest_digest != source.manifest_sha256:
            raise SpecificationFreezeError(
                f"master manifest hash mismatch for {canonical_manifest_path}"
            )
        manifest_document = _strict_json_object(manifest_bytes, canonical_manifest_path)
        entries = _manifest_artifacts(manifest_document, canonical_manifest_path)
        if len(entries) != source.required_leaf_count:
            raise SpecificationFreezeError(
                f"{canonical_manifest_path} leaf count differs from the reviewed policy"
            )
        total_leaf_references += len(entries)
        observed_leaf_paths: dict[str, str] = {}
        for raw_leaf_path, expected_digest in entries:
            leaf_path, leaf_file = _safe_repository_file(
                root, raw_leaf_path, f"{canonical_manifest_path}.artifact"
            )
            if leaf_path == canonical_manifest_path:
                raise SpecificationFreezeError("a master manifest may not hash itself")
            _register_path_identity(
                observed_leaf_paths, leaf_path, f"leaf in {canonical_manifest_path}"
            )
            folded_leaf = unicodedata.normalize("NFC", leaf_path).casefold()
            leaf_bytes = _read_bounded(leaf_file, leaf_path)
            observed_digest = hashlib.sha256(leaf_bytes).hexdigest()
            if observed_digest != expected_digest:
                raise SpecificationFreezeError(
                    f"nested artifact hash mismatch for {leaf_path} in {canonical_manifest_path}"
                )
            global_key = folded_leaf
            existing = global_artifacts.get(global_key)
            if existing is None:
                global_artifacts[global_key] = {
                    "path": leaf_path,
                    "sha256": observed_digest,
                    "size_bytes": len(leaf_bytes),
                    "artifact_set_ids": [source.artifact_set_id],
                    "integrity_state": "HASH_VERIFIED",
                    "schema_state": "NOT_EVALUATED_BOUNDED_SLICE",
                    "semantic_state": "DELEGATED_TO_CHILD_CONTRACT",
                }
            else:
                if existing["path"] != leaf_path or existing["sha256"] != observed_digest:
                    raise SpecificationFreezeError(
                        f"cross-manifest path identity conflict for {leaf_path}"
                    )
                cast(list[str], existing["artifact_set_ids"]).append(source.artifact_set_id)

        embedded_status = manifest_document.get(
            "status", manifest_document.get("implementation_status", "UNSPECIFIED")
        )
        if embedded_status is not None and not isinstance(embedded_status, str):
            raise SpecificationFreezeError(
                f"{canonical_manifest_path} has a non-scalar embedded status"
            )
        artifact_set_results.append(
            {
                "artifact_set_id": source.artifact_set_id,
                "ticket_id": source.ticket_id,
                "manifest_path": canonical_manifest_path,
                "manifest_sha256": manifest_digest,
                "leaf_count": len(entries),
                "integrity_state": "HASH_VERIFIED",
                "evidence_state": source.evidence_state,
                "embedded_status": embedded_status,
            }
        )

    artifacts = sorted(global_artifacts.values(), key=lambda item: cast(str, item["path"]))
    for item in artifacts:
        item["artifact_set_ids"] = sorted(cast(list[str], item["artifact_set_ids"]))

    blockers = [item.to_document() for item in policy.unresolved_blockers]
    blocker_codes = {cast(str, item["blocker_code"]) for item in blockers}
    claims: list[dict[str, object]] = []
    for claim_id, status, evidence_refs, conditions in _CLAIM_RULES:
        del evidence_refs
        unresolved_dependencies = [
            code for code in conditions if code.startswith("NEE-") and code not in blocker_codes
        ]
        if unresolved_dependencies:
            raise SpecificationFreezeError(
                f"claim {claim_id} references unregistered blockers {unresolved_dependencies!r}"
            )
        claims.append({"claim_id": claim_id, "status": status})

    ci_result = _ci_result(ci_evidence, commit)
    if dirty_worktree or commit == "UNCOMMITTED":
        maturity = "LOCAL_UNCOMMITTED"
    else:
        maturity = "COMMITTED_UNVERIFIED"

    policy_document = policy.to_document()
    export: dict[str, object] = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "freeze_id": POLICY_ID,
        "ticket_id": "NEE-110",
        "policy_identity": {
            "policy_id": POLICY_ID,
            "policy_sha256": hashlib.sha256(canonical_json_bytes(policy_document)).hexdigest(),
        },
        "repository_evidence": {
            "commit_sha": commit,
            "dirty_worktree": dirty_worktree,
            "evidence_maturity": maturity,
        },
        "artifact_sets": sorted(
            artifact_set_results, key=lambda item: cast(str, item["artifact_set_id"])
        ),
        "artifact_index": {
            "sha256": hashlib.sha256(
                canonical_json_bytes({"artifacts": artifacts})
            ).hexdigest(),
            "unique_artifact_count": len(artifacts),
            "reference_count": total_leaf_references,
        },
        "artifact_reference_count": total_leaf_references,
        "cross_contract_checks": [
            {
                "check_id": "ALL_REGISTERED_CHILD_BYTES",
                "status": "PASS",
            },
            {
                "check_id": "FULL_CROSS_CONTRACT_SEMANTIC_APPROVAL",
                "status": "BLOCKED",
            },
            {
                "check_id": "STRICT_OPERATIONAL_CONFIG_V1",
                "status": "BLOCKED",
            },
        ],
        "unresolved_blocker_codes": sorted(blocker_codes),
        "claims": sorted(claims, key=lambda item: cast(str, item["claim_id"])),
        "ci_evidence": ci_result,
        "effective_trials_registration": {
            "status": "UNREGISTERED_BLOCKER",
            "estimate": None,
            "estimator": None,
            "required_registration_fields": list(_EFFECTIVE_TRIALS_FIELDS),
        },
        "closure": {
            "integrity_state": "HASH_VERIFIED",
            "overall_state": "BLOCKED_UNRESOLVED_INPUTS",
            "accepted": False,
            "downstream_start_authorized": False,
            "blocked_issue_ids": list(_BLOCKED_DOWNSTREAM_ISSUES),
        },
        "canonicalization": CANONICALIZATION_ID,
    }
    export["derived_evidence_sha256"] = _derived_evidence_sha256(export)
    _validate_export_invariants(export)
    return SpecificationFreezeExport(export, _EXPORT_CONSTRUCTION_TOKEN)


def _derived_evidence_sha256(document: Mapping[str, object]) -> str:
    keys = (
        "artifact_sets",
        "artifact_index",
        "artifact_reference_count",
        "cross_contract_checks",
        "unresolved_blocker_codes",
        "claims",
        "effective_trials_registration",
        "closure",
    )
    try:
        derived = {key: document[key] for key in keys}
    except KeyError as exc:
        raise SpecificationFreezeError(f"export lacks derived evidence section {exc.args[0]}") from exc
    return hashlib.sha256(canonical_json_bytes(derived)).hexdigest()


def _validate_export_invariants(document: Mapping[str, object]) -> None:
    _require_exact_keys(
        document,
        {
            "schema_version",
            "freeze_id",
            "ticket_id",
            "policy_identity",
            "repository_evidence",
            "artifact_sets",
            "artifact_index",
            "artifact_reference_count",
            "cross_contract_checks",
            "unresolved_blocker_codes",
            "claims",
            "ci_evidence",
            "effective_trials_registration",
            "closure",
            "canonicalization",
            "derived_evidence_sha256",
        },
        "specification_freeze_export",
    )
    if (
        document["schema_version"] != EXPORT_SCHEMA_VERSION
        or document["freeze_id"] != POLICY_ID
        or document["ticket_id"] != "NEE-110"
        or document["canonicalization"] != CANONICALIZATION_ID
    ):
        raise SpecificationFreezeError("export identity differs from the bounded NEE-110A contract")
    policy_identity = _as_object(document["policy_identity"], "policy_identity")
    _require_exact_keys(policy_identity, {"policy_id", "policy_sha256"}, "policy_identity")
    if policy_identity != {"policy_id": POLICY_ID, "policy_sha256": POLICY_DOCUMENT_SHA256}:
        raise SpecificationFreezeError("export policy identity is not the immutable v1 registration")
    if document["artifact_reference_count"] != 51:
        raise SpecificationFreezeError("export artifact reference count differs from the reviewed set")
    artifact_index = _as_object(document["artifact_index"], "artifact_index")
    if artifact_index != {
        "sha256": ARTIFACT_INDEX_SHA256,
        "unique_artifact_count": 51,
        "reference_count": 51,
    }:
        raise SpecificationFreezeError("export leaf artifact index differs from policy v1")
    observed_derived = _required_sha256(
        document["derived_evidence_sha256"], "derived_evidence_sha256"
    )
    if observed_derived != DERIVED_EVIDENCE_SHA256 or _derived_evidence_sha256(
        document
    ) != DERIVED_EVIDENCE_SHA256:
        raise SpecificationFreezeError("export derived evidence index differs from policy v1")

    artifact_sets = [
        _as_object(item, f"artifact_sets[{index}]")
        for index, item in enumerate(_as_array(document["artifact_sets"], "artifact_sets"))
    ]
    artifact_ids = [item.get("artifact_set_id") for item in artifact_sets]
    if artifact_ids != sorted(_EXPECTED_ARTIFACT_SET_IDS):
        raise SpecificationFreezeError("export artifact-set identities differ from policy v1")
    for item in artifact_sets:
        if item.get("integrity_state") != "HASH_VERIFIED" or item.get(
            "evidence_state"
        ) != "COMMITTED_UNVERIFIED":
            raise SpecificationFreezeError("export artifact-set state was promoted")

    blocker_codes = _as_array(document["unresolved_blocker_codes"], "unresolved_blocker_codes")
    if blocker_codes != sorted(_EXPECTED_BLOCKER_CODES):
        raise SpecificationFreezeError("export unresolved blockers differ from policy v1")

    expected_claim_status = {claim_id: status for claim_id, status, _, _ in _CLAIM_RULES}
    claims = [
        _as_object(item, f"claims[{index}]")
        for index, item in enumerate(_as_array(document["claims"], "claims"))
    ]
    observed_claim_status = {
        cast(str, item.get("claim_id")): item.get("status") for item in claims
    }
    if observed_claim_status != expected_claim_status or len(claims) != len(_CLAIM_RULES):
        raise SpecificationFreezeError("export claim status was changed or duplicated")

    checks = [
        _as_object(item, f"cross_contract_checks[{index}]")
        for index, item in enumerate(
            _as_array(document["cross_contract_checks"], "cross_contract_checks")
        )
    ]
    if [(item.get("check_id"), item.get("status")) for item in checks] != [
        ("ALL_REGISTERED_CHILD_BYTES", "PASS"),
        ("FULL_CROSS_CONTRACT_SEMANTIC_APPROVAL", "BLOCKED"),
        ("STRICT_OPERATIONAL_CONFIG_V1", "BLOCKED"),
    ]:
        raise SpecificationFreezeError("export cross-contract checks differ from policy v1")

    effective = _as_object(
        document["effective_trials_registration"], "effective_trials_registration"
    )
    if (
        effective.get("status") != "UNREGISTERED_BLOCKER"
        or effective.get("estimate") is not None
        or effective.get("estimator") is not None
        or effective.get("required_registration_fields") != list(_EFFECTIVE_TRIALS_FIELDS)
    ):
        raise SpecificationFreezeError("effective-trials registration was promoted or changed")

    closure = _as_object(document["closure"], "closure")
    expected_closure: dict[str, object] = {
        "integrity_state": "HASH_VERIFIED",
        "overall_state": "BLOCKED_UNRESOLVED_INPUTS",
        "accepted": False,
        "downstream_start_authorized": False,
        "blocked_issue_ids": list(_BLOCKED_DOWNSTREAM_ISSUES),
    }
    if closure != expected_closure:
        raise SpecificationFreezeError("export closure was promoted or changed")

    repository = _as_object(document["repository_evidence"], "repository_evidence")
    expected_maturity = (
        "LOCAL_UNCOMMITTED"
        if repository.get("dirty_worktree") is True or repository.get("commit_sha") == "UNCOMMITTED"
        else "COMMITTED_UNVERIFIED"
    )
    if repository.get("evidence_maturity") != expected_maturity:
        raise SpecificationFreezeError("repository evidence maturity exceeds the v1 trust boundary")

    ci = _as_object(document["ci_evidence"], "ci_evidence")
    if ci.get("status") not in {
        "MISSING",
        "SHA_MISMATCH",
        "FAILED_REQUIRED_CHECKS",
        "CALLER_ASSERTED_UNVERIFIED",
    }:
        raise SpecificationFreezeError("CI evidence exceeds the v1 caller-asserted trust boundary")


def specification_freeze_bytes(document: SpecificationFreezeExport) -> bytes:
    if type(document) is not SpecificationFreezeExport:
        raise TypeError("specification_freeze_bytes requires a builder-created sealed export")
    return document.canonical_bytes


def specification_freeze_sha256(document: SpecificationFreezeExport) -> str:
    if type(document) is not SpecificationFreezeExport:
        raise TypeError("specification_freeze_sha256 requires a builder-created sealed export")
    return document.sha256


def is_windows() -> bool:
    """Small injectable boundary used only by path-safety tests."""

    return os.name == "nt"
