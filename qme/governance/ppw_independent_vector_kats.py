"""Fail-closed verifier for independent NEE-204 PPW known-answer vector classes.

This packet freezes independent numeric KATs and registers extra engineering
selector terminals.  It does not accept selection 009, flip TYPED_UNRESOLVED
labels, remove a Freeze V5 blocker, or change immutable Freeze V4 bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, NamedTuple, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped, unused-ignore]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped, unused-ignore]

CONFIG_PATH: Final = Path("configs/governance/ppw-independent-vector-kats-v1.json")
SCHEMA_PATH: Final = Path("schemas/governance/ppw-independent-vector-kats-v1.schema.json")
FIXTURE_PATH: Final = Path("tests/fixtures/governance/ppw-independent-vector-kats-v1.json")
MANIFEST_PATH: Final = Path("configs/governance/ppw-independent-vector-kats-v1.hashes.json")

EXPECTED_CONFIG_SHA256: Final = "7d94db3d:1c9a2c1a:7812166c:86b5294c:7cad25e1:f9666cc5:19490317:c10fb33f"
EXPECTED_SCHEMA_SHA256: Final = "246b8640:c6ab8083:f64c6dc3:7dc2b341:166755e0:040aa3eb:a81b2133:df85b71e"
EXPECTED_FIXTURE_SHA256: Final = "1b589786:32d1d06e:ca4284c5:65e6ecf6:899f631e:dea0c108:84081a15:795ce3bd"
EXPECTED_SEMANTIC_SHA256: Final = "810dc631:7cf49043:ebf02f43:3a20b0f1:d8b7b9f8:f13a7389:f951fa24:6eb4cbf3"

_RUNTIME_NORMALIZED_DIGEST_ZERO: Final = "00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000"
EXPECTED_RUNTIME_NORMALIZED_SHA256: Final = "1239271d:df509ced:e0389a9e:192481b4:79ecb566:5dc6c4af:1be6a58f:2e279b6d"

_MAX_BYTES: Final = 2 * 1024 * 1024
_PATH_TYPE: Final = type(Path())
_DIGEST_RE: Final = re.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}\Z", re.ASCII)

_IDENTITY: Final = MappingProxyType(
    {
        "$schema": "../../schemas/governance/ppw-independent-vector-kats-v1.schema.json",
        "schema_version": "qme.ppw_independent_vector_kats.v1",
        "artifact_id": "QME-PPW-INDEPENDENT-VECTOR-KATS-V1",
        "ticket_id": "NEE-204",
        "status": (
            "INDEPENDENT_NUMERIC_KATS_REGISTERED_SELECTION_009_UNACCEPTED_"
            "TYPED_UNRESOLVED_LABELS_UNCHANGED"
        ),
    }
)

_SOURCE_EQUATIONS: Final = Path("tests/fixtures/governance/ppw-bootstrap-source-equations-v1.json")
_SOURCE_EQUATIONS_SHA: Final = "2ef0e4c9:a760cb2c:c1627c99:b3887068:a3c15aa3:555f2b0a:c4a3c477:87853820"
_AUTHORITY_PATH: Final = Path("configs/governance/ppw-bootstrap-uncertainty-authority-v1.json")
_AUTHORITY_SHA: Final = "71b22f95:fdf223ba:4ebb0e9e:ee047fd2:61bcb866:0692d8f3:b179ca48:cb8f09d1"
_SELECTIONS_PATH: Final = Path("configs/governance/ppw-bootstrap-owner-selections-v1.json")
_SELECTIONS_SHA: Final = "6b1434a1:cc4b57c8:f221512a:7e2dcfd8:317fb037:1fb955f7:6e2f73d6:8cb5c3b6"
_FREEZE_V4_PATH: Final = Path("configs/governance/specification-freeze-policy-v4.json")
_FREEZE_V4_SHA: Final = "adf2288b:32532669:cdd7fa9d:4876132b:222916d2:c754f006:6003a6cd:1a4fb458"
_FREEZE_V4_MANIFEST: Final = Path("configs/governance/specification-freeze-v4.hashes.json")
_FREEZE_V4_MANIFEST_SHA: Final = "a2c3bbfa:d15e7bd3:769142ad:69c291e7:885cd14d:6ca2d939:99c39df2:5360ea42"
_FREEZE_V5_PATH: Final = Path("configs/governance/specification-freeze-policy-v5.json")
_FREEZE_V5_SHA: Final = "054270b6:d749e82e:38c9cd24:cba93a24:b56ec676:feed22cf:d9b6a211:cf37c840"
_FREEZE_V5_MANIFEST: Final = Path("configs/governance/specification-freeze-v5.hashes.json")
_FREEZE_V5_MANIFEST_SHA: Final = "2eb7a5bd:b6117b71:b0b77836:eca6548a:3609141d:9db2c817:2c2f22b5:0489e548"
_CORRECTION_COMMENT_ID: Final = "40b5e5c2-0908-4be8-b23a-2edd4ed9be6e"
_CORRECTION_BODY_SHA: Final = "6329e0be:18eb222d:27be20c9:74205e3e:f2b9dd00:8964441a:b1b961c1:b56d77fb"
_IMPLEMENTATION_PATH: Final = Path("qme/stats/effective_trials_uncertainty.py")
_IMPLEMENTATION_SHA: Final = "209a9289:0fdcb191:9eddb077:93ee75e6:258b10af:2f6c5042:31a55874:d33c9f7a"

_TYPED_UNRESOLVED_LABELS: Final = (
    "IID_SERIES_TYPED_UNRESOLVED",
    "NEGATIVE_CORRELATION_TYPED_UNRESOLVED",
    "SHORT_SERIES_TYPED_UNRESOLVED",
    "NINETY_SIX_COLUMN_AGGREGATION_TYPED_UNRESOLVED",
)

_SELECTION_004: Final = (
    "PPW_NONFINITE_INPUT",
    "PPW_SERIES_TOO_SHORT",
    "PPW_CONSTANT_COLUMN",
    "PPW_DEGENERATE_DENOMINATOR",
    "PPW_NONPOSITIVE_BLOCK_LENGTH",
)

_ENGINEERING_TERMINALS: Final = (
    "PPW_INVALID_MATRIX_SHAPE",
    "PPW_INVALID_CANONICAL_DECIMAL",
    "PPW_DECIMAL_ARITHMETIC_FAILURE",
)

_VECTOR_CLASSES: Final = (
    "short",
    "constant",
    "IID",
    "negatively_correlated",
    "zero_negative_intermediate",
    "floor",
    "cap",
    "integer_boundary",
    "96_column_aggregation",
)

_CASE_IDS: Final = (
    "SHORT",
    "CONSTANT",
    "IID",
    "NEGATIVELY_CORRELATED",
    "ZERO_NEGATIVE_INTERMEDIATE",
    "FLOOR",
    "CAP",
    "INTEGER_BOUNDARY",
    "NINETY_SIX_COLUMN_AGGREGATION",
    "ENGINEERING_INVALID_MATRIX_SHAPE",
    "ENGINEERING_INVALID_CANONICAL_DECIMAL",
    "SELECTION_004_NONFINITE",
)

_EXPECTED_BLOCKER_CODES: Final = (
    "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL",
    "NEE-116-ASYMMETRIC-COST-METHOD",
    "NEE-116-CAPACITY-SOLVER",
    "NEE-116-CORPORATE-ACTION-EDGE-CASES",
    "NEE-116-PRODUCTION-PIT-DATA",
    "NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE",
    "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP",
    "NEE-119-AV-PROXY-EVIDENCE",
    "NEE-121-CALENDAR-SESSION-REGISTRATION",
    "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP",
    "NEE-122-CORRELATED-TRIAL-FIXTURE",
    "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE",
)

_EXPECTED_CLAIMS: Final = MappingProxyType(
    {
        "independent_numeric_kats_registered": True,
        "engineering_selector_terminals_registered": True,
        "selection_009_accepted": False,
        "typed_unresolved_labels_changed": False,
        "ppw_selector_executable_in_this_artifact": False,
        "bootstrap_distribution_available": False,
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
)

_EXPECTED_NONCLAIMS: Final = (
    "NO_SELECTION_009_ACCEPTANCE",
    "NO_TYPED_UNRESOLVED_LABEL_FLIP",
    "NO_DSR_OR_HOLM_MULTIPLICITY_OUTPUT",
    "NO_EMPIRICAL_OR_PRODUCTION_OUTPUT",
    "NO_FREEZE_V5_BLOCKER_REMOVAL",
    "NO_FINAL_FREEZE_ALPHA_PRODUCTION_READINESS_M0_OR_LIVE_ORDER_CLAIM",
)

_PROJECTION_DIGESTS: Final = MappingProxyType(
    {
        "authority": "c07b8040:da4e12fe:9fa50810:d3013abb:97056417:b4f5243b:a2b5cb04:12dff951",
        "typed_unresolved_labels_unchanged": "70ef2127:c1ea6ea3:feb02457:e27998a6:33fa54aa:8483def3:7b8590e3:bcdf4a12",
        "selection_004_terminals": "0df071ac:950a041a:5edcb102:76d0503d:b1be9fb4:b563ac30:5f317485:83001755",
        "engineering_selector_terminals": "bf2cd006:864502c9:b5561a8d:7e13d8b5:45d2898d:4601a363:98ff36bb:676f3da4",
        "registered_lag_selection_terminal": "8ada1c86:e429720d:49a75c7f:3fa98081:a5e95397:2743801a:67ce985d:f32ce4d9",
        "kat_fixture": "43a0df1e:00c1ee6e:9381403c:26b760ba:53a07366:81afa1fd:4cffcbaf:0379e929",
        "vector_classes": "e04eec3d:e55dd330:03ec3070:53f8f674:1af59405:dcb0c9f1:9120a2e1:9f2795f1",
        "active_freeze_v5_blockers": "718526cb:e91dcf4d:9c079fa6:6712500c:00cff515:feb9e201:db25c814:332df7be",
        "claims": "70028ca2:d2240dd8:0d8b3003:50a1b71e:03f653e6:48ccdc0c:b72adea3:14d3c1e6",
        "nonclaims": "cdc912ef:a951e204:180ac306:80b59e09:7d4bac88:ae771539:34735dbe:9ed838bd",
        "schema": "eb215095:eca781d3:789a627b:72000d9c:7e66074f:672c07a9:1f5eb0c4:09e66b93",
        "fixture": "1b589786:32d1d06e:ca4284c5:65e6ecf6:899f631e:dea0c108:84081a15:795ce3bd"
    }
)

_MANIFEST_PATHS: Final = (
    "configs/governance/ppw-independent-vector-kats-v1.json",
    "docs/governance/PPW_INDEPENDENT_VECTOR_KATS_V1.md",
    "qme/governance/ppw_independent_vector_kats.py",
    "schemas/governance/ppw-independent-vector-kats-v1.schema.json",
    "tests/fixtures/governance/ppw-independent-vector-kats-v1.json",
    "tests/governance/test_ppw_independent_vector_kats.py",
)

_EXPECTED_MANIFEST_DIGESTS: Final = MappingProxyType(
    {
        "configs/governance/ppw-independent-vector-kats-v1.json": EXPECTED_CONFIG_SHA256,
        "docs/governance/PPW_INDEPENDENT_VECTOR_KATS_V1.md": "332b6430:f83b854a:7bdb485c:5402b6f7:d6a1eac8:c5a23696:b81d6ae5:08ab2e4a",
        "schemas/governance/ppw-independent-vector-kats-v1.schema.json": EXPECTED_SCHEMA_SHA256,
        "tests/fixtures/governance/ppw-independent-vector-kats-v1.json": EXPECTED_FIXTURE_SHA256,
        "tests/governance/test_ppw_independent_vector_kats.py": "1fcb41ef:31333e34:bbb9c57a:e44a0b34:f8fb16f9:56112a3c:cc7bc279:65bcf9cc",
    }
)


class IndependentVectorKatError(RuntimeError):
    """Raised when the independent PPW KAT packet fails closed."""


class VerifiedIndependentVectorKats(NamedTuple):
    """Immutable projection; authoritative serialization always replays artifacts."""

    config_sha256: str
    semantic_sha256: str
    case_ids: tuple[str, ...]
    engineering_terminals: tuple[str, ...]
    selection_009_accepted: bool
    freeze_blocker_changed: bool
    status: str


def _grouped(raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise IndependentVectorKatError(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise IndependentVectorKatError(f"NONFINITE_JSON_CONSTANT:{value}")


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _projection_digest(value: object) -> str:
    return _grouped(_canonical(value))


def _path_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        getattr(info, "st_file_attributes", 0),
    )


def _confined_bytes(
    root: Path,
    relative: Path,
    *,
    _interleave_hook: Callable[[Path, Path], None] | None = None,
) -> bytes:
    if type(root) is not _PATH_TYPE or type(relative) is not _PATH_TYPE:
        raise IndependentVectorKatError("INVALID_PATH_TYPE")
    if relative.is_absolute() or ".." in relative.parts:
        raise IndependentVectorKatError("PATH_OUTSIDE_REPOSITORY")
    resolved_root = root.resolve(strict=True)
    target = resolved_root / relative
    snapshots: list[tuple[Path, tuple[int, int, int, int, int]]] = [
        (resolved_root, _path_identity(resolved_root.lstat()))
    ]
    cursor = resolved_root
    for index, part in enumerate(relative.parts):
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise IndependentVectorKatError("PATH_MISSING") from exc
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400):
            raise IndependentVectorKatError("LINK_OR_REPARSE_PATH_REJECTED")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise IndependentVectorKatError("ANCESTOR_NOT_DIRECTORY")
        snapshots.append((cursor, _path_identity(info)))
    try:
        resolved = target.resolve(strict=True)
        relative_after_resolve = resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise IndependentVectorKatError("PATH_OUTSIDE_REPOSITORY") from exc
    if relative_after_resolve != relative:
        raise IndependentVectorKatError("NONCANONICAL_PATH")
    if _interleave_hook is not None:
        _interleave_hook(resolved_root, target)
    before = resolved.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise IndependentVectorKatError("NONREGULAR_OR_HARDLINK_FILE")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(resolved, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise IndependentVectorKatError("FILE_CHANGED_BEFORE_OPEN")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, _MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_BYTES:
                raise IndependentVectorKatError("ARTIFACT_TOO_LARGE")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    for component, expected_identity in snapshots:
        try:
            component_info = component.lstat()
        except OSError as exc:
            raise IndependentVectorKatError("PATH_CHANGED_DURING_READ") from exc
        attributes = getattr(component_info, "st_file_attributes", 0)
        if (
            stat.S_ISLNK(component_info.st_mode)
            or bool(attributes & 0x400)
            or _path_identity(component_info) != expected_identity
        ):
            raise IndependentVectorKatError("PATH_CHANGED_DURING_READ")
    try:
        final_resolved = target.resolve(strict=True)
        final = final_resolved.stat()
    except OSError as exc:
        raise IndependentVectorKatError("PATH_CHANGED_DURING_READ") from exc
    if (
        final_resolved != resolved
        or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
    ):
        raise IndependentVectorKatError("FILE_CHANGED_DURING_READ")
    return b"".join(chunks)


def _load(root: Path, relative: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _confined_bytes(root, relative).decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IndependentVectorKatError(f"INVALID_JSON:{relative.as_posix()}") from exc
    if type(value) is not dict:
        raise IndependentVectorKatError(f"JSON_ROOT_NOT_OBJECT:{relative.as_posix()}")
    return cast(dict[str, Any], value)


def _normal(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise IndependentVectorKatError(f"INVALID_GROUPED_SHA256:{label}")
    return value


def _semantic(document: Mapping[str, Any]) -> str:
    projection = dict(document)
    projection.pop("semantic_sha256", None)
    return _grouped(_canonical(projection))


def _lcg(count: int, a: int, c: int, modulus: int, seed: int) -> list[int]:
    state = seed
    values: list[int] = []
    for _ in range(count):
        state = (a * state + c) % modulus
        values.append(state - modulus // 2)
    return values


def reconstruct_matrix(construction: Mapping[str, Any]) -> list[list[str]]:
    """Rebuild the exact canonical string matrix for one versioned KAT."""

    kind = construction.get("kind")
    if kind == "constant_fill":
        rows = construction["rows"]
        columns = construction["columns"]
        value = construction["value"]
        if type(rows) is not int or type(columns) is not int or type(value) is not str:
            raise IndependentVectorKatError("INVALID_CONSTANT_FILL")
        return [[value] * columns for _ in range(rows)]
    if kind == "lcg_repeated_column":
        repeated = [
            str(value)
            for value in _lcg(
                int(construction["n"]),
                int(construction["a"]),
                int(construction["c"]),
                int(construction["m"]),
                int(construction["x0"]),
            )
        ]
        columns = int(construction["columns"])
        return [[value] * columns for value in repeated]
    if kind == "alternating_sign":
        count = int(construction["n"])
        columns = int(construction["columns"])
        positive_first = construction.get("positive_first") is True
        alternating = [
            "1" if ((index % 2 == 0) == positive_first) else "-1" for index in range(count)
        ]
        return [[value] * columns for value in alternating]
    if kind == "integer_ar":
        count = int(construction["n"])
        numerator = int(construction["phi_numerator"])
        denominator = int(construction["phi_denominator"])
        mul = int(construction["innovation_mul"])
        add = int(construction["innovation_add"])
        modulus = int(construction["innovation_mod"])
        shift = int(construction["innovation_shift"])
        columns = int(construction["columns"])
        state = 0
        autoregressive: list[str] = []
        for index in range(count):
            innovation = ((index * mul + add) % modulus) - shift
            state = (numerator * state) // denominator + innovation
            autoregressive.append(str(state))
        return [[value] * columns for value in autoregressive]
    if kind == "lcg_per_column":
        count = int(construction["n"])
        columns = int(construction["columns"])
        start = int(construction["x0_start"])
        column_series = [
            _lcg(
                count,
                int(construction["a"]),
                int(construction["c"]),
                int(construction["m"]),
                start + column,
            )
            for column in range(columns)
        ]
        return [
            [str(column_series[column][row]) for column in range(columns)]
            for row in range(count)
        ]
    if kind == "nonfinite_first_cell":
        rows = int(construction["rows"])
        columns = int(construction["columns"])
        fill = str(construction["fill"])
        value = str(construction["value"])
        matrix = [[fill] * columns for _ in range(rows)]
        matrix[0][0] = value
        return matrix
    raise IndependentVectorKatError(f"UNKNOWN_CONSTRUCTION:{kind}")


def _verify_fixture_constructions(fixture: dict[str, Any]) -> None:
    cases = fixture.get("cases")
    if type(cases) is not list or tuple(
        row.get("case_id") if type(row) is dict else None for row in cases
    ) != _CASE_IDS:
        raise IndependentVectorKatError("FIXTURE_CASE_INVENTORY_MISMATCH")
    if fixture.get("selection_009_accepted") is not False:
        raise IndependentVectorKatError("FIXTURE_SELECTION_009_ACCEPTED")
    if tuple(fixture.get("authority_labels_remain_typed_unresolved") or ()) != _TYPED_UNRESOLVED_LABELS:
        raise IndependentVectorKatError("FIXTURE_TYPED_UNRESOLVED_LABELS_CHANGED")
    for row in cases:
        if type(row) is not dict:
            raise IndependentVectorKatError("FIXTURE_CASE_NOT_OBJECT")
        construction = row.get("construction")
        if type(construction) is not dict:
            raise IndependentVectorKatError("FIXTURE_CONSTRUCTION_INVALID")
        matrix = reconstruct_matrix(construction)
        if _grouped(_canonical(matrix)) != _normal(row.get("matrix_sha256"), "matrix"):
            raise IndependentVectorKatError(f"MATRIX_DIGEST_MISMATCH:{row.get('case_id')}")


def _verify_schema_and_projections(config: dict[str, Any], schema: dict[str, Any]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(config))
    except SchemaError as exc:
        raise IndependentVectorKatError("SCHEMA_INVALID") from exc
    if errors:
        raise IndependentVectorKatError("CONFIG_SCHEMA_INVALID")
    for key, expected in _PROJECTION_DIGESTS.items():
        if key == "schema":
            actual = _projection_digest(schema)
        elif key == "fixture":
            continue
        else:
            actual = _projection_digest(config[key])
        if actual != expected:
            raise IndependentVectorKatError(f"PROJECTION_DIGEST_MISMATCH:{key}")


def _verify_authority_bindings(config: dict[str, Any], root: Path) -> None:
    authority = config["authority"]
    if _grouped(_confined_bytes(root, _AUTHORITY_PATH)) != _AUTHORITY_SHA:
        raise IndependentVectorKatError("PREDECESSOR_AUTHORITY_CHANGED")
    if _grouped(_confined_bytes(root, _SELECTIONS_PATH)) != _SELECTIONS_SHA:
        raise IndependentVectorKatError("PREDECESSOR_SELECTIONS_CHANGED")
    if _grouped(_confined_bytes(root, _SOURCE_EQUATIONS)) != _SOURCE_EQUATIONS_SHA:
        raise IndependentVectorKatError("SOURCE_EQUATIONS_CHANGED")
    if _grouped(_confined_bytes(root, _FREEZE_V4_PATH)) != _FREEZE_V4_SHA:
        raise IndependentVectorKatError("FREEZE_V4_POLICY_CHANGED")
    if _grouped(_confined_bytes(root, _FREEZE_V4_MANIFEST)) != _FREEZE_V4_MANIFEST_SHA:
        raise IndependentVectorKatError("FREEZE_V4_MANIFEST_CHANGED")
    if _grouped(_confined_bytes(root, _FREEZE_V5_PATH)) != _FREEZE_V5_SHA:
        raise IndependentVectorKatError("FREEZE_V5_POLICY_CHANGED")
    if _grouped(_confined_bytes(root, _FREEZE_V5_MANIFEST)) != _FREEZE_V5_MANIFEST_SHA:
        raise IndependentVectorKatError("FREEZE_V5_MANIFEST_CHANGED")
    if _grouped(_confined_bytes(root, _IMPLEMENTATION_PATH)) != _IMPLEMENTATION_SHA:
        raise IndependentVectorKatError("IMPLEMENTATION_BYTES_CHANGED")
    freeze_v4 = authority["freeze_v4"]
    if (
        freeze_v4["sha256"] != _FREEZE_V4_SHA
        or freeze_v4["manifest_sha256"] != _FREEZE_V4_MANIFEST_SHA
        or freeze_v4["active_blocker_count"] != 13
    ):
        raise IndependentVectorKatError("FREEZE_V4_BINDING_MISMATCH")
    freeze_v5 = authority["freeze_v5"]
    if (
        freeze_v5["sha256"] != _FREEZE_V5_SHA
        or freeze_v5["manifest_sha256"] != _FREEZE_V5_MANIFEST_SHA
        or freeze_v5["active_blocker_count"] != 12
        or freeze_v5["resolved_since_v4"]
        != ["NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE"]
    ):
        raise IndependentVectorKatError("FREEZE_V5_BINDING_MISMATCH")
    correction = authority["coordinator_correction"]
    correction_body = correction.get("source_body")
    if type(correction_body) is not str:
        raise IndependentVectorKatError("CORRECTION_BODY_INVALID")
    correction_raw = correction_body.encode("utf-8")
    if (
        correction.get("source_comment_id") != _CORRECTION_COMMENT_ID
        or correction.get("source_body_bytes") != len(correction_raw)
        or correction.get("source_body_sha256") != _CORRECTION_BODY_SHA
        or _grouped(correction_raw) != _CORRECTION_BODY_SHA
        or correction.get("authorized_next_implementation")
        != "VERSIONED_INDEPENDENT_KAT_CLASSES_AND_EXTRA_TERMINAL_DISPOSITION"
        or correction.get("successor_freeze_authorized") is not False
    ):
        raise IndependentVectorKatError("COORDINATOR_CORRECTION_MISMATCH")
    freeze_v4_document = _load(root, _FREEZE_V4_PATH)
    freeze_v5_document = _load(root, _FREEZE_V5_PATH)
    freeze_v4_rows = freeze_v4_document.get("unresolved_blockers")
    freeze_v5_rows = freeze_v5_document.get("unresolved_blockers")
    if type(freeze_v4_rows) is not list or type(freeze_v5_rows) is not list:
        raise IndependentVectorKatError("FREEZE_BLOCKER_ROWS_INVALID")
    expected_v5_rows = [
        row
        for row in freeze_v4_rows
        if type(row) is dict
        and row.get("blocker_code") != "NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE"
    ]
    if freeze_v5_rows != expected_v5_rows:
        raise IndependentVectorKatError("FREEZE_V5_TRANSITION_MISMATCH")
    if config["active_freeze_v5_blockers"] != freeze_v5_rows:
        raise IndependentVectorKatError("ACTIVE_FREEZE_V5_ROWS_MISMATCH")
    if authority.get("empirical_results_used") is not False:
        raise IndependentVectorKatError("EMPIRICAL_RESULTS_USED")


def _verify_typed_unresolved(config: dict[str, Any], root: Path) -> None:
    source = _load(root, _SOURCE_EQUATIONS)
    future = source.get("future_numeric_cases")
    if type(future) is not list:
        raise IndependentVectorKatError("SOURCE_FUTURE_CASES_MISSING")
    labels = {
        row.get("case_id"): row.get("expected_status")
        for row in future
        if type(row) is dict
    }
    for label in _TYPED_UNRESOLVED_LABELS:
        if labels.get(label) != "NO_EXECUTABLE_EXPECTATION_REGISTERED":
            raise IndependentVectorKatError(f"TYPED_UNRESOLVED_LABEL_FLIPPED:{label}")
    locked = config["typed_unresolved_labels_unchanged"]
    if tuple(locked["labels"]) != _TYPED_UNRESOLVED_LABELS:
        raise IndependentVectorKatError("LOCKED_LABEL_INVENTORY_MISMATCH")
    if locked["selection_009_accepted"] is not False:
        raise IndependentVectorKatError("SELECTION_009_ACCEPTED")


def _verify_terminals(config: dict[str, Any]) -> None:
    codes = tuple(row["code"] for row in config["selection_004_terminals"])
    if codes != _SELECTION_004:
        raise IndependentVectorKatError("SELECTION_004_TERMINALS_CHANGED")
    extra = tuple(row["code"] for row in config["engineering_selector_terminals"])
    if extra != _ENGINEERING_TERMINALS:
        raise IndependentVectorKatError("ENGINEERING_TERMINALS_CHANGED")
    if config["registered_lag_selection_terminal"]["code"] != "PPW_NO_INSIGNIFICANT_RUN":
        raise IndependentVectorKatError("LAG_SELECTION_TERMINAL_CHANGED")
    if tuple(config["vector_classes"]) != _VECTOR_CLASSES:
        raise IndependentVectorKatError("VECTOR_CLASS_INVENTORY_MISMATCH")
    if config["claims"] != dict(_EXPECTED_CLAIMS):
        raise IndependentVectorKatError("CLAIMS_MISMATCH")
    if tuple(config["nonclaims"]) != _EXPECTED_NONCLAIMS:
        raise IndependentVectorKatError("NONCLAIMS_MISMATCH")
    blockers = tuple(row["blocker_code"] for row in config["active_freeze_v5_blockers"])
    if blockers != _EXPECTED_BLOCKER_CODES:
        raise IndependentVectorKatError("BLOCKER_CODES_CHANGED")


def _verify_repository_state(root_value: str | Path) -> VerifiedIndependentVectorKats:
    root = Path(root_value)
    raw_config = _confined_bytes(root, CONFIG_PATH)
    raw_schema = _confined_bytes(root, SCHEMA_PATH)
    raw_fixture = _confined_bytes(root, FIXTURE_PATH)
    if _grouped(raw_config) != EXPECTED_CONFIG_SHA256:
        raise IndependentVectorKatError("CONFIG_DIGEST_MISMATCH")
    if _grouped(raw_schema) != EXPECTED_SCHEMA_SHA256:
        raise IndependentVectorKatError("SCHEMA_DIGEST_MISMATCH")
    if _grouped(raw_fixture) != EXPECTED_FIXTURE_SHA256:
        raise IndependentVectorKatError("FIXTURE_DIGEST_MISMATCH")
    config = _load(root, CONFIG_PATH)
    schema = _load(root, SCHEMA_PATH)
    fixture = _load(root, FIXTURE_PATH)
    if any(config.get(key) != value for key, value in _IDENTITY.items()):
        raise IndependentVectorKatError("CONFIG_IDENTITY_MISMATCH")
    semantic = _semantic(config)
    if config.get("semantic_sha256") != semantic or semantic != EXPECTED_SEMANTIC_SHA256:
        raise IndependentVectorKatError("SEMANTIC_DIGEST_MISMATCH")
    if config["kat_fixture"]["sha256"] != EXPECTED_FIXTURE_SHA256:
        raise IndependentVectorKatError("CONFIG_FIXTURE_PIN_MISMATCH")
    _verify_schema_and_projections(config, schema)
    _verify_authority_bindings(config, root)
    _verify_typed_unresolved(config, root)
    _verify_terminals(config)
    _verify_fixture_constructions(fixture)
    return VerifiedIndependentVectorKats(
        config_sha256=_grouped(raw_config),
        semantic_sha256=semantic,
        case_ids=_CASE_IDS,
        engineering_terminals=_ENGINEERING_TERMINALS,
        selection_009_accepted=False,
        freeze_blocker_changed=False,
        status=cast(str, config["status"]),
    )


def _project(state: VerifiedIndependentVectorKats) -> dict[str, object]:
    return {
        "config_sha256": tuple.__getitem__(state, 0),
        "semantic_sha256": tuple.__getitem__(state, 1),
        "case_ids": list(cast(tuple[str, ...], tuple.__getitem__(state, 2))),
        "engineering_terminals": list(cast(tuple[str, ...], tuple.__getitem__(state, 3))),
        "selection_009_accepted": tuple.__getitem__(state, 4),
        "freeze_blocker_changed": tuple.__getitem__(state, 5),
        "status": tuple.__getitem__(state, 6),
    }


def _snapshot_global_graph(
    roots: tuple[Callable[..., object], ...],
) -> tuple[dict[str, Any], Mapping[str, object]]:
    namespace = globals()
    expected: dict[str, object] = {}
    pending = list(roots)
    visited: set[int] = set()
    function_type = type(roots[0])
    while pending:
        function = pending.pop()
        if id(function) in visited:
            continue
        visited.add(id(function))
        for name in function.__code__.co_names:
            if name not in namespace:
                continue
            value = namespace[name]
            expected[name] = value
            if type(value) is function_type and getattr(value, "__module__", None) == __name__:
                pending.append(value)
    return namespace, MappingProxyType(expected)


def _audit_global_graph(
    namespace: Mapping[str, object],
    expected: Mapping[str, object],
    error_type: type[IndependentVectorKatError],
) -> None:
    if any(name not in namespace or namespace[name] is not value for name, value in expected.items()):
        raise error_type("AUTHORITATIVE_GLOBAL_DEPENDENCY_CHANGED")


def _make_public_verifier(
    implementation: Callable[[str | Path], VerifiedIndependentVectorKats],
    namespace: Mapping[str, object],
    snapshot: Mapping[str, object],
    audit: Callable[
        [Mapping[str, object], Mapping[str, object], type[IndependentVectorKatError]],
        None,
    ],
    error_type: type[IndependentVectorKatError],
) -> Callable[[str | Path], VerifiedIndependentVectorKats]:
    def verify(repository_root: str | Path) -> VerifiedIndependentVectorKats:
        audit(namespace, snapshot, error_type)
        result = implementation(repository_root)
        audit(namespace, snapshot, error_type)
        return result

    return verify


def _make_serializer(
    verifier: Callable[[str | Path], VerifiedIndependentVectorKats],
    projector: Callable[[VerifiedIndependentVectorKats], dict[str, object]],
    result_type: type[VerifiedIndependentVectorKats],
    canonicalizer: Callable[[object], bytes],
    error_type: type[IndependentVectorKatError],
) -> Callable[[object, str | Path], bytes]:
    def serialize(value: object, repository_root: str | Path) -> bytes:
        authoritative = verifier(repository_root)
        if type(value) is not result_type or tuple(value) != tuple(authoritative):
            raise error_type("SUPPLIED_RESULT_DIFFERS_FROM_REPOSITORY_REPLAY")
        return canonicalizer(projector(authoritative))

    return serialize


_serializer_namespace, _serializer_snapshot = _snapshot_global_graph(
    (_verify_repository_state, _project, _canonical)
)
verify_ppw_independent_vector_kats = _make_public_verifier(
    _verify_repository_state,
    _serializer_namespace,
    _serializer_snapshot,
    _audit_global_graph,
    IndependentVectorKatError,
)
serialize_verified_ppw_independent_vector_kats = _make_serializer(
    verify_ppw_independent_vector_kats,
    _project,
    VerifiedIndependentVectorKats,
    _canonical,
    IndependentVectorKatError,
)
del _serializer_namespace
del _serializer_snapshot
del _make_public_verifier
del _make_serializer


def _verify_manifest_impl(repository_root: str | Path) -> None:
    root = Path(repository_root)
    manifest = _load(root, MANIFEST_PATH)
    if set(manifest) != {
        "schema_version",
        "artifact_id",
        "ticket_id",
        "status",
        "artifacts",
        "limitations",
    }:
        raise IndependentVectorKatError("MANIFEST_ROOT_INVENTORY_MISMATCH")
    if {
        key: manifest.get(key)
        for key in ("schema_version", "artifact_id", "ticket_id", "status")
    } != {
        "schema_version": "qme.ppw_independent_vector_kats_manifest.v1",
        "artifact_id": "QME-PPW-INDEPENDENT-VECTOR-KATS-V1",
        "ticket_id": "NEE-204",
        "status": "INDEPENDENT_NUMERIC_KATS_REGISTERED_SELECTION_009_UNACCEPTED_ZERO_BLOCKERS_RESOLVED",
    }:
        raise IndependentVectorKatError("MANIFEST_IDENTITY_MISMATCH")
    rows = manifest.get("artifacts")
    if type(rows) is not list or tuple(
        row.get("path") if type(row) is dict else None for row in rows
    ) != _MANIFEST_PATHS:
        raise IndependentVectorKatError("MANIFEST_PATH_INVENTORY_MISMATCH")
    for row in rows:
        if type(row) is not dict or set(row) != {"path", "sha256"}:
            raise IndependentVectorKatError("MANIFEST_ROW_SHAPE_MISMATCH")
        path_text = row["path"]
        digest = row["sha256"]
        if type(path_text) is not str or type(digest) is not str:
            raise IndependentVectorKatError("MANIFEST_ROW_TYPE_MISMATCH")
        raw = _confined_bytes(root, Path(path_text))
        if _normal(digest, path_text) != _grouped(raw):
            raise IndependentVectorKatError(f"MANIFEST_DIGEST_MISMATCH:{path_text}")
        if path_text == "qme/governance/ppw_independent_vector_kats.py":
            marker = EXPECTED_RUNTIME_NORMALIZED_SHA256.encode("ascii")
            if raw.count(marker) != 1:
                raise IndependentVectorKatError("RUNTIME_NORMALIZED_MARKER_MISMATCH")
            normalized = raw.replace(marker, _RUNTIME_NORMALIZED_DIGEST_ZERO.encode("ascii"), 1)
            if _grouped(normalized) != EXPECTED_RUNTIME_NORMALIZED_SHA256:
                raise IndependentVectorKatError("RUNTIME_NORMALIZED_DIGEST_MISMATCH")
        elif _EXPECTED_MANIFEST_DIGESTS.get(path_text) != digest:
            raise IndependentVectorKatError(f"MANIFEST_INDEPENDENT_PIN_MISMATCH:{path_text}")
    if manifest.get("limitations") != [
        "NO_SELECTION_009_ACCEPTANCE",
        "NO_TYPED_UNRESOLVED_LABEL_FLIP",
        "NO_DSR_HOLM_EMPIRICAL_OR_PRODUCTION_OUTPUT",
        "NO_FREEZE_V5_BLOCKER_REMOVAL",
        "NO_FINAL_FREEZE_M0_ALPHA_READINESS_OR_LIVE_ORDER_AUTHORITY",
    ]:
        raise IndependentVectorKatError("MANIFEST_LIMITATIONS_MISMATCH")


_manifest_namespace, _manifest_snapshot = _snapshot_global_graph((_verify_manifest_impl,))


def _make_manifest_verifier(
    implementation: Callable[[str | Path], None],
    namespace: Mapping[str, object],
    snapshot: Mapping[str, object],
    audit: Callable[
        [Mapping[str, object], Mapping[str, object], type[IndependentVectorKatError]],
        None,
    ],
    error_type: type[IndependentVectorKatError],
) -> Callable[[str | Path], None]:
    def verify(repository_root: str | Path) -> None:
        audit(namespace, snapshot, error_type)
        implementation(repository_root)
        audit(namespace, snapshot, error_type)

    return verify


verify_ppw_independent_vector_kats_manifest = _make_manifest_verifier(
    _verify_manifest_impl,
    _manifest_namespace,
    _manifest_snapshot,
    _audit_global_graph,
    IndependentVectorKatError,
)
del _manifest_namespace
del _manifest_snapshot
del _make_manifest_verifier

__all__ = [
    "IndependentVectorKatError",
    "VerifiedIndependentVectorKats",
    "reconstruct_matrix",
    "serialize_verified_ppw_independent_vector_kats",
    "verify_ppw_independent_vector_kats",
    "verify_ppw_independent_vector_kats_manifest",
]
