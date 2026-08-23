"""Exact receipt checks for the owner-accepted bounded M0 AV pull set."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RECEIPT = REPO / "tests/fixtures/governance/av-m0-fixture-pull-summary-2026-08-16.json"


def _ungroup(value: str) -> str:
    return value.replace(":", "")

EXPECTED = (
    ("LISTING_STATUS", None, "20260816T033626707774Z-5f36e616dd82", "5f36e616:dd82e506:659e310d:b50ac70e:94da0b15:3b7fdf12:7c6d0970:f9c81030"),
    ("LISTING_STATUS", None, "20260816T033627569033Z-c88ac8e0948a", "c88ac8e0:948ac059:c4a42105:2bb470be:31fc10ad:448ef6c9:5210adf4:9378e684"),
    ("TIME_SERIES_DAILY", "AAPL", "20260816T033628897425Z-b1cd3580367c", "b1cd3580:367ce83d:ee43adde:c15ff856:13fcfbbb:35c4538d:82ddbb56:70035616"),
    ("DIVIDENDS", "AAPL", "20260816T033629270542Z-3760057d1606", "3760057d:1606f272:661446c5:004f58b8:1301385e:9cf9d2ba:90ec6b2f:c1416a0b"),
    ("SPLITS", "AAPL", "20260816T033630450654Z-22a120133281", "22a12013:328173ff:73f8d488:5ec57827:d39c3906:ba8bbd30:13d7c79e:bd2292d7"),
    ("TIME_SERIES_DAILY", "NVDA", "20260816T033632153341Z-7810fd11de27", "7810fd11:de2760e7:0f11f78e:60be1b96:5dfb921a:bf61c9db:87f3a393:f2ffde2b"),
    ("DIVIDENDS", "NVDA", "20260816T033632890563Z-c5145d3e0218", "c5145d3e:021889a8:d386f176:b11ba412:4cc4d193:deca0e9e:47449855:7519ab09"),
    ("SPLITS", "NVDA", "20260816T033634077188Z-a300dffc4d42", "a300dffc:4d426d69:c04fd8db:62f93bca:4de91f5e:c5b0195b:26194409:4fe7905f"),
    ("TIME_SERIES_DAILY", "MSFT", "20260816T033635745248Z-f744e62116ce", "f744e621:16ceb893:b2c3c5c8:2a8ded55:11d27377:4519f4a6:c4f3f48b:c49709ad"),
    ("DIVIDENDS", "MSFT", "20260816T033636537665Z-098339f935e9", "098339f9:35e9c023:3ae5577b:2a81cf64:f682f3ec:ec93d38e:eec9d65c:6a8f646e"),
    ("SPLITS", "MSFT", "20260816T033637657182Z-97cb08f881c0", "97cb08f8:81c0b649:21a61ad8:f538884a:19234ec7:d08dbfcd:249085c7:607b82e1"),
    ("TIME_SERIES_DAILY", "COST", "20260816T033639324101Z-8c80266aa764", "8c80266a:a764d0f4:1e84af8d:f7284458:73a6b1f9:d8624060:9edd8f6c:641c4a44"),
    ("DIVIDENDS", "COST", "20260816T033640095967Z-7355c1324529", "7355c132:4529bff2:014754e4:b61d9b90:7eaddd29:42c78871:84185892:92999971"),
    ("SPLITS", "COST", "20260816T033641238056Z-f397be293f28", "f397be29:3f283ef4:c7793613:e95271a6:7ff5ccf4:33f59f73:5a03b984:19f31aca"),
    ("TIME_SERIES_DAILY", "META", "20260816T033642845690Z-78da904cf51f", "78da904c:f51fe16c:af33e908:1900b885:60f5247c:89e8c00e:3b9ca6db:893505f8"),
    ("DIVIDENDS", "META", "20260816T033643664096Z-fcd17a4be5bc", "fcd17a4b:e5bcccc5:78e96913:3d0e9953:3632b44a:e9d48576:519d8b6c:b94b7a1b"),
    ("SPLITS", "META", "20260816T033644872720Z-db75876d1a54", "db75876d:1a54cd4b:3afd42db:b2f182b7:4f67e3cc:a35ba39b:171a9a45:846e1af8"),
    ("TIME_SERIES_DAILY", "ATVI", "20260816T033646517830Z-6c045f290591", "6c045f29:0591bbe7:79b33e2a:4783fa68:b162afef:e59cc593:4e8c7ca2:e6255190"),
    ("DIVIDENDS", "ATVI", "20260816T033647246267Z-e9949aabf4a4", "e9949aab:f4a4b887:3a190f1a:d1f08d64:0abed38c:d4e4dc23:c3b670a7:6846835a"),
    ("SPLITS", "ATVI", "20260816T033648490783Z-2a21ca40c220", "2a21ca40:c2200f4f:3203a4e6:40e82633:9fde8617:18ad4f3b:230ca356:5f3e45e2"),
    ("TIME_SERIES_DAILY", "BBBY", "20260816T033650146830Z-0feffbca8c1a", "0feffbca:8c1ab603:bff1001a:077ee7f5:84d42160:b87a08e3:34c87531:5dbe9715"),
    ("DIVIDENDS", "BBBY", "20260816T033650873094Z-c8ac086ba102", "c8ac086b:a1026a7e:6e72c9e1:cb409d13:77f565ac:08aeee73:0a45bab3:dbec4c4b"),
    ("SPLITS", "BBBY", "20260816T033652084426Z-c8ac086ba102", "c8ac086b:a1026a7e:6e72c9e1:cb409d13:77f565ac:08aeee73:0a45bab3:dbec4c4b"),
)


def test_exact_bounded_real_source_fixture_receipt() -> None:
    payload = RECEIPT.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == _ungroup(
        "56b35a5b:8f39d4a8:0ca5e6ec:d9b56423:948b1e65:d1a20a49:93a4d6f3:28e72e37"
    )
    document = json.loads(payload)
    assert document["run_id"] == "20260816T033624Z-av-m0-fixture-pulls"
    assert document["listing_date"] == "2026-07-31"
    assert document["counts"] == {"OK": 23}
    assert document["all_ok"] is True
    observed = tuple(
        (
            outcome["record"]["function"],
            outcome["record"]["symbol"],
            outcome["record"]["pull_id"],
            outcome["record"]["sha256"],
        )
        for outcome in document["outcomes"]
    )
    assert observed == EXPECTED


def test_receipt_contains_no_credentials_and_preserves_source_nonclaims() -> None:
    document = json.loads(RECEIPT.read_bytes())
    for outcome in document["outcomes"]:
        record = outcome["record"]
        assert record["response_class"] == "OK"
        assert record["http_status"] == 200
        assert {key.lower() for key in record["params_public"]} <= {
            "date",
            "function",
            "outputsize",
            "state",
            "symbol",
        }
        assert "apikey" not in record["public_url"].lower()
        assert record["body_logical_id"].startswith("raw/alpha_vantage/")
        assert record["meta_logical_id"].startswith("raw/alpha_vantage/")
    assert document["claims"] == {
        "freeze_blocker_changed": False,
        "production_pit_evidence_registered": False,
        "proxy_snapshot_reviewed": False,
        "raw_pulls_stored_immutably": True,
    }
