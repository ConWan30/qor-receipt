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
    assert out and out[-1]["home"] == 21 and out[-1]["away"] == 14
