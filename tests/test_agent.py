import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from qor_receipt.agent.capture import SizeGuard, frame_hash, replay_frames, resolve_device  # noqa: E402
from qor_receipt.agent.fixture import STRIPS, make_session  # noqa: E402
from qor_receipt.agent.hid import HidDecoder, pack_usb_report, parse_report  # noqa: E402
from qor_receipt.agent.pause_detect import PauseDetector, PauseThresholds  # noqa: E402
from qor_receipt.agent.runner import run_replay  # noqa: E402
from qor_receipt.cli import main  # noqa: E402
from qor_receipt.receipt import FAIL, SELF_ATTESTED, verify_receipt  # noqa: E402


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    return make_session(tmp_path_factory.mktemp("sess"))


# ---- hid
def test_parse_roundtrip_buttons_and_hat():
    for btns in [{"OPTIONS"}, {"CROSS", "UP"}, {"DOWN", "LEFT", "SQUARE"}, {"R2", "L1", "TRIANGLE", "CIRCLE"}, set()]:
        p = parse_report(pack_usb_report(btns, (10, 20, 30, 40), l2=5, r2=200))
        assert p["buttons"] == frozenset(btns) and p["sticks"] == (10, 20, 30, 40) and p["r2"] == 200


def test_bt_report_offset_and_garbage():
    usb = pack_usb_report({"CIRCLE"}, (1, 2, 3, 4))
    bt = bytes([0x31, 0x00]) + usb[1:] + bytes(78 - 64)
    assert parse_report(bt)["buttons"] == frozenset({"CIRCLE"})
    assert parse_report(b"\x05" * 64) is None and parse_report(b"\x01\x02") is None


def test_decimation_keeps_every_button_edge():
    d = HidDecoder(analog_hz=125)
    ev = []
    for t in range(0, 1000):  # 1 kHz, sticks change every report, button toggles every 3 ms
        btn = {"CROSS"} if (t // 3) % 2 else set()
        ev += d.feed(pack_usb_report(btn, (128 + (t % 100), 128, 128, 128)), t)
    edges = [e for e in ev if "button" in e]
    sticks = [e for e in ev if "sticks" in e]
    assert len(edges) == 333  # every toggle kept
    assert 120 <= len(sticks) <= 126  # ~125 Hz
    assert all(b["t_ms"] - a["t_ms"] >= 8 for a, b in zip(sticks, sticks[1:]))


# ---- capture
def test_size_guard_and_hash():
    g = SizeGuard()
    a, r1 = g.apply(np.zeros((360, 640, 3), np.uint8))
    b, r2 = g.apply(np.zeros((1080, 1920, 3), np.uint8))
    c, r3 = g.apply(np.zeros((1080, 1920, 3), np.uint8))
    assert (r1, r2, r3) == (False, True, False) and b.shape == (360, 640, 3) and g.resets == 1
    assert frame_hash(a) == frame_hash(b) and frame_hash(a) != frame_hash(np.ones((360, 640, 3), np.uint8))


def test_resolve_device_by_name_skips_obs():
    names = ["OBS Virtual Camera", "Integrated Camera", "USB3.0 Video", "USB Video Device"]
    assert resolve_device("USB3.0 Video", names) == 2
    assert resolve_device("usb3.0", names) == 2
    assert resolve_device("OBS", names) is None
    assert resolve_device("Elgato", names) is None


def test_replay_frames_resize_reset(tmp_path):
    cv2.imwrite(str(tmp_path / "1000.png"), np.zeros((360, 640, 3), np.uint8))
    cv2.imwrite(str(tmp_path / "1033.png"), np.zeros((720, 1280, 3), np.uint8))
    fr = list(replay_frames(tmp_path))
    assert [f.t_ms for f in fr] == [1000, 1033] and [f.reset for f in fr] == [False, True]
    assert fr[1].image.shape == (360, 640, 3)


# ---- pause detection (synthetic; NEEDS TUNING on real Madden frames)
def _gameplay(seed=0):
    rng = np.random.default_rng(seed)
    t = rng.integers(50, 140, size=(360, 640), dtype=np.uint8)
    return np.dstack([t * 0.6, t, t * 0.5]).astype(np.uint8)


def _paused(img):
    p = (img * 0.4).astype(np.uint8)
    p[72:288, 64:256] = 225
    return p


def test_pause_onset_and_debounce():
    d = PauseDetector()
    for i in range(10):
        assert not d.update(1000 + i * 33, _gameplay(i))
    d.update(1400, _paused(_gameplay(1)))
    assert not d.paused  # confirm_frames=2
    d.update(1433, _paused(_gameplay(2)))
    assert d.paused and d.onsets == [1400]  # onset = first frame of the run
    d.update(1466, _gameplay(3))
    assert not d.paused
    assert d.onset_after(1300) == 1400 and d.onset_after(1800) is None


def test_pause_not_triggered_by_dark_scene_or_bright_scene():
    d = PauseDetector()
    for i in range(10):
        d.update(i * 33, _gameplay(i))
    for i in range(5):
        d.update(400 + i * 33, (_gameplay(i) * 0.3).astype(np.uint8))  # fade to black, no menu
    assert d.onsets == []


def test_pause_thresholds_load(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"dim_ratio": 0.5, "panel_roi": [0, 0, 1, 1], "unknown": 1}))
    th = PauseThresholds.load(p)
    assert th.dim_ratio == 0.5 and th.panel_roi == (0, 0, 1, 1)


def test_pause_template_path(tmp_path):
    tpl = _paused(_gameplay(9))
    cv2.imwrite(str(tmp_path / "tpl.png"), tpl)
    d = PauseDetector(PauseThresholds(template_path=str(tmp_path / "tpl.png"), confirm_frames=1))
    assert d.update(0, _paused(_gameplay(9)))


# ---- scorebug strips are real reads
def test_fixture_strips_read():
    from qor_receipt._vendor.qoresence.vision.local_scorebug.reader import ScorebugReader

    r = ScorebugReader()
    for name, want in [("std_14_7_2nd_1-23.jpg", (14, 7, 2, "1:23")), ("spliced_21_24_4th_0-00.png", (21, 24, 4, "0:00"))]:
        f = np.full((360, 640, 3), 40, np.uint8)
        f[330:] = cv2.imread(str(STRIPS / name))
        x = r.read(f)
        assert x.ok and (x.left_score, x.right_score, x.quarter, x.clock_text) == want


# ---- end to end replay
def test_replay_end_to_end_verified(session, tmp_path):
    r = run_replay(session["frames_dir"], session["hid_log"])
    assert r["verify"]["status"] == SELF_ATTESTED and r["verify"]["trust_tier"] == "single-attested"
    assert r["verify"]["errors"] == [] and "referee_not_on_allowlist" in r["verify"]["partial"]
    rec = r["receipt"]
    assert (rec["result"]["home_score"], rec["result"]["away_score"], rec["result"]["winner"]) == (24, 21, "home")  # strip shows ATL-style away 21 (left) @ home 24 (right)
    assert [c["phase"] for c in rec["challenges"]] == ["kickoff", "halftime", "end"]
    assert all(c["result"]["ok"] and 0 <= c["result"]["latency_ms"] <= 600 for c in rec["challenges"])
    assert len(rec["checkpoints"]["home"]) >= 10
    assert rec["signals"]["motion_permille"]["home"] >= 300
    assert r["hid_decoder"]["reports_in"] > 10_000 and r["stats"]["scorebug_ok"] > 100
    # tamper: flip the score -> FAIL
    rec["result"]["home_score"] = 99
    assert verify_receipt(rec, referee_pub=r["referee_pub"])["status"] == FAIL


def test_replay_without_pause_menu_fails_challenge(tmp_path):
    s = make_session(tmp_path, skip_pause="halftime")
    r = run_replay(s["frames_dir"], s["hid_log"])
    assert r["verify"]["status"] == FAIL
    assert "home:halftime:challenge_failed:pause_not_seen_on_video" in r["verify"]["errors"]


def test_replay_late_video_fails(tmp_path):
    s = make_session(tmp_path, pause_latency_ms=900)
    r = run_replay(s["frames_dir"], s["hid_log"])
    assert any("video_latency_too_high" in e for e in r["verify"]["errors"])


def test_cli_replay_and_verify(session, tmp_path, capsys):
    out = tmp_path / "r.json"
    assert main(["agent", "--replay", session["frames_dir"], "--hid-log", session["hid_log"], "--out", str(out)]) == 1  # SELF_ATTESTED
    info = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert info["verify"]["status"] == SELF_ATTESTED
    assert main(["verify", str(out), "--referee-pub", info["referee_pub"]]) == 1


def test_committed_replay_vector_verifies():
    v = json.loads((STRIPS.parents[3] / "tests" / "vectors" / "agent_replay_receipt.json").read_text())
    assert verify_receipt(v["receipt"], referee_pub=v["referee_pub"])["status"] == SELF_ATTESTED


def test_agent_scorebug_gone_final():
    """Q4 low clock, then the strip vanishes for >30 s (no pause) -> FINAL with basis scorebug_gone."""
    from qor_receipt._vendor.qoresence.vision.local_scorebug.profile import MADDEN27_STANDARD
    from qor_receipt._vendor.qoresence.vision.local_scorebug.reader import FrameRead
    from qor_receipt.agent.capture import Frame
    from qor_receipt.agent.runner import AgentConfig, AgentCore, Prompt

    class FakeReader:
        def __init__(self):
            self.script = []

        def read(self, img):
            return self.script.pop(0)

        def reference_band(self, img):
            return np.zeros((30, 640, 3), np.uint8), "ok"

    ok = FrameRead(ok=True, reason="ok", profile_id=MADDEN27_STANDARD.profile_id, left_score=21, right_score=17, quarter=4,
                   clock_seconds=3, clock_text="0:03", clock_red=True)
    gone = FrameRead(ok=False, reason="layout_unknown", profile_id=MADDEN27_STANDARD.profile_id)
    fr = FakeReader()
    core = AgentCore(None, None, AgentConfig(), Prompt(quiet=True), scorebug=fr)
    core.mid, core.start_ms = "m", 0
    core._issue = lambda phase, t: core.challenges.setdefault(phase, {"t": t})
    img = np.random.default_rng(0).integers(60, 200, (360, 640, 3), dtype=np.uint8)
    t = 0
    fr.script = [ok] * 20 + [gone] * 200
    for _ in range(20 + 140):  # 140 * 250 ms = 35 s of no strip
        t += 250
        core.on_frame(Frame(t, img, "h"))
    assert core.final is not None and core.final.basis == "scorebug_gone"
    assert (core.final.away, core.final.home) == (21, 17)  # left = away and "end" in core.challenges
    assert any(r.get("obs") == "scorebug_absent" for r in core.timeline)


def test_agent_short_absence_not_final():
    """Same, but the strip is only gone 20 s (replay length): fail closed."""
    import qor_receipt.agent.runner as rn

    core = rn.AgentCore(None, None, rn.AgentConfig(), rn.Prompt(quiet=True))
    core.timeline = [{"t_ms": i * 1000, "home": 21, "away": 17, "quarter": 4, "clock_s": 3} for i in range(4)]
    core.timeline.append({"t_ms": 4_000, "until_ms": 24_000, "obs": "scorebug_absent", "n": 100})
    core._check_final(24_000)
    assert core.final is None
