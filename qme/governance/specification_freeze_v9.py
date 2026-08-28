"""Fail-closed verifier for Specification Freeze V9 CI-authority successor amendment.

Freeze V9 registers the bounded parallel workflow and shard-verification
machinery as current CI authority. It cannot rewrite Freeze V8, cannot rewrite
``.github/workflows/ci.yml``, cannot raise the 30-minute per-job ceiling, and
cannot change M0 claims.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, NoReturn, cast

from jsonschema import (  # type: ignore[import-untyped, unused-ignore]
    Draft202012Validator,
    FormatChecker,
)

from qme.governance.specification_freeze_v8 import (
    verify_specification_freeze_v8,
    verify_specification_freeze_v8_manifest,
)

__all__ = [
    "CANDIDATE_ID",
    "CANDIDATE_STATUS",
    "MANIFEST_PATH",
    "POLICY_PATH",
    "POLICY_SCHEMA_PATH",
    "REQUIRED_CHECK_CONTEXTS",
    "SpecificationFreezeV9Error",
    "VerifiedSpecificationFreezeV9",
    "normalize_grouped_sha256",
    "serialize_verified_specification_freeze_v9",
    "verify_predecessor_freeze_v8",
    "verify_specification_freeze_v9",
    "verify_specification_freeze_v9_manifest",
]

POLICY_PATH = Path("configs/governance/specification-freeze-policy-v9.json")
POLICY_SCHEMA_PATH = Path("schemas/governance/specification-freeze-policy-v9.schema.json")
MANIFEST_PATH = Path("configs/governance/specification-freeze-v9.hashes.json")

CANDIDATE_ID: Final = "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V9"
CANDIDATE_STATUS: Final = (
    "CANDIDATE_OWNER_AUTHORIZED_CI_AUTHORITY_TRANSITION_V8_UNCHANGED_"
    "PENDING_EXACT_BYTE_LOCK_MERGE_AND_PROTECTED_MAIN_PARALLEL_CI"
)
REQUIRED_CHECK_CONTEXTS: Final = (
    "static-build",
    "tests-data-architecture",
    "tests-rest",
    "secrets-fixture-publication",
    "foundation-parallel",
    "nee123-posix",
    "deterministic-replay",
)
PARALLEL_JOBS: Final = (
    "static-build",
    "tests-data-architecture",
    "tests-rest",
    "secrets-fixture-publication",
    "nee123-posix",
    "foundation-parallel",
)
CEILING_MINUTES: Final = 30
REPLAY_TIMEOUT_MINUTES: Final = 20

_POLICY_PATH = POLICY_PATH.as_posix()
_POLICY_SCHEMA_PATH = POLICY_SCHEMA_PATH.as_posix()
_MANIFEST_PATH = MANIFEST_PATH.as_posix()
_RUNTIME_PATH = "qme/governance/specification_freeze_v9.py"
_CI_PATH = ".github/workflows/ci.yml"
_PARALLEL_PATH = ".github/workflows/qme-ci-parallel.yml"
_REPLAY_PATH = ".github/workflows/m0-substantive-evidence-linux.yml"
_SHARDS_PATH = "scripts/verify_test_shards.py"
_SHARDS_TEST_PATH = "tests/foundation/test_verify_test_shards.py"
_V8_POLICY_PATH = "configs/governance/specification-freeze-policy-v8.json"
_V8_MANIFEST_PATH = "configs/governance/specification-freeze-v8.hashes.json"
_DOCS_PATH = "docs/governance/SPECIFICATION_FREEZE_V9.md"
_DISPOSITION_PATH = (
    "docs/governance/blocker-transition-receipts/freeze-v9-ci-authority/OWNER-DISPOSITION.md"
)
_RECEIPT_PATH = "docs/governance/blocker-transition-receipts/freeze-v9-ci-authority/RECEIPT.md"
_TESTS_PATH = "tests/governance/test_specification_freeze_v9.py"

# fmt: off
_EXPECTED_POLICY_SHA256 = "b08cbcbe:19446fe2:e77a60d6:49454e41:c6713e84:41f706c8:ccc5f4dc:6aef5fb7"
_EXPECTED_POLICY_SEMANTIC_SHA256 = "6649a975:4ec877f6:9e0b434f:eb0d75cb:4f1fe1c0:1acb1f5e:8859274d:8120a019"
_EXPECTED_SCHEMA_SHA256 = "0b7ef3e3:ed0ba22b:c6c8d7ff:eb3a91b2:74128fca:463eb1ce:0273a6b8:5a20f4bc"
_EXPECTED_DOCS_SHA256 = "41a21942:9b35ee4e:fb494519:0566966f:dbc68164:a794e06c:fb16452e:b31a69ad"
_EXPECTED_DISPOSITION_SHA256 = "905e9644:037d1741:b765840f:d3a1899f:72f1e983:a6e17510:c77e14ce:eefda904"
_EXPECTED_RECEIPT_SHA256 = "cf195a61:abf9331a:9c3c57dc:f7c22aa0:1257e48d:f07a2871:dc1176c5:5e1b684b"
_EXPECTED_TESTS_SHA256 = "dde3eec9:d25ccd19:4f2153ac:cd49701d:a2abbc2d:1a0a94ca:7cec1de5:6863cdd2"
_EXPECTED_RUNTIME_NORMALIZED_SHA256 = "ff49044b:65dc330f:a0f1bbf8:432b6ef8:ec212106:5ffbcf99:8b16f1ee:b888ad88"
# fmt: on

_PINNED_CI_SHA256 = "a2f84258:c1b694cd:6e2761fd:5b4a07c2:c7306cf4:5368af1e:e5c5ff7a:c933992f"
_PINNED_PARALLEL_SHA256 = "b0848415:0adfc712:98bc10f6:9acc931d:abaed0ff:39b48f11:2e79f81f:928d1aff"
_PINNED_SHARDS_SHA256 = "53f5e660:cd4a7218:1f93140a:d2d5e24f:c22e8a8c:4a31682f:d8ea09b0:f3839ecb"
_PINNED_REPLAY_SHA256 = "ed440006:55c9f83c:c08ae8d9:6f996508:25450fe7:8b0458ca:7283109a:562df037"
_PINNED_SHARDS_TEST_SHA256 = "94d3150a:a0231062:b6d82662:089cbb7a:9c9f0035:42837e41:bb0be71c:7db029d1"
_PINNED_V8_POLICY_SHA256 = "34925587:f2782d25:d72e8983:fd8f45be:cfaaf8a1:24c6114a:ae36537c:2c16c15d"
_PINNED_V8_MANIFEST_SHA256 = "e0562c4f:675303a1:26b66c10:47d03b4a:8a21c736:adc4c75e:f76aa51c:4244065c"

_EXPECTED_OWNED_NONRUNTIME_LEAVES: Final[Mapping[str, str]] = MappingProxyType(
    {
        _POLICY_PATH: _EXPECTED_POLICY_SHA256,
        _POLICY_SCHEMA_PATH: _EXPECTED_SCHEMA_SHA256,
        _DOCS_PATH: _EXPECTED_DOCS_SHA256,
        _DISPOSITION_PATH: _EXPECTED_DISPOSITION_SHA256,
        _RECEIPT_PATH: _EXPECTED_RECEIPT_SHA256,
        _TESTS_PATH: _EXPECTED_TESTS_SHA256,
        _CI_PATH: _PINNED_CI_SHA256,
        _PARALLEL_PATH: _PINNED_PARALLEL_SHA256,
        _REPLAY_PATH: _PINNED_REPLAY_SHA256,
        _SHARDS_PATH: _PINNED_SHARDS_SHA256,
        _SHARDS_TEST_PATH: _PINNED_SHARDS_TEST_SHA256,
        _V8_POLICY_PATH: _PINNED_V8_POLICY_SHA256,
        _V8_MANIFEST_PATH: _PINNED_V8_MANIFEST_SHA256,
    }
)

_EXPECTED_MANIFEST_PATHS: Final[tuple[str, ...]] = (
    _CI_PATH,
    _PARALLEL_PATH,
    _REPLAY_PATH,
    _V8_MANIFEST_PATH,
    _V8_POLICY_PATH,
    _POLICY_PATH,
    _DISPOSITION_PATH,
    _RECEIPT_PATH,
    _DOCS_PATH,
    _RUNTIME_PATH,
    _POLICY_SCHEMA_PATH,
    _SHARDS_PATH,
    _SHARDS_TEST_PATH,
    _TESTS_PATH,
)

_PARALLEL_REQUIRED_SNIPPETS: Final[tuple[str, ...]] = (
    "python scripts/check_secrets.py",
    "git diff --exit-code",
    "python -m ruff check qme tests scripts",
    "python -m mypy qme scripts/verify_lock.py scripts/check_secrets.py scripts/verify_test_shards.py",
    "python scripts/verify_test_shards.py verify",
    "--full manifests/nodes-full.json",
    "--shard manifests/nodes-data-architecture.json",
    "--shard manifests/nodes-rest.json",
    "POSIX publication tests were skipped",
    "fixture manifests are not deterministic",
)

_EXPECTED_CLAIMS: Final[Mapping[str, bool]] = MappingProxyType(
    {
        "ci_authority_is_bounded_parallel_workflow": True,
        "serial_foundation_retired_as_acceptance_gate": True,
        "thirty_minute_ceiling_retained": True,
        "v8_bytes_unchanged": True,
        "ci_yml_immutable": True,
        "production_ready": False,
        "live_order_authority": False,
        "nee128_complete": False,
        "milestone_m1_complete": False,
        "pr67_merge_authorized": False,
        "cancelled_serial_foundation_called_successful": False,
        "timeout_increase_authorized": False,
    }
)


class SpecificationFreezeV9Error(ValueError):
    """Raised when any Freeze V9 policy, pin, ceiling, or claim check fails."""


class VerifiedSpecificationFreezeV9:
    """Opaque verified result. Construction outside the private verifier is invalid."""

    __slots__ = (
        "_status",
        "_policy_sha256",
        "_semantic_sha256",
        "_required_check_contexts",
        "_parallel_job_timeouts",
        "_claims",
    )

    def __new__(cls, *_args: object, **_kwargs: object) -> VerifiedSpecificationFreezeV9:
        raise SpecificationFreezeV9Error("verified values are verifier-created only")

    @property
    def status(self) -> str:
        return cast(str, object.__getattribute__(self, "_status"))

    @property
    def policy_sha256(self) -> str:
        return cast(str, object.__getattribute__(self, "_policy_sha256"))

    @property
    def semantic_sha256(self) -> str:
        return cast(str, object.__getattribute__(self, "_semantic_sha256"))

    @property
    def required_check_contexts(self) -> tuple[str, ...]:
        return cast(tuple[str, ...], object.__getattribute__(self, "_required_check_contexts"))

    @property
    def parallel_job_timeouts(self) -> Mapping[str, int]:
        return cast(Mapping[str, int], object.__getattribute__(self, "_parallel_job_timeouts"))

    @property
    def claims(self) -> Mapping[str, bool]:
        return cast(Mapping[str, bool], object.__getattribute__(self, "_claims"))


def normalize_grouped_sha256(value: object, field: str) -> str:
    """Normalize an exact grouped SHA-256 string to 64 lowercase hex digits."""

    if type(value) is not str:
        raise SpecificationFreezeV9Error(f"{field} must be a string")
    if re.fullmatch(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}", value) is None:
        raise SpecificationFreezeV9Error(f"{field} is not grouped SHA-256")
    return value.replace(":", "")


def _grouped(digest: str) -> str:
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _fail(message: str) -> NoReturn:
    raise SpecificationFreezeV9Error(message)


def _normal(value: object, field: str) -> str:
    return normalize_grouped_sha256(value, field)


def _exact_dict(value: object, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        _fail(f"{field} must be an exact object")
    return cast(dict[str, Any], value)


def _exact_list(value: object, field: str) -> list[Any]:
    if type(value) is not list:
        _fail(f"{field} must be an exact array")
    return value


def _exact_str(value: object, field: str) -> str:
    if type(value) is not str:
        _fail(f"{field} must be exact text")
    return value


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    _fail(f"non-finite JSON constant rejected: {value}")


def _path_identity(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_mode),
        int(info.st_nlink),
    )


def _read_bytes(relative: str, root: Path) -> bytes:
    if relative.startswith("/") or relative.startswith("\\") or ".." in Path(relative).parts:
        _fail(f"unsafe artifact path: {relative}")
    target = (root / relative).resolve(strict=True)
    try:
        target.relative_to(root.resolve(strict=True))
    except ValueError:
        _fail(f"artifact escapes repository root: {relative}")
    info = target.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _fail(f"single-link regular file required: {relative}")
    if info.st_size > 4 * 1024 * 1024:
        _fail(f"artifact exceeds size cap: {relative}")
    identity_before = _path_identity(target)
    raw = target.read_bytes()
    if _path_identity(target) != identity_before:
        _fail(f"artifact changed during read: {relative}")
    if len(raw) != info.st_size:
        _fail(f"artifact size drifted during read: {relative}")
    return raw


def _digest(relative: str, root: Path) -> str:
    return hashlib.sha256(_read_bytes(relative, root)).hexdigest()


def _load_json(relative: str, root: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_bytes(relative, root)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpecificationFreezeV9Error(f"artifact is not strict UTF-8 JSON: {relative}") from exc
    if type(value) is not dict:
        _fail(f"JSON root must be an object: {relative}")
    return cast(dict[str, Any], value), raw


def _semantic_digest(document: dict[str, Any]) -> str:
    projected = dict(document)
    projected.pop("semantic_sha256", None)
    raw = (
        json.dumps(
            projected,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _normalized_runtime_sha(root: Path) -> str:
    raw = _read_bytes(_RUNTIME_PATH, root)
    replaced, count = re.compile(
        rb'(_EXPECTED_RUNTIME_NORMALIZED_SHA256 = ")[0-9a-f:]{71}("\r?\n)'
    ).subn(rb"\g<1>PENDING\g<2>", raw)
    if count != 1:
        _fail("runtime normalized self-pin marker count drift")
    return hashlib.sha256(replaced).hexdigest()


def _parse_job_timeouts(text: str, workflow: str) -> dict[str, int]:
    timeouts: dict[str, int] = {}
    current_job: str | None = None
    in_jobs = False
    for raw_line in text.splitlines():
        if raw_line == "jobs:":
            in_jobs = True
            continue
        if not in_jobs:
            continue
        job_match = re.fullmatch(r"  ([A-Za-z0-9_-]+):", raw_line)
        if job_match is not None:
            current_job = job_match.group(1)
            continue
        timeout_match = re.fullmatch(r"    timeout-minutes: ([0-9]+)", raw_line)
        if timeout_match is not None:
            if current_job is None:
                _fail(f"{workflow} timeout is not bound to a job")
            minutes = int(timeout_match.group(1))
            if current_job in timeouts:
                _fail(f"{workflow} job {current_job} has duplicate timeout")
            timeouts[current_job] = minutes
    if not timeouts:
        _fail(f"{workflow} has no job timeouts")
    return timeouts


def _verify_ceiling(text: str, workflow: str, *, expected_jobs: tuple[str, ...], minutes: int) -> Mapping[str, int]:
    for match in re.finditer(r"timeout-minutes:\s*([0-9]+)", text):
        value = int(match.group(1))
        if value > CEILING_MINUTES:
            _fail(f"{workflow} timeout {value} exceeds the 30-minute ceiling")
        if workflow == _PARALLEL_PATH and value != minutes:
            _fail(f"{workflow} timeout {value} is not exactly {minutes}")
    timeouts = _parse_job_timeouts(text, workflow)
    if tuple(timeouts) != expected_jobs:
        _fail(f"{workflow} job set or order changed")
    if any(value != minutes for value in timeouts.values()):
        _fail(f"{workflow} job timeout drift")
    return MappingProxyType(timeouts)


def _verify_parallel_controls(text: str) -> None:
    for snippet in _PARALLEL_REQUIRED_SNIPPETS:
        if snippet not in text:
            _fail(f"parallel workflow weakened: missing {snippet}")


def _verify_replay_controls(text: str) -> None:
    timeouts = _parse_job_timeouts(text, _REPLAY_PATH)
    if tuple(timeouts) != ("deterministic-replay",):
        _fail("replay job set changed")
    if timeouts["deterministic-replay"] != REPLAY_TIMEOUT_MINUTES:
        _fail("replay timeout was raised or lowered")
    if "timeout-minutes: 20" not in text:
        _fail("replay 20-minute ceiling marker missing")


def _verify_v8_claims(root: Path) -> None:
    policy, raw = _load_json(_V8_POLICY_PATH, root)
    if hashlib.sha256(raw).hexdigest() != _normal(_PINNED_V8_POLICY_SHA256, "V8 policy"):
        _fail("Freeze V8 policy bytes changed")
    claims = _exact_dict(policy.get("claims"), "V8 claims")
    if (
        claims.get("milestone_m0_complete") is not True
        or claims.get("production_ready") is not False
        or claims.get("live_order_authority") is not False
        or policy.get("policy_status") != "M0_COMPLETE_0_ACTIVE_FINAL_FREEZE"
    ):
        _fail("Freeze V8 M0 claims changed")
    manifest, manifest_raw = _load_json(_V8_MANIFEST_PATH, root)
    if hashlib.sha256(manifest_raw).hexdigest() != _normal(
        _PINNED_V8_MANIFEST_SHA256, "V8 manifest"
    ):
        _fail("Freeze V8 manifest bytes changed")
    if manifest.get("status") != "M0_COMPLETE_0_ACTIVE":
        _fail("Freeze V8 manifest status changed")


def _verify_policy(policy: dict[str, Any], policy_raw: bytes, root: Path) -> tuple[str, str, Mapping[str, int]]:
    policy_digest = hashlib.sha256(policy_raw).hexdigest()
    if policy_digest != _normal(_EXPECTED_POLICY_SHA256, "V9 policy"):
        _fail("V9 policy raw hash mismatch")
    semantic = _semantic_digest(policy)
    if semantic != _normal(_EXPECTED_POLICY_SEMANTIC_SHA256, "V9 semantic") or semantic != _normal(
        policy.get("semantic_sha256"), "V9 semantic field"
    ):
        _fail("V9 semantic hash mismatch")
    schema, schema_raw = _load_json(_POLICY_SCHEMA_PATH, root)
    if hashlib.sha256(schema_raw).hexdigest() != _normal(_EXPECTED_SCHEMA_SHA256, "V9 schema"):
        _fail("V9 schema raw hash mismatch")
    Draft202012Validator.check_schema(schema)
    errors = tuple(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(policy))
    if errors:
        _fail(f"V9 schema validation failed: {errors[0].message}")
    if schema.get("const") != policy:
        _fail("V9 schema const binding drifted from policy")
    if policy.get("candidate_id") != CANDIDATE_ID or policy.get("status") != CANDIDATE_STATUS:
        _fail("V9 candidate identity changed")
    if policy.get("predecessor_freeze") != "V8":
        _fail("V9 predecessor identity changed")
    contexts = tuple(_exact_str(item, "required context") for item in _exact_list(
        policy.get("required_check_contexts"), "required_check_contexts"
    ))
    if contexts != REQUIRED_CHECK_CONTEXTS:
        _fail("required-check context list changed")
    claims = _exact_dict(policy.get("claims"), "V9 claims")
    if dict(claims) != dict(_EXPECTED_CLAIMS):
        _fail("V9 claims changed")
    disposition = _exact_dict(policy.get("owner_disposition"), "owner disposition")
    body = _exact_str(disposition.get("source_body"), "owner disposition body")
    expected_body = _read_bytes(_DISPOSITION_PATH, root).decode("utf-8")
    if body != expected_body:
        _fail("owner disposition body is not the exact receipt file")
    body_raw = body.encode("utf-8")
    if hashlib.sha256(body_raw).hexdigest() != _normal(
        disposition.get("source_body_sha256"), "owner disposition hash"
    ):
        _fail("owner disposition body hash mismatch")
    if disposition.get("disposition") != (
        "APPROVED — FREEZE_V9_CI_AUTHORITY_TRANSITION; 30_MIN_PER_JOB_RETAINED"
    ):
        _fail("owner disposition token changed")
    historical = _exact_dict(policy.get("historical_pr68"), "PR #68 history")
    if historical.get("classification") != "MERGED_WITH_PROTECTED_MAIN_CI_CEILING_EXCEPTION":
        _fail("PR #68 classification changed")
    if historical.get("cancelled_protected_main_called_successful") is not False:
        _fail("cancelled protected-main runs were relabeled successful")
    pr67 = _exact_dict(policy.get("pr67_boundary"), "PR #67 boundary")
    if pr67.get("merge_authorized") is not False or pr67.get("closes_nee128") is not False:
        _fail("PR #67 merge or NEE-128 closure was authorized")
    parallel_text = _read_bytes(_PARALLEL_PATH, root).decode("utf-8")
    _verify_parallel_controls(parallel_text)
    timeouts = _verify_ceiling(
        parallel_text, _PARALLEL_PATH, expected_jobs=PARALLEL_JOBS, minutes=CEILING_MINUTES
    )
    _verify_replay_controls(_read_bytes(_REPLAY_PATH, root).decode("utf-8"))
    _verify_v8_claims(root)
    return policy_digest, semantic, timeouts


def verify_specification_freeze_v9_manifest(
    repository_root: Path | None = None,
) -> Mapping[str, str]:
    """Verify the Freeze V9 hash manifest against pinned leaves."""

    root = (repository_root or Path.cwd()).resolve(strict=True)
    manifest, _ = _load_json(_MANIFEST_PATH, root)
    if (
        tuple(manifest) != ("schema_version", "artifact_id", "status", "artifacts")
        or manifest.get("schema_version") != "qme.hash_manifest.v1"
        or manifest.get("artifact_id") != CANDIDATE_ID
        or manifest.get("status") != "CANDIDATE_CI_AUTHORITY_TRANSITION"
    ):
        _fail("Freeze V9 manifest identity changed")
    observed: dict[str, str] = {}
    for raw_row in _exact_list(manifest.get("artifacts"), "V9 manifest rows"):
        row = _exact_dict(raw_row, "V9 manifest row")
        if tuple(row) != ("path", "sha256"):
            _fail("V9 manifest row shape changed")
        member = row.get("path")
        if type(member) is not str or member in observed:
            _fail("V9 manifest path invalid or duplicated")
        value = _digest(member, root)
        if value != _normal(row.get("sha256"), member):
            _fail(f"V9 manifest leaf mismatch: {member}")
        observed[member] = value
    if tuple(observed) != _EXPECTED_MANIFEST_PATHS:
        _fail("Freeze V9 manifest membership or order changed")
    for member, expected in _EXPECTED_OWNED_NONRUNTIME_LEAVES.items():
        if observed.get(member) != _normal(expected, member):
            _fail(f"Freeze V9 full-local-repin rejected: {member}")
    if _normalized_runtime_sha(root) != _normal(
        _EXPECTED_RUNTIME_NORMALIZED_SHA256, "runtime normalized self hash"
    ):
        _fail("Freeze V9 runtime normalized self hash mismatch")
    return MappingProxyType(observed)


def verify_specification_freeze_v9(
    repository_root: Path | None = None,
) -> VerifiedSpecificationFreezeV9:
    """Verify Freeze V9 policy, pins, ceiling, and predecessor V8 claims."""

    root = (repository_root or Path.cwd()).resolve(strict=True)
    verify_specification_freeze_v9_manifest(root)
    policy, policy_raw = _load_json(_POLICY_PATH, root)
    policy_digest, semantic, timeouts = _verify_policy(policy, policy_raw, root)
    result = object.__new__(VerifiedSpecificationFreezeV9)
    object.__setattr__(result, "_status", CANDIDATE_STATUS)
    object.__setattr__(result, "_policy_sha256", _grouped(policy_digest))
    object.__setattr__(result, "_semantic_sha256", _grouped(semantic))
    object.__setattr__(result, "_required_check_contexts", REQUIRED_CHECK_CONTEXTS)
    object.__setattr__(result, "_parallel_job_timeouts", timeouts)
    object.__setattr__(result, "_claims", MappingProxyType(dict(_EXPECTED_CLAIMS)))
    return result


def serialize_verified_specification_freeze_v9(
    value: VerifiedSpecificationFreezeV9,
) -> Mapping[str, Any]:
    """Serialize a verifier-created Freeze V9 result."""

    if type(value) is not VerifiedSpecificationFreezeV9:
        _fail("verified freeze result must have exact type")
    return MappingProxyType(
        {
            "status": value.status,
            "policy_sha256": value.policy_sha256,
            "semantic_sha256": value.semantic_sha256,
            "required_check_contexts": list(value.required_check_contexts),
            "parallel_job_timeouts": dict(value.parallel_job_timeouts),
            "claims": dict(value.claims),
        }
    )


def verify_predecessor_freeze_v8(repository_root: Path | None = None) -> None:
    """Replay native Freeze V8 verification without mutating V8 bytes."""

    root = (repository_root or Path.cwd()).resolve(strict=True)
    verified = verify_specification_freeze_v8(repository_root=root)
    verify_specification_freeze_v8_manifest(repository_root=root)
    if verified.status != "M0_COMPLETE_0_ACTIVE_FINAL_FREEZE":
        _fail("native Freeze V8 status changed")
