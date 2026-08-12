"""Strict operational configuration boundary for QME v0.1."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from qme.foundation.data_root import DataRootLayout

CONFIG_SCHEMA_VERSION: Final = "qme.config.v1"
CONFIG_MAX_BYTES: Final = 65_536
CONFIG_MAX_JSON_DEPTH: Final = 8
DATA_ROOT_ENVIRONMENT_VARIABLE: Final = "QME_DATA_ROOT"
CANONICAL_DATA_SOURCE: Final = "alpha_vantage"
EXECUTION_VENUE: Final = "webull"

_CONFIG_FIELDS: Final = frozenset(
    {
        "schema_version",
        "data_root_environment_variable",
        "windows_data_root_example",
        "canonical_data_source",
        "execution_venue",
        "allow_network_in_backtest",
        "allow_unconfirmed_live_orders",
    }
)


class ConfigError(ValueError):
    """Raised when operational configuration is missing, ambiguous, or unsafe."""


def _json_depth(value: object) -> int:
    if isinstance(value, Mapping):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 1


def _parse_config_bytes(payload: bytes) -> dict[str, object]:
    if type(payload) is not bytes or not 1 <= len(payload) <= CONFIG_MAX_BYTES:
        raise ConfigError(f"configuration bytes must be from 1 through {CONFIG_MAX_BYTES}")

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ConfigError(f"configuration contains duplicate key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ConfigError(f"configuration contains non-finite JSON constant: {value}")

    try:
        document = json.loads(
            payload,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except ConfigError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigError("configuration must be UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise ConfigError("configuration must contain one JSON object")
    if _json_depth(document) > CONFIG_MAX_JSON_DEPTH:
        raise ConfigError("configuration exceeds the registered JSON depth")
    return cast(dict[str, object], document)


def _required_text(value: object, field: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigError(f"{field} must be non-empty trimmed text")
    if len(value) > maximum:
        raise ConfigError(f"{field} exceeds {maximum} characters")
    return value


def _validate_config_document(document: Mapping[str, object]) -> None:
    observed = frozenset(document)
    if observed != _CONFIG_FIELDS:
        raise ConfigError(
            "configuration fields differ: "
            f"missing={sorted(_CONFIG_FIELDS - observed)}, "
            f"extra={sorted(observed - _CONFIG_FIELDS)}"
        )
    if document["schema_version"] != CONFIG_SCHEMA_VERSION:
        raise ConfigError("unsupported configuration schema_version")
    if document["data_root_environment_variable"] != DATA_ROOT_ENVIRONMENT_VARIABLE:
        raise ConfigError("data-root environment variable is not the registered authority")
    data_root_example = _required_text(
        document["windows_data_root_example"], "windows_data_root_example"
    )
    if not Path(data_root_example).is_absolute() or data_root_example.startswith(("\\\\", "//")):
        raise ConfigError("windows_data_root_example must be one absolute local example path")
    if document["canonical_data_source"] != CANONICAL_DATA_SOURCE:
        raise ConfigError("canonical_data_source differs from the registered source")
    if document["execution_venue"] != EXECUTION_VENUE:
        raise ConfigError("execution_venue differs from the registered venue")
    for field in ("allow_network_in_backtest", "allow_unconfirmed_live_orders"):
        if type(document[field]) is not bool:
            raise ConfigError(f"{field} must be a JSON boolean")
        if document[field] is not False:
            raise ConfigError(f"{field} must remain false in qme.config.v1")


@dataclass(frozen=True, slots=True)
class QmeConfig:
    """Validated config values plus a separately resolved local data-root layout."""

    schema_version: str
    data_root_environment_variable: str
    windows_data_root_example: str
    canonical_data_source: str
    execution_venue: str
    allow_network_in_backtest: bool
    allow_unconfirmed_live_orders: bool
    config_sha256: str
    data_root: DataRootLayout

    def __post_init__(self) -> None:
        if type(self.data_root) is not DataRootLayout:
            raise ConfigError("QmeConfig requires an exact validated DataRootLayout")
        document = self.policy_document()
        _validate_config_document(document)
        if not isinstance(self.config_sha256, str) or len(self.config_sha256) != 64:
            raise ConfigError("config_sha256 must be a lowercase SHA-256 digest")
        try:
            int(self.config_sha256, 16)
        except ValueError as error:
            raise ConfigError("config_sha256 must be a lowercase SHA-256 digest") from error
        if self.config_sha256 != self.config_sha256.lower():
            raise ConfigError("config_sha256 must be a lowercase SHA-256 digest")

    def policy_document(self) -> dict[str, object]:
        """Return the reviewed config values; the example path is never a runtime default."""

        return {
            "schema_version": self.schema_version,
            "data_root_environment_variable": self.data_root_environment_variable,
            "windows_data_root_example": self.windows_data_root_example,
            "canonical_data_source": self.canonical_data_source,
            "execution_venue": self.execution_venue,
            "allow_network_in_backtest": self.allow_network_in_backtest,
            "allow_unconfirmed_live_orders": self.allow_unconfirmed_live_orders,
        }

    def manifest_document(self) -> dict[str, object]:
        """Return root-independent evidence suitable for canonical run manifests."""

        return {
            "schema_version": self.schema_version,
            "config_sha256": self.config_sha256,
            "data_root_environment_variable": self.data_root_environment_variable,
            "canonical_data_source": self.canonical_data_source,
            "execution_venue": self.execution_venue,
            "allow_network_in_backtest": self.allow_network_in_backtest,
            "allow_unconfirmed_live_orders": self.allow_unconfirmed_live_orders,
        }


def load_qme_config_bytes(
    payload: bytes,
    *,
    repository_root: Path,
    environ: Mapping[str, str] | None = None,
) -> QmeConfig:
    """Validate exact config bytes and resolve QME_DATA_ROOT without applying defaults."""

    document = _parse_config_bytes(payload)
    _validate_config_document(document)
    layout = DataRootLayout.from_environment(
        repository_root=repository_root,
        environ=os.environ if environ is None else environ,
    )
    return QmeConfig(
        schema_version=cast(str, document["schema_version"]),
        data_root_environment_variable=cast(
            str, document["data_root_environment_variable"]
        ),
        windows_data_root_example=cast(str, document["windows_data_root_example"]),
        canonical_data_source=cast(str, document["canonical_data_source"]),
        execution_venue=cast(str, document["execution_venue"]),
        allow_network_in_backtest=cast(bool, document["allow_network_in_backtest"]),
        allow_unconfirmed_live_orders=cast(
            bool, document["allow_unconfirmed_live_orders"]
        ),
        config_sha256=hashlib.sha256(payload).hexdigest(),
        data_root=layout,
    )


def _is_reparse(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _is_within(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath(
            (os.path.normcase(str(path)), os.path.normcase(str(parent)))
        ) == os.path.normcase(str(parent))
    except ValueError:
        return False


def _reject_aliased_source_path(config_path: Path, repository_root: Path) -> None:
    """Reject alternate streams and links before ``resolve`` can hide them."""

    repository_lexical = Path(os.path.abspath(repository_root))
    candidate_lexical = Path(os.path.abspath(config_path))
    if not _is_within(candidate_lexical, repository_lexical):
        raise ConfigError("configuration must be a file inside the source repository")
    relative = Path(os.path.relpath(candidate_lexical, repository_lexical))
    current = repository_lexical
    for part in relative.parts:
        if part in {"", ".", ".."} or ":" in part:
            raise ConfigError("configuration path contains an unsafe component")
        current /= part
        if current.exists() and _is_reparse(current):
            raise ConfigError("configuration path crosses a symlink or junction")


def load_qme_config(
    config_path: Path,
    *,
    repository_root: Path,
    environ: Mapping[str, str] | None = None,
) -> QmeConfig:
    """Read one repository-owned regular config file once and validate its exact bytes."""

    if not config_path.is_absolute() or not repository_root.is_absolute():
        raise ConfigError("config_path and repository_root must be absolute")
    _reject_aliased_source_path(config_path, repository_root)
    try:
        repository = repository_root.resolve(strict=True)
        candidate = config_path.resolve(strict=True)
    except OSError as error:
        raise ConfigError("configuration or repository path does not exist") from error
    if not repository.is_dir() or not _is_within(candidate, repository):
        raise ConfigError("configuration must be a file inside the source repository")
    if not candidate.is_file():
        raise ConfigError("configuration must be one regular file")
    try:
        with candidate.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if not 1 <= before.st_size <= CONFIG_MAX_BYTES:
                raise ConfigError(
                    f"configuration bytes must be from 1 through {CONFIG_MAX_BYTES}"
                )
            payload = handle.read(CONFIG_MAX_BYTES + 1)
            after = os.fstat(handle.fileno())
        current_stat = candidate.stat()
    except ConfigError:
        raise
    except OSError as error:
        raise ConfigError("configuration could not be read") from error
    signatures = {
        (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        for item in (before, after, current_stat)
    }
    if len(signatures) != 1 or len(payload) != before.st_size:
        raise ConfigError("configuration changed while it was being read")
    return load_qme_config_bytes(
        payload,
        repository_root=repository,
        environ=environ,
    )
