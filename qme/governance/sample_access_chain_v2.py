"""Deterministic, bounded NEE-176 sample-access-chain export and proof kernel."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat as stat_module
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, NamedTuple, cast

STATUS: Final = "BOUNDED_ACCESS_CHAIN_IMPLEMENTATION_CANDIDATE"
GENESIS_HASH: Final = "0" * 64
MIN_KNOWN_ANSWER_EVENTS: Final = 10_000
MAX_EVENT_BYTES: Final = 2 * 1024 * 1024
MAX_EXPORT_BYTES: Final = 64 * 1024 * 1024
MAX_COMPACT_REGISTRY_BYTES: Final = 2 * 1024 * 1024
VERSIONED_REGISTRY_ID: Final = "NEE-176-COMPACT-EXPERIMENT-REGISTRY-V2"
VERSIONED_EVENT_SCHEMA: Final = "qme.compact_experiment_registry_event.v2"
VERSIONED_EXPORT_SCHEMA: Final = "qme.compact_experiment_registry_export.v2"
VERSIONED_EVENT_DOMAIN: Final = b"qme.compact_experiment_registry.v2.event\x00"
VERSIONED_STATE_DOMAIN: Final = b"qme.compact_experiment_registry.v2.state\x00"
VERSIONED_EXPORT_DOMAIN: Final = b"qme.compact_experiment_registry.v2.export\x00"
VERSIONED_COMPACT_EVENT_TYPE: Final = "SAMPLE_ACCESS_BOUND_COMPACT"
OBJECT_DOMAIN: Final = b"qme.sample_access_chain.v2.object\x00"
LEAF_DOMAIN: Final = b"qme.sample_access_chain.v2.leaf\x00"
NODE_DOMAIN: Final = b"qme.sample_access_chain.v2.node\x00"
ROOT_DOMAIN: Final = b"qme.sample_access_chain.v2.root\x00"
ODD_NODE_RULE: Final = "DUPLICATE_LAST_DIGEST_AT_EACH_LEVEL"
EVENT_KEYS: Final = frozenset(
    {
        "schema_version", "governance_contract_id", "event_id", "sequence",
        "previous_event_hash", "accessed_at", "actor_id", "purpose", "event_type",
        "trial_id", "run_id", "query_id", "analysis_as_of", "data_vintage_at",
        "data_vintage_sha256", "request_content_sha256", "parent_event_hash",
        "contract_version", "sample_classification", "requested_start", "requested_end",
        "access_mode", "artifact_bindings", "event_hash",
    }
)
EVENT_TYPES: Final = frozenset(
    {"ACCESS_ATTEMPT", "ACCESS_SUCCESS", "ACCESS_DENIAL", "ACCESS_RETRY"}
)
CLASSIFICATIONS: Final = frozenset(
    {
        "DEVELOPMENT_2011_2018",
        "ONE_TIME_HISTORICAL_CONFIRMATION_2019_2021",
        "RETROSPECTIVE_EXTERNAL_STRESS_2022_PLUS",
        "PROSPECTIVE_AFTER_FREEZE",
    }
)
ACCESS_MODES: Final = frozenset({"READ", "MATERIALIZE", "EXPORT"})
CAUSAL_EQUAL_FIELDS: Final = (
    "access_mode", "analysis_as_of", "artifact_bindings", "contract_version",
    "data_vintage_at", "data_vintage_sha256", "purpose", "query_id",
    "request_content_sha256", "requested_end", "requested_start", "run_id",
    "sample_classification", "trial_id",
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_GROUPED_HASH_RE = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_EXPECTED_AUTHORITY: Final = {
    "protected_main_commit": "e2fc6953:48f1f29d:3352fb94:f862c70b:71430e53",
    "sample_holdout_v2": {
        "path": "configs/governance/sample-holdout-v2.json",
        "sha256": "c0437ecb:49787492:f5573213:103a09fc:c6d87182:ac0cc270:4b65769d:2c89e11f",
        "governance_contract_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V2",
        "status": "ACTIVE_GOVERNANCE_AUTHORITY_BLOCKERS_REMAIN",
    },
    "inherited_event_schema": {
        "path": "schemas/governance/sample-access-event-v1.schema.json",
        "sha256": "2c01f376:636c2aae:8cda666e:77f8aff5:17f5072b:729b21cf:2eed8eb4:52a786cb",
        "schema_version": "qme.sample_access_event.v1",
        "governance_contract_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1",
        "lineage_rule": "PRESERVE_V1_EVENT_BYTES_AND_HASH_SEMANTICS_NO_REWRITE",
    },
    "proposal": {
        "path": "docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md",
        "sha256": "5869d313:e179e442:e305704e:7cff5031:786d7452:73c0f864:bc910784:8685847c",
        "role": "REGISTERED_PROPOSAL_PROVENANCE_NOT_PRODUCTION_EVIDENCE",
    },
    "m0_registration": {
        "path": "configs/governance/m0-registration-v1.json",
        "sha256": "fc61bb24:5e99c5a7:ac8de1ad:f909b785:2f3a651c:7925d0fb:63037db7:45946756",
    },
    "experiment_family_registration": {
        "path": "configs/governance/experiment-family-registration-v1.json",
        "sha256": "016ac219:ea2dd117:58ebbb3d:c32b89b0:41d951e5:878812c1:0fbc396f:a4f09b40",
    },
    "freeze_v3": {
        "path": "configs/governance/specification-freeze-policy-v3.json",
        "sha256": "a8af9098:52e71ec1:b91a5c23:30290bec:967e443b:d616997b:4020a599:0af0ec53",
        "semantic_sha256": "9c3f240e:c22f3716:b5576d84:630e34da:e9c50e2a:fbdd8486:2baa9134:037b799a",
        "active_blocker_count": 14,
    },
    "registry_v1": {
        "path": "configs/governance/experiment-registry-v1.json",
        "sha256": "16cc38d2:ae3b6e1d:261691b5:6f0e6960:554f5926:420e9527:d8880c8b:5e1d53be",
        "role": "VERSIONED_CONSUMER_LINEAGE_NOT_MODIFIED",
        "protected_business_runtime": {
            "path": "qme/experiments/registry.py",
            "sha256": "dba251da:59362a2f:c44c9660:58a3f067:f0e810a3:9551a929:e5e603e5:6f014785",
            "registry_id": "NEE-122-GLOBAL-EXPERIMENT-REGISTRY-V1",
            "event_schema_version": "qme.experiment_registry_event.v1",
            "replay_api": "qme.experiments.registry.replay_registry",
        },
        "nee121_holdout_manifest_binding": {
            "artifact_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1",
            "source_id": "configs/governance/sample-holdout-v1.hashes.json",
            "sha256": "e28864a8:067f8e23:3ea880f0:08229991:6d986ed6:8614a678:2616aa93:f96f91a2",
        },
    },
    "transitive_manifests": [
        {"path": "configs/governance/sample-holdout-v2.hashes.json", "sha256": "675902c9:c355604f:365811a5:6f2b1c98:658ffacf:59af56c8:b7f30b2b:5c6b5e83"},
        {"path": "configs/governance/m0-registration-v1.hashes.json", "sha256": "b1375860:485bf393:df34d588:545bf1a2:738f5efd:c703825c:62c3430c:b427c6db"},
        {"path": "configs/governance/specification-freeze-v3.hashes.json", "sha256": "5a492ded:1fc4cc3b:3d9756dd:b816234a:72009dc0:80e38f99:f8a40110:43d035d4"},
        {"path": "configs/governance/experiment-registry-v1.hashes.json", "sha256": "aebdca44:dc207f9d:87b5349b:0c9fe19a:f5ac77ab:fcfdc53d:0662a9a1:98dd728f"},
    ],
}
_EXPECTED_CLAIMS: Final = {
    "production_access_chain_inclusion_available": False,
    "production_sample_access_evidence_available": False,
    "prospective_observations_consumable": False,
    "production_specification_accepted": False,
    "milestone_m0_complete": False,
    "production_ready": False,
    "empirical_performance_available": False,
    "alpha_proven": False,
    "blocker_cleared": False,
}
_NEE121_V2_CONFIG_SHA256: Final = (
    "c0437ecb:49787492:f5573213:103a09fc:c6d87182:ac0cc270:4b65769d:2c89e11f"
)
_NEE121_V2_MANIFEST_SHA256: Final = (
    "675902c9:c355604f:365811a5:6f2b1c98:658ffacf:59af56c8:b7f30b2b:5c6b5e83"
)
_REGISTERED_POLICY_NEE121_BINDING: Final = {
    "artifact_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1",
    "source_id": "configs/governance/sample-holdout-v1.hashes.json",
    "sha256": "e28864a8:067f8e23:3ea880f0:08229991:6d986ed6:8614a678:2616aa93:f96f91a2",
}


class SampleAccessChainV2Error(ValueError):
    """Fail-closed V2 contract error."""


def _reject_constant(value: str) -> None:
    raise SampleAccessChainV2Error(f"non-finite JSON constant is forbidden: {value}")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SampleAccessChainV2Error(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json_bytes(raw: bytes, *, maximum: int) -> Any:
    if type(raw) is not bytes or not raw or len(raw) > maximum:
        raise SampleAccessChainV2Error("JSON byte size is outside the registered bound")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise SampleAccessChainV2Error("JSON must be strict UTF-8") from error
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except (json.JSONDecodeError, TypeError) as error:
        raise SampleAccessChainV2Error("invalid strict JSON") from error


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SampleAccessChainV2Error("value is not canonical-JSON encodable") from error


def _freeze_json(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    return value


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain_json(item) for item in value]
    return value


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _group(digest: str) -> str:
    if _HASH_RE.fullmatch(digest) is None:
        raise SampleAccessChainV2Error("digest is not lowercase SHA-256")
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _ungroup(digest: Any) -> str:
    if type(digest) is not str or _GROUPED_HASH_RE.fullmatch(digest) is None:
        raise SampleAccessChainV2Error("grouped digest must be exactly 8x8 lowercase hex")
    return digest.replace(":", "")


def _raw_hash(value: Any, name: str) -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        raise SampleAccessChainV2Error(f"{name} must be lowercase SHA-256")
    return value


def _text(value: Any, name: str) -> str:
    if (
        type(value) is not str
        or not value
        or value.strip() != value
        or "\x00" in value
        or len(value) > 4096
        or unicodedata.normalize("NFC", value) != value
    ):
        raise SampleAccessChainV2Error(f"{name} must be nonempty canonical text")
    return value


def _exact_dict(value: Any, keys: set[str] | frozenset[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(keys):
        raise SampleAccessChainV2Error(f"{name} has the wrong exact key set")
    return cast(dict[str, Any], value)


def _timestamp(value: Any, name: str) -> datetime:
    text = _text(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise SampleAccessChainV2Error(f"{name} is not an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SampleAccessChainV2Error(f"{name} must include an offset")
    return parsed


def _latest_timestamp_text(events: Sequence[Mapping[str, Any]]) -> str | None:
    """Return the original text at the latest UTC instant, keeping the first tie."""

    latest_text: str | None = None
    latest_instant: datetime | None = None
    for event in events:
        text = cast(str, event["accessed_at"])
        instant = _timestamp(text, "accessed_at").astimezone(UTC)
        if latest_instant is None or instant > latest_instant:
            latest_text = text
            latest_instant = instant
    return latest_text


def validate_v1_event(document: Any) -> dict[str, Any]:
    event = _exact_dict(document, EVENT_KEYS, "sample access event")
    if event["schema_version"] != "qme.sample_access_event.v1":
        raise SampleAccessChainV2Error("event schema version changed")
    if event["governance_contract_id"] != "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1":
        raise SampleAccessChainV2Error("event governance contract changed")
    _text(event["event_id"], "event_id")
    if type(event["sequence"]) is not int or event["sequence"] < 1:
        raise SampleAccessChainV2Error("event sequence must be a positive integer")
    for name in (
        "previous_event_hash", "data_vintage_sha256", "request_content_sha256",
        "parent_event_hash", "event_hash",
    ):
        _raw_hash(event[name], name)
    accessed_at = _timestamp(event["accessed_at"], "accessed_at")
    analysis_as_of = _timestamp(event["analysis_as_of"], "analysis_as_of")
    data_vintage_at = _timestamp(event["data_vintage_at"], "data_vintage_at")
    del accessed_at
    for name in ("actor_id", "purpose", "trial_id", "run_id", "query_id", "contract_version"):
        _text(event[name], name)
    if event["contract_version"] != "v1":
        raise SampleAccessChainV2Error("event contract_version must equal protected literal v1")
    if event["event_type"] not in EVENT_TYPES:
        raise SampleAccessChainV2Error("unsupported event type")
    if event["sample_classification"] not in CLASSIFICATIONS:
        raise SampleAccessChainV2Error("unsupported sample classification")
    if event["access_mode"] not in ACCESS_MODES:
        raise SampleAccessChainV2Error("unsupported access mode")
    for name in ("requested_start", "requested_end"):
        if type(event[name]) is not str or _DATE_RE.fullmatch(event[name]) is None:
            raise SampleAccessChainV2Error(f"{name} must be canonical YYYY-MM-DD")
        try:
            datetime.fromisoformat(event[name])
        except ValueError as error:
            raise SampleAccessChainV2Error(f"{name} is not a valid date") from error
    if event["requested_start"] > event["requested_end"]:
        raise SampleAccessChainV2Error("requested sample range is reversed")
    if event["event_type"] == "ACCESS_SUCCESS":
        if data_vintage_at > analysis_as_of:
            raise SampleAccessChainV2Error("success uses a future data vintage")
        if event["requested_end"] > analysis_as_of.date().isoformat():
            raise SampleAccessChainV2Error("success reads after analysis_as_of")
        if (
            event["sample_classification"] == "DEVELOPMENT_2011_2018"
            and event["requested_end"] > "2018-12-31"
        ):
            raise SampleAccessChainV2Error("development success reads post-2018")
        if (
            event["sample_classification"]
            == "ONE_TIME_HISTORICAL_CONFIRMATION_2019_2021"
            and event["requested_end"] > "2021-12-31"
        ):
            raise SampleAccessChainV2Error("confirmation success reads 2022+")
    bindings = event["artifact_bindings"]
    if type(bindings) is not list or not bindings:
        raise SampleAccessChainV2Error("artifact_bindings must be a nonempty exact list")
    identifiers: set[str] = set()
    for raw_binding in bindings:
        binding = _exact_dict(raw_binding, {"artifact_id", "artifact_sha256"}, "artifact binding")
        identifier = _text(binding["artifact_id"], "artifact_id")
        _raw_hash(binding["artifact_sha256"], "artifact_sha256")
        if identifier in identifiers:
            raise SampleAccessChainV2Error("artifact binding IDs are duplicated")
        identifiers.add(identifier)
    payload = dict(event)
    observed = cast(str, payload.pop("event_hash"))
    if observed != _sha(canonical_json_bytes(payload)):
        raise SampleAccessChainV2Error("V1 event hash does not bind canonical payload")
    if len(canonical_json_bytes(event)) >= MAX_EVENT_BYTES:
        raise SampleAccessChainV2Error("compact event reaches the 2 MiB bound")
    return event


def validate_causal_chain(events: Sequence[Any]) -> tuple[dict[str, Any], ...]:
    if type(events) not in (list, tuple) or not events:
        raise SampleAccessChainV2Error("chain must be a nonempty exact list or tuple")
    previous = GENESIS_HASH
    previous_time: datetime | None = None
    seen_ids: set[str] = set()
    prior_by_hash: dict[str, dict[str, Any]] = {}
    validated: list[dict[str, Any]] = []
    for expected, raw in enumerate(events, 1):
        event = validate_v1_event(raw)
        if event["sequence"] != expected:
            raise SampleAccessChainV2Error("event sequence is not contiguous")
        if event["previous_event_hash"] != previous:
            raise SampleAccessChainV2Error("previous_event_hash does not match chain head")
        event_id = cast(str, event["event_id"])
        if event_id in seen_ids:
            raise SampleAccessChainV2Error("event_id is duplicated")
        accessed_at = _timestamp(event["accessed_at"], "accessed_at")
        if previous_time is not None and accessed_at < previous_time:
            raise SampleAccessChainV2Error("access timestamps are not monotone")
        event_type = event["event_type"]
        parent = cast(str, event["parent_event_hash"])
        if event_type == "ACCESS_ATTEMPT":
            if parent != GENESIS_HASH:
                raise SampleAccessChainV2Error("attempt causal parent is not genesis")
        elif parent not in prior_by_hash:
            raise SampleAccessChainV2Error("causal parent is absent or follows child")
        elif event_type == "ACCESS_RETRY":
            if prior_by_hash[parent]["event_type"] != "ACCESS_DENIAL":
                raise SampleAccessChainV2Error("retry does not parent a denial")
        elif prior_by_hash[parent]["event_type"] not in {"ACCESS_ATTEMPT", "ACCESS_RETRY"}:
            raise SampleAccessChainV2Error("result does not parent an attempt or retry")
        if event_type != "ACCESS_ATTEMPT":
            causal_parent = prior_by_hash[parent]
            for field in CAUSAL_EQUAL_FIELDS:
                if event[field] != causal_parent[field]:
                    raise SampleAccessChainV2Error(
                        f"causal child and parent disagree on {field}"
                    )
        previous = cast(str, event["event_hash"])
        previous_time = accessed_at
        seen_ids.add(event_id)
        prior_by_hash[previous] = event
        validated.append(event)
    return tuple(validated)


def _object_digest(event: Mapping[str, Any]) -> str:
    return _sha(OBJECT_DOMAIN + canonical_json_bytes(event))


def _leaf_digest(sequence: int, object_digest: str) -> str:
    return _sha(LEAF_DOMAIN + sequence.to_bytes(8, "big") + bytes.fromhex(object_digest))


def _merkle_levels(leaves: Sequence[str]) -> list[list[str]]:
    if not leaves:
        raise SampleAccessChainV2Error("Merkle tree requires at least one leaf")
    levels = [list(leaves)]
    while len(levels[-1]) > 1:
        current = levels[-1]
        parents: list[str] = []
        for index in range(0, len(current), 2):
            left = current[index]
            right = current[index + 1] if index + 1 < len(current) else left
            parents.append(_sha(NODE_DOMAIN + bytes.fromhex(left) + bytes.fromhex(right)))
        levels.append(parents)
    return levels


def _validated_predecessor(value: Any) -> dict[str, Any]:
    predecessor = _exact_dict(
        value,
        {
            "kind", "prior_export_sha256", "prior_merkle_root",
            "prior_event_count", "prior_head_event_hash",
        },
        "predecessor commitment",
    )
    if predecessor["kind"] == "GENESIS":
        if predecessor != {
            "kind": "GENESIS", "prior_export_sha256": None,
            "prior_merkle_root": None, "prior_event_count": 0,
            "prior_head_event_hash": GENESIS_HASH,
        }:
            raise SampleAccessChainV2Error("genesis predecessor sentinel changed")
    elif predecessor["kind"] == "PRIOR_EXPORT":
        for name in ("prior_export_sha256", "prior_merkle_root", "prior_head_event_hash"):
            _raw_hash(predecessor[name], name)
        if type(predecessor["prior_event_count"]) is not int or predecessor["prior_event_count"] < 1:
            raise SampleAccessChainV2Error("predecessor prior count is invalid")
    else:
        raise SampleAccessChainV2Error("predecessor kind is invalid")
    return predecessor


def _root_digest(
    chain_id: str,
    genesis_event_hash: str,
    predecessor: Mapping[str, Any],
    event_tree_root: str,
) -> str:
    envelope = {
        "chain_id": chain_id,
        "genesis_event_hash": genesis_event_hash,
        "predecessor": dict(predecessor),
    }
    return _sha(
        ROOT_DOMAIN + canonical_json_bytes(envelope) + bytes.fromhex(event_tree_root)
    )


def build_export(
    events: Sequence[Any],
    *,
    export_id: str,
    chain_id: str = "NEE-121-GLOBAL-SAMPLE-ACCESS-CHAIN",
    predecessor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validated = validate_causal_chain(events)
    export_name = _text(export_id, "export_id")
    chain_name = _text(chain_id, "chain_id")
    predecessor_document: dict[str, Any]
    if predecessor is None:
        predecessor_document = {
            "kind": "GENESIS", "prior_export_sha256": None, "prior_merkle_root": None,
            "prior_event_count": 0, "prior_head_event_hash": GENESIS_HASH,
        }
    else:
        predecessor_document = _validated_predecessor(dict(predecessor))
        if predecessor_document["kind"] != "PRIOR_EXPORT":
            raise SampleAccessChainV2Error("non-genesis predecessor must be PRIOR_EXPORT")
        prior_count = cast(int, predecessor_document["prior_event_count"])
        if prior_count >= len(validated):
            raise SampleAccessChainV2Error(
                "predecessor event count must be strictly below export event_count"
            )
        if validated[prior_count - 1]["event_hash"] != predecessor_document["prior_head_event_hash"]:
            raise SampleAccessChainV2Error(
                "predecessor head does not identify the declared event prefix"
            )
    objects: list[dict[str, Any]] = []
    index: list[dict[str, Any]] = []
    leaves: list[str] = []
    for event in validated:
        digest = _object_digest(event)
        leaf = _leaf_digest(cast(int, event["sequence"]), digest)
        objects.append({"content_sha256": digest, "event": event})
        index.append(
            {
                "sequence": event["sequence"], "event_hash": event["event_hash"],
                "content_sha256": digest, "leaf_sha256": leaf,
            }
        )
        leaves.append(leaf)
    document: dict[str, Any] = {
        "schema_version": "qme.sample_access_chain_export.v2",
        "export_id": export_name,
        "chain_id": chain_name,
        "genesis_event_hash": GENESIS_HASH,
        "predecessor": predecessor_document,
        "status": STATUS,
        "event_schema_version": "qme.sample_access_event.v1",
        "canonicalization": "UTF8_JSON_SORT_KEYS_COMPACT_NO_NAN",
        "object_hash_rule": "SHA256(OBJECT_DOMAIN || CANONICAL_FULL_EVENT_BYTES)",
        "leaf_hash_rule": "SHA256(LEAF_DOMAIN || UINT64_BE_SEQUENCE || OBJECT_DIGEST)",
        "node_hash_rule": "SHA256(NODE_DOMAIN || LEFT_DIGEST || RIGHT_DIGEST)",
        "root_hash_rule": "SHA256(ROOT_DOMAIN || CANONICAL_CHAIN_LINEAGE || EVENT_TREE_ROOT)",
        "odd_node_rule": ODD_NODE_RULE,
        "event_count": len(validated),
        "head_event_hash": validated[-1]["event_hash"],
        "ordered_index_sha256": _sha(canonical_json_bytes(index)),
        "merkle_root": _root_digest(
            chain_name,
            GENESIS_HASH,
            predecessor_document,
            _merkle_levels(leaves)[-1][0],
        ),
        "ordered_index": index,
        "objects": objects,
    }
    if len(canonical_json_bytes(document)) >= MAX_EXPORT_BYTES:
        raise SampleAccessChainV2Error("export reaches the 64 MiB bound")
    return document


def build_inclusion_proof(export: Mapping[str, Any], sequence: int) -> dict[str, Any]:
    verified = _validate_export(export)
    if type(sequence) is not int or not 1 <= sequence <= verified["event_count"]:
        raise SampleAccessChainV2Error("proof sequence is outside export")
    leaves = [cast(str, row["leaf_sha256"]) for row in verified["ordered_index"]]
    levels = _merkle_levels(leaves)
    cursor = sequence - 1
    steps: list[dict[str, str]] = []
    for level in levels[:-1]:
        if cursor % 2 == 0:
            sibling_index = cursor + 1 if cursor + 1 < len(level) else cursor
            side = "RIGHT"
        else:
            sibling_index = cursor - 1
            side = "LEFT"
        steps.append({"side": side, "sibling_sha256": level[sibling_index]})
        cursor //= 2
    row = verified["ordered_index"][sequence - 1]
    event = strict_json_bytes(
        canonical_json_bytes(verified["objects"][sequence - 1]["event"]),
        maximum=MAX_EVENT_BYTES,
    )
    return {
        "schema_version": "qme.sample_access_chain_proof.v2",
        "export_id": verified["export_id"], "sequence": sequence,
        "chain_id": verified["chain_id"], "genesis_event_hash": verified["genesis_event_hash"],
        "predecessor": strict_json_bytes(
            canonical_json_bytes(verified["predecessor"]), maximum=MAX_EVENT_BYTES
        ),
        "head_event_hash": verified["head_event_hash"],
        "ordered_index_sha256": verified["ordered_index_sha256"],
        "export_sha256": _sha(canonical_json_bytes(verified)),
        "leaf_count": verified["event_count"], "event": event,
        "event_hash": row["event_hash"], "content_sha256": row["content_sha256"],
        "leaf_sha256": row["leaf_sha256"], "merkle_root": verified["merkle_root"],
        "odd_node_rule": ODD_NODE_RULE, "steps": steps,
    }


def verify_strict_extension(prior_export: Mapping[str, Any], new_export: Mapping[str, Any]) -> None:
    """Require a longer export with byte-exact index and object prefixes."""

    prior = _validate_export(prior_export)
    new = _validate_export(new_export)
    prior_count = cast(int, prior["event_count"])
    if cast(int, new["event_count"]) <= prior_count:
        raise SampleAccessChainV2Error("extension must strictly increase chain length")
    if new["chain_id"] != prior["chain_id"] or new["genesis_event_hash"] != prior["genesis_event_hash"]:
        raise SampleAccessChainV2Error("extension chain or genesis differs")
    if new["predecessor"] != {
        "kind": "PRIOR_EXPORT",
        "prior_export_sha256": _sha(canonical_json_bytes(prior)),
        "prior_merkle_root": prior["merkle_root"],
        "prior_event_count": prior_count,
        "prior_head_event_hash": prior["head_event_hash"],
    }:
        raise SampleAccessChainV2Error("extension predecessor commitment differs")
    if new["ordered_index"][:prior_count] != prior["ordered_index"]:
        raise SampleAccessChainV2Error("extension ordered-index prefix differs")
    if new["objects"][:prior_count] != prior["objects"]:
        raise SampleAccessChainV2Error("extension content-object prefix differs")


def _validate_lineage(
    export: Mapping[str, Any],
    prior_resolver: SampleAccessChainResolver | None,
) -> None:
    predecessor = export["predecessor"]
    if predecessor["kind"] == "GENESIS":
        if prior_resolver is not None:
            raise SampleAccessChainV2Error("genesis export must not have a prior resolver")
        return
    if prior_resolver is None:
        raise SampleAccessChainV2Error(
            "PRIOR_EXPORT lineage requires an independently verified prior resolver"
        )
    if type(prior_resolver) is not SampleAccessChainResolver:
        raise SampleAccessChainV2Error("prior resolver must have exact immutable type")
    verify_strict_extension(dict(prior_resolver.export), export)


def export_commitment(export: Mapping[str, Any]) -> dict[str, Any]:
    checked = _validate_export(export)
    return {
        "schema_version": "qme.sample_access_chain_export_commitment.v2",
        "export_id": checked["export_id"],
        "chain_id": checked["chain_id"],
        "genesis_event_hash": checked["genesis_event_hash"],
        "predecessor": strict_json_bytes(
            canonical_json_bytes(checked["predecessor"]), maximum=MAX_EVENT_BYTES
        ),
        "event_count": checked["event_count"],
        "head_event_hash": checked["head_event_hash"],
        "ordered_index_sha256": checked["ordered_index_sha256"],
        "merkle_root": checked["merkle_root"],
        "export_sha256": _sha(canonical_json_bytes(checked)),
    }


def verify_inclusion_proof(proof: Any, trusted_commitment: Any) -> None:
    commitment = _exact_dict(
        trusted_commitment,
        {
            "schema_version", "export_id", "chain_id", "genesis_event_hash",
            "predecessor", "event_count", "head_event_hash",
            "ordered_index_sha256", "merkle_root", "export_sha256",
        },
        "trusted export commitment",
    )
    if commitment["schema_version"] != "qme.sample_access_chain_export_commitment.v2":
        raise SampleAccessChainV2Error("trusted commitment schema changed")
    export_id = _text(commitment["export_id"], "trusted export_id")
    _text(commitment["chain_id"], "trusted chain_id")
    if commitment["genesis_event_hash"] != GENESIS_HASH:
        raise SampleAccessChainV2Error("trusted genesis hash changed")
    predecessor = _validated_predecessor(commitment["predecessor"])
    if "/" in export_id or "\\" in export_id or ".." in export_id:
        raise SampleAccessChainV2Error("trusted export_id must be an identity, not a path")
    if type(commitment["event_count"]) is not int or commitment["event_count"] < 1:
        raise SampleAccessChainV2Error("trusted event_count is invalid")
    for name in (
        "head_event_hash", "ordered_index_sha256", "merkle_root", "export_sha256"
    ):
        _raw_hash(commitment[name], f"trusted {name}")
    keys = {
        "schema_version", "export_id", "sequence", "event_hash", "content_sha256",
        "leaf_sha256", "merkle_root", "odd_node_rule", "steps", "leaf_count", "event",
        "chain_id", "genesis_event_hash", "head_event_hash", "ordered_index_sha256",
        "export_sha256", "predecessor",
    }
    item = _exact_dict(proof, keys, "inclusion proof")
    if item["schema_version"] != "qme.sample_access_chain_proof.v2":
        raise SampleAccessChainV2Error("proof schema changed")
    if item["export_id"] != export_id:
        raise SampleAccessChainV2Error("proof export_id differs from trusted commitment")
    for name in (
        "chain_id", "genesis_event_hash", "predecessor", "head_event_hash", "ordered_index_sha256",
        "export_sha256",
    ):
        if item[name] != commitment[name]:
            raise SampleAccessChainV2Error(
                f"proof {name} differs from trusted commitment"
            )
    if item["odd_node_rule"] != ODD_NODE_RULE:
        raise SampleAccessChainV2Error("proof odd-node rule changed")
    sequence = item["sequence"]
    if type(sequence) is not int or sequence < 1:
        raise SampleAccessChainV2Error("proof sequence is invalid")
    leaf_count = item["leaf_count"]
    if type(leaf_count) is not int or leaf_count < sequence or leaf_count > 50_000:
        raise SampleAccessChainV2Error("proof leaf_count is invalid")
    if leaf_count != commitment["event_count"]:
        raise SampleAccessChainV2Error("proof leaf_count differs from trusted commitment")
    event = validate_v1_event(item["event"])
    if event["sequence"] != sequence or event["event_hash"] != item["event_hash"]:
        raise SampleAccessChainV2Error("proof event identity does not match sequence and hash")
    for name in ("event_hash", "content_sha256", "leaf_sha256", "merkle_root"):
        _raw_hash(item[name], name)
    expected_leaf = _leaf_digest(sequence, cast(str, item["content_sha256"]))
    if item["content_sha256"] != _object_digest(event):
        raise SampleAccessChainV2Error("proof content digest does not bind full event")
    if item["leaf_sha256"] != expected_leaf:
        raise SampleAccessChainV2Error("proof leaf does not bind object and sequence")
    steps = item["steps"]
    if type(steps) is not list or len(steps) > 63:
        raise SampleAccessChainV2Error("proof step list is invalid")
    current = expected_leaf
    cursor = sequence - 1
    width = leaf_count
    for raw_step in steps:
        step = _exact_dict(raw_step, {"side", "sibling_sha256"}, "proof step")
        sibling = _raw_hash(step["sibling_sha256"], "sibling_sha256")
        expected_side = "LEFT" if cursor % 2 else "RIGHT"
        if step["side"] != expected_side:
            raise SampleAccessChainV2Error("proof side disagrees with sequence path")
        if cursor % 2 == 0 and cursor + 1 >= width and sibling != current:
            raise SampleAccessChainV2Error("odd-node proof must duplicate current digest")
        if step["side"] == "LEFT":
            current = _sha(NODE_DOMAIN + bytes.fromhex(sibling) + bytes.fromhex(current))
        elif step["side"] == "RIGHT":
            current = _sha(NODE_DOMAIN + bytes.fromhex(current) + bytes.fromhex(sibling))
        else:
            raise SampleAccessChainV2Error("proof side is invalid")
        cursor //= 2
        width = (width + 1) // 2
    if width != 1:
        raise SampleAccessChainV2Error("proof path length does not reach root width")
    current = _root_digest(
        cast(str, item["chain_id"]),
        cast(str, item["genesis_event_hash"]),
        predecessor,
        current,
    )
    if current != item["merkle_root"]:
        raise SampleAccessChainV2Error("proof does not reach the registered root")
    if current != commitment["merkle_root"]:
        raise SampleAccessChainV2Error("proof root differs from trusted commitment")


def _validate_export(document: Any) -> dict[str, Any]:
    keys = {
        "schema_version", "export_id", "status", "event_schema_version",
        "canonicalization", "object_hash_rule", "leaf_hash_rule", "node_hash_rule",
        "root_hash_rule", "odd_node_rule", "event_count", "head_event_hash", "ordered_index_sha256",
        "merkle_root", "ordered_index", "objects", "chain_id", "genesis_event_hash",
        "predecessor",
    }
    export = _exact_dict(document, keys, "sample-access export")
    if len(canonical_json_bytes(export)) >= MAX_EXPORT_BYTES:
        raise SampleAccessChainV2Error("export reaches the 64 MiB bound")
    expected_literals = {
        "schema_version": "qme.sample_access_chain_export.v2",
        "status": STATUS,
        "event_schema_version": "qme.sample_access_event.v1",
        "canonicalization": "UTF8_JSON_SORT_KEYS_COMPACT_NO_NAN",
        "object_hash_rule": "SHA256(OBJECT_DOMAIN || CANONICAL_FULL_EVENT_BYTES)",
        "leaf_hash_rule": "SHA256(LEAF_DOMAIN || UINT64_BE_SEQUENCE || OBJECT_DIGEST)",
        "node_hash_rule": "SHA256(NODE_DOMAIN || LEFT_DIGEST || RIGHT_DIGEST)",
        "root_hash_rule": "SHA256(ROOT_DOMAIN || CANONICAL_CHAIN_LINEAGE || EVENT_TREE_ROOT)",
        "odd_node_rule": ODD_NODE_RULE,
    }
    for name, value in expected_literals.items():
        if export[name] != value:
            raise SampleAccessChainV2Error(f"export {name} changed")
    _text(export["export_id"], "export_id")
    _text(export["chain_id"], "chain_id")
    if export["genesis_event_hash"] != GENESIS_HASH:
        raise SampleAccessChainV2Error("export genesis hash changed")
    predecessor = _validated_predecessor(export["predecessor"])
    count = export["event_count"]
    if type(count) is not int or count < 1:
        raise SampleAccessChainV2Error("export event_count is invalid")
    index = export["ordered_index"]
    objects = export["objects"]
    if type(index) is not list or type(objects) is not list or len(index) != count or len(objects) != count:
        raise SampleAccessChainV2Error("export arrays do not match event_count")
    if export["ordered_index_sha256"] != _sha(canonical_json_bytes(index)):
        raise SampleAccessChainV2Error("ordered index hash mismatch")
    events: list[dict[str, Any]] = []
    leaves: list[str] = []
    seen_objects: set[str] = set()
    for expected, (raw_row, raw_object) in enumerate(zip(index, objects, strict=True), 1):
        row = _exact_dict(
            raw_row, {"sequence", "event_hash", "content_sha256", "leaf_sha256"}, "index row"
        )
        obj = _exact_dict(raw_object, {"content_sha256", "event"}, "content object")
        if row["sequence"] != expected:
            raise SampleAccessChainV2Error("ordered index is not contiguous")
        event = validate_v1_event(obj["event"])
        digest = _object_digest(event)
        leaf = _leaf_digest(expected, digest)
        if obj["content_sha256"] != digest or row != {
            "sequence": expected, "event_hash": event["event_hash"],
            "content_sha256": digest, "leaf_sha256": leaf,
        }:
            raise SampleAccessChainV2Error("content object and ordered index differ")
        if digest in seen_objects:
            raise SampleAccessChainV2Error("content object digest is duplicated")
        seen_objects.add(digest)
        events.append(event)
        leaves.append(leaf)
    validated = validate_causal_chain(events)
    if predecessor["kind"] == "PRIOR_EXPORT":
        prior_count = cast(int, predecessor["prior_event_count"])
        if prior_count >= count:
            raise SampleAccessChainV2Error(
                "predecessor event count must be strictly below export event_count"
            )
        if validated[prior_count - 1]["event_hash"] != predecessor["prior_head_event_hash"]:
            raise SampleAccessChainV2Error(
                "predecessor head does not identify the declared event prefix"
            )
    if export["head_event_hash"] != validated[-1]["event_hash"]:
        raise SampleAccessChainV2Error("export head hash mismatch")
    if export["merkle_root"] != _root_digest(
        cast(str, export["chain_id"]),
        GENESIS_HASH,
        predecessor,
        _merkle_levels(leaves)[-1][0],
    ):
        raise SampleAccessChainV2Error("export Merkle root mismatch")
    return export


class VerifiedSampleAccessChain:
    """Immutable replay data that must be independently revalidated to serialize."""

    __slots__ = ("_event_count", "_export_sha256", "_head", "_root")
    _event_count: int
    _export_sha256: str
    _head: str
    _root: str

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("VerifiedSampleAccessChain cannot be constructed directly")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("VerifiedSampleAccessChain cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise TypeError("VerifiedSampleAccessChain is immutable")

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def export_sha256(self) -> str:
        return self._export_sha256

    @property
    def head_event_hash(self) -> str:
        return self._head

    @property
    def merkle_root(self) -> str:
        return self._root


class SampleAccessChainResolver:
    """Immutable resolver built only after complete export validation."""

    __slots__ = ("_by_hash", "_export_bytes", "_prior_resolver", "_seal")
    _by_hash: Mapping[str, bytes]
    _export_bytes: bytes
    _prior_resolver: SampleAccessChainResolver | None
    _seal: str

    def __init__(
        self,
        export: Mapping[str, Any],
        *,
        prior_resolver: SampleAccessChainResolver | None = None,
    ) -> None:
        verified = _validate_export(export)
        if prior_resolver is not None and type(prior_resolver) is not SampleAccessChainResolver:
            raise SampleAccessChainV2Error("prior resolver must have exact immutable type")
        _validate_lineage(verified, prior_resolver)
        frozen = canonical_json_bytes(verified)
        reparsed = strict_json_bytes(frozen, maximum=MAX_EXPORT_BYTES)
        checked = _validate_export(reparsed)
        by_hash = {
            row["event"]["event_hash"]: canonical_json_bytes(row["event"])
            for row in checked["objects"]
        }
        object.__setattr__(self, "_export_bytes", frozen)
        object.__setattr__(self, "_by_hash", MappingProxyType(by_hash))
        object.__setattr__(self, "_prior_resolver", prior_resolver)
        object.__setattr__(self, "_seal", _sha(frozen))

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise TypeError("SampleAccessChainResolver is immutable")

    def resolve(self, event_hash: str) -> Mapping[str, Any]:
        digest = _raw_hash(event_hash, "event_hash")
        try:
            event_bytes = self._by_hash[digest]
        except KeyError as error:
            raise SampleAccessChainV2Error("event is absent from verified resolver") from error
        event = strict_json_bytes(event_bytes, maximum=MAX_EVENT_BYTES)
        return cast(Mapping[str, Any], _freeze_json(validate_v1_event(event)))

    @classmethod
    def from_repository_file(
        cls,
        root: Path,
        relative: str,
        *,
        prior_resolver: SampleAccessChainResolver | None = None,
    ) -> SampleAccessChainResolver:
        """Resolve and read one confined export file through one verified handle."""

        raw = _confined_file(root, relative, MAX_EXPORT_BYTES)
        document = strict_json_bytes(raw, maximum=MAX_EXPORT_BYTES)
        if type(document) is not dict:
            raise SampleAccessChainV2Error("export file root must be exact object")
        return cls(document, prior_resolver=prior_resolver)

    @property
    def export_sha256(self) -> str:
        return self._seal

    @property
    def export(self) -> Mapping[str, Any]:
        document = strict_json_bytes(self._export_bytes, maximum=MAX_EXPORT_BYTES)
        return MappingProxyType(_validate_export(document))

    @property
    def prior_export_commitment(self) -> Mapping[str, Any] | None:
        if self._prior_resolver is None:
            return None
        return MappingProxyType(export_commitment(dict(self._prior_resolver.export)))

    def compact_registry(self, *, current_run_id: str) -> dict[str, Any]:
        return build_compact_registry(
            dict(self.export),
            current_run_id=current_run_id,
            prior_resolver=self._prior_resolver,
        )


class _EmbeddedExperimentRegistryPrefixResolver:
    """Legacy embedded-chain reader retained only as a migration test oracle."""

    __slots__ = ("_export_bytes", "_sha256")
    _export_bytes: bytes
    _sha256: str

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("experiment registry prefix resolver is immutable")

    def __init__(self, export_bytes: bytes) -> None:
        if type(export_bytes) is not bytes:
            raise SampleAccessChainV2Error("registry prefix export must be exact bytes")
        raw = export_bytes
        if len(raw) >= MAX_EXPORT_BYTES:
            raise SampleAccessChainV2Error("registry prefix export reaches the 64 MiB bound")
        parsed = strict_json_bytes(raw, maximum=MAX_EXPORT_BYTES)
        if type(parsed) is not dict or canonical_json_bytes(parsed) != raw:
            raise SampleAccessChainV2Error(
                "registry prefix must use exact canonical protected-export bytes"
            )
        self._replay_exact(raw)
        object.__setattr__(self, "_export_bytes", raw)
        object.__setattr__(self, "_sha256", _sha(raw))

    @staticmethod
    def _replay_exact(raw: bytes) -> Any:
        from qme.experiments.registry import RegistryError, replay_registry

        document = strict_json_bytes(raw, maximum=MAX_EXPORT_BYTES)
        item = _exact_dict(
            document,
            {
                "schema_version", "registry_id", "causal_authority", "integrity_scope",
                "source_verification_disposition", "production_blockers", "event_count",
                "head_hash", "state_sha256", "events", "policies", "families", "trials",
                "timestamp_anomalies", "export_hash",
            },
            "experiment registry prefix export",
        )
        events = item["events"]
        if type(events) is not list:
            raise SampleAccessChainV2Error("registry prefix events must be an exact list")
        try:
            replay = replay_registry(events)
        except RegistryError as error:
            raise SampleAccessChainV2Error("protected experiment registry replay failed") from error
        if replay.to_export_document() != item:
            raise SampleAccessChainV2Error(
                "registry prefix export differs from protected deterministic replay"
            )
        return replay

    @property
    def export_sha256(self) -> str:
        return self._sha256

    @property
    def export(self) -> Mapping[str, Any]:
        document = strict_json_bytes(self._export_bytes, maximum=MAX_EXPORT_BYTES)
        if type(document) is not dict:
            raise SampleAccessChainV2Error("registry prefix export root changed")
        self._replay_exact(self._export_bytes)
        return MappingProxyType(document)

    @property
    def replay(self) -> Any:
        return self._replay_exact(self._export_bytes)


def _outcome_projection(events: Sequence[Mapping[str, Any]], current_run_id: str) -> dict[str, Any]:
    run_id = _text(current_run_id, "current_run_id")
    successes = [event for event in events if event["event_type"] == "ACCESS_SUCCESS"]
    exposed_identities = sorted(
        {
            (
                cast(str, event["sample_classification"]),
                cast(str, event["requested_start"]),
                cast(str, event["requested_end"]),
                cast(str, event["access_mode"]),
                cast(str, event["analysis_as_of"]),
                cast(str, event["data_vintage_at"]),
                cast(str, event["data_vintage_sha256"]),
            )
            for event in successes
        }
    )
    return {
        "all_access_success_event_hashes": [event["event_hash"] for event in successes],
        "current_run_access_success_event_hashes": [
            event["event_hash"] for event in successes if event["run_id"] == run_id
        ],
        "latest_success_accessed_at": _latest_timestamp_text(successes),
        "global_exposed_window_identities": [
            {
                "classification": identity[0],
                "start": identity[1],
                "end": identity[2],
                "access_mode": identity[3],
                "analysis_as_of": identity[4],
                "data_vintage_at": identity[5],
                "data_vintage_sha256": identity[6],
            }
            for identity in exposed_identities
        ],
    }


def _apply_versioned_registry_binding_v2(
    registry: Any,
    resolver: SampleAccessChainResolver,
    registry_prefix: _EmbeddedExperimentRegistryPrefixResolver,
) -> Mapping[str, Any]:
    """Apply protected V1 registry replay gates using internally derived authority."""

    context, _, _ = _derive_registry_context(resolver, registry_prefix)

    from qme.experiments.registry import (  # Linux-safe: does not import the store module
        RegistryError,
        validate_nee121_sample_access_binding,
    )

    if type(resolver) is not SampleAccessChainResolver:
        raise SampleAccessChainV2Error("V2 adapter requires exact resolver type")
    keys = {
        "trial_id", "trial_registration_event_hash", "access_contract_binding",
        "trial_status", "current_run_id", "registered_at", "run_started_at",
        "binding_occurred_at", "acknowledged_prefix_count", "registered_windows",
        "frozen_artifacts", "outcome", "nee121_v2_config_sha256",
        "nee121_v2_manifest_sha256",
        "acknowledged_prior_export_commitment",
    }
    state = _exact_dict(context, keys, "V2 registry adapter context")
    if state["trial_status"] != "RUNNING":
        raise SampleAccessChainV2Error("sample access binding requires a RUNNING trial")
    if (
        _raw_hash(state["nee121_v2_config_sha256"], "nee121_v2_config_sha256")
        != _ungroup(_NEE121_V2_CONFIG_SHA256)
        or _raw_hash(state["nee121_v2_manifest_sha256"], "nee121_v2_manifest_sha256")
        != _ungroup(_NEE121_V2_MANIFEST_SHA256)
    ):
        raise SampleAccessChainV2Error("active NEE-121 V2 config or manifest binding differs")
    if state["access_contract_binding"] != {
        "artifact_id": _REGISTERED_POLICY_NEE121_BINDING["artifact_id"],
        "source_id": _REGISTERED_POLICY_NEE121_BINDING["source_id"],
        "sha256": _ungroup(_REGISTERED_POLICY_NEE121_BINDING["sha256"]),
    }:
        raise SampleAccessChainV2Error(
            "sample access contract binding differs from registered policy authority"
        )
    compact = resolver.compact_registry(
        current_run_id=_text(state["current_run_id"], "current_run_id")
    )
    if registry != compact:
        raise SampleAccessChainV2Error("compact registry differs from resolver export")
    export = resolver.export
    chain = [object_row["event"] for object_row in export["objects"]]
    trial_id = _text(state["trial_id"], "trial_id")
    registration_hash = _raw_hash(
        state["trial_registration_event_hash"], "trial_registration_event_hash"
    )
    binding = {
        "access_contract_binding": state["access_contract_binding"],
        "access_event_chain": chain,
        "sample_access_log_head_hash": export["head_event_hash"],
        "trial_registration_event_hash": registration_hash,
    }
    try:
        validate_nee121_sample_access_binding(binding, expected_trial_id=trial_id)
    except RegistryError as error:
        raise SampleAccessChainV2Error("protected V1 access validator rejected compact chain") from error
    prefix_count = state["acknowledged_prefix_count"]
    if type(prefix_count) is not int or isinstance(prefix_count, bool) or not 0 <= prefix_count < len(chain):
        raise SampleAccessChainV2Error("acknowledged prefix count is invalid or non-extending")
    predecessor = export["predecessor"]
    if prefix_count == 0:
        if predecessor["kind"] != "GENESIS" or state["acknowledged_prior_export_commitment"] is not None:
            raise SampleAccessChainV2Error(
                "zero acknowledged prefix requires the exact genesis predecessor"
            )
    else:
        trusted_prior = state["acknowledged_prior_export_commitment"]
        actual_prior = resolver.prior_export_commitment
        if type(trusted_prior) is not dict or actual_prior is None:
            raise SampleAccessChainV2Error(
                "nonzero acknowledged prefix requires a trusted prior export commitment"
            )
        if trusted_prior != dict(actual_prior):
            raise SampleAccessChainV2Error(
                "acknowledged prior export commitment differs from verified lineage"
            )
        if (
            predecessor["kind"] != "PRIOR_EXPORT"
            or predecessor["prior_event_count"] != prefix_count
            or predecessor["prior_head_event_hash"] != chain[prefix_count - 1]["event_hash"]
            or predecessor["prior_export_sha256"] != trusted_prior["export_sha256"]
            or predecessor["prior_merkle_root"] != trusted_prior["merkle_root"]
            or trusted_prior["event_count"] != prefix_count
            or trusted_prior["head_event_hash"] != predecessor["prior_head_event_hash"]
            or trusted_prior["chain_id"] != export["chain_id"]
            or trusted_prior["genesis_event_hash"] != export["genesis_event_hash"]
        ):
            raise SampleAccessChainV2Error(
                "acknowledged prefix differs from the full prior export commitment"
            )
    suffix = chain[prefix_count:]
    run_id = _text(state["current_run_id"], "current_run_id")
    if any(event["trial_id"] != trial_id or event["run_id"] != run_id for event in suffix):
        raise SampleAccessChainV2Error("new compact suffix is not owned by the bound trial run")
    registered_at = _timestamp(state["registered_at"], "registered_at")
    run_started_at = _timestamp(state["run_started_at"], "run_started_at")
    binding_at = _timestamp(state["binding_occurred_at"], "binding_occurred_at")
    trial_events = [event for event in chain if event["trial_id"] == trial_id]
    current_run_events = [event for event in trial_events if event["run_id"] == run_id]
    if not current_run_events or current_run_events[0]["event_type"] != "ACCESS_ATTEMPT":
        raise SampleAccessChainV2Error("bound trial run does not begin with an access attempt")
    if any(_timestamp(event["accessed_at"], "accessed_at") < max(registered_at, run_started_at) for event in current_run_events):
        raise SampleAccessChainV2Error("access predates registration or run start")
    if any(_timestamp(event["accessed_at"], "accessed_at") > binding_at for event in current_run_events):
        raise SampleAccessChainV2Error("binding timestamp predates access")
    windows_raw = state["registered_windows"]
    if type(windows_raw) is not list or not windows_raw:
        raise SampleAccessChainV2Error("registered_windows must be a nonempty exact list")
    window_id_by_identity: dict[tuple[str, ...], str] = {}
    for raw_window in windows_raw:
        window = _exact_dict(
            raw_window,
            {"window_id", "classification", "start", "end", "access_mode", "analysis_as_of", "data_vintage_at", "data_vintage_sha256"},
            "registered window",
        )
        window_identity = (
            window["classification"], window["start"], window["end"], window["access_mode"],
            window["analysis_as_of"], window["data_vintage_at"], window["data_vintage_sha256"],
        )
        window_id = _text(window["window_id"], "window_id")
        if window_identity in window_id_by_identity or window_id in window_id_by_identity.values():
            raise SampleAccessChainV2Error("registered window identity or ID is duplicated")
        window_id_by_identity[window_identity] = window_id

    def event_window_identity(event: Mapping[str, Any]) -> tuple[str, ...]:
        return (
            cast(str, event["sample_classification"]), cast(str, event["requested_start"]),
            cast(str, event["requested_end"]), cast(str, event["access_mode"]),
            cast(str, event["analysis_as_of"]), cast(str, event["data_vintage_at"]),
            cast(str, event["data_vintage_sha256"]),
        )
    if any(event_window_identity(event) not in window_id_by_identity for event in trial_events):
        raise SampleAccessChainV2Error("bound access does not match a registered seven-field window")
    artifacts_raw = state["frozen_artifacts"]
    if type(artifacts_raw) is not list or not artifacts_raw:
        raise SampleAccessChainV2Error("frozen_artifacts must be a nonempty exact list")
    frozen_market: set[tuple[str, str]] = set()
    frozen_data_hashes: set[str] = set()
    for raw_artifact in artifacts_raw:
        artifact = _exact_dict(raw_artifact, {"role", "artifact_id", "sha256"}, "frozen artifact")
        role = artifact["role"]
        if role not in {"DATA", "UNIVERSE"}:
            raise SampleAccessChainV2Error("adapter accepts only frozen DATA/UNIVERSE artifacts")
        pair = (_text(artifact["artifact_id"], "artifact_id"), _raw_hash(artifact["sha256"], "artifact sha256"))
        frozen_market.add(pair)
        if role == "DATA":
            frozen_data_hashes.add(pair[1])
    expected_artifacts = frozen_market | {("QME-NEE122-TRIAL-REGISTRATION-EVENT", registration_hash)}
    for event in trial_events:
        observed = {(item["artifact_id"], item["artifact_sha256"]) for item in event["artifact_bindings"]}
        if observed != expected_artifacts or event["data_vintage_sha256"] not in frozen_data_hashes:
            raise SampleAccessChainV2Error("bound event differs from frozen artifacts or vintage")
    successes = {
        event["event_hash"]: event
        for event in trial_events
        if event["event_type"] == "ACCESS_SUCCESS"
    }
    current_run_successes = {
        digest: event for digest, event in successes.items() if event["run_id"] == run_id
    }
    outcome = _exact_dict(
        state["outcome"],
        {"occurred_at", "access_success_event_hashes", "required_sample_window_ids"},
        "outcome binding",
    )
    citations = outcome["access_success_event_hashes"]
    required_windows = outcome["required_sample_window_ids"]
    if type(citations) is not list or not citations or len(set(citations)) != len(citations):
        raise SampleAccessChainV2Error("outcome citations must be nonempty and unique")
    if not set(citations).issubset(current_run_successes):
        raise SampleAccessChainV2Error("outcome cites no exact current-run success")
    outcome_at = _timestamp(outcome["occurred_at"], "outcome occurred_at")
    if outcome_at < max(_timestamp(event["accessed_at"], "accessed_at") for event in successes.values()):
        raise SampleAccessChainV2Error("outcome predates latest successful access")
    if type(required_windows) is not list or not required_windows or len(set(required_windows)) != len(required_windows):
        raise SampleAccessChainV2Error("required sample window IDs must be nonempty and unique")
    cited_windows = {
        window_id_by_identity[event_window_identity(current_run_successes[digest])]
        for digest in citations
    }
    exposed_windows = {
        window_id_by_identity[event_window_identity(event)] for event in successes.values()
    }
    if cited_windows != set(required_windows) or exposed_windows != set(required_windows):
        raise SampleAccessChainV2Error("cited or exposed window IDs differ from frozen plan")
    return MappingProxyType(
        {
            "schema_version": "qme.sample_access_chain_registry_adapter_state.v2",
            "trial_id": trial_id, "run_id": run_id, "head_event_hash": export["head_event_hash"],
            "event_count": export["event_count"], "all_success_event_hashes": tuple(successes),
            "latest_success_accessed_at": _latest_timestamp_text(
                list(successes.values())
            ),
            "cited_success_event_hashes": tuple(citations),
            "cited_window_ids": tuple(sorted(cited_windows)),
            "exposed_window_ids": tuple(sorted(exposed_windows)),
        }
    )


def _derive_registry_context(
    resolver: SampleAccessChainResolver,
    registry_prefix: _EmbeddedExperimentRegistryPrefixResolver,
) -> tuple[dict[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    if type(registry_prefix) is not _EmbeddedExperimentRegistryPrefixResolver:
        raise SampleAccessChainV2Error(
            "versioned consumer requires exact immutable registry-prefix resolver"
        )
    registry_export = dict(registry_prefix.export)
    if registry_prefix.export_sha256 != _sha(canonical_json_bytes(registry_export)):
        raise SampleAccessChainV2Error(
            "registry-prefix content commitment differs from replayed bytes"
        )
    replay = registry_prefix.replay
    access_export = dict(resolver.export)
    chain = [cast(dict[str, Any], row["event"]) for row in access_export["objects"]]
    matching_trials: list[Mapping[str, Any]] = []
    matching_bindings: list[dict[str, Any]] = []
    for trial in replay.trials:
        for raw_binding in cast(tuple[Any, ...], trial["sample_access_bindings"]):
            binding = _plain_json(raw_binding)
            if (
                type(binding) is dict
                and binding.get("access_event_chain") == chain
                and binding.get("sample_access_log_head_hash") == access_export["head_event_hash"]
            ):
                matching_trials.append(trial)
                matching_bindings.append(binding)
    if len(matching_trials) != 1:
        raise SampleAccessChainV2Error(
            "registry prefix must contain exactly one trial binding for the compact chain"
        )
    trial = matching_trials[0]
    binding = matching_bindings[0]
    if trial["status"] != "RUNNING" or type(trial["run_id"]) is not str:
        raise SampleAccessChainV2Error("bound registry trial is not RUNNING")
    run_id = trial["run_id"]
    run_attempts = cast(tuple[Mapping[str, Any], ...], trial["run_attempts"])
    current_runs = [item for item in run_attempts if item["run_id"] == run_id]
    if len(current_runs) != 1:
        raise SampleAccessChainV2Error("registry prefix current run is absent or ambiguous")
    binding_events = [
        event
        for event in replay.events
        if event.event_type.value == "SAMPLE_ACCESS_BOUND"
        and event.trial_id == trial["trial_id"]
        and _plain_json(event.payload) == binding
    ]
    if len(binding_events) != 1:
        raise SampleAccessChainV2Error("registry access binding event is absent or ambiguous")
    outcomes = cast(tuple[Mapping[str, Any], ...], trial["outcomes"])
    if len(outcomes) != 1:
        raise SampleAccessChainV2Error(
            "bounded versioned consumer requires exactly one protected outcome"
        )
    outcome = outcomes[0]
    outcome_events = [
        event
        for event in replay.events
        if event.event_type.value == "OUTCOME_RECORDED"
        and event.trial_id == trial["trial_id"]
        and _plain_json(event.payload) == _plain_json(outcome)
    ]
    if len(outcome_events) != 1:
        raise SampleAccessChainV2Error("registry outcome event is absent or ambiguous")
    registration = cast(Mapping[str, Any], trial["registration"])
    matching_policies = [
        policy
        for policy in replay.policies
        if policy.policy_id == registration["policy_id"]
        and policy.policy_version == registration["policy_version"]
    ]
    if len(matching_policies) != 1:
        raise SampleAccessChainV2Error("registry trial policy is absent or ambiguous")
    policy = matching_policies[0]
    if _plain_json(policy.nee121_holdout_manifest_binding) != binding[
        "access_contract_binding"
    ]:
        raise SampleAccessChainV2Error(
            "registry access binding differs from its replayed policy"
        )
    plans = {
        item["plan_id"]: item
        for item in cast(tuple[Mapping[str, Any], ...], registration["planned_outcomes"])
    }
    plan = plans.get(outcome["plan_id"])
    if plan is None:
        raise SampleAccessChainV2Error("registry outcome has no registered plan")
    prefix_count = cast(int, access_export["predecessor"]["prior_event_count"])
    prior_commitment = (
        None if prefix_count == 0 else dict(resolver.prior_export_commitment or {})
    )
    if prefix_count and not prior_commitment:
        raise SampleAccessChainV2Error("compact predecessor lacks verified prior resolver")
    artifacts = [
        {
            "role": item["role"],
            "artifact_id": item["artifact_id"],
            "sha256": item["sha256"],
        }
        for item in cast(tuple[Mapping[str, Any], ...], registration["artifact_bindings"])
        if item["role"] in {"DATA", "UNIVERSE"}
    ]
    context = {
        "trial_id": trial["trial_id"],
        "trial_registration_event_hash": trial["registered_event_hash"],
        "access_contract_binding": _plain_json(binding["access_contract_binding"]),
        "trial_status": trial["status"],
        "current_run_id": run_id,
        "registered_at": trial["registered_at"],
        "run_started_at": current_runs[0]["started_at"],
        "binding_occurred_at": binding_events[0].to_document()["occurred_at"],
        "acknowledged_prefix_count": prefix_count,
        "acknowledged_prior_export_commitment": prior_commitment,
        "registered_windows": [
            _plain_json(item)
            for item in cast(tuple[Mapping[str, Any], ...], registration["sample_windows"])
        ],
        "frozen_artifacts": artifacts,
        "outcome": {
            "occurred_at": outcome_events[0].to_document()["occurred_at"],
            "access_success_event_hashes": list(outcome["access_success_event_hashes"]),
            "required_sample_window_ids": list(plan["required_sample_window_ids"]),
        },
        "nee121_v2_config_sha256": _ungroup(_NEE121_V2_CONFIG_SHA256),
        "nee121_v2_manifest_sha256": _ungroup(_NEE121_V2_MANIFEST_SHA256),
    }
    prefix_commitment = MappingProxyType(
        {
            "path": (
                "content-addressed/sha256/"
                f"{registry_prefix.export_sha256}.experiment-registry-export-v1.json"
            ),
            "sha256": registry_prefix.export_sha256,
            "event_count": registry_export["event_count"],
            "head_hash": registry_export["head_hash"],
            "state_sha256": registry_export["state_sha256"],
            "export_hash": registry_export["export_hash"],
        }
    )
    successful_accesses = {
        event["event_hash"]: event
        for raw_binding in cast(tuple[Mapping[str, Any], ...], trial["sample_access_bindings"])
        for event in cast(tuple[Mapping[str, Any], ...], raw_binding["access_event_chain"])
        if event["event_type"] == "ACCESS_SUCCESS"
        and event["trial_id"] == trial["trial_id"]
    }
    window_id_by_identity = {
        (
            item["classification"], item["start"], item["end"], item["access_mode"],
            item["analysis_as_of"], item["data_vintage_at"], item["data_vintage_sha256"],
        ): item["window_id"]
        for item in cast(tuple[Mapping[str, Any], ...], registration["sample_windows"])
    }

    def identity(event: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            event["sample_classification"], event["requested_start"], event["requested_end"],
            event["access_mode"], event["analysis_as_of"], event["data_vintage_at"],
            event["data_vintage_sha256"],
        )

    protected_expected = MappingProxyType(
        {
            "schema_version": "qme.sample_access_chain_registry_adapter_state.v2",
            "trial_id": trial["trial_id"],
            "run_id": run_id,
            "head_event_hash": access_export["head_event_hash"],
            "event_count": access_export["event_count"],
            "all_success_event_hashes": tuple(successful_accesses),
            "latest_success_accessed_at": _latest_timestamp_text(
                list(successful_accesses.values())
            ),
            "cited_success_event_hashes": tuple(outcome["access_success_event_hashes"]),
            "cited_window_ids": tuple(
                sorted(
                    {
                        window_id_by_identity[identity(successful_accesses[digest])]
                        for digest in outcome["access_success_event_hashes"]
                    }
                )
            ),
            "exposed_window_ids": tuple(
                sorted(
                    {
                        window_id_by_identity[identity(event)]
                        for event in successful_accesses.values()
                    }
                )
            ),
        }
    )
    return context, prefix_commitment, protected_expected


def _replay_embedded_registry_binding_v2(
    registry: Any,
    resolver: SampleAccessChainResolver,
    registry_prefix: _EmbeddedExperimentRegistryPrefixResolver,
) -> Mapping[str, Any]:
    """Derive all adapter authority from an independently replayed V1 prefix."""

    context, prefix_commitment, protected_expected = _derive_registry_context(
        resolver, registry_prefix
    )
    state = dict(_apply_versioned_registry_binding_v2(registry, resolver, registry_prefix))
    if state != dict(protected_expected):
        raise SampleAccessChainV2Error(
            "compact adapter state differs from protected V1 replay projection"
        )
    state["experiment_registry_prefix"] = prefix_commitment
    return MappingProxyType(state)


def _domain_document_hash(domain: bytes, document: Mapping[str, Any]) -> str:
    return _sha(domain + canonical_json_bytes(document))


def _canonical_timestamp(value: Any, name: str) -> str:
    parsed = _timestamp(value, name)
    canonical = parsed.isoformat().replace("+00:00", "Z")
    if value != canonical:
        raise SampleAccessChainV2Error(f"{name} must use canonical UTC Z form")
    return canonical


def _canonical_aware_datetime(value: Any, name: str) -> str:
    """Canonicalize an exact, offset-aware datetime without ambient-TZ fallback."""

    if type(value) is not datetime:
        raise SampleAccessChainV2Error(f"{name} must be an exact datetime")
    try:
        offset = value.utcoffset()
    except (OverflowError, TypeError, ValueError) as error:
        raise SampleAccessChainV2Error(f"{name} has an invalid UTC offset") from error
    if value.tzinfo is None or offset is None:
        raise SampleAccessChainV2Error(f"{name} must include a UTC offset")
    try:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (OverflowError, TypeError, ValueError) as error:
        raise SampleAccessChainV2Error(f"{name} cannot be canonicalized to UTC") from error


def _contains_key(value: Any, forbidden: str) -> bool:
    if type(value) is dict:
        return forbidden in value or any(
            _contains_key(item, forbidden) for item in value.values()
        )
    if type(value) is list:
        return any(_contains_key(item, forbidden) for item in value)
    return False


def _validated_access_export_commitment(value: Any) -> dict[str, Any]:
    item = _exact_dict(
        value,
        {
            "schema_version", "export_id", "chain_id", "genesis_event_hash",
            "predecessor", "event_count", "head_event_hash",
            "ordered_index_sha256", "merkle_root", "export_sha256",
        },
        "compact access export commitment",
    )
    if item["schema_version"] != "qme.sample_access_chain_export_commitment.v2":
        raise SampleAccessChainV2Error("compact access commitment schema changed")
    export_id = _text(item["export_id"], "compact access export_id")
    if "/" in export_id or "\\" in export_id or ".." in export_id:
        raise SampleAccessChainV2Error("compact access export_id is path-like")
    _text(item["chain_id"], "compact access chain_id")
    if item["genesis_event_hash"] != GENESIS_HASH:
        raise SampleAccessChainV2Error("compact access genesis hash changed")
    predecessor = _validated_predecessor(item["predecessor"])
    count = item["event_count"]
    if type(count) is not int or isinstance(count, bool) or count < 1:
        raise SampleAccessChainV2Error("compact access event_count must be positive")
    return {
        **item,
        "predecessor": predecessor,
        "head_event_hash": _raw_hash(item["head_event_hash"], "access head hash"),
        "ordered_index_sha256": _raw_hash(
            item["ordered_index_sha256"], "access ordered index hash"
        ),
        "merkle_root": _raw_hash(item["merkle_root"], "access Merkle root"),
        "export_sha256": _raw_hash(item["export_sha256"], "access export hash"),
    }


def _versioned_compact_payload(value: Any) -> dict[str, Any]:
    item = _exact_dict(
        value,
        {
            "access_contract_binding", "access_export_commitment",
            "access_export_content_path", "compact_registry_sha256",
            "current_run_id", "trial_registration_shadow_v1_event_hash",
        },
        "compact sample-access binding payload",
    )
    binding = _exact_dict(
        item["access_contract_binding"],
        {"artifact_id", "source_id", "sha256"},
        "compact access contract binding",
    )
    binding = {
        "artifact_id": _text(binding["artifact_id"], "binding artifact_id"),
        "source_id": _text(binding["source_id"], "binding source_id"),
        "sha256": _raw_hash(binding["sha256"], "binding sha256"),
    }
    commitment = _validated_access_export_commitment(
        item["access_export_commitment"]
    )
    expected_path = (
        "content-addressed/sha256/"
        f"{commitment['export_sha256']}.sample-access-chain-export-v2.json"
    )
    if item["access_export_content_path"] != expected_path:
        raise SampleAccessChainV2Error("compact access content path is not derived")
    return {
        "access_contract_binding": binding,
        "access_export_commitment": commitment,
        "access_export_content_path": expected_path,
        "compact_registry_sha256": _raw_hash(
            item["compact_registry_sha256"], "compact registry sha256"
        ),
        "current_run_id": _text(item["current_run_id"], "current_run_id"),
        "trial_registration_shadow_v1_event_hash": _raw_hash(
            item["trial_registration_shadow_v1_event_hash"],
            "trial registration shadow V1 event hash",
        ),
    }


def build_versioned_compact_binding_payload(
    resolver: SampleAccessChainResolver,
    *,
    current_run_id: str,
    access_contract_binding: Mapping[str, Any],
    trial_registration_shadow_v1_event_hash: str,
) -> dict[str, Any]:
    """Build the compact V2 receipt without embedding the access-event chain."""

    if type(resolver) is not SampleAccessChainResolver:
        raise SampleAccessChainV2Error("compact binding requires exact resolver type")
    export = dict(resolver.export)
    commitment = export_commitment(export)
    compact = resolver.compact_registry(current_run_id=current_run_id)
    payload = {
        "access_contract_binding": _plain_json(access_contract_binding),
        "access_export_commitment": commitment,
        "access_export_content_path": (
            "content-addressed/sha256/"
            f"{commitment['export_sha256']}.sample-access-chain-export-v2.json"
        ),
        "compact_registry_sha256": _sha(canonical_json_bytes(compact)),
        "current_run_id": current_run_id,
        "trial_registration_shadow_v1_event_hash": (
            trial_registration_shadow_v1_event_hash
        ),
    }
    normalized = _versioned_compact_payload(payload)
    if _contains_key(normalized, "access_event_chain"):
        raise SampleAccessChainV2Error("compact binding cannot embed an access chain")
    return normalized


def _normalize_versioned_payload(
    event_type: str,
    trial_id: str | None,
    payload: Any,
    occurred_at: str,
) -> dict[str, Any]:
    if type(payload) is not dict:
        raise SampleAccessChainV2Error("versioned event payload must be an exact object")
    if event_type == VERSIONED_COMPACT_EVENT_TYPE:
        if trial_id is None:
            raise SampleAccessChainV2Error("compact access binding requires trial_id")
        return _versioned_compact_payload(payload)
    if event_type == "SAMPLE_ACCESS_BOUND":
        raise SampleAccessChainV2Error(
            "embedded V1 SAMPLE_ACCESS_BOUND is forbidden in the V2 source registry"
        )
    from qme.experiments.registry import EventType, ExperimentEvent, RegistryError

    try:
        protected_type = EventType(event_type)
        shadow = ExperimentEvent.create(
            event_id="NEE-176-V2-PAYLOAD-VALIDATION",
            sequence=1,
            previous_event_hash=GENESIS_HASH,
            occurred_at=_timestamp(occurred_at, "occurred_at"),
            actor_id="NEE-176-V2-PAYLOAD-VALIDATION",
            event_type=protected_type,
            trial_id=trial_id,
            payload=payload,
        )
    except (RegistryError, ValueError) as error:
        raise SampleAccessChainV2Error(
            "versioned event payload violates protected V1 business syntax"
        ) from error
    return cast(dict[str, Any], _plain_json(shadow.payload))


_VERSIONED_EVENT_KEYS: Final = frozenset(
    {
        "schema_version", "registry_id", "event_id", "sequence",
        "previous_event_hash", "occurred_at", "actor_id", "event_type",
        "trial_id", "payload", "event_hash",
    }
)


def _validate_versioned_registry_event(value: Any) -> dict[str, Any]:
    item = _exact_dict(value, _VERSIONED_EVENT_KEYS, "versioned registry event")
    if (
        item["schema_version"] != VERSIONED_EVENT_SCHEMA
        or item["registry_id"] != VERSIONED_REGISTRY_ID
    ):
        raise SampleAccessChainV2Error("versioned registry event identity changed")
    event_id = _text(item["event_id"], "versioned event_id")
    sequence = item["sequence"]
    if type(sequence) is not int or isinstance(sequence, bool) or sequence < 1:
        raise SampleAccessChainV2Error("versioned event sequence must be positive")
    previous = _raw_hash(item["previous_event_hash"], "versioned previous hash")
    occurred_at = _canonical_timestamp(item["occurred_at"], "versioned occurred_at")
    actor_id = _text(item["actor_id"], "versioned actor_id")
    event_type = _text(item["event_type"], "versioned event_type")
    trial_id = item["trial_id"]
    if trial_id is not None:
        trial_id = _text(trial_id, "versioned trial_id")
    payload = _normalize_versioned_payload(
        event_type, cast(str | None, trial_id), item["payload"], occurred_at
    )
    unsigned = {
        "schema_version": VERSIONED_EVENT_SCHEMA,
        "registry_id": VERSIONED_REGISTRY_ID,
        "event_id": event_id,
        "sequence": sequence,
        "previous_event_hash": previous,
        "occurred_at": occurred_at,
        "actor_id": actor_id,
        "event_type": event_type,
        "trial_id": trial_id,
        "payload": payload,
    }
    event_hash = _raw_hash(item["event_hash"], "versioned event hash")
    if event_hash != _domain_document_hash(VERSIONED_EVENT_DOMAIN, unsigned):
        raise SampleAccessChainV2Error("versioned event hash does not match content")
    normalized = {**unsigned, "event_hash": event_hash}
    if _contains_key(normalized, "access_event_chain"):
        raise SampleAccessChainV2Error(
            "V2 source registry must not serialize access_event_chain"
        )
    if len(canonical_json_bytes(normalized)) >= MAX_EVENT_BYTES:
        raise SampleAccessChainV2Error("versioned event reaches the 2 MiB bound")
    return normalized


def _resolver_index(
    access_resolvers: tuple[SampleAccessChainResolver, ...],
) -> dict[str, SampleAccessChainResolver]:
    if type(access_resolvers) is not tuple:
        raise SampleAccessChainV2Error("access resolvers must be an exact immutable tuple")
    result: dict[str, SampleAccessChainResolver] = {}
    for resolver in access_resolvers:
        if type(resolver) is not SampleAccessChainResolver:
            raise SampleAccessChainV2Error("access resolver has non-exact type")
        export = dict(resolver.export)
        digest = _sha(canonical_json_bytes(export))
        if resolver.export_sha256 != digest or digest in result:
            raise SampleAccessChainV2Error(
                "access resolver commitment changed or is duplicated"
            )
        result[digest] = resolver
    return result


class _VersionedRegistryMaterial(NamedTuple):
    events: tuple[dict[str, Any], ...]
    shadow_events: tuple[Any, ...]
    protected_replay: Any
    compact_resolvers: Mapping[str, SampleAccessChainResolver]
    compact_events: tuple[dict[str, Any], ...]
    business_projection: Mapping[str, Any]


def _business_projection(
    protected_replay: Any,
    compact_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    receipts_by_trial: dict[str, list[dict[str, Any]]] = {}
    for event in compact_events:
        trial_id = cast(str, event["trial_id"])
        receipts_by_trial.setdefault(trial_id, []).append(
            cast(dict[str, Any], _plain_json(event["payload"]))
        )
    trials: list[dict[str, Any]] = []
    for frozen_trial in protected_replay.trials:
        trial = cast(dict[str, Any], _plain_json(frozen_trial))
        trial.pop("registered_event_hash")
        trial.pop("sample_access_bindings")
        trial["sample_access_receipts"] = receipts_by_trial.get(trial["trial_id"], [])
        trials.append(trial)
    projection = {
        "schema_version": "qme.compact_experiment_registry_business_state.v2",
        "protected_business_rule": {
            "registry_id": "NEE-122-GLOBAL-EXPERIMENT-REGISTRY-V1",
            "event_schema_version": "qme.experiment_registry_event.v1",
            "replay_api": "qme.experiments.registry.replay_registry",
            "source_path": "qme/experiments/registry.py",
            "source_sha256": _ungroup(
                "dba251da:59362a2f:c44c9660:58a3f067:f0e810a3:9551a929:e5e603e5:6f014785"
            ),
        },
        "policies": [
            {
                "policy": policy.to_document(),
                "family_frozen_sequence": protected_replay.policy_family_frozen_sequences[
                    policy.policy_id
                ],
                "family_freeze_cause": (
                    None
                    if protected_replay.policy_family_frozen_sequences[policy.policy_id]
                    is None
                    else "FIRST_TRIAL_STARTED"
                ),
            }
            for policy in protected_replay.policies
        ],
        "trials": trials,
        "bound_access_head_hashes": sorted(
            cast(str, event["payload"]["access_export_commitment"]["head_event_hash"])
            for event in compact_events
        ),
        "timestamp_anomalies": [
            _plain_json(item) for item in protected_replay.timestamp_anomalies
        ],
    }
    if _contains_key(projection, "access_event_chain"):
        raise SampleAccessChainV2Error("business projection leaked an embedded access chain")
    return projection


def _replay_versioned_registry_material(
    values: Sequence[Mapping[str, Any]],
    access_resolvers: tuple[SampleAccessChainResolver, ...],
) -> _VersionedRegistryMaterial:
    if type(values) not in {list, tuple}:
        raise SampleAccessChainV2Error("versioned registry events must be an exact list or tuple")
    resolver_by_hash = _resolver_index(access_resolvers)
    events: list[dict[str, Any]] = []
    shadow_events: list[Any] = []
    compact_events: list[dict[str, Any]] = []
    used_resolvers: dict[str, SampleAccessChainResolver] = {}
    event_ids: set[str] = set()
    shadow_registration_hashes: dict[str, str] = {}
    from qme.experiments.registry import (
        EventType,
        ExperimentEvent,
        RegistryError,
        replay_registry,
    )

    for raw in values:
        if type(raw) is not dict:
            raise SampleAccessChainV2Error("versioned registry event must be exact object")
        event = _validate_versioned_registry_event(raw)
        expected_sequence = len(events) + 1
        expected_previous = events[-1]["event_hash"] if events else GENESIS_HASH
        if event["sequence"] != expected_sequence:
            raise SampleAccessChainV2Error("versioned event sequence is not contiguous")
        if event["previous_event_hash"] != expected_previous:
            raise SampleAccessChainV2Error("versioned event previous hash forks the source chain")
        if event["event_id"] in event_ids:
            raise SampleAccessChainV2Error("versioned event_id must be append-only unique")
        event_ids.add(event["event_id"])
        shadow_previous = shadow_events[-1].event_hash if shadow_events else GENESIS_HASH
        if event["event_type"] == VERSIONED_COMPACT_EVENT_TYPE:
            payload = cast(dict[str, Any], event["payload"])
            commitment = cast(dict[str, Any], payload["access_export_commitment"])
            digest = cast(str, commitment["export_sha256"])
            resolver = resolver_by_hash.get(digest)
            if resolver is None:
                raise SampleAccessChainV2Error(
                    "compact event resolver is absent from the immutable resolver set"
                )
            export = dict(resolver.export)
            if export_commitment(export) != commitment:
                raise SampleAccessChainV2Error(
                    "compact event commitment differs from resolved access export"
                )
            compact = resolver.compact_registry(
                current_run_id=cast(str, payload["current_run_id"])
            )
            if _sha(canonical_json_bytes(compact)) != payload["compact_registry_sha256"]:
                raise SampleAccessChainV2Error("compact registry commitment differs")
            trial_id = cast(str, event["trial_id"])
            if shadow_registration_hashes.get(trial_id) != payload[
                "trial_registration_shadow_v1_event_hash"
            ]:
                raise SampleAccessChainV2Error(
                    "compact binding does not identify the translated V1 registration"
                )
            chain = [row["event"] for row in export["objects"]]
            shadow_payload = {
                "access_contract_binding": payload["access_contract_binding"],
                "access_event_chain": chain,
                "sample_access_log_head_hash": export["head_event_hash"],
                "trial_registration_event_hash": payload[
                    "trial_registration_shadow_v1_event_hash"
                ],
            }
            shadow_type = EventType.SAMPLE_ACCESS_BOUND
            used_resolvers[digest] = resolver
            compact_events.append(event)
        else:
            try:
                shadow_type = EventType(event["event_type"])
            except ValueError as error:
                raise SampleAccessChainV2Error(
                    "versioned event has no protected V1 translation"
                ) from error
            shadow_payload = event["payload"]
        try:
            shadow = ExperimentEvent.create(
                event_id=event["event_id"],
                sequence=event["sequence"],
                previous_event_hash=shadow_previous,
                occurred_at=_timestamp(event["occurred_at"], "occurred_at"),
                actor_id=event["actor_id"],
                event_type=shadow_type,
                trial_id=event["trial_id"],
                payload=shadow_payload,
            )
        except RegistryError as error:
            raise SampleAccessChainV2Error("V2-to-V1 shadow translation failed") from error
        if shadow_type is EventType.TRIAL_REGISTERED:
            assert event["trial_id"] is not None
            shadow_registration_hashes[event["trial_id"]] = shadow.event_hash
        events.append(event)
        shadow_events.append(shadow)
    if set(used_resolvers) != set(resolver_by_hash):
        raise SampleAccessChainV2Error("resolver set contains an unused or missing export")
    try:
        protected = replay_registry(shadow_events)
    except RegistryError as error:
        raise SampleAccessChainV2Error(
            "protected V1 business replay rejected translated V2 registry"
        ) from error
    projection = _business_projection(protected, compact_events)
    return _VersionedRegistryMaterial(
        tuple(events), tuple(shadow_events), protected,
        MappingProxyType(used_resolvers), tuple(compact_events),
        MappingProxyType(projection),
    )


def make_next_versioned_registry_event_v2(
    events: Sequence[Mapping[str, Any]],
    *,
    event_id: str,
    occurred_at: datetime,
    actor_id: str,
    event_type: str,
    trial_id: str | None,
    payload: Mapping[str, Any],
    access_resolvers: tuple[SampleAccessChainResolver, ...] = (),
) -> dict[str, Any]:
    timestamp = _canonical_aware_datetime(occurred_at, "occurred_at")
    if type(events) not in {list, tuple}:
        raise SampleAccessChainV2Error("prior events must be exact list or tuple")
    prior = _replay_versioned_registry_material(events, access_resolvers)
    unsigned = {
        "schema_version": VERSIONED_EVENT_SCHEMA,
        "registry_id": VERSIONED_REGISTRY_ID,
        "event_id": _text(event_id, "versioned event_id"),
        "sequence": len(prior.events) + 1,
        "previous_event_hash": (
            prior.events[-1]["event_hash"] if prior.events else GENESIS_HASH
        ),
        "occurred_at": timestamp,
        "actor_id": _text(actor_id, "versioned actor_id"),
        "event_type": _text(event_type, "versioned event_type"),
        "trial_id": None if trial_id is None else _text(trial_id, "versioned trial_id"),
        "payload": _plain_json(payload),
    }
    unsigned["payload"] = _normalize_versioned_payload(
        cast(str, unsigned["event_type"]), cast(str | None, unsigned["trial_id"]),
        unsigned["payload"], timestamp,
    )
    event = {
        **unsigned,
        "event_hash": _domain_document_hash(VERSIONED_EVENT_DOMAIN, unsigned),
    }
    return _validate_versioned_registry_event(event)


def deterministic_versioned_registry_export_v2(
    events: Sequence[Mapping[str, Any]],
    access_resolvers: tuple[SampleAccessChainResolver, ...],
) -> dict[str, Any]:
    material = _replay_versioned_registry_material(events, access_resolvers)
    projection = dict(material.business_projection)
    unsigned = {
        "schema_version": VERSIONED_EXPORT_SCHEMA,
        "registry_id": VERSIONED_REGISTRY_ID,
        "status": STATUS,
        "business_rule_binding": projection["protected_business_rule"],
        "event_count": len(material.events),
        "head_hash": material.events[-1]["event_hash"] if material.events else GENESIS_HASH,
        "source_events_sha256": _sha(canonical_json_bytes(list(material.events))),
        "shadow_v1_head_hash": material.protected_replay.head_hash,
        "shadow_v1_state_sha256": material.protected_replay.state_sha256,
        "business_projection_sha256": _domain_document_hash(
            VERSIONED_STATE_DOMAIN, projection
        ),
        "events": list(material.events),
        "business_projection": projection,
    }
    document = {
        **unsigned,
        "export_hash": _domain_document_hash(VERSIONED_EXPORT_DOMAIN, unsigned),
    }
    raw = canonical_json_bytes(document)
    if b'"access_event_chain"' in raw:
        raise SampleAccessChainV2Error("V2 registry export leaked an embedded chain")
    if len(raw) >= MAX_EXPORT_BYTES:
        raise SampleAccessChainV2Error("V2 registry export reaches the 64 MiB bound")
    return document


class VersionedExperimentRegistryResolver:
    """Immutable resolver for a compact V2 registry plus external access exports."""

    __slots__ = ("_access_resolvers", "_export_bytes", "_sha256")
    _access_resolvers: tuple[SampleAccessChainResolver, ...]
    _export_bytes: bytes
    _sha256: str

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("versioned experiment registry resolver is immutable")

    def __init__(
        self,
        export_bytes: bytes,
        access_resolvers: tuple[SampleAccessChainResolver, ...],
    ) -> None:
        if type(export_bytes) is not bytes:
            raise SampleAccessChainV2Error("V2 registry export must be exact bytes")
        if len(export_bytes) >= MAX_EXPORT_BYTES:
            raise SampleAccessChainV2Error("V2 registry export reaches the 64 MiB bound")
        if b'"access_event_chain"' in export_bytes:
            raise SampleAccessChainV2Error("V2 registry input embeds a full access chain")
        parsed = strict_json_bytes(export_bytes, maximum=MAX_EXPORT_BYTES)
        if type(parsed) is not dict or canonical_json_bytes(parsed) != export_bytes:
            raise SampleAccessChainV2Error("V2 registry export is not exact canonical JSON")
        expected = deterministic_versioned_registry_export_v2(
            cast(list[dict[str, Any]], parsed.get("events")), access_resolvers
        )
        if parsed != expected:
            raise SampleAccessChainV2Error("V2 registry export differs from deterministic replay")
        object.__setattr__(self, "_access_resolvers", access_resolvers)
        object.__setattr__(self, "_export_bytes", export_bytes)
        object.__setattr__(self, "_sha256", _sha(export_bytes))

    @property
    def export_sha256(self) -> str:
        if self._sha256 != _sha(self._export_bytes):
            raise SampleAccessChainV2Error("V2 registry resolver commitment changed")
        return self._sha256

    @property
    def content_path(self) -> str:
        return (
            "content-addressed/sha256/"
            f"{self.export_sha256}.compact-experiment-registry-export-v2.json"
        )

    @property
    def export(self) -> Mapping[str, Any]:
        parsed = strict_json_bytes(self._export_bytes, maximum=MAX_EXPORT_BYTES)
        if type(parsed) is not dict:
            raise SampleAccessChainV2Error("V2 registry export root changed")
        expected = deterministic_versioned_registry_export_v2(
            cast(list[dict[str, Any]], parsed.get("events")), self._access_resolvers
        )
        if parsed != expected:
            raise SampleAccessChainV2Error("V2 registry export no longer replays")
        return MappingProxyType(parsed)

    def _material(self) -> _VersionedRegistryMaterial:
        export = dict(self.export)
        return _replay_versioned_registry_material(
            cast(list[dict[str, Any]], export["events"]), self._access_resolvers
        )


def _access_adapter_state(
    trial: Mapping[str, Any],
    access_resolver: SampleAccessChainResolver,
) -> dict[str, Any]:
    trial_id = cast(str, trial["trial_id"])
    run_id = cast(str, trial["run_id"])
    export = dict(access_resolver.export)
    events = [row["event"] for row in export["objects"]]
    successes = {
        event["event_hash"]: event
        for event in events
        if event["event_type"] == "ACCESS_SUCCESS" and event["trial_id"] == trial_id
    }
    outcomes = cast(list[dict[str, Any]], trial["outcomes"])
    if len(outcomes) != 1:
        raise SampleAccessChainV2Error("bounded V2 consumer requires exactly one outcome")
    outcome = outcomes[0]
    citations = cast(list[str], outcome["access_success_event_hashes"])
    registration = cast(dict[str, Any], trial["registration"])
    windows = cast(list[dict[str, Any]], registration["sample_windows"])
    window_id_by_identity = {
        (
            item["classification"], item["start"], item["end"], item["access_mode"],
            item["analysis_as_of"], item["data_vintage_at"], item["data_vintage_sha256"],
        ): item["window_id"]
        for item in windows
    }

    def identity(event: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            event["sample_classification"], event["requested_start"],
            event["requested_end"], event["access_mode"], event["analysis_as_of"],
            event["data_vintage_at"], event["data_vintage_sha256"],
        )

    try:
        cited_windows = sorted(
            {window_id_by_identity[identity(successes[digest])] for digest in citations}
        )
        exposed_windows = sorted(
            {window_id_by_identity[identity(event)] for event in successes.values()}
        )
    except KeyError as error:
        raise SampleAccessChainV2Error("access projection has an unregistered citation") from error
    return {
        "schema_version": "qme.sample_access_chain_registry_adapter_state.v2",
        "trial_id": trial_id,
        "run_id": run_id,
        "head_event_hash": export["head_event_hash"],
        "event_count": export["event_count"],
        "all_success_event_hashes": tuple(successes),
        "latest_success_accessed_at": _latest_timestamp_text(list(successes.values())),
        "cited_success_event_hashes": tuple(citations),
        "cited_window_ids": tuple(cited_windows),
        "exposed_window_ids": tuple(exposed_windows),
    }


def replay_versioned_registry_binding_v2(
    registry_resolver: VersionedExperimentRegistryResolver,
    *,
    trial_id: str,
) -> Mapping[str, Any]:
    """Replay a true compact V2 registry and compare external and V1-shadow state."""

    if type(registry_resolver) is not VersionedExperimentRegistryResolver:
        raise SampleAccessChainV2Error("consumer requires exact V2 registry resolver type")
    material = registry_resolver._material()
    selected = [
        cast(dict[str, Any], _plain_json(trial))
        for trial in material.protected_replay.trials
        if trial["trial_id"] == _text(trial_id, "trial_id")
    ]
    if len(selected) != 1:
        raise SampleAccessChainV2Error("V2 registry trial is absent or ambiguous")
    trial = selected[0]
    if trial["status"] != "RUNNING" or type(trial["run_id"]) is not str:
        raise SampleAccessChainV2Error("V2 registry trial must be RUNNING")
    receipts = [
        event
        for event in material.compact_events
        if event["trial_id"] == trial_id
        and event["payload"]["current_run_id"] == trial["run_id"]
    ]
    if not receipts:
        raise SampleAccessChainV2Error("current run has no compact access receipt")
    receipt = receipts[-1]
    digest = cast(str, receipt["payload"]["access_export_commitment"]["export_sha256"])
    access_resolver = material.compact_resolvers[digest]
    external_state = _access_adapter_state(trial, access_resolver)
    shadow_bindings = cast(list[dict[str, Any]], trial["sample_access_bindings"])
    protected_chain = cast(list[dict[str, Any]], shadow_bindings[-1]["access_event_chain"])
    receipt_predecessor = cast(
        dict[str, Any], receipt["payload"]["access_export_commitment"]["predecessor"]
    )
    protected_export = build_export(
        protected_chain,
        export_id=cast(str, receipt["payload"]["access_export_commitment"]["export_id"]),
        predecessor=None if receipt_predecessor["kind"] == "GENESIS" else receipt_predecessor,
    )
    protected_state = _access_adapter_state(
        trial,
        SampleAccessChainResolver(
            protected_export,
            prior_resolver=access_resolver._prior_resolver,
        ),
    )
    if external_state != protected_state:
        raise SampleAccessChainV2Error(
            "external compact state differs from protected V1 shadow replay"
        )
    export = dict(registry_resolver.export)
    result = dict(external_state)
    result["experiment_registry_prefix"] = MappingProxyType(
        {
            "path": registry_resolver.content_path,
            "sha256": registry_resolver.export_sha256,
            "event_count": export["event_count"],
            "head_hash": export["head_hash"],
            "shadow_v1_state_sha256": export["shadow_v1_state_sha256"],
            "business_projection_sha256": export["business_projection_sha256"],
            "export_hash": export["export_hash"],
        }
    )
    return MappingProxyType(result)


def build_compact_registry(
    export: Mapping[str, Any],
    *,
    current_run_id: str,
    prior_resolver: SampleAccessChainResolver | None = None,
) -> dict[str, Any]:
    checked = _validate_export(export)
    _validate_lineage(checked, prior_resolver)
    document = {
        "schema_version": "qme.sample_access_chain_compact_registry.v2",
        "registry_id": "NEE-176-SAMPLE-ACCESS-CHAIN-COMPACT-REGISTRY-V2",
        "status": STATUS,
        "export_id": checked["export_id"],
        "chain_id": checked["chain_id"],
        "genesis_event_hash": checked["genesis_event_hash"],
        "predecessor": checked["predecessor"],
        "event_count": checked["event_count"],
        "head_event_hash": checked["head_event_hash"],
        "ordered_index_sha256": checked["ordered_index_sha256"],
        "merkle_root": checked["merkle_root"],
        "export_sha256": _sha(canonical_json_bytes(checked)),
        "current_run_id": _text(current_run_id, "current_run_id"),
        "outcome_projection": _outcome_projection(
            [row["event"] for row in checked["objects"]], current_run_id
        ),
    }
    if len(canonical_json_bytes(document)) >= MAX_COMPACT_REGISTRY_BYTES:
        raise SampleAccessChainV2Error("compact registry reaches the 2 MiB bound")
    return document


def _independently_replayed_values(
    registry: Any,
    resolver: SampleAccessChainResolver,
) -> tuple[int, str, str, str]:
    if type(resolver) is not SampleAccessChainResolver:
        raise SampleAccessChainV2Error(
            "registry replay requires exact immutable resolver type"
        )
    item = _exact_dict(
        registry,
        {
            "schema_version", "registry_id", "status", "export_id", "event_count",
            "head_event_hash", "ordered_index_sha256", "merkle_root", "export_sha256",
            "current_run_id", "outcome_projection", "chain_id", "genesis_event_hash",
            "predecessor",
        },
        "compact registry",
    )
    if item["schema_version"] != "qme.sample_access_chain_compact_registry.v2":
        raise SampleAccessChainV2Error("compact registry schema changed")
    if (
        item["registry_id"] != "NEE-176-SAMPLE-ACCESS-CHAIN-COMPACT-REGISTRY-V2"
        or item["status"] != STATUS
    ):
        raise SampleAccessChainV2Error("compact registry identity or status changed")
    export = dict(resolver.export)
    if resolver.export_sha256 != _sha(canonical_json_bytes(export)):
        raise SampleAccessChainV2Error("resolver content commitment differs from export bytes")
    expected = resolver.compact_registry(
        current_run_id=cast(str, item["current_run_id"])
    )
    if item != expected or resolver.export_sha256 != item["export_sha256"]:
        raise SampleAccessChainV2Error("compact registry does not bind verified export")
    for row in cast(list[dict[str, Any]], export["ordered_index"]):
        event = resolver.resolve(cast(str, row["event_hash"]))
        if event["sequence"] != row["sequence"]:
            raise SampleAccessChainV2Error("resolver returned wrong event")
    return (
        cast(int, item["event_count"]),
        cast(str, item["export_sha256"]),
        cast(str, item["head_event_hash"]),
        cast(str, item["merkle_root"]),
    )


def replay_compact_registry(
    registry: Any,
    resolver: SampleAccessChainResolver,
) -> VerifiedSampleAccessChain:
    values = _independently_replayed_values(registry, resolver)
    result = object.__new__(VerifiedSampleAccessChain)
    object.__setattr__(result, "_event_count", values[0])
    object.__setattr__(result, "_export_sha256", values[1])
    object.__setattr__(result, "_head", values[2])
    object.__setattr__(result, "_root", values[3])
    return result


def serialize_verified_result(
    result: VerifiedSampleAccessChain,
    registry: Any,
    resolver: SampleAccessChainResolver,
) -> dict[str, Any]:
    """Serialize only after independent export, lineage, resolver, and registry replay."""

    if type(result) is not VerifiedSampleAccessChain:
        raise SampleAccessChainV2Error("result must have exact immutable data type")
    expected = _independently_replayed_values(registry, resolver)
    try:
        observed = (
            result.event_count,
            result.export_sha256,
            result.head_event_hash,
            result.merkle_root,
        )
    except AttributeError as error:
        raise SampleAccessChainV2Error("verified result is incomplete") from error
    if observed != expected:
        raise SampleAccessChainV2Error(
            "result differs from independently revalidated artifacts"
        )
    return {
        "status": STATUS,
        "event_count": expected[0],
        "export_sha256": expected[1],
        "head_event_hash": expected[2],
        "merkle_root": expected[3],
    }


def generate_known_answer_events(count: int = MIN_KNOWN_ANSWER_EVENTS) -> list[dict[str, Any]]:
    if type(count) is not int or not MIN_KNOWN_ANSWER_EVENTS <= count <= 50_000:
        raise SampleAccessChainV2Error("known-answer event count must be 10,000..50,000")
    events: list[dict[str, Any]] = []
    previous = GENESIS_HASH
    last_attempt = GENESIS_HASH
    last_denial = GENESIS_HASH
    last_retry = GENESIS_HASH
    for sequence in range(1, count + 1):
        phase = (sequence - 1) % 4
        group = (sequence - 1) // 4
        if phase == 0:
            event_type, parent = "ACCESS_ATTEMPT", GENESIS_HASH
        elif phase == 1:
            event_type, parent = "ACCESS_DENIAL", last_attempt
        elif phase == 2:
            event_type, parent = "ACCESS_RETRY", last_denial
        else:
            event_type, parent = "ACCESS_SUCCESS", last_retry
        timestamp = (
            datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=sequence - 1)
        ).isoformat().replace("+00:00", "Z")
        bindings = [
            {"artifact_id": "SYNTHETIC-KAT", "artifact_sha256": _sha(b"nee-176-kat")},
            {"artifact_id": "TRIAL-REGISTRATION", "artifact_sha256": _sha(b"nee-176-trial")},
        ]
        payload: dict[str, Any] = {
            "schema_version": "qme.sample_access_event.v1",
            "governance_contract_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1",
            "event_id": f"nee-176-kat-{sequence:05d}", "sequence": sequence,
            "previous_event_hash": previous, "accessed_at": timestamp,
            "actor_id": "nee-176-deterministic-generator", "purpose": "BOUNDED_SYNTHETIC_KNOWN_ANSWER",
            "event_type": event_type, "trial_id": f"trial-{group:05d}",
            "run_id": "nee-176-prior-run" if group < 2_250 else "nee-176-kat-run",
            "query_id": f"query-{group:05d}",
            "analysis_as_of": "2026-01-31T00:00:00Z", "data_vintage_at": "2026-01-01T00:00:00Z",
            "data_vintage_sha256": _sha(f"vintage:{group}".encode()),
            "request_content_sha256": _sha(f"request:{group}".encode()),
            "parent_event_hash": parent, "contract_version": "v1",
            "sample_classification": "RETROSPECTIVE_EXTERNAL_STRESS_2022_PLUS",
            "requested_start": "2022-01-01", "requested_end": "2025-12-31",
            "access_mode": "READ",
            "artifact_bindings": bindings,
        }
        event_hash = _sha(canonical_json_bytes(payload))
        event = {**payload, "event_hash": event_hash}
        events.append(event)
        previous = event_hash
        if phase == 0:
            last_attempt = event_hash
        elif phase == 1:
            last_denial = event_hash
        elif phase == 2:
            last_retry = event_hash
    return events


def _kat_registry_policy_and_registration() -> tuple[Any, dict[str, Any], str, str]:
    from qme.experiments.registry import CostSelectionRole, PolicyMode, RegistryPolicy

    def binding(artifact_id: str) -> dict[str, str]:
        return {
            "artifact_id": artifact_id,
            "source_id": "NEE-176-BOUNDED-KAT",
            "sha256": _sha(artifact_id.encode()),
        }

    policy = RegistryPolicy(
        policy_id="NEE-176-KAT-POLICY-V1",
        policy_version=1,
        mode=PolicyMode.SYNTHETIC_TEST_ONLY,
        policy_binding=binding("NEE-176-KAT-POLICY-V1"),
        nee121_access_schema_binding=binding("qme.sample_access_event.v1"),
        nee121_holdout_manifest_binding={
            "artifact_id": _REGISTERED_POLICY_NEE121_BINDING["artifact_id"],
            "source_id": _REGISTERED_POLICY_NEE121_BINDING["source_id"],
            "sha256": _ungroup(_REGISTERED_POLICY_NEE121_BINDING["sha256"]),
        },
        axis_values={
            "filter": ("NONE", "QQQ_TR_SMA_14", "QQQ_TR_SMA_200", "SPY_TR_SMA_200"),
            "holding_period": ("H1", "H2", "H3"),
            "lookback": ("L1", "L2", "L3", "L4"),
            "rebalance": ("R1", "R2"),
        },
        cost_scenario_ids=("COST_LOW", "COST_BASE", "COST_HIGH"),
        cost_selection_role=CostSelectionRole.REPORTING_ONLY,
        family_size_m=96,
        predecessor_policy_id=None,
        predecessor_head_hash=None,
        predecessor_state_sha256=None,
    )
    data_hash = _sha(b"nee-176-versioned-kat-data")
    universe_hash = _sha(b"nee-176-versioned-kat-universe")
    roles = (
        "AGENT_OVERLAY", "BENCHMARK", "CODE", "CONFIG", "COST", "DATA",
        "FILTER", "HOLDING_PERIOD", "LOOKBACK", "REBALANCE", "SCHEMA",
        "SIGNAL", "TAX", "UNIVERSE",
    )
    registration = {
        "family_id": "NEE-176-KAT-FAMILY",
        "hypothesis_id": "NEE-176-KAT-HYPOTHESIS",
        "owner_id": "NEE-176-KAT",
        "parent_trial_id": None,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "configuration_class": "REGISTERED_GRID",
        "structural_configuration_id": "L1-H1-R1-NONE",
        "cost_scenario_ids": list(policy.cost_scenario_ids or ()),
        "selection_cost_scenario_id": "COST_BASE",
        "cost_selection_role": policy.cost_selection_role.value,
        "planned_outcomes": [
            {
                "plan_id": f"PLAN-{cost_id}",
                "outcome_artifact_id": f"NEE-176-KAT-OUTCOME-{cost_id}",
                "validation_report_schema_id": "qme.synthetic_validation_report.v1",
                "metric_id": "ANNUALIZED_SHARPE",
                "required_sample_window_ids": ["KAT-WINDOW"],
                "selection_role": (
                    "PRIMARY_SELECTION" if cost_id == "COST_BASE" else "REPORTING_ONLY"
                ),
                "benchmark_id": "QQQ_TR",
                "cost_scenario_id": cost_id,
                "direction": "HIGHER_IS_BETTER",
            }
            for cost_id in policy.cost_scenario_ids or ()
        ],
        "repository": {
            "repository_id": "D-QUANT-STOCKS-KAT",
            "commit_sha": "1" * 40,
            "tree_sha": "2" * 40,
            "dirty_worktree": False,
            "dirty_patch_binding": None,
            "untracked_manifest_binding": None,
        },
        "sample_windows": [
            {
                "window_id": "KAT-WINDOW",
                "classification": "RETROSPECTIVE_EXTERNAL_STRESS_2022_PLUS",
                "start": "2022-01-01",
                "end": "2025-12-31",
                "access_mode": "READ",
                "analysis_as_of": "2026-01-31T00:00:00Z",
                "data_vintage_at": "2026-01-01T00:00:00Z",
                "data_vintage_sha256": data_hash,
            }
        ],
        "dimension_registration": {
            "agent_overlay_id": "NONE", "benchmark_id": "QQQ_TR",
            "cost_id": "THREE_REPORTS", "filter_id": "NONE",
            "holding_period_id": "H1", "lookback_id": "L1",
            "rebalance_id": "R1", "signal_id": "QME_12_1",
            "tax_id": "REGISTERED_TRANSACTION_TAX_ONLY",
            "universe_id": "NASDAQ100_PIT",
        },
        "artifact_bindings": [
            {
                "role": role,
                "artifact_id": f"KAT-{role}",
                "source_id": "NEE-176-BOUNDED-KAT",
                "sha256": (
                    data_hash if role == "DATA"
                    else universe_hash if role == "UNIVERSE"
                    else _sha(role.encode())
                ),
            }
            for role in roles
        ],
    }
    return policy, registration, data_hash, universe_hash


def _kat_access_events(
    registration_hash: str,
    data_hash: str,
    universe_hash: str,
    count: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    prior_by_type: dict[str, str] = {}
    for sequence in range(1, count + 1):
        phase = (sequence - 1) % 4
        event_type = (
            "ACCESS_ATTEMPT", "ACCESS_DENIAL", "ACCESS_RETRY", "ACCESS_SUCCESS"
        )[phase]
        if event_type == "ACCESS_ATTEMPT":
            parent = GENESIS_HASH
        else:
            parent = prior_by_type[
                {
                    "ACCESS_DENIAL": "ACCESS_ATTEMPT",
                    "ACCESS_RETRY": "ACCESS_DENIAL",
                    "ACCESS_SUCCESS": "ACCESS_RETRY",
                }[event_type]
            ]
        run_id = "NEE-176-KAT-RUN-1" if sequence <= 9_000 else "NEE-176-KAT-RUN-2"
        unsigned = {
            "schema_version": "qme.sample_access_event.v1",
            "governance_contract_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1",
            "event_id": f"nee-176-versioned-kat-{sequence:05d}",
            "sequence": sequence,
            "previous_event_hash": events[-1]["event_hash"] if events else GENESIS_HASH,
            "accessed_at": (
                datetime(2026, 1, 2, tzinfo=UTC) + timedelta(seconds=sequence - 1)
            ).isoformat().replace("+00:00", "Z"),
            "actor_id": "nee-176-versioned-kat",
            "purpose": "BOUNDED_SYNTHETIC_KNOWN_ANSWER",
            "event_type": event_type,
            "trial_id": "NEE-176-KAT-TRIAL",
            "run_id": run_id,
            "query_id": "NEE-176-KAT-QUERY",
            "analysis_as_of": "2026-01-31T00:00:00Z",
            "data_vintage_at": "2026-01-01T00:00:00Z",
            "data_vintage_sha256": data_hash,
            "request_content_sha256": _sha(b"nee-176-versioned-kat-request"),
            "parent_event_hash": parent,
            "contract_version": "v1",
            "sample_classification": "RETROSPECTIVE_EXTERNAL_STRESS_2022_PLUS",
            "requested_start": "2022-01-01",
            "requested_end": "2025-12-31",
            "access_mode": "READ",
            "artifact_bindings": [
                {
                    "artifact_id": "QME-NEE122-TRIAL-REGISTRATION-EVENT",
                    "artifact_sha256": registration_hash,
                },
                {"artifact_id": "KAT-DATA", "artifact_sha256": data_hash},
                {"artifact_id": "KAT-UNIVERSE", "artifact_sha256": universe_hash},
            ],
        }
        event = {
            **unsigned,
            "event_hash": _sha(canonical_json_bytes(unsigned)),
        }
        events.append(event)
        prior_by_type[event_type] = cast(str, event["event_hash"])
    return events


_KNOWN_ANSWER_VERSIONED_CACHE: bytes | None = None


def _known_answer_versioned_registry() -> dict[str, Any]:
    global _KNOWN_ANSWER_VERSIONED_CACHE
    if _KNOWN_ANSWER_VERSIONED_CACHE is not None:
        cached = strict_json_bytes(
            _KNOWN_ANSWER_VERSIONED_CACHE, maximum=MAX_EVENT_BYTES
        )
        if type(cached) is not dict:
            raise SampleAccessChainV2Error("versioned KAT cache root changed")
        return cached
    from qme.experiments.registry import validation_report_binding

    policy, registration, data_hash, universe_hash = _kat_registry_policy_and_registration()
    registry_events: list[dict[str, Any]] = []
    registry_events.append(
        make_next_versioned_registry_event_v2(
            registry_events,
            event_id="NEE-176-KAT-POLICY",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            actor_id="NEE-176-KAT",
            event_type="POLICY_REGISTERED",
            trial_id=None,
            payload={"policy": policy.to_document()},
        )
    )
    registry_events.append(
        make_next_versioned_registry_event_v2(
            registry_events,
            event_id="NEE-176-KAT-REGISTRATION",
            occurred_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
            actor_id="NEE-176-KAT",
            event_type="TRIAL_REGISTERED",
            trial_id="NEE-176-KAT-TRIAL",
            payload=registration,
        )
    )
    registration_hash = cast(
        str,
        deterministic_versioned_registry_export_v2(registry_events, ())[
            "shadow_v1_head_hash"
        ],
    )
    access_events = _kat_access_events(
        registration_hash, data_hash, universe_hash, MIN_KNOWN_ANSWER_EVENTS
    )
    first_export = build_export(
        access_events[:9_000], export_id="NEE-176-KAT-ACCESS-PREFIX"
    )
    first_resolver = SampleAccessChainResolver(first_export)
    registry_events.append(
        make_next_versioned_registry_event_v2(
            registry_events,
            event_id="NEE-176-KAT-START-1",
            occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
            actor_id="NEE-176-KAT",
            event_type="TRIAL_STARTED",
            trial_id="NEE-176-KAT-TRIAL",
            payload={"run_id": "NEE-176-KAT-RUN-1", "retry_reason": None},
        )
    )
    first_payload = build_versioned_compact_binding_payload(
        first_resolver,
        current_run_id="NEE-176-KAT-RUN-1",
        access_contract_binding=policy.nee121_holdout_manifest_binding,
        trial_registration_shadow_v1_event_hash=registration_hash,
    )
    registry_events.append(
        make_next_versioned_registry_event_v2(
            registry_events,
            event_id="NEE-176-KAT-COMPACT-1",
            occurred_at=datetime(2026, 1, 2, 2, 30, tzinfo=UTC),
            actor_id="NEE-176-KAT",
            event_type=VERSIONED_COMPACT_EVENT_TYPE,
            trial_id="NEE-176-KAT-TRIAL",
            payload=first_payload,
        )
    )
    registry_events.append(
        make_next_versioned_registry_event_v2(
            registry_events,
            event_id="NEE-176-KAT-START-2",
            occurred_at=datetime(2026, 1, 2, 2, 30, tzinfo=UTC),
            actor_id="NEE-176-KAT",
            event_type="TRIAL_STARTED",
            trial_id="NEE-176-KAT-TRIAL",
            payload={
                "run_id": "NEE-176-KAT-RUN-2",
                "retry_reason": "TECHNICAL RETRY BEFORE OUTCOME",
            },
            access_resolvers=(first_resolver,),
        )
    )
    predecessor = {
        "kind": "PRIOR_EXPORT",
        "prior_export_sha256": _sha(canonical_json_bytes(first_export)),
        "prior_merkle_root": first_export["merkle_root"],
        "prior_event_count": first_export["event_count"],
        "prior_head_event_hash": first_export["head_event_hash"],
    }
    second_export = build_export(
        access_events,
        export_id="NEE-176-KAT-ACCESS-CURRENT",
        predecessor=predecessor,
    )
    second_resolver = SampleAccessChainResolver(
        second_export, prior_resolver=first_resolver
    )
    second_payload = build_versioned_compact_binding_payload(
        second_resolver,
        current_run_id="NEE-176-KAT-RUN-2",
        access_contract_binding=policy.nee121_holdout_manifest_binding,
        trial_registration_shadow_v1_event_hash=registration_hash,
    )
    registry_events.append(
        make_next_versioned_registry_event_v2(
            registry_events,
            event_id="NEE-176-KAT-COMPACT-2",
            occurred_at=datetime(2026, 1, 2, 3, tzinfo=UTC),
            actor_id="NEE-176-KAT",
            event_type=VERSIONED_COMPACT_EVENT_TYPE,
            trial_id="NEE-176-KAT-TRIAL",
            payload=second_payload,
            access_resolvers=(first_resolver,),
        )
    )
    current_successes = [
        event["event_hash"]
        for event in access_events[9_000:]
        if event["event_type"] == "ACCESS_SUCCESS"
    ]
    report = {
        "schema_version": "qme.synthetic_validation_report.v1",
        "status": "TEST_ONLY",
        "observations": 1_000,
        "metric_id": "ANNUALIZED_SHARPE",
        "benchmark_id": "QQQ_TR",
        "cost_scenario_id": "COST_BASE",
        "direction": "HIGHER_IS_BETTER",
    }
    registry_events.append(
        make_next_versioned_registry_event_v2(
            registry_events,
            event_id="NEE-176-KAT-OUTCOME",
            occurred_at=datetime(2026, 1, 2, 4, tzinfo=UTC),
            actor_id="NEE-176-KAT",
            event_type="OUTCOME_RECORDED",
            trial_id="NEE-176-KAT-TRIAL",
            payload={
                "access_success_event_hashes": current_successes,
                "plan_id": "PLAN-COST_BASE",
                "outcome_binding": {
                    "artifact_id": "NEE-176-KAT-OUTCOME-COST_BASE",
                    "source_id": "NEE-176-BOUNDED-KAT",
                    "sha256": _sha(b"NEE-176-KAT-OUTCOME-COST_BASE"),
                },
                "validation_report": report,
                "validation_report_binding": validation_report_binding(
                    "NEE-176-KAT-VALIDATION", "NEE-176-BOUNDED-KAT", report
                ),
            },
            access_resolvers=(first_resolver, second_resolver),
        )
    )
    registry_export = deterministic_versioned_registry_export_v2(
        registry_events, (first_resolver, second_resolver)
    )
    resolver = VersionedExperimentRegistryResolver(
        canonical_json_bytes(registry_export), (first_resolver, second_resolver)
    )
    state = replay_versioned_registry_binding_v2(
        resolver, trial_id="NEE-176-KAT-TRIAL"
    )
    registry_raw = canonical_json_bytes(registry_export)
    if b'"access_event_chain"' in registry_raw:
        raise SampleAccessChainV2Error("known answer leaked an embedded chain")
    compact_event_bytes = [
        len(canonical_json_bytes(event))
        for event in registry_events
        if event["event_type"] == VERSIONED_COMPACT_EVENT_TYPE
    ]
    summary = {
        "external_event_count": len(access_events),
        "external_export_sha256": second_resolver.export_sha256,
        "external_merkle_root": second_export["merkle_root"],
        "external_head_event_hash": second_export["head_event_hash"],
        "registry_event_count": registry_export["event_count"],
        "compact_binding_count": 2,
        "registry_export_sha256": resolver.export_sha256,
        "registry_head_hash": registry_export["head_hash"],
        "shadow_v1_head_hash": registry_export["shadow_v1_head_hash"],
        "shadow_v1_state_sha256": registry_export["shadow_v1_state_sha256"],
        "business_projection_sha256": registry_export["business_projection_sha256"],
        "registry_serialized_bytes": len(registry_raw),
        "maximum_registry_event_bytes": max(
            len(canonical_json_bytes(event)) for event in registry_events
        ),
        "compact_binding_event_bytes": compact_event_bytes,
        "maximum_compact_binding_growth_bytes": abs(
            compact_event_bytes[1] - compact_event_bytes[0]
        ),
        "contains_access_event_chain_key": False,
        "current_state_sha256": _sha(canonical_json_bytes(_plain_json(state))),
    }
    _KNOWN_ANSWER_VERSIONED_CACHE = canonical_json_bytes(summary)
    return summary


def known_answer_summary(count: int = MIN_KNOWN_ANSWER_EVENTS) -> dict[str, Any]:
    events = generate_known_answer_events(count)
    prefix_count = 9_000
    prior_export = build_export(
        events[:prefix_count], export_id="NEE-176-DETERMINISTIC-9000-EVENT-PREFIX-KAT"
    )
    predecessor = {
        "kind": "PRIOR_EXPORT",
        "prior_export_sha256": _sha(canonical_json_bytes(prior_export)),
        "prior_merkle_root": prior_export["merkle_root"],
        "prior_event_count": prior_export["event_count"],
        "prior_head_event_hash": prior_export["head_event_hash"],
    }
    export = build_export(
        events,
        export_id="NEE-176-DETERMINISTIC-10000-EVENT-KAT",
        predecessor=predecessor,
    )
    verify_strict_extension(prior_export, export)
    prior_resolver = SampleAccessChainResolver(prior_export)
    registry = build_compact_registry(
        export,
        current_run_id="nee-176-kat-run",
        prior_resolver=prior_resolver,
    )
    outcome = cast(dict[str, Any], registry["outcome_projection"])
    registry_summary = {
        key: value for key, value in registry.items() if key != "outcome_projection"
    }
    registry_summary.update(
        {
            "outcome_projection_sha256": _sha(canonical_json_bytes(outcome)),
            "all_success_count": len(outcome["all_access_success_event_hashes"]),
                "current_run_success_count": len(outcome["current_run_access_success_event_hashes"]),
                "latest_success_accessed_at": outcome["latest_success_accessed_at"],
                "global_exposed_window_identity_count": len(
                    outcome["global_exposed_window_identities"]
                ),
        }
    )
    sequences = [1, 2, 3, count // 2, count - 1, count]
    proofs = [build_inclusion_proof(export, sequence) for sequence in sequences]
    summary = {
        "schema_version": "qme.sample_access_chain_known_answer.v1",
        "generator": {
            "event_count": count, "pattern": "ATTEMPT_DENIAL_RETRY_SUCCESS_REPEAT",
            "timestamp_rule": "BASE_2026_01_01_PLUS_SEQUENCE_MINUS_ONE_SECONDS_UTC",
        },
        "first_event_hash": events[0]["event_hash"], "last_event_hash": events[-1]["event_hash"],
        "export_sha256": registry["export_sha256"], "ordered_index_sha256": registry["ordered_index_sha256"],
        "merkle_root": registry["merkle_root"], "compact_registry_summary": registry_summary,
        "extension_summary": {
            "prior_export_id": prior_export["export_id"],
            "prior_event_count": prior_export["event_count"],
            "prior_head_event_hash": prior_export["head_event_hash"],
            "prior_merkle_root": prior_export["merkle_root"],
            "prior_export_sha256": predecessor["prior_export_sha256"],
            "extension_event_count": export["event_count"],
            "extension_suffix_count": export["event_count"] - prior_export["event_count"],
            "extension_current_run_id": "nee-176-kat-run",
            "prefix_index_sha256": _sha(
                canonical_json_bytes(export["ordered_index"][:prefix_count])
            ),
            "prefix_objects_sha256": _sha(
                canonical_json_bytes(export["objects"][:prefix_count])
            ),
        },
        "versioned_registry_consumer": {
            "caller_authored_context_allowed": False,
            "authority_source": "CONTENT_ADDRESSED_COMPACT_V2_REGISTRY_WITH_IN_MEMORY_PROTECTED_V1_SHADOW_REPLAY",
            "commitment_fields": [
                "CONTENT_ADDRESSED_DERIVED_PATH", "RAW_SHA256", "EVENT_COUNT",
                "HEAD_HASH", "SHADOW_V1_STATE_SHA256",
                "BUSINESS_PROJECTION_SHA256", "EXPORT_HASH",
            ],
            "required_event_types": [
                "POLICY_REGISTERED", "TRIAL_REGISTERED", "TRIAL_STARTED",
                "SAMPLE_ACCESS_BOUND_COMPACT", "OUTCOME_RECORDED",
            ],
        },
        "versioned_registry_known_answer": _known_answer_versioned_registry(),
        "proof_sequences": sequences, "proofs_sha256": _sha(canonical_json_bytes(proofs)),
        "terminal_proof": proofs[-1],
        "serialized_export_bytes": len(canonical_json_bytes(export)),
        "maximum_serialized_event_bytes": max(len(canonical_json_bytes(event)) for event in events),
    }
    return cast(dict[str, Any], _group_hash_strings(summary))


def _group_hash_strings(value: Any) -> Any:
    """Represent fixture digests as exact 8x8 groups without changing wire formats."""

    if type(value) is str and _HASH_RE.fullmatch(value) is not None:
        return _group(value)
    if type(value) is list:
        return [_group_hash_strings(item) for item in value]
    if type(value) is dict:
        return {key: _group_hash_strings(item) for key, item in value.items()}
    return value


def _confined_file(root: Path, relative: str, maximum: int) -> bytes:
    if type(relative) is not str or not relative or "\\" in relative:
        raise SampleAccessChainV2Error("artifact path must be canonical repository-relative POSIX")
    pure = Path(relative)
    if pure.is_absolute() or ".." in pure.parts or str(pure).replace("\\", "/") != relative:
        raise SampleAccessChainV2Error("artifact path escapes repository root")
    resolved_root = root.resolve(strict=True)
    candidate = resolved_root.joinpath(*relative.split("/"))
    try:
        current = resolved_root
        for part in relative.split("/"):
            current /= part
            component_stat = current.lstat()
            if current.is_symlink() or (
                getattr(component_stat, "st_file_attributes", 0) & 0x400
            ):
                raise SampleAccessChainV2Error(
                    "symlink or reparse-point path component is forbidden"
                )
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
        path_stat = candidate.stat()
    except SampleAccessChainV2Error:
        raise
    except (OSError, ValueError) as error:
        raise SampleAccessChainV2Error("artifact is absent or outside repository") from error
    attributes = getattr(path_stat, "st_file_attributes", 0)
    if attributes & getattr(os.stat_result, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise SampleAccessChainV2Error("reparse-point artifact is forbidden")
    if not candidate.is_file() or path_stat.st_size <= 0 or path_stat.st_size > maximum:
        raise SampleAccessChainV2Error("artifact is nonregular or outside size bound")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
        with os.fdopen(descriptor, "rb") as handle:
            handle_stat = os.fstat(handle.fileno())
            if not stat_module.S_ISREG(handle_stat.st_mode):
                raise SampleAccessChainV2Error("artifact handle is not a regular file")
            if (handle_stat.st_dev, handle_stat.st_ino, handle_stat.st_size) != (
                path_stat.st_dev, path_stat.st_ino, path_stat.st_size,
            ):
                raise SampleAccessChainV2Error("artifact changed between resolution and open")
            raw = handle.read(maximum + 1)
    except OSError as error:
        raise SampleAccessChainV2Error("artifact could not be opened safely") from error
    if not raw or len(raw) > maximum:
        raise SampleAccessChainV2Error("artifact handle bytes are outside size bound")
    return raw


def _replay_manifest(root: Path, path: str, grouped_digest: str) -> None:
    raw = _confined_file(root, path, 2 * 1024 * 1024)
    if _sha(raw) != _ungroup(grouped_digest):
        raise SampleAccessChainV2Error(f"transitive manifest bytes changed: {path}")
    manifest = strict_json_bytes(raw, maximum=2 * 1024 * 1024)
    if type(manifest) is not dict:
        raise SampleAccessChainV2Error("transitive manifest root must be exact object")
    artifacts = manifest.get("artifacts")
    rows: list[tuple[str, str]] = []
    if type(artifacts) is list:
        for item in artifacts:
            row = _exact_dict(item, {"path", "sha256"}, "manifest row")
            digest = row["sha256"]
            raw_digest = _ungroup(digest) if type(digest) is str and ":" in digest else _raw_hash(digest, "manifest digest")
            rows.append((_text(row["path"], "manifest path"), raw_digest))
    elif type(artifacts) is dict:
        for member_path, digest in artifacts.items():
            rows.append((_text(member_path, "manifest path"), _ungroup(digest)))
    else:
        raise SampleAccessChainV2Error("transitive manifest artifacts have unknown shape")
    if not rows or len({path for path, _ in rows}) != len(rows):
        raise SampleAccessChainV2Error("transitive manifest is empty or has duplicate paths")
    for member_path, expected in rows:
        if _sha(_confined_file(root, member_path, MAX_EXPORT_BYTES)) != expected:
            raise SampleAccessChainV2Error(f"transitive manifest leaf changed: {member_path}")


EXPECTED_CONFIG_SHA256: Final = "a9323152:0601a8a9:223d37c1:88b3aa03:b09af0fb:a3b40fe4:3396f7f7:c5845a2e"
EXPECTED_SCHEMA_SHA256: Final = "6611a793:25ed2a39:fe9faa74:abf53ec0:840d0cac:1797c9c2:0bae7c66:692a7aba"
EXPECTED_REGISTRY_SCHEMA_SHA256: Final = "7f1b7c4f:90fc5214:583d7e2e:4a1d6532:ae9ce9ba:20001d91:e823e729:58cb41ef"
EXPECTED_FIXTURE_SHA256: Final = "2cd2cb4a:a850e8d2:013fa5be:9b93d562:58acce14:1dc9366d:70c094cc:f86b9669"
OWN_MANIFEST_PATHS: Final = (
    ".github/workflows/sample-access-chain-linux.yml",
    "configs/governance/sample-access-chain-v2-evidence.json",
    "docs/governance/SAMPLE_ACCESS_CHAIN_V2.md",
    "qme/governance/sample_access_chain_v2.py",
    "scripts/generate_sample_access_chain_v2_fixture.py",
    "schemas/governance/compact-experiment-registry-v2.schema.json",
    "schemas/governance/sample-access-chain-v2-compact-registry.schema.json",
    "schemas/governance/sample-access-chain-v2-evidence.schema.json",
    "schemas/governance/sample-access-chain-v2-export.schema.json",
    "schemas/governance/sample-access-chain-v2-proof.schema.json",
    "tests/fixtures/governance/sample-access-chain-v2-known-answer.json",
    "tests/governance/test_sample_access_chain_v2.py",
)


def verify_sample_access_chain_v2(root: Path) -> Mapping[str, Any]:
    config_raw = _confined_file(root, "configs/governance/sample-access-chain-v2-evidence.json", 2 * 1024 * 1024)
    schema_raw = _confined_file(root, "schemas/governance/sample-access-chain-v2-evidence.schema.json", 4 * 1024 * 1024)
    fixture_raw = _confined_file(root, "tests/fixtures/governance/sample-access-chain-v2-known-answer.json", 2 * 1024 * 1024)
    registry_schema_raw = _confined_file(
        root, "schemas/governance/compact-experiment-registry-v2.schema.json",
        2 * 1024 * 1024,
    )
    if _sha(config_raw) != _ungroup(EXPECTED_CONFIG_SHA256):
        raise SampleAccessChainV2Error("evidence config bytes changed")
    if _sha(schema_raw) != _ungroup(EXPECTED_SCHEMA_SHA256):
        raise SampleAccessChainV2Error("evidence schema bytes changed")
    if _sha(fixture_raw) != _ungroup(EXPECTED_FIXTURE_SHA256):
        raise SampleAccessChainV2Error("known-answer fixture bytes changed")
    if _sha(registry_schema_raw) != _ungroup(EXPECTED_REGISTRY_SCHEMA_SHA256):
        raise SampleAccessChainV2Error("compact experiment-registry schema bytes changed")
    config = strict_json_bytes(config_raw, maximum=2 * 1024 * 1024)
    schema = strict_json_bytes(schema_raw, maximum=4 * 1024 * 1024)
    fixture = strict_json_bytes(fixture_raw, maximum=2 * 1024 * 1024)
    if type(config) is not dict or type(schema) is not dict or schema.get("const") != config:
        raise SampleAccessChainV2Error("exact-const schema and config differ")
    if config.get("status") != STATUS or config.get("ticket_id") != "NEE-176":
        raise SampleAccessChainV2Error("evidence identity or status changed")
    semantic_payload = dict(config)
    semantic_observed = _ungroup(semantic_payload.pop("semantic_sha256", None))
    if _sha(canonical_json_bytes(semantic_payload)) != semantic_observed:
        raise SampleAccessChainV2Error("evidence semantic hash mismatch")
    authority = config.get("authority")
    if authority != _EXPECTED_AUTHORITY:
        raise SampleAccessChainV2Error("authority projection changed")
    for binding in cast(list[dict[str, Any]], authority["transitive_manifests"]):
        row = _exact_dict(binding, {"path", "sha256"}, "authority manifest binding")
        _replay_manifest(root, cast(str, row["path"]), cast(str, row["sha256"]))
    event_schema = cast(dict[str, Any], authority["inherited_event_schema"])
    if _sha(_confined_file(root, event_schema["path"], 2 * 1024 * 1024)) != _ungroup(event_schema["sha256"]):
        raise SampleAccessChainV2Error("inherited V1 event schema changed")
    expected = known_answer_summary(MIN_KNOWN_ANSWER_EVENTS)
    if fixture != expected:
        raise SampleAccessChainV2Error("known-answer fixture does not replay")
    blockers = config.get("active_blockers")
    freeze = strict_json_bytes(
        _confined_file(root, "configs/governance/specification-freeze-policy-v3.json", 2 * 1024 * 1024),
        maximum=2 * 1024 * 1024,
    )
    if type(freeze) is not dict or blockers != freeze.get("unresolved_blockers") or len(cast(list[Any], blockers)) != 14:
        raise SampleAccessChainV2Error("all 14 Freeze V3 blockers must remain active")
    claims = config.get("claims")
    if claims != _EXPECTED_CLAIMS:
        raise SampleAccessChainV2Error("candidate no-claims must remain false")
    return MappingProxyType(cast(dict[str, Any], config))


def verify_sample_access_chain_v2_manifest(root: Path) -> Mapping[str, Any]:
    raw = _confined_file(
        root, "configs/governance/sample-access-chain-v2.hashes.json", 2 * 1024 * 1024
    )
    manifest = strict_json_bytes(raw, maximum=2 * 1024 * 1024)
    item = _exact_dict(
        manifest,
        {"schema_version", "artifact_id", "status", "production_status", "artifacts"},
        "sample-access V2 manifest",
    )
    if item != {
        **item,
        "schema_version": "qme.hash_manifest.v1",
        "artifact_id": "NEE-176-SAMPLE-ACCESS-CHAIN-V2-SLICE",
        "status": STATUS,
        "production_status": "BLOCKED_NO_PRODUCTION_ACCESS_EXPORT_OR_PROTECTED_ACCEPTANCE",
    }:
        raise SampleAccessChainV2Error("sample-access V2 manifest header changed")
    rows = item["artifacts"]
    if type(rows) is not list or len(rows) != len(OWN_MANIFEST_PATHS):
        raise SampleAccessChainV2Error("sample-access V2 manifest member count changed")
    observed_paths: list[str] = []
    for raw_row in rows:
        row = _exact_dict(raw_row, {"path", "sha256"}, "sample-access V2 manifest row")
        path = _text(row["path"], "manifest path")
        observed_paths.append(path)
        if _sha(_confined_file(root, path, MAX_EXPORT_BYTES)) != _ungroup(row["sha256"]):
            raise SampleAccessChainV2Error(f"sample-access V2 manifest leaf changed: {path}")
    if tuple(observed_paths) != OWN_MANIFEST_PATHS:
        raise SampleAccessChainV2Error("sample-access V2 manifest path order changed")
    return MappingProxyType(item)


__all__ = [
    "MAX_COMPACT_REGISTRY_BYTES", "MAX_EVENT_BYTES", "MAX_EXPORT_BYTES", "MIN_KNOWN_ANSWER_EVENTS",
    "SampleAccessChainResolver", "SampleAccessChainV2Error", "VerifiedSampleAccessChain",
    "VersionedExperimentRegistryResolver",
    "build_compact_registry", "build_export", "build_inclusion_proof",
    "build_versioned_compact_binding_payload", "canonical_json_bytes",
    "deterministic_versioned_registry_export_v2",
    "export_commitment", "generate_known_answer_events", "known_answer_summary", "replay_compact_registry",
    "make_next_versioned_registry_event_v2",
    "replay_versioned_registry_binding_v2",
    "serialize_verified_result", "strict_json_bytes", "validate_causal_chain", "validate_v1_event",
    "verify_inclusion_proof", "verify_sample_access_chain_v2", "verify_strict_extension",
    "verify_sample_access_chain_v2_manifest",
]
