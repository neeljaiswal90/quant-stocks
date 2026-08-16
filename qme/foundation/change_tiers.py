"""Change-tier policy: proportionate process, enforced against the tracked tree.

Loads ``configs/governance/change-tier-policy-v1.json``, classifies every
tracked file into a tier by first-matching glob, and enforces the tier's
structural rules:

* every tracked path must classify (no silent new directories);
* the self-verifying / capability-sealed style is confined to T0 verifiers
  (plus an explicit grandfathered list in T1);
* hash manifests, ``.manifest.json`` files, and receipts may exist only in T0.

This module is intentionally written in the plain T2 style it mandates: no
self-pinned digests, no sealed result types, no forge guards. It is invoked by
``tests/foundation/test_change_tier_policy.py`` on every CI run and can be
run locally with ``python -m qme.foundation.change_tiers``.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

POLICY_PATH = Path("configs/governance/change-tier-policy-v1.json")
POLICY_SCHEMA_PATH = Path("schemas/governance/change-tier-policy-v1.schema.json")

TIER_ORDER: tuple[str, ...] = (
    "T0_FROZEN_CONTRACT",
    "T1_ACCEPTED_KERNEL",
    "T2_ENGINEERING",
    "T3_DOCUMENTATION",
)


class ChangeTierPolicyError(ValueError):
    """Raised when the policy file is malformed or the tree violates it."""


@dataclass(frozen=True)
class TierRule:
    tier: str
    glob: str


@dataclass
class ChangeTierPolicy:
    rules: tuple[TierRule, ...]
    python_source_patterns: tuple[re.Pattern[str], ...]
    path_suffix_patterns: tuple[str, ...]
    grandfathered_self_pinning: frozenset[str]
    grandfathered_manifests: frozenset[str]
    self_exclusions: frozenset[str]
    raw: Mapping[str, Any]


@dataclass
class TierReport:
    """Result of classifying and checking the tracked tree."""

    files_by_tier: dict[str, list[str]] = field(
        default_factory=lambda: {tier: [] for tier in TIER_ORDER}
    )
    lines_by_tier: dict[str, int] = field(default_factory=lambda: dict.fromkeys(TIER_ORDER, 0))
    unclassified: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unclassified and not self.violations

    def render(self) -> str:
        total = sum(self.lines_by_tier.values()) or 1
        lines = ["change-tier report"]
        for tier in TIER_ORDER:
            n_files = len(self.files_by_tier[tier])
            n_lines = self.lines_by_tier[tier]
            lines.append(f"  {tier:20s} files={n_files:4d} lines={n_lines:7d} ({100 * n_lines / total:5.1f}%)")
        if self.unclassified:
            lines.append("  UNCLASSIFIED PATHS (add an ordered_rule):")
            lines.extend(f"    {path}" for path in self.unclassified)
        if self.violations:
            lines.append("  VIOLATIONS:")
            lines.extend(f"    {item}" for item in self.violations)
        lines.append("  status: " + ("OK" if self.ok else "FAIL"))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _glob_matches(pattern: str, path: str) -> bool:
    """POSIX-style glob where ``**`` matches across separators and ``*`` does not."""
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")
    if "**" in pattern:
        # General case: translate ** to a cross-separator wildcard.
        regex = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        return re.fullmatch(regex, path) is not None
    if "/" not in pattern:
        # Root-level filename pattern only (e.g. "*.md" does not match docs/x.md).
        return "/" not in path and fnmatch.fnmatchcase(path, pattern)
    return fnmatch.fnmatchcase(path, pattern)


def load_policy(repository_root: Path) -> ChangeTierPolicy:
    raw_text = (repository_root / POLICY_PATH).read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    if not isinstance(raw, dict):
        raise ChangeTierPolicyError("policy root must be an object")

    rules: list[TierRule] = []
    for item in raw.get("ordered_rules", []):
        if not isinstance(item, dict):
            raise ChangeTierPolicyError("ordered_rules entries must be objects")
        tier = item.get("tier")
        glob = item.get("glob")
        if tier not in TIER_ORDER or not isinstance(glob, str) or not glob:
            raise ChangeTierPolicyError(f"invalid ordered_rule: {item!r}")
        rules.append(TierRule(tier=tier, glob=glob))

    forbidden = raw.get("forbidden_in_non_t0", {})
    if not isinstance(forbidden, dict):
        raise ChangeTierPolicyError("forbidden_in_non_t0 must be an object")
    src_patterns = tuple(
        re.compile(str(pattern)) for pattern in forbidden.get("python_source_patterns", [])
    )
    suffixes = tuple(str(item) for item in forbidden.get("path_suffix_patterns", []))

    tiers = raw.get("tiers", {})
    t1 = tiers.get("T1_ACCEPTED_KERNEL", {}) if isinstance(tiers, dict) else {}
    grandfathered = frozenset(str(item) for item in t1.get("self_pinning_grandfathered_paths", []))
    grandfathered_manifests = frozenset(
        str(item) for item in t1.get("hashes_manifests_grandfathered_paths", [])
    )
    exclusions = frozenset(str(item) for item in raw.get("checker_self_exclusions", []))

    return ChangeTierPolicy(
        rules=tuple(rules),
        python_source_patterns=src_patterns,
        path_suffix_patterns=suffixes,
        grandfathered_self_pinning=grandfathered,
        grandfathered_manifests=grandfathered_manifests,
        self_exclusions=exclusions,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Classification and checks
# ---------------------------------------------------------------------------


def classify(policy: ChangeTierPolicy, path: str) -> str | None:
    """Return the tier for ``path`` (posix, repository-relative), or None."""
    for rule in policy.rules:
        if _glob_matches(rule.glob, path):
            return rule.tier
    return None


def tracked_files(repository_root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ChangeTierPolicyError(f"git ls-files failed: {completed.stderr.strip()}")
    return sorted(item.replace("\\", "/") for item in completed.stdout.split("\0") if item)


def _matches_suffix(path: str, suffix_pattern: str) -> bool:
    if "*" in suffix_pattern:
        return fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], "*" + suffix_pattern)
    return path.endswith(suffix_pattern)


def check_tree(
    repository_root: Path,
    policy: ChangeTierPolicy,
    paths: Iterable[str] | None = None,
) -> TierReport:
    """Classify and check ``paths`` (default: all tracked files)."""
    report = TierReport()
    candidates: Sequence[str] = list(paths) if paths is not None else tracked_files(repository_root)

    for path in candidates:
        tier = classify(policy, path)
        if tier is None:
            report.unclassified.append(path)
            continue
        report.files_by_tier[tier].append(path)

        full = repository_root / path
        if not full.is_file():
            continue

        # Line accounting for the tier balance metric (source and JSON only).
        if path.endswith((".py", ".json", ".yml", ".yaml", ".md", ".toml")):
            with contextlib.suppress(OSError), full.open(
                "r", encoding="utf-8", errors="replace"
            ) as handle:
                report.lines_by_tier[tier] += sum(1 for _ in handle)

        if tier == "T0_FROZEN_CONTRACT" or path in policy.self_exclusions:
            continue

        # Structural rules for non-T0 paths.
        for suffix in policy.path_suffix_patterns:
            if path in policy.grandfathered_manifests:
                break
            if _matches_suffix(path, suffix):
                report.violations.append(
                    f"{path}: '{suffix}' artifacts are reserved for T0_FROZEN_CONTRACT "
                    f"(classified {tier})"
                )

        if path.endswith(".py") and path not in policy.grandfathered_self_pinning:
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for pattern in policy.python_source_patterns:
                match = pattern.search(text)
                if match is not None:
                    line_no = text.count("\n", 0, match.start()) + 1
                    report.violations.append(
                        f"{path}:{line_no}: self-pinning/sealing signature "
                        f"'{match.group(0).strip()}' is reserved for T0 verifiers "
                        f"(classified {tier})"
                    )
                    break

    return report


def check_repository(repository_root: Path) -> TierReport:
    return check_tree(repository_root, load_policy(repository_root))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]) if args else Path.cwd()
    report = check_repository(root)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
