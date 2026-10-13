import pytest

from qor_receipt.demo import timeline
from qor_receipt.final_detect import detect_final, timelines_agree


def test_final():
    f = detect_final(timeline(0))
    assert f.final and (f.home, f.away, f.quarter) == (24, 17, 4)


def test_not_final_cases():
    tl = timeline(0)
    assert detect_final(tl[:-4]).reason == "clock_running"
    assert detect_final(tl[:-2]).reason == "not_stable"
    assert detect_final([{"t_ms": 0, "home": 3, "away": 0, "quarter": 2, "clock_s": 0}]).reason == "not_fourth_quarter"
    assert detect_final(timeline(0, (17, 17))).reason == "tied_overtime_expected"
    assert detect_final([]).reason == "no_reads"


def test_timelines():
    a = timeline(0)
    assert timelines_agree(a, timeline(800)) == []
    assert timelines_agree(a, timeline(9_000))
    assert "state_sequence_differs" in timelines_agree(a, timeline(0, (24, 20)))


def test_vendored_agreement_adapter():
    pytest.importorskip("cv2")
    from qor_receipt._vendor.qoresence.vision.local_scorebug.profile import MADDEN27_STANDARD
    from qor_receipt._vendor.qoresence.vision.local_scorebug.reader import FrameRead
    from qor_receipt.final_detect import agreed_reads_from_frames

    fr = FrameRead(ok=True, reason="ok", profile_id=MADDEN27_STANDARD.profile_id, left_score=21, right_score=14,
                   quarter=4, clock_seconds=0)
    out = agreed_reads_from_frames([(i * 300_000_000, fr, "h") for i in range(6)])
    assert out and out[-1]["away"] == 21 and out[-1]["home"] == 14  # left = away


# ---- end-of-game fallbacks (final_basis) -------------------------------------------------------------

def _q4(t, h, a, clk, red=False):
    return {"t_ms": t, "home": h, "away": a, "quarter": 4, "clock_s": clk, "clock_red": red}


def _low_tail(score=(21, 17), t0=0):
    # Q4 reads ending on a low red clock, then the red 0:00 is unreadable (clock None)
    h, a = score
    return [_q4(t0, h, a, 40), _q4(t0 + 1_000, h, a, 4, True), _q4(t0 + 2_000, h, a, None),
            _q4(t0 + 3_000, h, a, None)]


def _gone(t, dur):
    return {"t_ms": t, "until_ms": t + dur, "obs": "scorebug_absent", "n": dur // 200}


def test_clock_zero_basis():
    f = detect_final(timeline(0))
    assert f.basis == "clock_zero"


def test_red_zero_needs_more_reads():
    reds = [_q4(i * 1_500, 21, 17, 0, True) for i in range(4)]
    assert detect_final(reds).reason == "not_stable"          # 4 red 0:00 reads < need_red=5
    reds.append(_q4(6_000, 21, 17, 0, True))
    f = detect_final(reds)
    assert f.final and f.basis == "clock_zero"


def test_scorebug_gone_basis():
    tl = _low_tail() + [_gone(4_000, 31_000)]
    f = detect_final(tl)
    assert f.final and f.basis == "scorebug_gone" and (f.home, f.away) == (21, 17)


def test_scorebug_gone_fail_closed():
    assert not detect_final(_low_tail() + [_gone(4_000, 20_000)]).final          # too short (TD replay length)
    assert not detect_final(_low_tail() + [_gone(30_000, 40_000)]).final         # starts too long after last read
    assert not detect_final(_low_tail((17, 17)) + [_gone(4_000, 40_000)]).final  # tied -> OT
    late = [_q4(0, 21, 17, 40), _q4(1_000, 21, 17, 38), _q4(2_000, 21, 17, None)]
    assert not detect_final(late + [_gone(3_000, 60_000)]).final                 # last known clock 0:38: not low
    # a new score read after the gap (PAT) cancels it; the new state has no low clock + no fresh absence
    tl = _low_tail() + [_gone(4_000, 10_000), _q4(15_000, 21, 23, None)]
    assert not detect_final(tl).final
    # quarter 2 never qualifies, however long the strip is gone (halftime)
    q2 = [dict(r, quarter=2) for r in _low_tail()]
    assert detect_final(q2 + [_gone(4_000, 90_000)]).reason == "not_fourth_quarter"
    # unstable tail (score just changed) -> not final
    tl = [_q4(0, 21, 17, 4), _q4(1_000, 21, 23, 3)]
    assert not detect_final(tl + [_gone(2_000, 60_000)]).final


def test_final_screen_basis():
    scr = {"t_ms": 5_000, "until_ms": 9_000, "obs": "final_screen", "n": 20}
    f = detect_final(_low_tail() + [scr])
    assert f.final and f.basis == "final_screen"
    assert not detect_final(_low_tail() + [dict(scr, n=2)]).final
    assert not detect_final(_low_tail() + [dict(scr, until_ms=6_000)]).final


def test_timelines_ignore_obs_and_weakest_basis():
    from qor_receipt.final_detect import weakest_basis

    a = timeline(0)
    assert timelines_agree(a + [_gone(10**9, 40_000)], timeline(800)) == []
    assert weakest_basis(["clock_zero", "scorebug_gone"]) == "scorebug_gone"
    assert weakest_basis(["clock_zero", None]) == "clock_zero"
