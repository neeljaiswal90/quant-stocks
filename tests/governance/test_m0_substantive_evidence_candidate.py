from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
from pathlib import Path

import pytest

import qme.governance.m0_substantive_evidence_candidate as candidate
from qme.governance.m0_substantive_evidence_candidate import (
    CANDIDATE_ID,
    CANDIDATE_PATH,
    CANDIDATE_STATUS,
    MANIFEST_PATH,
    SCHEMA_PATH,
    M0SubstantiveEvidenceCandidateError,
    VerifiedM0SubstantiveEvidenceCandidate,
    serialize_m0_substantive_evidence_candidate,
    verify_m0_substantive_evidence_candidate,
    verify_m0_substantive_evidence_candidate_manifest,
)

ROOT = Path(__file__).parents[2]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _group(value: str) -> str:
    return ":".join(value[index : index + 8] for index in range(0, len(value), 8))


def _candidate() -> dict[str, object]:
    return json.loads((ROOT / CANDIDATE_PATH).read_text(encoding="utf-8"))


def _copy_repository_slice(tmp_path: Path) -> Path:
    config = _candidate()
    paths = {
        CANDIDATE_PATH,
        SCHEMA_PATH,
        MANIFEST_PATH,
        "qme/governance/m0_substantive_evidence_candidate.py",
        "configs/governance/specification-freeze-policy-v7.json",
        "configs/governance/specification-freeze-v7.hashes.json",
    }
    for row in config["artifact_inventory"]:  # type: ignore[index, union-attr]
        paths.add(row["path"])  # type: ignore[index, union-attr]
    freeze_manifest = json.loads(
        (ROOT / "configs/governance/specification-freeze-v7.hashes.json").read_text(
            encoding="utf-8"
        )
    )
    paths.update(row["path"] for row in freeze_manifest["artifacts"])
    manifest = json.loads((ROOT / MANIFEST_PATH).read_text(encoding="utf-8"))
    paths.update(row["path"] for row in manifest["artifacts"])
    for relative in paths:
        source = ROOT / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return tmp_path


def test_verifier_and_manifest_replay_exact_candidate() -> None:
    value = verify_m0_substantive_evidence_candidate(ROOT)
    verify_m0_substantive_evidence_candidate_manifest(ROOT)
    assert value.candidate_id == CANDIDATE_ID
    assert value.status == CANDIDATE_STATUS
    assert value.active_blocker_count == 9
    assert value.proposed_post_state_active_count == 2
    assert value.review_complete is False
    assert value.milestone_m0_complete is False
    assert dict(serialize_m0_substantive_evidence_candidate(value, ROOT)) == {
        "candidate_id": CANDIDATE_ID,
        "status": CANDIDATE_STATUS,
        "config_sha256": candidate._CONFIG_SHA,
        "semantic_sha256": candidate._SEMANTIC_SHA,
        "active_blocker_count": 9,
        "proposed_post_state_active_count": 2,
        "proposed_removed_blocker_codes": [
            "NEE-116-ASYMMETRIC-COST-METHOD",
            "NEE-116-CORPORATE-ACTION-EDGE-CASES",
            "NEE-116-PRODUCTION-PIT-DATA",
            "NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE",
            "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP",
            "NEE-119-AV-PROXY-EVIDENCE",
            "NEE-121-CALENDAR-SESSION-REGISTRATION",
        ],
        "retained_terminal_blocker_codes": [
            "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL",
            "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP",
        ],
        "review_complete": False,
        "milestone_m0_complete": False,
    }


def test_candidate_exact_freeze_v7_arithmetic_and_nonclaims() -> None:
    config = _candidate()
    assert len(config["active_blocker_rows_verbatim"]) == 9  # type: ignore[arg-type]
    transition = config["proposed_transition"]  # type: ignore[assignment]
    assert transition["pre_state"] == {  # type: ignore[index]
        "active": 9,
        "historical_resolved_or_superseded": 21,
    }
    assert transition["post_state"] == {  # type: ignore[index]
        "active": 2,
        "historical_resolved_or_superseded": 28,
    }
    assert transition["transition_performed_by_this_candidate"] is False  # type: ignore[index]
    claims = config["claims"]  # type: ignore[assignment]
    assert claims["candidate_registered"] is True  # type: ignore[index]
    assert all(value is False for key, value in claims.items() if key != "candidate_registered")  # type: ignore[union-attr]


def test_schema_is_exact_const() -> None:
    config = _candidate()
    schema = json.loads((ROOT / SCHEMA_PATH).read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["const"] == config


@pytest.mark.parametrize(
    "path,mutation",
    [
        (
            "tests/fixtures/governance/corporate-action-corrections-oracle-v2.json",
            b"\nLOCAL-REPIN",
        ),
        ("qme/quant/tax_lots.py", b"\n# LOCAL-REPIN\n"),
        (
            "tests/fixtures/governance/av-proxy-independent-review-sample-v2.json",
            b"\nLOCAL-REPIN",
        ),
        (
            "tests/fixtures/governance/ndx-membership-2026-07-31-approved-snapshot.json",
            b"\nLOCAL-REPIN",
        ),
    ],
)
def test_full_local_evidence_repin_rejected(
    tmp_path: Path,
    path: str,
    mutation: bytes,
) -> None:
    root = _copy_repository_slice(tmp_path)
    artifact = root / path
    artifact.write_bytes(artifact.read_bytes() + mutation)
    config_path = root / CANDIDATE_PATH
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for row in config["artifact_inventory"]:
        if row["path"] == path:
            row["sha256"] = _group(_sha(artifact))
            row["bytes"] = artifact.stat().st_size
            break
    semantic = dict(config)
    semantic.pop("$schema")
    semantic.pop("semantic_sha256")
    config["semantic_sha256"] = _group(hashlib.sha256(
        (
            json.dumps(
                semantic,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest())
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    schema_path = root / SCHEMA_PATH
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["const"] = config
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(M0SubstantiveEvidenceCandidateError, match="DIGEST_MISMATCH"):
        verify_m0_substantive_evidence_candidate(root)


def test_duplicate_key_and_nonfinite_rejected(tmp_path: Path) -> None:
    root = _copy_repository_slice(tmp_path)
    config_path = root / CANDIDATE_PATH
    raw = config_path.read_text(encoding="utf-8")
    config_path.write_text(raw.replace("{", '{"candidate_id":NaN,', 1), encoding="utf-8")
    with pytest.raises(M0SubstantiveEvidenceCandidateError):
        verify_m0_substantive_evidence_candidate(root)


def test_verified_result_cannot_be_constructed_or_forged() -> None:
    with pytest.raises(TypeError):
        VerifiedM0SubstantiveEvidenceCandidate()
    genuine = verify_m0_substantive_evidence_candidate(ROOT)
    forged = object.__new__(VerifiedM0SubstantiveEvidenceCandidate)
    object.__setattr__(forged, "_state", (*object.__getattribute__(genuine, "_state")[:-2], True, True))
    with pytest.raises(M0SubstantiveEvidenceCandidateError, match="STATE_MISMATCH"):
        serialize_m0_substantive_evidence_candidate(forged, ROOT)


def test_serializer_ignores_public_global_and_property_poison(monkeypatch: pytest.MonkeyPatch) -> None:
    genuine = verify_m0_substantive_evidence_candidate(ROOT)
    expected = dict(serialize_m0_substantive_evidence_candidate(genuine, ROOT))
    monkeypatch.setattr(candidate, "verify_m0_substantive_evidence_candidate", lambda _root: object())
    monkeypatch.setattr(candidate, "_verify_repository_state", lambda _root: ("POISON",))
    monkeypatch.setattr(candidate, "_projection", lambda _state: {"status": "PRODUCTION_READY"})
    monkeypatch.setattr(
        VerifiedM0SubstantiveEvidenceCandidate,
        "status",
        property(lambda _self: "PRODUCTION_READY"),
    )
    assert dict(serialize_m0_substantive_evidence_candidate(genuine, ROOT)) == expected


def test_manifest_full_local_repin_rejected(tmp_path: Path) -> None:
    root = _copy_repository_slice(tmp_path)
    doc = root / "docs/governance/M0_SUBSTANTIVE_EVIDENCE_CANDIDATE_V1.md"
    doc.write_bytes(doc.read_bytes() + b"\nLOCAL REPIN\n")
    manifest_path = root / MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["artifacts"]:
        if row["path"] == "docs/governance/M0_SUBSTANTIVE_EVIDENCE_CANDIDATE_V1.md":
            row["sha256"] = _group(_sha(doc))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(M0SubstantiveEvidenceCandidateError, match="MANIFEST_PIN_MISMATCH"):
        verify_m0_substantive_evidence_candidate_manifest(root)


def test_hardlink_rejected(tmp_path: Path) -> None:
    original = tmp_path / "a.json"
    linked = tmp_path / "b.json"
    original.write_text("{}\n", encoding="utf-8")
    os.link(original, linked)
    with pytest.raises(M0SubstantiveEvidenceCandidateError, match="HARDLINK"):
        candidate._confined_bytes(tmp_path, Path("a.json"))


def test_symlink_rejected_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    link = tmp_path / "link.json"
    target.write_text("{}\n", encoding="utf-8")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(M0SubstantiveEvidenceCandidateError, match="LINK_OR_REPARSE"):
        candidate._confined_bytes(tmp_path, Path("link.json"))


def test_ancestor_swap_interleaving_rejected(tmp_path: Path) -> None:
    inside = tmp_path / "inside"
    outside = tmp_path / "outside"
    inside.mkdir()
    outside.mkdir()
    (inside / "value.json").write_text("{}\n", encoding="utf-8")
    (outside / "value.json").write_text('{"poison":true}\n', encoding="utf-8")

    def swap(_root: Path, _target: Path) -> None:
        moved = tmp_path / "inside-old"
        inside.rename(moved)
        try:
            inside.symlink_to(outside, target_is_directory=True)
        except OSError:
            moved.rename(inside)
            pytest.skip("directory symlink creation unavailable")

    with pytest.raises(M0SubstantiveEvidenceCandidateError):
        candidate._confined_bytes(
            tmp_path,
            Path("inside/value.json"),
            interleave_hook=swap,
        )


def test_authoritative_serializer_closure_does_not_resolve_public_workers() -> None:
    source = inspect.getsource(serialize_m0_substantive_evidence_candidate)
    assert "verify_m0_substantive_evidence_candidate" not in source
    assert "_verify_repository_state" not in source
    assert "_projection" not in source
    closure = inspect.getclosurevars(serialize_m0_substantive_evidence_candidate)
    assert "verify_worker" in closure.nonlocals
    assert "projection_worker" in closure.nonlocals
