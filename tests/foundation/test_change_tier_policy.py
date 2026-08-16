"""Architecture test: the change-tier policy is valid and the tracked tree obeys it.

Runs on every CI invocation. Failing cases below prove the checker detects the
patterns it is meant to keep out of pipeline code — the test would be worthless
if it only ever passed on the current tree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from qme.foundation.change_tiers import (
    POLICY_PATH,
    POLICY_SCHEMA_PATH,
    TIER_ORDER,
    ChangeTierPolicy,
    check_repository,
    check_tree,
    classify,
    load_policy,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def policy() -> ChangeTierPolicy:
    return load_policy(ROOT)


# ---------------------------------------------------------------------------
# Policy artifact validity
# ---------------------------------------------------------------------------


def test_policy_validates_against_schema() -> None:
    schema = json.loads((ROOT / POLICY_SCHEMA_PATH).read_text("utf-8"))
    config = json.loads((ROOT / POLICY_PATH).read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(config))
    assert not errors, errors[0].message if errors else ""
    assert schema["additionalProperties"] is False


def test_policy_claims_are_fail_closed(policy: ChangeTierPolicy) -> None:
    claims = policy.raw["claims"]
    assert claims["reduces_existing_t0_ceremony"] is False
    assert claims["changes_any_frozen_artifact"] is False
    assert claims["enforced_in_ci"] is True


def test_every_grandfathered_path_exists_and_is_t1(policy: ChangeTierPolicy) -> None:
    # Grandfather lists must not silently rot into dangling exemptions.
    for path in sorted(policy.grandfathered_self_pinning | policy.grandfathered_manifests):
        assert (ROOT / path).is_file(), path
        assert classify(policy, path) == "T1_ACCEPTED_KERNEL", path


# ---------------------------------------------------------------------------
# The tracked tree obeys the policy
# ---------------------------------------------------------------------------


def test_tracked_tree_is_fully_classified_and_clean() -> None:
    report = check_repository(ROOT)
    assert report.ok, report.render()
    # Sanity: every tier is populated (guards against a rule ordering bug that
    # swallows everything into one tier).
    for tier in TIER_ORDER:
        assert report.files_by_tier[tier], f"{tier} matched no files"


def test_representative_paths_classify_as_intended(policy: ChangeTierPolicy) -> None:
    expected = {
        "configs/governance/specification-freeze-policy-v4.json": "T0_FROZEN_CONTRACT",
        "qme/governance/specification_freeze_v4.py": "T0_FROZEN_CONTRACT",
        "qme/promotion/decision_v2.py": "T0_FROZEN_CONTRACT",
        "qme/quant/contract_v2.py": "T0_FROZEN_CONTRACT",
        ".github/workflows/ci.yml": "T0_FROZEN_CONTRACT",
        "qme/stats/effective_trials_uncertainty.py": "T1_ACCEPTED_KERNEL",
        "qme/quant/regulatory_fees.py": "T1_ACCEPTED_KERNEL",
        "qme/foundation/change_tiers.py": "T2_ENGINEERING",
        "qme/ui_snapshot/builder.py": "T2_ENGINEERING",
        "qme/cli/foundation.py": "T2_ENGINEERING",
        "scripts/check_secrets.py": "T2_ENGINEERING",
        "tests/foundation/test_change_tier_policy.py": "T2_ENGINEERING",
        # Future pipeline packages land in T2 by the qme/** fallback.
        "qme/data/alpha_vantage/client.py": "T2_ENGINEERING",
        "qme/signal/momentum.py": "T2_ENGINEERING",
        "qme/backtest/engine.py": "T2_ENGINEERING",
        "docs/implementation/QME_IMPLEMENTATION_MEMORY.md": "T3_DOCUMENTATION",
        "README.md": "T3_DOCUMENTATION",
        # docs/governance is governance, not general documentation.
        "docs/governance/SPECIFICATION_FREEZE_V4.md": "T0_FROZEN_CONTRACT",
    }
    for path, tier in expected.items():
        assert classify(policy, path) == tier, path


# ---------------------------------------------------------------------------
# The checker catches what it claims to catch
# ---------------------------------------------------------------------------


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_self_pin_in_pipeline_code_is_a_violation(policy: ChangeTierPolicy, tmp_path: Path) -> None:
    rel = "qme/signal/momentum.py"
    _write(
        tmp_path,
        rel,
        'from typing import Final\nEXPECTED_CONFIG_SHA256: Final = "00"\n',
    )
    report = check_tree(tmp_path, policy, [rel])
    assert not report.ok
    assert any("self-pinning/sealing" in v and rel in v for v in report.violations)


@pytest.mark.parametrize(
    "snippet",
    [
        "_SEAL_CAPABILITY = object()\n",
        'raise ValueError("FORGED_RESULT")\n',
        "def _confined_bytes(root, relative):\n    pass\n",
        "_PREDECESSOR_HASHES = {}\n",
        "graph = _snapshot_global_graph((f,))\n",
        "x = 'RUNTIME_SELF_PIN_MISMATCH'\n",
    ],
)
def test_each_sealing_signature_is_caught_in_t2(
    policy: ChangeTierPolicy, tmp_path: Path, snippet: str
) -> None:
    rel = "qme/backtest/engine.py"
    _write(tmp_path, rel, snippet)
    report = check_tree(tmp_path, policy, [rel])
    assert not report.ok, snippet


def test_hashes_manifest_in_pipeline_dir_is_a_violation(
    policy: ChangeTierPolicy, tmp_path: Path
) -> None:
    rel = "tests/fixtures/backtest/run-v1.hashes.json"
    _write(tmp_path, rel, "{}\n")
    report = check_tree(tmp_path, policy, [rel])
    assert not report.ok
    assert any(".hashes.json" in v for v in report.violations)


def test_receipt_in_pipeline_dir_is_a_violation(policy: ChangeTierPolicy, tmp_path: Path) -> None:
    rel = "tests/fixtures/backtest/owner-decision-receipt-v1.json"
    _write(tmp_path, rel, "{}\n")
    report = check_tree(tmp_path, policy, [rel])
    assert not report.ok


def test_same_signature_is_allowed_in_t0(policy: ChangeTierPolicy, tmp_path: Path) -> None:
    rel = "qme/governance/new_verifier.py"
    _write(tmp_path, rel, 'EXPECTED_CONFIG_SHA256 = "00"\n_CAP = object()\n')
    report = check_tree(tmp_path, policy, [rel])
    assert report.ok, report.render()


def test_new_t1_kernel_may_not_self_pin(policy: ChangeTierPolicy, tmp_path: Path) -> None:
    rel = "qme/stats/new_kernel.py"
    _write(tmp_path, rel, 'EXPECTED_KAT_SHA256 = "00"\n')
    report = check_tree(tmp_path, policy, [rel])
    assert not report.ok


def test_unclassified_path_fails(policy: ChangeTierPolicy, tmp_path: Path) -> None:
    rel = "notebooks/scratch.py"
    _write(tmp_path, rel, "x = 1\n")
    report = check_tree(tmp_path, policy, [rel])
    assert not report.ok
    assert report.unclassified == [rel]


def test_ordered_rules_first_match_wins(policy: ChangeTierPolicy) -> None:
    # qme/quant/contract_v2.py is listed before the qme/quant/** T1 rule and
    # must therefore land in T0; every other qme/quant file lands in T1.
    assert classify(policy, "qme/quant/contract_v2.py") == "T0_FROZEN_CONTRACT"
    assert classify(policy, "qme/quant/equations.py") == "T1_ACCEPTED_KERNEL"


def test_root_md_glob_does_not_swallow_nested_docs(policy: ChangeTierPolicy) -> None:
    # "*.md" is a root-only rule; nested governance docs must still be T0.
    assert classify(policy, "docs/governance/X.md") == "T0_FROZEN_CONTRACT"
    assert classify(policy, "SYSTEM_AUDIT_2026-08-07.md") == "T3_DOCUMENTATION"
