"""AI referee: integrity in code, Jev consensus, seeing adapters, signed verdicts.

Jev answers for the 22 real Madden frames (+3 doctored copies) were recorded from the live API
(eval/referee_ai_frames.py --record) so CI runs offline with no key."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from qor_receipt.jcs import sha256_hex
from qor_receipt.keys import SoftwareKey
from qor_receipt.referee_ai import integrity as integ
from qor_receipt.referee_ai import verdict as vd
from qor_receipt.referee_ai.jev import JevJudge, build_request, consensus, parse, questions
from qor_receipt.referee_ai.referee import Frame, decide, judge_frames, referee_bundle
from qor_receipt.referee_ai.scenarios import GROUND_TRUTH, SCENARIOS
from qor_receipt.referee_ai.vision import MetaMuseVision, Observation, get_vision

FX = Path(__file__).parent / "fixtures" / "referee_ai"
ANSWERS = json.loads((FX / "jev_answers.json").read_text())


def jev_obs(name: str) -> Observation:
    return parse(ANSWERS[name]["answers"])


class _NoCall:
    available = False
    model = "recorded"
    calls: list = []


# ------------------------------------------------------------------ integrity rules (pure code)

def test_legal_steps_and_monotonic():
    ok = [{"away": 0, "home": 0, "quarter": 1, "clock_s": 900}, {"away": 7, "home": 0, "quarter": 1, "clock_s": 600},
          {"away": 7, "home": 3, "quarter": 2, "clock_s": 800}, {"away": 15, "home": 3, "quarter": 3, "clock_s": 10},
          {"away": 15, "home": 5, "quarter": 4, "clock_s": 0}, {"away": 16, "home": 5, "quarter": 5, "clock_s": 0}]
    assert integ.check_progression(ok) == []
    assert any("not a legal" in e for e in integ.check_progression([{"away": 0, "home": 0}, {"away": 4, "home": 0}]))
    assert any("backwards" in e for e in integ.check_progression([{"away": 7, "home": 0}, {"away": 0, "home": 0}]))
    assert any("clock ran backwards" in e for e in integ.check_progression(
        [{"away": 0, "home": 0, "quarter": 2, "clock_s": 100}, {"away": 0, "home": 0, "quarter": 2, "clock_s": 400}]))
    assert any("quarter went backwards" in e for e in integ.check_progression(
        [{"away": 0, "home": 0, "quarter": 3}, {"away": 0, "home": 0, "quarter": 2}]))
    assert integ.check_final({"away": 7, "home": 8}, {"away": 7, "home": 7})


def test_consensus_needs_two_confident_agreeing_frames():
    hi = Observation("jev", True, away=7, home=8, conf={"away": 950, "home": 990})
    lo = Observation("jev", True, away=7, home=9, conf={"away": 950, "home": 600})
    assert consensus([hi])["accepted"] == {}                       # one frame is not enough
    c = consensus([hi, lo])
    assert "home" not in c["accepted"] and c["accepted"]["away"] == 7  # low-conf 9 neither counts nor accepts
    other = Observation("jev", True, away=7, home=6, conf={"away": 950, "home": 990})
    assert "home" in consensus([hi, hi, other])["abstained"]       # confident disagreement -> abstain


def test_jev_has_no_tamper_question_and_fails_closed():
    assert "tampered" not in questions()                            # tampering is the integrity layer's job
    seen = {}

    def post(body, key, timeout):
        seen["body"] = body
        return 500, "boom"

    import numpy as np

    j = JevJudge(key="test-key", post=post)
    o = j.observe(np.zeros((360, 640, 3), dtype="uint8"))
    assert not o.ok and o.reason == "http_500"
    ref = next(iter(seen["body"]["assets"]))
    assert seen["body"]["state"]["frame"] == ref                   # every asset ref is referenced
    assert JevJudge(key="").observe(None).reason == "no_api_key"
    b = build_request(b"x", "png")
    assert b["model"] == "jev-1.14.0"


def test_meta_adapter_is_a_stub(monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    assert get_vision("meta").observe(None).reason == "meta_key_missing"
    monkeypatch.setenv("META_MUSE_API_KEY", "x")
    assert MetaMuseVision().observe(None).reason == "meta_adapter_not_implemented"


# ------------------------------------------------------------------ 22 real frames, recorded Jev

def test_recorded_jev_accuracy_on_real_frames():
    types = [(n, g[0]) for n, g in GROUND_TRUTH.items() if g[0] and not n.startswith("TAMPER")]
    assert sum(jev_obs(n).frame_type == t for n, t in types) >= len(types) - 2
    scores = [(n, g) for n, g in GROUND_TRUTH.items() if g[1] is not None and not n.startswith("TAMPER")]
    assert sum((jev_obs(n).away, jev_obs(n).home) == (g[1], g[2]) for n, g in scores) >= len(scores) - 2


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenarios(name):
    cv2 = pytest.importorskip("cv2")
    names, claim, expect = SCENARIOS[name]
    vis = get_vision("scorebug")
    frames = []
    for i, n in enumerate(names):
        f = Frame(id=n, img=cv2.imread(str(FX / n)), t_ms=i)
        f.obs = [vis.observe(f.img), jev_obs(n)]
        frames.append(f)
    fr = judge_frames(frames, vision=None, jev=_NoCall(), claim=claim)
    verdict, reasons = decide(integrity_errors=[], frame_result=fr, claim=claim)
    assert verdict == expect, reasons
    if name.startswith("doctored"):
        # caught by integrity code (legal steps / monotonic clock / final == last score), not by a model flag
        assert any(k in reasons[0] for k in ("not a legal", "ran backwards", "!= last scorebug")), reasons


# ------------------------------------------------------------------ QorCap bundle format

def make_bundle(d: Path, scores: list[tuple[int, int, int, int]], final=None) -> Path:
    """Write a bundle in QorCap's real on-disk format (src/evidence.rs, src/bundle.rs, src/engine.rs)."""
    d.mkdir(parents=True, exist_ok=True)
    segs = [os.urandom(2000) for _ in range(4)]
    (d / "video.mp4").write_bytes(b"".join(segs))
    head, off, lines = integ.GENESIS, 0, []
    for i, s in enumerate(segs):
        sha = hashlib.sha256(s).hexdigest()
        new = integ.link(head, i, sha)
        lines.append({"type": "segment", "seq": i, "file": "video.mp4", "offset": off, "bytes": len(s), "sha256": sha,
                      "prev": head, "chain_head": new, "wall_ms": 1000 + i * 1000, "kind": "init" if i == 0 else "media"})
        head, off = new, off + len(s)
    (d / "manifest.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    prev, cps, tl = integ.GENESIS, [], []
    for i, (a, h, q, c) in enumerate(scores):
        ext = {"chain_head": head, "match_id": "m1", "role": "home", "score": {"away": a, "home": h, "quarter": q, "clock_s": c,
               "clock_red": False}, "segment_seq": 3, "trigger": "score_change", "mono_ms": i}
        body = {"seq": i, "t_ms": 1000 + i, "frames_hash": sha256_hex(ext), "input_hash": sha256_hex([]), "prev": prev}
        cp = {**body, "hash": sha256_hex(body), "qorcap": ext}
        cps.append(cp)
        prev = cp["hash"]
        tl.append({"away": a, "home": h, "quarter": q, "clock_s": c, "clock_red": False, "t_ms": 1000 + i})
    (d / "checkpoints.jsonl").write_text("\n".join(json.dumps(x) for x in cps) + "\n")
    (d / "timeline.jsonl").write_text("\n".join(json.dumps(x) for x in tl) + "\n")
    a, h, q, _ = scores[-1]
    fin = final or {"away": a, "home": h, "quarter": q, "basis": "clock_zero", "t_ms": 9000}
    (d / "bundle.json").write_text(json.dumps({"schema": "qorcap-bundle/v0", "match_id": "m1", "chain_head": head,
                                               "checkpoint_head": prev, "segments": 4, "checkpoints": len(cps),
                                               "final": fin, "clips": []}))
    return d


GAME = [(0, 0, 1, 900), (0, 7, 1, 600), (3, 7, 2, 300), (3, 14, 3, 120), (10, 14, 4, 30), (10, 14, 4, 0)]


def test_bundle_clean_abstains_without_seeing(tmp_path):
    b = make_bundle(tmp_path / "b", GAME)
    assert integ.verify_bundle(b)["ok"]
    v = referee_bundle(b)
    assert v["verdict"] == vd.ABSTAIN and v["integrity"]["ok"], v["reasons"]


def test_bundle_tampering_is_disputed(tmp_path):
    b = make_bundle(tmp_path / "flip", GAME)
    raw = bytearray((b / "video.mp4").read_bytes())
    raw[2500] ^= 1
    (b / "video.mp4").write_bytes(bytes(raw))
    v = referee_bundle(b)
    assert v["verdict"] == vd.DISPUTE and any("SHA-256 mismatch" in r for r in v["reasons"])
    # edited score state inside a checkpoint (hash chain not recomputed)
    b2 = make_bundle(tmp_path / "edit", GAME)
    cps = [json.loads(x) for x in (b2 / "checkpoints.jsonl").read_text().splitlines()]
    cps[-1]["qorcap"]["score"]["home"] = 21
    (b2 / "checkpoints.jsonl").write_text("\n".join(json.dumps(x) for x in cps) + "\n")
    assert any("frames_hash" in r for r in referee_bundle(b2)["reasons"])
    # a fully re-hashed but impossible score history is still caught (legal steps)
    b3 = make_bundle(tmp_path / "jump", [(0, 0, 1, 900), (0, 21, 1, 800)])
    assert any("not a legal" in r for r in referee_bundle(b3)["reasons"])
    # bundle final that differs from the last scorebug score
    b4 = make_bundle(tmp_path / "fin", GAME, final={"away": 10, "home": 17, "quarter": 4})
    assert any("!= last scorebug" in r for r in referee_bundle(b4)["reasons"])


def test_verdict_signature_and_cli(tmp_path, capsys):
    from qor_receipt.cli import main

    b = make_bundle(tmp_path / "b", GAME)
    rc = main(["referee-ai", str(b), "--vision", "none", "--no-jev", "--key", str(tmp_path / "k.key")])
    assert rc == 1                                                   # abstain
    v = json.loads((b / vd.FILENAME).read_text())
    assert vd.verify_verdict(v) == []
    v["verdict"] = "accept"
    assert "bad_signature" in vd.verify_verdict(v)
    s = vd.sign({"schema": vd.SCHEMA, "verdict": "abstain"}, SoftwareKey())
    assert vd.verify_verdict(s, pub=SoftwareKey().pubkey_hex) == ["key_mismatch"]


REAL = os.environ.get("QORCAP_BUNDLE", "/tmp/qm/matches/match-20261014-155852")


@pytest.mark.skipif(not Path(REAL, "bundle.json").exists(), reason="no real QorCap bundle on this machine")
def test_real_qorcap_bundle_integrity():
    r = integ.verify_bundle(REAL)
    assert r["ok"], r["errors"]
    assert r["info"]["receipt_verify"]["status"] in ("VERIFIED", "PARTIAL")


def test_check_final_end_menu_read_allows_one_final_play_past_scorebug():
    last = {"away": 0, "home": 17}
    # Madden match-20261016-143212: scorebug last read 0-17, end-of-game menu 0-20 (final-play FG).
    assert integ.check_final({"away": 0, "home": 20}, last) != []                      # scorebug methods: strict
    assert integ.check_final({"away": 0, "home": 20}, last, method="end_menu_read") == []
    assert integ.check_final({"away": 0, "home": 17}, last, method="end_menu_read") == []
    assert integ.check_final({"away": 0, "home": 14}, last, method="end_menu_read")    # never backwards
    assert integ.check_final({"away": 3, "home": 20}, last, method="end_menu_read")    # both sides moved
    assert integ.check_final({"away": 0, "home": 30}, last, method="end_menu_read")    # not one play
    assert integ.final_method({"basis": "end_menu"}) == "end_menu_read"
    assert integ.final_method({"method": "end_menu_read", "final_basis": "end_menu"}) == "end_menu_read"
    assert integ.final_method({"basis": "clock_zero"}) is None
