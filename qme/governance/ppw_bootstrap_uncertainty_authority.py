"""Fail-closed verifier for the NEE-204 PPW source-equation authority packet."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Final, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped, unused-ignore]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped, unused-ignore]

CONFIG_PATH: Final = Path("configs/governance/ppw-bootstrap-uncertainty-authority-v1.json")
SCHEMA_PATH: Final = Path("schemas/governance/ppw-bootstrap-uncertainty-authority-v1.schema.json")
FIXTURE_PATH: Final = Path("tests/fixtures/governance/ppw-bootstrap-source-equations-v1.json")
MANIFEST_PATH: Final = Path("configs/governance/ppw-bootstrap-uncertainty-authority-v1.hashes.json")

EXPECTED_CONFIG_SHA256: Final = "71b22f95:fdf223ba:4ebb0e9e:ee047fd2:61bcb866:0692d8f3:b179ca48:cb8f09d1"
EXPECTED_SCHEMA_SHA256: Final = "144d3622:0cd93394:283282f4:b04f41ca:605ad83e:35927e6b:9f6b65eb:90db5c66"
EXPECTED_FIXTURE_SHA256: Final = "2ef0e4c9:a760cb2c:c1627c99:b3887068:a3c15aa3:555f2b0a:c4a3c477:87853820"
EXPECTED_SEMANTIC_SHA256: Final = "cc6dd002:b5c44722:75e953a3:b364a0b0:638a2b96:342cd6d9:8dc18162:63ca798c"

_MAX_JSON_BYTES: Final = 2 * 1024 * 1024
_IDENTITY: Final = {
    "$schema": "../../schemas/governance/ppw-bootstrap-uncertainty-authority-v1.schema.json",
    "schema_version": "qme.ppw_bootstrap_uncertainty_authority.v1",
    "artifact_id": "QME-PPW-BOOTSTRAP-UNCERTAINTY-AUTHORITY-V1",
    "ticket_id": "NEE-204",
    "status": "SOURCE_EQUATIONS_REGISTERED_OWNER_SELECTIONS_UNRESOLVED_NO_EXECUTION",
}
_FORMULAS: Final = {
    "scope": "COMMON_CORRECTED_STATIONARY_BOOTSTRAP_OBJECTIVE_EQUATIONS_ONLY",
    "flat_top_kernel": "lambda(u) = 1 for abs(u) <= 1/2; 2 * (1 - abs(u)) for 1/2 < abs(u) <= 1; 0 otherwise",
    "weighted_lag_moment": "G_hat = sum_{k=-M..M}(lambda(k / M) * abs(k) * gamma_hat(k))",
    "spectral_density_at_zero_estimate": "g_hat_0 = sum_{k=-M..M}(lambda(k / M) * gamma_hat(k))",
    "corrected_stationary_bootstrap_denominator": "D_hat_SB = 2 * (g_hat_0 ^ 2)",
    "raw_stationary_expected_block_length": "b_hat_SB_raw = ((2 * (G_hat ^ 2) / D_hat_SB) ^ (1 / 3)) * (n ^ (1 / 3))",
    "equivalent_raw_formula": "b_hat_SB_raw = ((2 * (G_hat ^ 2) * n / D_hat_SB) ^ (1 / 3))",
    "correction_supersession": "THE_PUBLISHED_CORRECTION_REPLACES_THE_ORIGINAL_STATIONARY_BOOTSTRAP_D_SB_EXPRESSION",
    "division_or_nonfinite_rule": "NO_NUMERIC_OUTPUT_IS_AUTHORIZED_WHEN_D_hat_SB_IS_ZERO_NONFINITE_OR_UNDEFINED",
    "implementation_status": "SYMBOLIC_SOURCE_EQUATIONS_ONLY_NOT_EXECUTABLE",
}
_SOURCE_HASHES: Final = {
    "original_method_paper": "4147841b:f492f8fc:d5cebc92:281681ef:68e06814:52e68b46:a0330a98:0b3fc7cb",
    "published_correction": "d9e84ad9:762debea:9634192a:97fd37a9:5e49df1f:e2975e6f:c32cd983:af6d1d22",
    "corrected_author_code": "55dea54a:42f8095c:7da4cd61:8fba651c:08677b7b:4fc5dfab:e9b08b77:30157ed0",
    "corrected_author_code_dependency": "503d61f4:68f318b0:5fa3c1cc:fea8acf6:ebf97762:fb83913b:63a08bd2:7df2185e",
    "author_code_index": "825f6621:796d372e:07a96091:416f9bb5:d6565dd2:fe01901c:25d97705:84faf30d",
}
_AUTHORITY_BINDINGS: Final = {
    "proposal": ("docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md", "5869d313:e179e442:e305704e:7cff5031:786d7452:73c0f864:bc910784:8685847c", None),
    "experiment_family": ("configs/governance/experiment-family-registration-v1.json", "016ac219:ea2dd117:58ebbb3d:c32b89b0:41d951e5:878812c1:0fbc396f:a4f09b40", None),
    "owner_supplement_a0": ("configs/governance/owner-mandate-supplement-2026-08-13-v1.json", "289aa1f5:5f586142:1730f146:611f42a1:10dab0a3:596294eb:4171b6dd:3acb5ee5", "configs/governance/owner-mandate-supplement-2026-08-13-v1.hashes.json"),
    "economic_promotion_v2": ("configs/quant/economic-promotion-decision-v2.json", "02d055b0:26d9352e:aa0979cd:c158d9df:26ed6aad:06259567:291970c9:0a9359a8", "configs/quant/economic-promotion-decision-v2.hashes.json"),
    "effective_trials_point_kernel_v1": ("configs/governance/effective-trials-point-kernel-v1.json", "f007e903:5e9e9a7e:89357b68:90317cd7:64ea8ee4:dca423c5:10a57ece:5eb670e8", "configs/governance/effective-trials-point-evidence-v1.hashes.json"),
    "freeze_v4": ("configs/governance/specification-freeze-policy-v4.json", "adf2288b:32532669:cdd7fa9d:4876132b:222916d2:c754f006:6003a6cd:1a4fb458", "configs/governance/specification-freeze-v4.hashes.json"),
}
_MANIFEST_HASHES: Final = {
    "configs/governance/owner-mandate-supplement-2026-08-13-v1.hashes.json": "e5a7214d:1f686f7a:3966b487:30883a49:b7667e75:dc20a592:aa5d1f8d:c4861193",
    "configs/quant/economic-promotion-decision-v2.hashes.json": "793caa4e:5a5b29ce:e746c050:96f25c98:f93aaf26:a2cce442:4de1c6a0:98f0ed87",
    "configs/governance/effective-trials-point-evidence-v1.hashes.json": "121db413:12c69c5a:30ac31ce:cb116c75:83e68636:afed78f7:91c34269:f995edd5",
    "configs/governance/specification-freeze-v4.hashes.json": "a2c3bbfa:d15e7bd3:769142ad:69c291e7:885cd14d:6ca2d939:99c39df2:5360ea42",
    "tests/fixtures/stats/deterministic-kernel-v1.manifest.json": "a7ecc4f5:91139853:d9142fc6:a7d03208:be73ff19:ea066f74:99ee7166:7b5cbf26",
}
_SELECTIONS: Final = (
    ("PPW-UNRESOLVED-001-96-COLUMN-AGGREGATION", "OWNER_SELECTION_REQUIRED", "NO_MAXIMUM_MEDIAN_MEAN_QUANTILE_OR_FIRST_COLUMN_AGGREGATION_MAY_BE_ASSUMED"),
    ("PPW-UNRESOLVED-002-FINITE-SAMPLE-AUTOCOVARIANCE", "OWNER_SELECTION_REQUIRED", "NO_SILENT_PARITY_WITH_MATLAB_cov_OR_PAPER_NOTATION"),
    ("PPW-UNRESOLVED-003-LAG_SELECTION-AND-FALLBACK", "OWNER_SELECTION_REQUIRED", "NO_EMPTY_FIND_ZERO_LAG_OR_AUTHOR_CODE_B_MAX_FALLBACK_MAY_BE_ASSUMED"),
    ("PPW-UNRESOLVED-004-DEGENERATE-INPUTS", "OWNER_SELECTION_REQUIRED", "NO_NUMERIC_ONE_BLOCK_OR_POINT_FALLBACK_MAY_BE_EMITTED"),
    ("PPW-UNRESOLVED-005-SHARED-ROW-INDEX-AND-REFIT", "OWNER_SELECTION_REQUIRED", "NO_INDEPENDENT_PER_COLUMN_INDEX_STREAM_OR_FIXED_CORRELATION_BOOTSTRAP"),
    ("PPW-UNRESOLVED-006-RNG-AND-BLOCK-DRAW-CONSTRUCTION", "OWNER_SELECTION_REQUIRED", "NO_ONCE_ONLY_OR_PER_REPLICATE_PPW_RESELECTION_OR_KERNEL_HANDOFF_IS_IMPLIED"),
    ("PPW-UNRESOLVED-007-P97_5-QUANTILE", "OWNER_SELECTION_REQUIRED", "NO_NEAREST_RANK_LINEAR_INTERPOLATION_OR_NEE120_ORDER_STATISTIC_SUBSTITUTION"),
    ("PPW-UNRESOLVED-008-INVALID-REPLICATE-AND-FALLBACK", "OWNER_SELECTION_REQUIRED", "NO_REPLICATE_DELETION_RETRY_MINIMUM_VALID_COUNT_OR_AUTOMATIC_M96_SUBSTITUTION"),
    ("PPW-UNRESOLVED-009-END-TO-END-INTERVAL-KAT", "EVIDENCE_BLOCKED_BY_OWNER_SELECTIONS", "NO_EXPECTED_INTERVAL_OR_N_EFF_USED_VALUE_MAY_BE_FROZEN_BEFORE_001_THROUGH_008_CLEAR"),
)
_CLAIMS: Final = {
    "ppw_selector_executable": False,
    "bootstrap_interval_available": False,
    "n_eff_used_available": False,
    "dsr_available": False,
    "holm_available": False,
    "empirical_output_available": False,
    "production_inference_available": False,
    "freeze_blocker_changed": False,
    "milestone_m0_complete": False,
    "production_ready": False,
    "alpha_proven": False,
    "live_order_authority": False,
}
_NONCLAIMS: Final = [
    "NO_EXECUTABLE_PPW_SELECTOR",
    "NO_BOOTSTRAP_INTERVAL_OR_N_EFF_USED",
    "NO_DSR_OR_HOLM_MULTIPLICITY_OUTPUT",
    "NO_EMPIRICAL_OR_PRODUCTION_OUTPUT",
    "NO_FREEZE_V4_BLOCKER_REMOVAL",
    "NO_ALPHA_PRODUCTION_READINESS_M0_OR_LIVE_ORDER_CLAIM",
]
_OWN_MANIFEST_PATHS: Final = (
    "configs/governance/ppw-bootstrap-uncertainty-authority-v1.json",
    "schemas/governance/ppw-bootstrap-uncertainty-authority-v1.schema.json",
    "qme/governance/ppw_bootstrap_uncertainty_authority.py",
    "tests/fixtures/governance/ppw-bootstrap-source-equations-v1.json",
    "tests/governance/test_ppw_bootstrap_uncertainty_authority.py",
    "docs/governance/PPW_BOOTSTRAP_UNCERTAINTY_AUTHORITY_V1.md",
)
type _VerifiedState = tuple[
    str,
    str,
    tuple[str, ...],
    tuple[str, ...],
    str,
]


class PpwBootstrapAuthorityError(RuntimeError):
    """Raised when authority evidence fails closed."""


class VerifiedPpwBootstrapAuthority:
    """Immutable data whose serializer independently replays repository evidence."""

    __slots__ = (
        "_active_blocker_codes",
        "_config_sha256",
        "_semantic_sha256",
        "_status",
        "_unresolved_selection_ids",
    )

    _active_blocker_codes: tuple[str, ...]
    _config_sha256: str
    _semantic_sha256: str
    _status: str
    _unresolved_selection_ids: tuple[str, ...]

    def __new__(cls, *_args: object, **_kwargs: object) -> VerifiedPpwBootstrapAuthority:
        raise TypeError("VerifiedPpwBootstrapAuthority is created only by repository verification")

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("VerifiedPpwBootstrapAuthority cannot be subclassed")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("VerifiedPpwBootstrapAuthority is immutable")

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
    def unresolved_selection_ids(self) -> tuple[str, ...]:
        return self._unresolved_selection_ids

    @property
    def status(self) -> str:
        return self._status


def _normal(value: object, label: str) -> str:
    if type(value) is not str:
        raise PpwBootstrapAuthorityError(f"{label} must be grouped SHA-256")
    parts = value.split(":")
    if len(parts) != 8 or any(len(part) != 8 or any(ch not in "0123456789abcdef" for ch in part) for part in parts):
        raise PpwBootstrapAuthorityError(f"{label} must be exactly 8 groups of 8 lowercase hex")
    return "".join(parts)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PpwBootstrapAuthorityError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _nonfinite(token: str) -> None:
    raise PpwBootstrapAuthorityError(f"non-finite JSON token: {token}")


def _confined_bytes(relative: Path, root: Path, *, limit: int = _MAX_JSON_BYTES) -> bytes:
    if relative.is_absolute() or ".." in relative.parts:
        raise PpwBootstrapAuthorityError("path is not repository relative")
    resolved_root = root.resolve(strict=True)
    target = resolved_root.joinpath(relative)
    cursor = resolved_root
    for part in relative.parts:
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise PpwBootstrapAuthorityError(f"path is missing: {relative.as_posix()}") from exc
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400):
            raise PpwBootstrapAuthorityError(f"linked/reparse path forbidden: {relative.as_posix()}")
    try:
        relative_target = target.resolve(strict=True).relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise PpwBootstrapAuthorityError(f"path escapes or is missing: {relative.as_posix()}") from exc
    if relative_target != relative:
        raise PpwBootstrapAuthorityError(f"noncanonical path forbidden: {relative.as_posix()}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise PpwBootstrapAuthorityError(f"nonregular or oversized file: {relative.as_posix()}")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    final = target.stat()
    if len(raw) > limit or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns):
        raise PpwBootstrapAuthorityError(f"file changed during read: {relative.as_posix()}")
    return raw


def _load(relative: Path, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _confined_bytes(relative, root).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PpwBootstrapAuthorityError(f"invalid JSON: {relative.as_posix()}") from exc
    if type(value) is not dict:
        raise PpwBootstrapAuthorityError(f"JSON root must be an object: {relative.as_posix()}")
    return cast(dict[str, Any], value)


def _sha(relative: Path, root: Path) -> str:
    return hashlib.sha256(_confined_bytes(relative, root)).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _semantic(document: dict[str, Any]) -> str:
    content = dict(document)
    content.pop("semantic_sha256", None)
    return hashlib.sha256(_canonical(content)).hexdigest()


def _projection_hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _verify_complete_registered_inventory(
    config: dict[str, Any], schema: dict[str, Any], fixture: dict[str, Any]
) -> None:
    expected_root = {
        "$schema",
        "schema_version",
        "artifact_id",
        "ticket_id",
        "status",
        "semantic_sha256",
        "authority",
        "primary_sources",
        "corrected_common_equations",
        "official_author_code_observations",
        "protected_registered_overlays",
        "unresolved_owner_selections",
        "source_equation_fixture_contract",
        "active_freeze_v4_blockers",
        "claims",
        "nonclaims",
    }
    if set(config) != expected_root:
        raise PpwBootstrapAuthorityError("config root inventory changed")
    expected_projection_digests = {
        "authority": "f691ab53:0ebfdac0:d87de7dc:a34a3e05:1d447403:d2bbb326:36a687da:d46ed11e",
        "primary_sources": "c0e6e39a:f7aa3068:b4f443d3:d8a76b8f:cba5a8a2:94046918:7b64d978:5d8a985d",
        "corrected_common_equations": "19b81c05:beae1ffb:6a57d551:c97fe5ec:93889bc5:a45f0624:85f87e49:f485925a",
        "official_author_code_observations": "978ea88e:e6b1707c:cb4530a0:7b206714:e47b5563:ba63007e:2d8cf9d1:125ce0c8",
        "protected_registered_overlays": "3dd2b1b0:1d189579:5d4e1a75:6c620205:ed1f76f7:05a0d4fe:c20f542b:1e77fb42",
        "unresolved_owner_selections": "25620f1a:c027702a:64508e54:b301c549:8c453662:8b407de7:966278ab:dc8de31a",
        "source_equation_fixture_contract": "6fdc2b03:58c8ad98:301fab8a:c755aa54:97a4b6d2:3ecc67f9:ce28a8e0:d3dca72e",
        "active_freeze_v4_blockers": "a70d5145:fa8f4b47:bc1bb222:9c7a7661:5673fb4e:e4e6e8c8:89b87921:d2f219ff",
        "claims": "451448db:1fdd551b:cf1d417d:4efcdde0:00caeb22:e45ab152:c931ca3a:e87340fd",
        "nonclaims": "a8f9df6d:3eae9fad:765e047c:fb079c68:6e3f725d:134deced:018a1152:16d5f2f4",
    }
    for key, expected in expected_projection_digests.items():
        if _projection_hash(config[key]) != _normal(expected, f"expected {key} projection"):
            raise PpwBootstrapAuthorityError(f"complete registered inventory changed: {key}")

    expected_required = [
        "$schema",
        "schema_version",
        "artifact_id",
        "ticket_id",
        "status",
        "semantic_sha256",
        "authority",
        "primary_sources",
        "corrected_common_equations",
        "official_author_code_observations",
        "protected_registered_overlays",
        "unresolved_owner_selections",
        "source_equation_fixture_contract",
        "active_freeze_v4_blockers",
        "claims",
        "nonclaims",
    ]
    if (
        set(schema) != {"$schema", "$id", "title", "type", "additionalProperties", "required", "properties", "$defs"}
        or schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema"
        or schema.get("$id")
        != "https://qme.local/schemas/governance/ppw-bootstrap-uncertainty-authority-v1.schema.json"
        or schema.get("title") != "QME PPW Bootstrap Uncertainty Authority V1"
        or schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or schema.get("required") != expected_required
        or type(schema.get("properties")) is not dict
        or list(cast(dict[str, Any], schema["properties"])) != expected_required
    ):
        raise PpwBootstrapAuthorityError("schema metadata or exact required inventory changed")
    if _projection_hash(schema) != _normal(
        "0a55b38e:21513cc0:ad97a121:cbfdabd5:a4738982:3a398cde:5e0b42d9:dccb71f9",
        "expected complete schema projection",
    ):
        raise PpwBootstrapAuthorityError("complete schema semantics changed")
    if _projection_hash(fixture) != _normal(
        "c837691b:e214fe10:404754aa:449efd7c:631b7bcc:351d4a28:43af6b01:a8ed4c57",
        "expected complete fixture projection",
    ):
        raise PpwBootstrapAuthorityError("complete fixture semantics changed")
    try:
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(config))
    except SchemaError as exc:
        raise PpwBootstrapAuthorityError("Draft 2020-12 schema is invalid") from exc
    if errors:
        raise PpwBootstrapAuthorityError("config does not satisfy the exact Draft 2020-12 schema")


def _words_digest(words: object) -> str:
    if type(words) is not list or len(words) != 8 or any(type(word) is not int or word < 0 or word > 0xFFFFFFFF for word in words):
        raise PpwBootstrapAuthorityError("manifest SHA words are invalid")
    return b"".join(int(word).to_bytes(4, "big") for word in words).hex()


def _replay_manifest(relative: Path, expected_sha: str, root: Path) -> None:
    if _sha(relative, root) != _normal(expected_sha, f"{relative.as_posix()} hash"):
        raise PpwBootstrapAuthorityError(f"protected manifest bytes changed: {relative.as_posix()}")
    manifest = _load(relative, root)
    rows = manifest.get("artifacts")
    if type(rows) is not list or not rows:
        raise PpwBootstrapAuthorityError(f"manifest artifacts invalid: {relative.as_posix()}")
    seen: set[str] = set()
    for row in rows:
        if type(row) is not dict:
            raise PpwBootstrapAuthorityError("manifest row must be an object")
        keys = set(row)
        if keys == {"path", "sha256"}:
            expected_leaf = _normal(row["sha256"], "manifest leaf SHA")
        elif keys == {"path", "sha256_words_be"}:
            expected_leaf = _words_digest(row["sha256_words_be"])
        else:
            raise PpwBootstrapAuthorityError("manifest row shape changed")
        member = row.get("path")
        if type(member) is not str or not member or member in seen:
            raise PpwBootstrapAuthorityError("manifest path is invalid or duplicated")
        seen.add(member)
        if _sha(Path(member), root) != expected_leaf:
            raise PpwBootstrapAuthorityError(f"protected manifest leaf changed: {member}")


def _verify_sources(config: dict[str, Any]) -> None:
    sources = config.get("primary_sources")
    if type(sources) is not dict or set(sources) != set(_SOURCE_HASHES) | {"network_retrieval_rule"}:
        raise PpwBootstrapAuthorityError("primary source inventory changed")
    for key, digest in _SOURCE_HASHES.items():
        source = sources.get(key)
        if type(source) is not dict:
            raise PpwBootstrapAuthorityError(f"primary source missing: {key}")
        field = "author_pdf_sha256" if key in {"original_method_paper", "published_correction"} else "sha256"
        if _normal(source.get(field), f"{key} digest") != _normal(digest, f"expected {key} digest"):
            raise PpwBootstrapAuthorityError(f"primary source identity changed: {key}")
    if sources.get("network_retrieval_rule") != "HASH_AND_SIZE_IDENTIFY_REVIEWED_BYTES_RUNTIME_VERIFIER_IS_NETWORK_FREE":
        raise PpwBootstrapAuthorityError("network-free source rule changed")


def _verify_authorities(config: dict[str, Any], root: Path) -> None:
    authority = config.get("authority")
    if type(authority) is not dict or authority.get("empirical_results_used") is not False:
        raise PpwBootstrapAuthorityError("authority root changed")
    for key, (path, digest, manifest_path) in _AUTHORITY_BINDINGS.items():
        binding = authority.get(key)
        if type(binding) is not dict or binding.get("path") != path or _normal(binding.get("sha256"), f"{key} SHA") != _normal(digest, f"expected {key} SHA"):
            raise PpwBootstrapAuthorityError(f"authority binding changed: {key}")
        if _sha(Path(path), root) != _normal(digest, f"expected {key} SHA"):
            raise PpwBootstrapAuthorityError(f"protected authority bytes changed: {path}")
        if manifest_path is not None:
            if binding.get("manifest_path") != manifest_path or _normal(binding.get("manifest_sha256"), f"{key} manifest SHA") != _normal(_MANIFEST_HASHES[manifest_path], f"expected {key} manifest SHA"):
                raise PpwBootstrapAuthorityError(f"authority manifest binding changed: {key}")
            _replay_manifest(Path(manifest_path), _MANIFEST_HASHES[manifest_path], root)
    stats = authority.get("deterministic_stats_kernel")
    stats_path = "tests/fixtures/stats/deterministic-kernel-v1.manifest.json"
    if type(stats) is not dict or stats != {
        "manifest_path": stats_path,
        "manifest_sha256": _MANIFEST_HASHES[stats_path],
        "implementation_status": "BOUNDED_KERNEL_BLOCK_SELECTOR_UNAVAILABLE",
    }:
        raise PpwBootstrapAuthorityError("deterministic-kernel boundary changed")
    _replay_manifest(Path(stats_path), _MANIFEST_HASHES[stats_path], root)


def _verify_semantics(config: dict[str, Any], fixture: dict[str, Any], root: Path) -> tuple[str, ...]:
    if config.get("corrected_common_equations") != _FORMULAS:
        raise PpwBootstrapAuthorityError("corrected common equations changed")
    _verify_sources(config)
    selections = config.get("unresolved_owner_selections")
    if type(selections) is not list or len(selections) != len(_SELECTIONS):
        raise PpwBootstrapAuthorityError("unresolved selection inventory changed")
    observed: list[str] = []
    for row, expected in zip(selections, _SELECTIONS, strict=True):
        if type(row) is not dict or set(row) != {"selection_id", "status", "question", "forbidden_inference", "clear_condition"}:
            raise PpwBootstrapAuthorityError("unresolved selection shape changed")
        if (row["selection_id"], row["status"], row["forbidden_inference"]) != expected:
            raise PpwBootstrapAuthorityError(f"unresolved selection changed: {expected[0]}")
        if type(row["question"]) is not str or type(row["clear_condition"]) is not str or not row["question"] or not row["clear_condition"]:
            raise PpwBootstrapAuthorityError("unresolved selection explanation missing")
        observed.append(cast(str, row["selection_id"]))
    if config.get("claims") != _CLAIMS or config.get("nonclaims") != _NONCLAIMS:
        raise PpwBootstrapAuthorityError("claims/nonclaims changed")
    freeze = _load(Path(_AUTHORITY_BINDINGS["freeze_v4"][0]), root)
    blockers = config.get("active_freeze_v4_blockers")
    if type(blockers) is not list or blockers != freeze.get("unresolved_blockers") or len(blockers) != 13:
        raise PpwBootstrapAuthorityError("Freeze V4 blocker lineage changed")
    if fixture.get("status") != "SYMBOLIC_AND_BOUNDARY_KATS_ONLY_NUMERIC_SELECTOR_UNRESOLVED" or fixture.get("forbidden_numeric_fields") != ["selected_block_length", "bootstrap_distribution", "bootstrap_interval", "n_eff_used", "dsr", "holm_adjusted_p"]:
        raise PpwBootstrapAuthorityError("source-equation fixture non-executable boundary changed")
    future = fixture.get("future_numeric_cases")
    if type(future) is not list or len(future) != 9 or any(type(row) is not dict or row.get("expected_status") != "NO_EXECUTABLE_EXPECTATION_REGISTERED" for row in future):
        raise PpwBootstrapAuthorityError("future numeric fixture obligations changed")
    return tuple(observed)


def _make_verified_result(state: _VerifiedState) -> VerifiedPpwBootstrapAuthority:
    config_sha256, semantic_sha256, active_blocker_codes, unresolved_selection_ids, status = state
    instance = object.__new__(VerifiedPpwBootstrapAuthority)
    object.__setattr__(instance, "_config_sha256", config_sha256)
    object.__setattr__(instance, "_semantic_sha256", semantic_sha256)
    object.__setattr__(instance, "_active_blocker_codes", active_blocker_codes)
    object.__setattr__(instance, "_unresolved_selection_ids", unresolved_selection_ids)
    object.__setattr__(instance, "_status", status)
    return instance


def _result_state(value: object) -> _VerifiedState:
    if type(value) is not VerifiedPpwBootstrapAuthority:
        raise PpwBootstrapAuthorityError("verified result must have exact public type")
    try:
        config_sha256 = object.__getattribute__(value, "_config_sha256")
        semantic_sha256 = object.__getattribute__(value, "_semantic_sha256")
        active_blocker_codes = object.__getattribute__(value, "_active_blocker_codes")
        unresolved_selection_ids = object.__getattribute__(value, "_unresolved_selection_ids")
        status = object.__getattribute__(value, "_status")
    except (AttributeError, TypeError) as exc:
        raise PpwBootstrapAuthorityError("verified result is incomplete") from exc
    if (
        type(config_sha256) is not str
        or type(semantic_sha256) is not str
        or type(active_blocker_codes) is not tuple
        or any(type(item) is not str for item in active_blocker_codes)
        or type(unresolved_selection_ids) is not tuple
        or any(type(item) is not str for item in unresolved_selection_ids)
        or type(status) is not str
    ):
        raise PpwBootstrapAuthorityError("verified result field types changed")
    return (
        config_sha256,
        semantic_sha256,
        active_blocker_codes,
        unresolved_selection_ids,
        status,
    )


def _state_projection(state: _VerifiedState) -> dict[str, object]:
    config_sha256, semantic_sha256, active_blocker_codes, unresolved_selection_ids, status = state
    return {
        "config_sha256": config_sha256,
        "semantic_sha256": semantic_sha256,
        "active_blocker_codes": list(active_blocker_codes),
        "unresolved_selection_ids": list(unresolved_selection_ids),
        "status": status,
    }


def _verify_repository_state(repository_root: str | Path) -> _VerifiedState:
    root = Path(repository_root)
    config_raw = _sha(CONFIG_PATH, root)
    schema_raw = _sha(SCHEMA_PATH, root)
    fixture_raw = _sha(FIXTURE_PATH, root)
    if config_raw != _normal(EXPECTED_CONFIG_SHA256, "expected config SHA") or schema_raw != _normal(EXPECTED_SCHEMA_SHA256, "expected schema SHA") or fixture_raw != _normal(EXPECTED_FIXTURE_SHA256, "expected fixture SHA"):
        raise PpwBootstrapAuthorityError("local authority evidence bytes changed")
    config = _load(CONFIG_PATH, root)
    schema = _load(SCHEMA_PATH, root)
    fixture = _load(FIXTURE_PATH, root)
    _verify_complete_registered_inventory(config, schema, fixture)
    if {key: config.get(key) for key in _IDENTITY} != _IDENTITY:
        raise PpwBootstrapAuthorityError("authority identity changed")
    if schema.get("title") != "QME PPW Bootstrap Uncertainty Authority V1" or schema.get("additionalProperties") is not False:
        raise PpwBootstrapAuthorityError("schema identity/strictness changed")
    semantic = _semantic(config)
    if _normal(config.get("semantic_sha256"), "config semantic SHA") != semantic or semantic != _normal(EXPECTED_SEMANTIC_SHA256, "expected semantic SHA"):
        raise PpwBootstrapAuthorityError("semantic commitment changed")
    _verify_authorities(config, root)
    selection_ids = _verify_semantics(config, fixture, root)
    blockers = cast(list[dict[str, Any]], config["active_freeze_v4_blockers"])
    return (
        config_raw,
        semantic,
        tuple(cast(str, row["blocker_code"]) for row in blockers),
        selection_ids,
        cast(str, config["status"]),
    )


def verify_ppw_bootstrap_uncertainty_authority(repository_root: str | Path) -> VerifiedPpwBootstrapAuthority:
    """Verify local and transitive evidence without selecting or running PPW."""

    return _make_verified_result(_verify_repository_state(repository_root))


def serialize_verified_ppw_bootstrap_uncertainty_authority(
    value: object, repository_root: str | Path
) -> bytes:
    """Serialize only after independent repository replay and exact field comparison."""

    authoritative_state = _verify_repository_state(repository_root)
    supplied_state = _result_state(value)
    if supplied_state != authoritative_state:
        raise PpwBootstrapAuthorityError("verified result differs from independently replayed evidence")
    return _canonical(_state_projection(authoritative_state))


def verify_ppw_bootstrap_uncertainty_authority_manifest(repository_root: str | Path) -> None:
    """Replay the exact reviewed six-leaf outer manifest."""

    root = Path(repository_root)
    manifest = _load(MANIFEST_PATH, root)
    if set(manifest) != {"schema_version", "artifact_id", "status", "artifacts", "limitations"} or manifest.get("schema_version") != "qme.ppw_bootstrap_uncertainty_authority_manifest.v1" or manifest.get("artifact_id") != "QME-PPW-BOOTSTRAP-UNCERTAINTY-AUTHORITY-V1" or manifest.get("status") != "REVIEWED_SOURCE_EQUATIONS_OWNER_SELECTIONS_UNRESOLVED" or manifest.get("limitations") != _NONCLAIMS:
        raise PpwBootstrapAuthorityError("outer manifest identity changed")
    rows = manifest.get("artifacts")
    if type(rows) is not list or len(rows) != len(_OWN_MANIFEST_PATHS):
        raise PpwBootstrapAuthorityError("outer manifest membership changed")
    for row, expected_path in zip(rows, _OWN_MANIFEST_PATHS, strict=True):
        if type(row) is not dict or set(row) != {"path", "sha256"} or row.get("path") != expected_path:
            raise PpwBootstrapAuthorityError("outer manifest row/order changed")
        if _sha(Path(expected_path), root) != _normal(row.get("sha256"), "outer manifest leaf SHA"):
            raise PpwBootstrapAuthorityError(f"outer manifest leaf changed: {expected_path}")


__all__ = [
    "PpwBootstrapAuthorityError",
    "VerifiedPpwBootstrapAuthority",
    "serialize_verified_ppw_bootstrap_uncertainty_authority",
    "verify_ppw_bootstrap_uncertainty_authority",
    "verify_ppw_bootstrap_uncertainty_authority_manifest",
]
