import copy
import json
from pathlib import Path

import pytest

from qor_receipt.cli import main
from qor_receipt.demo import run_match
from qor_receipt.keys import SoftwareKey
from qor_receipt.receipt import DUAL, FAIL, PARTIAL, SINGLE, VERIFIED, sign_player, verify_receipt


def test_dual_verified(ref):
    client, clock, key = ref
    rec, _ = run_match(client, clock, "dual")
    v = verify_receipt(rec, referee_pub=key.pubkey_hex)
    assert v == {"status": VERIFIED, "trust_tier": DUAL, "errors": [], "partial": []}


def test_single_tier(ref):
    client, clock, key = ref
    rec, _ = run_match(client, clock, "single")
    v = verify_receipt(rec, referee_pub=key.pubkey_hex)
    assert v["status"] == VERIFIED and v["trust_tier"] == SINGLE


def test_unpinned_is_partial(ref):
    client, clock, _ = ref
    rec, _ = run_match(client, clock)
    assert verify_receipt(rec)["status"] == PARTIAL


def test_missing_challenge_partial(ref):
    client, clock, key = ref
    rec, _ = run_match(client, clock, skip_phase="halftime")
    v = verify_receipt(rec, referee_pub=key.pubkey_hex)
    assert v["status"] == PARTIAL and "home:challenge_missing:halftime" in v["partial"]


def test_replayed_pattern_fails(ref):
    client, clock, key = ref
    rec, _ = run_match(client, clock, pattern_override=["OPTIONS", "CROSS", "CROSS", "CROSS", "CROSS"])
    v = verify_receipt(rec, referee_pub=key.pubkey_hex)
    assert v["status"] == FAIL and any("challenge_failed:pattern_mismatch" in e for e in v["errors"])


@pytest.fixture
def good(ref):
    client, clock, key = ref
    rec, pkeys = run_match(client, clock)
    return rec, key, pkeys


TAMPERS = {
    "score": lambda r: r["result"].__setitem__("home_score", 99),
    "winner": lambda r: r["result"].__setitem__("winner", "away"),
    "drop_cp": lambda r: r["checkpoints"]["home"].pop(5),
    "cp_hash": lambda r: r["checkpoints"]["away"][2].__setitem__("frames_hash", "e" * 64),
    "challenge_result": lambda r: r["challenges"][0]["result"].__setitem__("ok", False),
    "nonce": lambda r: r["challenges"][0].__setitem__("nonce", "ab" * 16),
    "tier_upgrade": lambda r: (r["match"].__setitem__("mode", "single"), r["players"].pop()),
    "drop_player_sig": lambda r: r["signatures"]["players"].pop("away"),
    "swap_referee": lambda r: r["referee"].__setitem__("pubkey", SoftwareKey().pubkey_hex),
    "disagree": lambda r: r["agreement"].__setitem__("timelines_agree", False),
    "schema": lambda r: r.__setitem__("schema", "qor-receipt/v1"),
    "garbage": lambda r: r.__setitem__("players", "x"),
}


@pytest.mark.parametrize("name", sorted(TAMPERS))
def test_tamper_fails(good, name):
    rec, key, _ = good
    t = copy.deepcopy(rec)
    TAMPERS[name](t)
    assert verify_receipt(t, referee_pub=key.pubkey_hex)["status"] == FAIL


def test_player_resign_without_referee_fails(good):
    rec, key, pkeys = good
    t = copy.deepcopy(rec)
    t["result"]["home_score"] = 40
    t = sign_player(sign_player(t, "home", pkeys["home"]), "away", pkeys["away"])
    v = verify_receipt(t, referee_pub=key.pubkey_hex)
    assert v["status"] == FAIL and "bad_referee_signature" in v["errors"]


def test_wrong_pinned_referee(good):
    rec, _, _ = good
    assert "referee_key_mismatch" in verify_receipt(rec, referee_pub=SoftwareKey().pubkey_hex)["errors"]


def test_referee_rejects_unsigned_and_chain_breaks(ref):
    client, _, _ = ref
    k = SoftwareKey()
    mid = client.post("/matches", json={"players": [{"role": "home", "player_id": "p", "pubkey": k.pubkey_hex}]}).json()["match_id"]
    bad = {"role": "home", "payload": {"seq": 0}, "sig": "00" * 64}
    assert client.post(f"/matches/{mid}/checkpoints", json=bad).status_code == 401
    from qor_receipt.checkpoints import make_checkpoint
    from qor_receipt.jcs import canonicalize
    cp = make_checkpoint(1, 0, "0" * 64, "0" * 64, "0" * 64)
    req = {"role": "home", "payload": cp, "sig": k.sign(canonicalize(cp))}
    assert client.post(f"/matches/{mid}/checkpoints", json=req).status_code == 409
    assert client.post(f"/matches/{mid}/finalize").status_code == 409


def test_dual_disagreement_blocks_finalize(ref):
    client, clock, _ = ref
    resp, _ = run_match(client, clock, "dual", away_final=(24, 20))
    assert resp.status_code == 409 and "final_score_differs" in resp.text


def test_cli_roundtrip(tmp_path, capsys):
    out = tmp_path / "r.json"
    assert main(["emit", "--demo", str(out)]) == 0
    pub = json.loads(capsys.readouterr().out)["referee_pub"]
    assert main(["verify", str(out), "--referee-pub", pub]) == 0
    assert main(["verify", str(out)]) == 1
    r = json.loads(out.read_text()); r["result"]["away_score"] = 30; out.write_text(json.dumps(r))
    assert main(["verify", str(out), "--referee-pub", pub]) == 2


def test_shared_vector_still_verifies():
    d = json.loads((Path(__file__).parent / "vectors" / "demo_receipt_dual.json").read_text())
    assert verify_receipt(d["receipt"], referee_pub=d["referee_pub"])["status"] == VERIFIED
