import pytest

from qor_receipt.challenge import ALPHABET, PAUSE, motion_correlation_permille, new_nonce, pattern_for, verify_response
from qor_receipt.demo import perform

NONCE = "00112233445566778899aabbccddeeff"


def test_pattern_deterministic_and_shape():
    p = pattern_for(NONCE)
    assert p == pattern_for(NONCE) and p[0] == PAUSE and len(p) == 5
    assert all(b in ALPHABET for b in p[1:]) and all(a != b for a, b in zip(p[1:], p[2:]))


def test_patterns_vary():
    assert len({tuple(pattern_for(new_nonce())) for _ in range(50)}) > 40


def _v(events, seen=1_120, **kw):
    return verify_response(pattern_for(NONCE), events, issued_ms=0, deadline_ms=20_000, pause_seen_ms=seen, **kw)


def test_good_response():
    r = _v(perform(pattern_for(NONCE), 1_000))
    assert r.ok and r.latency_ms == 120


def test_noise_before_pause_ok():
    ev = [{"t_ms": 500, "button": "CROSS", "down": True}] + perform(pattern_for(NONCE), 1_000)
    assert _v(ev).ok


@pytest.mark.parametrize("case,reason", [
    ("wrong", "pattern_mismatch"), ("nopause", "no_pause_press"), ("late", "no_pause_press"),
    ("before_issue", "no_pause_press"), ("extra", "pattern_mismatch"),
])
def test_bad_hid(case, reason):
    p = pattern_for(NONCE)
    if case == "wrong":
        ev = perform([PAUSE] + list(reversed(p[1:])) if p[1:] != p[:0:-1] else [PAUSE] + ["CROSS"] * 4, 1_000)
    elif case == "nopause":
        ev = perform(p[1:], 1_000)
    elif case == "late":
        ev = perform(p, 25_000)
    elif case == "before_issue":
        ev = perform(p, -5_000)
    else:
        ev = perform(p[:2] + ["TRIANGLE" if p[2] != "TRIANGLE" else "CROSS"] + p[2:], 1_000)
    assert _v(ev).reason == reason


@pytest.mark.parametrize("seen,reason", [(None, "pause_not_seen_on_video"), (900, "video_before_input"),
                                         (2_000, "video_latency_too_high")])
def test_bad_video(seen, reason):
    r = _v(perform(pattern_for(NONCE), 1_000), seen=seen)
    assert not r.ok and r.reason == reason


def test_motion_correlation():
    x = [((i * 7) % 11) * 10 for i in range(40)]
    assert motion_correlation_permille(x, [0, 0] + x[:-2]) > 900
    assert motion_correlation_permille(x, [50] * 40) == -1000
    assert motion_correlation_permille(x, [((i * 5) % 3) * 9 for i in range(40)]) < 500
