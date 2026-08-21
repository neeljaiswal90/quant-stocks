from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

import qme.governance.nee116_capacity_solver_freeze_candidate as candidate

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(candidate.CANDIDATE_PATH)
SCHEMA = Path(candidate.SCHEMA_PATH)
MANIFEST = Path(candidate.MANIFEST_PATH)
RUNTIME = Path("qme/governance/nee116_capacity_solver_freeze_candidate.py")
DOC = Path("docs/governance/NEE_116_CAPACITY_SOLVER_FREEZE_CANDIDATE_V1.md")
TEST = Path("tests/governance/test_nee116_capacity_solver_freeze_candidate.py")


def _load(path: Path, root: Path = ROOT) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(
        (root / path).read_text("utf-8"),
        object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"nonfinite: {value}")),
    )
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _canonical(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _grouped(raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", "utf-8")


def _required_paths() -> tuple[str, ...]:
    values = {
        candidate.CANDIDATE_PATH,
        candidate.SCHEMA_PATH,
        candidate.MANIFEST_PATH,
        "configs/governance/specification-freeze-v6.hashes.json",
        RUNTIME.as_posix(),
        DOC.as_posix(),
        TEST.as_posix(),
        *candidate._FREEZE_LEAVES,
        *candidate._EVIDENCE_LEAVES,
    }
    return tuple(sorted(values))


def _copy_root(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    for relative in _required_paths():
        source = ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return root


def _mutate_json(
    path: Path,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    document = _load(path, path.parents[len(path.parts) - 1])
    mutate(document)
    _write_json(path, document)


def _manifest_repin(root: Path, relative: str) -> None:
    manifest = _load(MANIFEST, root)
    digest = _grouped((root / relative).read_bytes())
    for row in manifest["artifacts"]:
        if row["path"] == relative:
            row["sha256"] = digest
            break
    else:
        raise AssertionError(relative)
    _write_json(root / MANIFEST, manifest)


def test_schema_exact_const_and_candidate_verification() -> None:
    config = _load(CONFIG)
    schema = _load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(config)) == []
    assert schema["const"] == config
    verified = candidate.verify_nee116_capacity_solver_freeze_candidate(ROOT)
    assert verified.candidate_id == candidate.CANDIDATE_ID
    assert verified.status == candidate.CANDIDATE_STATUS
    assert verified.active_blocker_count == 10
    assert verified.historical_resolved_or_superseded_count == 20
    assert verified.target_blocker_cleared is False


def test_independent_semantic_hash_and_exact_transition() -> None:
    config = _load(CONFIG)
    recorded = config.pop("semantic_sha256")
    assert recorded == _grouped(_canonical(config))
    transition = config["proposed_transition"]
    assert transition["pre_state"] == {
        "active": 10,
        "historical_resolved_or_superseded": 20,
    }
    assert transition["post_state"] == {
        "active": 9,
        "historical_resolved_or_superseded": 21,
    }
    assert transition["removes_exactly"] == [candidate.TARGET_BLOCKER_CODE]
    assert len(transition["retained_active_blocker_codes_in_order"]) == 9


def test_freeze_v6_and_lineage_inventories_are_exact() -> None:
    config = _load(CONFIG)
    freeze_manifest = _load(Path("configs/governance/specification-freeze-v6.hashes.json"))
    assert {row["path"]: row["sha256"] for row in freeze_manifest["artifacts"]} == dict(
        candidate._FREEZE_LEAVES
    )
    assert {row["path"]: row["sha256"] for row in config["lineage"]} == dict(
        candidate._EVIDENCE_LEAVES
    )
    for path, digest in {
        **candidate._FREEZE_LEAVES,
        **candidate._EVIDENCE_LEAVES,
    }.items():
        assert _grouped((ROOT / path).read_bytes()) == digest


def test_manifest_and_serializer_replay() -> None:
    rows = candidate.verify_nee116_capacity_solver_freeze_candidate_manifest(ROOT)
    assert tuple(rows) == tuple(candidate._MANIFEST_LEAF_PINS)
    verified = candidate.verify_nee116_capacity_solver_freeze_candidate(ROOT)
    projection = dict(candidate.serialize_nee116_capacity_solver_freeze_candidate(verified, ROOT))
    assert projection == {
        "candidate_id": candidate.CANDIDATE_ID,
        "status": candidate.CANDIDATE_STATUS,
        "config_sha256": candidate._CONFIG_SHA,
        "semantic_sha256": candidate._SEMANTIC_SHA,
        "freeze_policy_sha256": candidate._FREEZE_POLICY_SHA,
        "active_blocker_count": 10,
        "historical_resolved_or_superseded_count": 20,
        "target_blocker_code": candidate.TARGET_BLOCKER_CODE,
        "retained_active_blocker_codes": list(candidate._RETAINED_CODES),
        "target_blocker_cleared": False,
    }


def test_direct_result_construction_rejects() -> None:
    with pytest.raises(TypeError, match="repository verification"):
        candidate.VerifiedNee116CapacitySolverFreezeCandidate()


def test_forged_result_state_rejects() -> None:
    genuine = candidate.verify_nee116_capacity_solver_freeze_candidate(ROOT)
    forged = object.__new__(candidate.VerifiedNee116CapacitySolverFreezeCandidate)
    state = list(object.__getattribute__(genuine, "_state"))
    state[1] = "PRODUCTION_READY"
    object.__setattr__(forged, "_state", tuple(state))
    with pytest.raises(
        candidate.Nee116CapacitySolverFreezeCandidateError,
        match="fresh repository replay",
    ):
        candidate.serialize_nee116_capacity_solver_freeze_candidate(forged, ROOT)


def test_serializer_ignores_public_verifier_and_property_poison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verified = candidate.verify_nee116_capacity_solver_freeze_candidate(ROOT)
    monkeypatch.setattr(
        candidate,
        "verify_nee116_capacity_solver_freeze_candidate",
        lambda _root: object(),
    )
    monkeypatch.setattr(
        candidate.VerifiedNee116CapacitySolverFreezeCandidate,
        "status",
        property(lambda _self: "PRODUCTION_READY"),
    )
    projection = dict(candidate.serialize_nee116_capacity_solver_freeze_candidate(verified, ROOT))
    assert projection["status"] == candidate.CANDIDATE_STATUS
    assert projection["target_blocker_cleared"] is False


def test_public_worker_globals_are_not_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    original_verify = candidate._VERIFY_WORKER
    original_serialize = candidate._SERIALIZE_WORKER
    original_manifest = candidate._MANIFEST_WORKER
    monkeypatch.setattr(candidate, "_VERIFY_WORKER", lambda _root: "forged")
    monkeypatch.setattr(candidate, "_SERIALIZE_WORKER", lambda *_args: {"status": "forged"})
    monkeypatch.setattr(candidate, "_MANIFEST_WORKER", lambda _root: {})
    verified = candidate.verify_nee116_capacity_solver_freeze_candidate(ROOT)
    assert type(verified) is candidate.VerifiedNee116CapacitySolverFreezeCandidate
    assert (
        dict(candidate.serialize_nee116_capacity_solver_freeze_candidate(verified, ROOT))["status"]
        == candidate.CANDIDATE_STATUS
    )
    assert candidate.verify_nee116_capacity_solver_freeze_candidate_manifest(ROOT)
    assert original_verify is not candidate._VERIFY_WORKER
    assert original_serialize is not candidate._SERIALIZE_WORKER
    assert original_manifest is not candidate._MANIFEST_WORKER


def test_selective_module_global_poison_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    before = candidate.verify_nee116_capacity_solver_freeze_candidate(ROOT)
    monkeypatch.setattr(candidate, "CANDIDATE_STATUS", "PRODUCTION_READY")
    monkeypatch.setattr(candidate, "TARGET_BLOCKER_CODE", "FORGED")
    monkeypatch.setattr(candidate, "_ACTIVE_ROWS", ())
    monkeypatch.setattr(candidate, "_RETAINED_CODES", ())
    monkeypatch.setattr(candidate, "_MAX_BYTES", 1)
    monkeypatch.setattr(candidate.__dict__["hashlib"], "sha256", lambda _raw=b"": None)
    monkeypatch.setattr(candidate.__dict__["json"], "loads", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        candidate.__dict__["os"],
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("poison")),
    )
    monkeypatch.setattr(candidate.__dict__["stat"], "S_ISREG", lambda _mode: False)
    monkeypatch.setattr(candidate, "cast", lambda _kind, _value: {"status": "forged"})
    after = candidate.verify_nee116_capacity_solver_freeze_candidate(ROOT)
    for verified in (before, after):
        projection = dict(
            candidate.serialize_nee116_capacity_solver_freeze_candidate(verified, ROOT)
        )
        assert projection["status"] == (
            "READY_FOR_FRESH_INDEPENDENT_DELTA_REVIEW_BLOCKER_REMAINS_ACTIVE"
        )
        assert projection["target_blocker_code"] == "NEE-116-CAPACITY-SOLVER"
        assert projection["target_blocker_cleared"] is False


def test_private_worker_closure_has_no_authoritative_module_global_lookups() -> None:
    forbidden = {
        "CANDIDATE_ID",
        "CANDIDATE_PATH",
        "SCHEMA_PATH",
        "MANIFEST_PATH",
        "CANDIDATE_STATUS",
        "TARGET_BLOCKER_CODE",
        "_TARGET_ROW",
        "_ACTIVE_ROWS",
        "_RETAINED_CODES",
        "_MAX_BYTES",
        "hashlib",
        "json",
        "os",
        "stat",
        "re",
        "cast",
        "MappingProxyType",
        "Path",
    }
    seen: set[int] = set()

    def visit(function: Any) -> None:
        if id(function) in seen or not hasattr(function, "__code__"):
            return
        seen.add(id(function))
        assert forbidden.isdisjoint(function.__code__.co_names)
        closure = function.__closure__ or ()
        for cell in closure:
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if (
                callable(value)
                and hasattr(value, "__code__")
                and getattr(value, "__module__", None) == candidate.__name__
            ):
                visit(value)

    for worker in (
        candidate._VERIFY_WORKER,
        candidate._SERIALIZE_WORKER,
        candidate._MANIFEST_WORKER,
    ):
        visit(worker)


@pytest.mark.parametrize(
    "relative",
    [
        "configs/governance/specification-freeze-policy-v6.json",
        "qme/quant/capacity_solver_v3.py",
        "docs/governance/external-review-results-2026-08-18/A3-V2/A3-V2-VERDICT.md",
    ],
)
def test_bound_leaf_substitution_rejects(tmp_path: Path, relative: str) -> None:
    root = _copy_root(tmp_path)
    with (root / relative).open("ab") as handle:
        handle.write(b"\nsubstitution")
    with pytest.raises(candidate.Nee116CapacitySolverFreezeCandidateError):
        candidate.verify_nee116_capacity_solver_freeze_candidate(root)


def test_freeze_manifest_full_local_repin_rejects(tmp_path: Path) -> None:
    root = _copy_root(tmp_path)
    policy_path = root / "configs/governance/specification-freeze-policy-v6.json"
    policy = _load(Path("configs/governance/specification-freeze-policy-v6.json"), root)
    policy["unresolved_blockers"][0]["description"] += " forged"
    clone = dict(policy)
    clone.pop("semantic_sha256")
    policy["semantic_sha256"] = _grouped(_canonical(clone))
    _write_json(policy_path, policy)
    freeze_manifest_path = root / "configs/governance/specification-freeze-v6.hashes.json"
    freeze_manifest = _load(Path("configs/governance/specification-freeze-v6.hashes.json"), root)
    for row in freeze_manifest["artifacts"]:
        if row["path"] == "configs/governance/specification-freeze-policy-v6.json":
            row["sha256"] = _grouped(policy_path.read_bytes())
    _write_json(freeze_manifest_path, freeze_manifest)
    with pytest.raises(
        candidate.Nee116CapacitySolverFreezeCandidateError,
        match="Freeze V6 manifest bytes changed",
    ):
        candidate.verify_nee116_capacity_solver_freeze_candidate(root)


def test_candidate_config_schema_manifest_full_local_repin_rejects(tmp_path: Path) -> None:
    root = _copy_root(tmp_path)
    config = _load(CONFIG, root)
    config["claims"]["target_blocker_cleared"] = True
    clone = dict(config)
    clone.pop("semantic_sha256")
    config["semantic_sha256"] = _grouped(_canonical(clone))
    _write_json(root / CONFIG, config)
    schema = _load(SCHEMA, root)
    schema["const"] = config
    _write_json(root / SCHEMA, schema)
    _manifest_repin(root, CONFIG.as_posix())
    _manifest_repin(root, SCHEMA.as_posix())
    with pytest.raises(
        candidate.Nee116CapacitySolverFreezeCandidateError,
        match="candidate config bytes changed",
    ):
        candidate.verify_nee116_capacity_solver_freeze_candidate(root)


@pytest.mark.parametrize(
    "relative",
    [
        DOC.as_posix(),
        TEST.as_posix(),
        "docs/quant/NEE_116_CAPACITY_SOLVER_IMPLEMENTATION_V3.md",
        "tests/quant/test_capacity_solver_v3.py",
    ],
)
def test_manifest_nonruntime_leaf_full_local_repin_rejects(tmp_path: Path, relative: str) -> None:
    root = _copy_root(tmp_path)
    with (root / relative).open("ab") as handle:
        handle.write(b"\nlocal repin")
    _manifest_repin(root, relative)
    with pytest.raises(
        candidate.Nee116CapacitySolverFreezeCandidateError,
        match="independently pinned leaf changed",
    ):
        candidate.verify_nee116_capacity_solver_freeze_candidate_manifest(root)


def test_runtime_full_local_repin_rejects(tmp_path: Path) -> None:
    root = _copy_root(tmp_path)
    runtime = root / RUNTIME
    runtime.write_bytes(runtime.read_bytes() + b"\n# local repin\n")
    _manifest_repin(root, RUNTIME.as_posix())
    with pytest.raises(
        candidate.Nee116CapacitySolverFreezeCandidateError,
        match="normalized self hash",
    ):
        candidate.verify_nee116_capacity_solver_freeze_candidate_manifest(root)


def test_manifest_duplicate_and_unsafe_paths_reject(tmp_path: Path) -> None:
    root = _copy_root(tmp_path)
    manifest = _load(MANIFEST, root)
    manifest["artifacts"][0]["path"] = "../outside"
    _write_json(root / MANIFEST, manifest)
    with pytest.raises(candidate.Nee116CapacitySolverFreezeCandidateError):
        candidate.verify_nee116_capacity_solver_freeze_candidate_manifest(root)


def test_hardlinked_evidence_rejects(tmp_path: Path) -> None:
    root = _copy_root(tmp_path)
    relative = "qme/quant/capacity_solver_v3.py"
    target = root / relative
    payload = target.read_bytes()
    target.unlink()
    external = tmp_path / "external-capacity.py"
    external.write_bytes(payload)
    os.link(external, target)
    with pytest.raises(
        candidate.Nee116CapacitySolverFreezeCandidateError,
        match="single-link|artifact open failed",
    ):
        candidate.verify_nee116_capacity_solver_freeze_candidate(root)


def test_symlinked_evidence_rejects(tmp_path: Path) -> None:
    root = _copy_root(tmp_path)
    relative = "qme/quant/capacity_solver_v3.py"
    target = root / relative
    external = tmp_path / "external-capacity.py"
    external.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(external)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(candidate.Nee116CapacitySolverFreezeCandidateError):
        candidate.verify_nee116_capacity_solver_freeze_candidate(root)


def test_exact_path_type_required() -> None:
    with pytest.raises(
        candidate.Nee116CapacitySolverFreezeCandidateError,
        match="exact pathlib.Path",
    ):
        candidate.verify_nee116_capacity_solver_freeze_candidate(cast(Path, object()))


def test_runtime_has_no_truncation_marker_or_ambient_qme_import() -> None:
    raw = (ROOT / RUNTIME).read_text("utf-8")
    assert "tokens truncated" not in raw
    assert "from qme." not in raw
    assert "import qme." not in raw


def test_claims_and_nonclaims_remain_fail_closed() -> None:
    config = _load(CONFIG)
    assert config["claims"]["capacity_engineering_evidence_ready_for_transition_review"] is True
    for key in (
        "candidate_changes_active_freeze",
        "target_blocker_cleared",
        "any_blocker_cleared",
        "successor_freeze_published",
        "owner_signoff_on_exact_bytes_complete",
        "fresh_independent_delta_review_complete",
        "milestone_m0_complete",
        "empirical_capacity_available",
        "production_ready",
        "live_order_authority",
    ):
        assert config["claims"][key] is False
    assert config["nonclaims"] == [
        "NO_BLOCKER_IS_CLEARED_BY_THIS_CANDIDATE",
        "FREEZE_V6_REMAINS_10_ACTIVE_20_HISTORICAL",
        "NO_EMPIRICAL_PORTFOLIO_CAPACITY_VALUE_IS_AVAILABLE",
        "NO_PRODUCTION_OR_LIVE_ORDER_AUTHORITY",
        "MILESTONE_M0_COMPLETE_IS_FALSE",
        "NEE_116_REMAINS_IN_PROGRESS_AFTER_THE_PROPOSED_ROW_TRANSITION",
    ]
