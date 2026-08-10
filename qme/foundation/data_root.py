"""Strict contract for QME data stored outside the source repository."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class DataRootError(ValueError):
    """Raised when a configured data root violates the storage boundary."""


def _casefolded(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _is_within(path: Path, parent: Path) -> bool:
    candidate = _casefolded(path)
    boundary = _casefolded(parent)
    try:
        return os.path.commonpath((candidate, boundary)) == boundary
    except ValueError:
        return False


def _reject_reparse_components(path: Path) -> None:
    current = path
    while current != current.parent:
        if current.exists():
            is_junction = getattr(current, "is_junction", lambda: False)
            if current.is_symlink() or bool(is_junction()):
                raise DataRootError(f"data root crosses a symlink or junction: {current}")
        current = current.parent


@dataclass(frozen=True, slots=True)
class DataRootLayout:
    """Resolved data-root paths; absolute paths never enter canonical artifact IDs."""

    root: Path
    raw: Path
    derived: Path
    runs: Path

    @classmethod
    def from_path(cls, value: str | os.PathLike[str], *, repository_root: Path) -> DataRootLayout:
        raw_value = str(value).strip()
        if not raw_value:
            raise DataRootError("data root is required")
        if raw_value.startswith(("\\\\", "//")):
            raise DataRootError("UNC data roots are not supported")

        candidate = Path(raw_value).expanduser()
        if not candidate.is_absolute():
            raise DataRootError("data root must be an absolute path")
        resolved = candidate.resolve(strict=False)
        anchor = Path(resolved.anchor).resolve(strict=False)
        if resolved == anchor:
            raise DataRootError("a filesystem or drive root cannot be the data root")

        for part in resolved.parts:
            stem = part.rstrip(" .").split(".", 1)[0].upper()
            if stem in _RESERVED_WINDOWS_NAMES:
                raise DataRootError(f"data root contains a reserved device name: {part}")

        repository = repository_root.resolve(strict=False)
        _reject_reparse_components(candidate)
        if _is_within(resolved, repository):
            raise DataRootError("data root must be outside the source repository")

        return cls(
            root=resolved,
            raw=resolved / "raw",
            derived=resolved / "derived",
            runs=resolved / "runs",
        )

    @classmethod
    def from_environment(
        cls,
        *,
        repository_root: Path,
        environ: Mapping[str, str] | None = None,
    ) -> DataRootLayout:
        values = os.environ if environ is None else environ
        configured = values.get("QME_DATA_ROOT")
        if configured is None:
            raise DataRootError("QME_DATA_ROOT is not configured")
        return cls.from_path(configured, repository_root=repository_root)

    def initialize(self, *, dry_run: bool = False) -> tuple[Path, Path, Path]:
        directories = (self.raw, self.derived, self.runs)
        if not dry_run:
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
        return directories

    def logical_artifact_id(self, path: Path) -> str:
        """Return a root-independent POSIX identifier for a data artifact."""

        resolved = path.resolve(strict=False)
        if not _is_within(resolved, self.root):
            raise DataRootError("artifact path is outside the configured data root")
        return resolved.relative_to(self.root).as_posix()
