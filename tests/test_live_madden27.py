"""Real Madden 27 frames from the 2026-10-13 live session (qor-session-20261013-162241, 640x360).

Fixtures are the bottom 30 px scorebug band (rows 330..360) and 160x90 thumbnails for pause frames.
Ground truth: ATL vs CIN, 0-0 -> 6-0 -> 7-0 -> 13-0 -> 14-0 (ATL), into the 3rd quarter.
"""
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from qor_receipt._vendor.qoresence.vision.local_scorebug.agreement import AgreementWindow  # noqa: E402
from qor_receipt._vendor.qoresence.vision.local_scorebug.reader import ScorebugReader  # noqa: E402
from qor_receipt.agent.capture import Frame, frame_hash  # noqa: E402
from qor_receipt.agent.pause_detect import PauseDetector  # noqa: E402
from qor_receipt.agent.runner import AgentConfig, AgentCore, Prompt  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "madden27_live"

# name -> (left, right, quarter, clock_text or None)  | None = must refuse
EXPECT = {
    "q1_0-0_324": (0, 0, 1, "3:24"),
    "q1_6-0": (6, 0, 1, None),
    "q1_7-0_000": (7, 0, 1, "0:00"),
    "q2_7-0_234": (7, 0, 2, "2:34"),
    "q2_7-0_437": (7, 0, 2, None),
    "q2_13-0_red": (13, 0, 2, None),   # red final-minute clock: score/quarter still read
    "q2_14-0_red": (14, 0, 2, None),
    "q3_14-0": (14, 0, 3, None),
    "q2_14-0_022": None,                # compact strip (abbreviations hidden): refuse, never guess
    "refuse_panel_covered": None,       # stat banner over the score panel
    "refuse_pause": None,               # pause menu, no strip
    "refuse_score_unsure": None,        # play-call overlay
}


def _frame(name):
    f = np.full((360, 640, 3), 40, np.uint8)
    f[330:] = cv2.imread(str(FX / f"{name}.png"))
    return f


@pytest.fixture(scope="module")
def reader():
    return ScorebugReader()


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_live_frame_reads(reader, name):
    x = reader.read(_frame(name))
    want = EXPECT[name]
    if want is None:
        assert not x.ok
        return
    assert x.ok, x.reason
    assert (x.left_score, x.right_score, x.quarter) == want[:3]
    if want[3] is not None:
        assert x.clock_text == want[3]
    else:
        assert x.clock_text is None and x.clock_seconds is None


def test_live_agreement_locks_q3(reader):
    w = AgreementWindow()
    img = _frame("q3_14-0")
    res = None
    for i in range(3):
        res = w.offer(reader.read(img), clock_ns=i * 250_000_000, crop_hash="h", session_id="s")
    assert res.agreed and (res.read.pair, res.read.quarter) == ((14, 0), 3)


def test_agent_logs_agreed_reads_and_health():
    shown = []

    class P(Prompt):
        def show(self, text):
            shown.append(text)

    core = AgentCore(None, None, AgentConfig(health_interval_ms=1_000), P(quiet=True))
    core.mid, core.start_ms = "m", 0
    imgs = [_frame("q2_7-0_234"), _frame("refuse_panel_covered")]
    t = 0
    for img in [imgs[0]] * 4 + [imgs[1]] * 2 + [imgs[0]] * 3:
        t += 250
        core.on_frame(Frame(t, img, frame_hash(img)))
    assert any(s.startswith("scorebug agreed: 7-0 Q2") for s in shown)
    assert sum(s.startswith("scorebug agreed") for s in shown) == 1  # only NEW agreed values are printed
    health = [s for s in shown if s.startswith("scorebug health")]
    assert health and "score_panel_covered=2" in health[-1]
    assert core.stats["scorebug_ok"] == 7 and core.stats["scorebug_reads"] == 9


def test_agent_reader_exception_is_counted_not_silent():
    shown = []

    class P(Prompt):
        def show(self, text):
            shown.append(text)

    class Boom:
        def read(self, img):
            raise ValueError("bad frame")

    core = AgentCore(None, None, AgentConfig(), P(quiet=True), scorebug=Boom())
    core.mid = "m"
    img = _frame("q2_7-0_234")
    core.on_frame(Frame(0, img, frame_hash(img)))
    assert core.stats["scorebug_errors"] == 1 and any("bad frame" in s for s in shown)


@pytest.mark.parametrize("name,want", [("pause_menu_3rd", True), ("pause_menu_1st", True), ("gameplay", False)])
def test_pause_detect_real_menu(name, want):
    d = PauseDetector()
    img = cv2.imread(str(FX / f"{name}_thumb.png"))
    assert d.classify(d.features(img)) is want
    d2 = PauseDetector()  # end to end: no gameplay baseline needed, onset at first frame of the run
    d2.update(100, img)
    assert d2.update(133, img) is want and (d2.onsets == [100]) is want
