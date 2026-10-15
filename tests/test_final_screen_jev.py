"""Jev final-screen shadow detector: all HTTP mocked (no key, no network)."""
from __future__ import annotations

import base64
import json
import threading
import time

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from qor_receipt.agent import final_screen_jev as fj  # noqa: E402


class FakeHTTP:
    def __init__(self, status=200, data=None, delay=0.0, exc=None):
        self.calls = []
        self.status, self.delay, self.exc = status, delay, exc
        self.data = data if data is not None else {"model": "jev-1.14.0", "answers": {
            fj.QUESTION_ID: {"type": "noul", "noul": 0.97}}, "usage": {"input_tokens": 900, "output_tokens": 1}}

    def __call__(self, url, *, headers, content, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(content), "timeout": timeout})
        if self.delay:
            time.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.status, self.data


def frame(h=360, w=640, seed=0):
    return np.random.default_rng(seed).integers(0, 255, (h, w, 3), dtype=np.uint8)


def _all_strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _all_strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _all_strings(v)


def _decoded(asset):
    return cv2.imdecode(np.frombuffer(base64.b64decode(asset["data_base64"]), np.uint8), cv2.IMREAD_COLOR)


def test_request_shape_headers_model_pin_asset_referenced():
    http = FakeHTTP()
    rec = fj.JevFinalScreenDetector("k-test", http_post=http).classify(frame())
    (c,) = http.calls
    assert c["url"] == "https://api.typesafe.ai/v1/systemone"
    assert c["headers"]["TypeSafe-Danger-Zone-Images"] == "true"
    assert c["headers"]["Authorization"] == "Bearer k-test"
    b = c["body"]
    assert b["model"] == "jev-1.14.0" and b["model"] != "jev-latest"
    q = b["questions"][fj.QUESTION_ID]
    assert q["type"] == "noul" and "final score screen" in q["instructions"] and set(q["criteria"]) == {"true", "false"}
    (ref,) = b["assets"]
    assert b["state"] == {"frame": ref}
    a = b["assets"][ref]
    assert a["type"] == "image" and a["format"] in ("png", "jpg") and "\n" not in a["data_base64"]
    for k in b["assets"]:  # every asset referenced somewhere else in the body (else the API 400s)
        assert any(k in s for s in _all_strings({"s": b["state"], "q": b["questions"]}))
    assert rec["answer"] == "yes" and rec["p_yes"] == 0.97 and rec["model"] == "jev-1.14.0"
    assert rec["frame_sha256"] and rec["latency_ms"] >= 0 and "error" not in rec


def test_examples_go_in_criteria_and_are_referenced():
    http = FakeHTTP()
    fj.JevFinalScreenDetector("k", http_post=http, examples=[(frame(seed=1), True), (frame(seed=2), False)]).classify(frame())
    b = http.calls[0]["body"]
    assert len(b["assets"]) == 3
    crit = b["questions"][fj.QUESTION_ID]["criteria"]
    assert len(crit["true"]["examples"]) == 1 and len(crit["false"]["examples"]) == 1
    strings = list(_all_strings({"s": b["state"], "q": b["questions"]}))
    assert all(any(k in s for s in strings) for k in b["assets"])


@pytest.mark.parametrize("shape", [(360, 640), (90, 160), (30, 640), (2160, 3840), (4000, 300), (100, 100), (1568, 1568)])
def test_prepare_image_within_limits(shape):
    data, fmt, w, h = fj.prepare_image(frame(*shape))
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (h, w)
    assert fj.MIN_SIDE <= w <= fj.MAX_SIDE and fj.MIN_SIDE <= h <= fj.MAX_SIDE
    assert fj.MIN_PIXELS <= w * h <= fj.MAX_PIXELS
    assert len(data) <= fj.MAX_IMAGE_BYTES and fmt in ("png", "jpg")


def test_native_frame_not_resized():
    _, _, w, h = fj.prepare_image(frame())
    assert (w, h) == (640, 360)


@pytest.mark.parametrize("http", [
    FakeHTTP(exc=TimeoutError("read timed out")),
    FakeHTTP(status=400, data={"error": "bad"}),
    FakeHTTP(status=500, data="oops"),
    FakeHTTP(data={"answers": {}}),
    FakeHTTP(data={"answers": {fj.QUESTION_ID: {"type": "noul", "noul": 7}}}),
])
def test_errors_fail_closed(http):
    rec = fj.JevFinalScreenDetector("k", http_post=http).classify(frame())
    assert rec["answer"] == "unknown" and rec["p_yes"] is None and "error" in rec


def test_no_key_fails_closed_without_calling():
    http = FakeHTTP()
    rec = fj.JevFinalScreenDetector("", http_post=http).classify(frame())
    assert rec["answer"] == "unknown" and not http.calls


def test_timeout_passed_through():
    http = FakeHTTP()
    fj.JevFinalScreenDetector("k", http_post=http, timeout_s=3.5).classify(frame())
    assert http.calls[0]["timeout"] == 3.5


def test_gates():
    for p, want in [(0.95, "yes"), (0.5, "unsure"), (0.05, "no")]:
        http = FakeHTTP(data={"answers": {fj.QUESTION_ID: {"type": "noul", "noul": p}}})
        assert fj.JevFinalScreenDetector("k", http_post=http).classify(frame())["answer"] == want


def test_rate_limit_and_nonblocking_submit():
    t = [0.0]
    http = FakeHTTP(delay=0.3)
    logs = []
    d = fj.JevFinalScreenDetector("k", http_post=http, log=logs.append, min_interval_s=5, clock=lambda: t[0])
    t0 = time.perf_counter()
    assert d.submit(frame(), 1) is True
    assert time.perf_counter() - t0 < 0.1  # never blocks on the HTTP call
    t[0] = 1.0
    assert d.submit(frame(), 2) is False  # inside the interval
    t[0] = 6.0
    assert d.submit(frame(), 3) in (True, False)  # True only if the worker already took frame 1
    d.drain()
    t[0] = 20.0
    assert d.submit(frame(), 4) is True
    d.drain()
    d.close()
    assert d.stats["rate_limited"] == 1
    assert len(http.calls) == d.stats["submitted"]
    assert all(r["jev_final_screen"]["mode"] == "shadow" for r in logs)


def test_resolve_mode():
    assert fj.resolve_mode({}) == "off"
    assert fj.resolve_mode({"TYPESAFE_API_KEY": "x"}) == "shadow"
    assert fj.resolve_mode({"TYPESAFE_API_KEY": "x", "QORTIME_JEV_FINAL_SCREEN": "off"}) == "off"
    assert fj.resolve_mode({"QORTIME_JEV_FINAL_SCREEN": "shadow"}) == "off"  # no key -> nothing to call
    with pytest.raises(ValueError):
        fj.resolve_mode({"QORTIME_JEV_FINAL_SCREEN": "on"})
    assert fj.from_env(env={}) is None


def _run_core(shadow, absent_frames):
    from qor_receipt._vendor.qoresence.vision.local_scorebug.profile import MADDEN27_STANDARD
    from qor_receipt._vendor.qoresence.vision.local_scorebug.reader import FrameRead
    from qor_receipt.agent.capture import Frame
    from qor_receipt.agent.runner import AgentConfig, AgentCore, Prompt

    ok = FrameRead(ok=True, reason="ok", profile_id=MADDEN27_STANDARD.profile_id, left_score=21, right_score=17,
                   quarter=4, clock_seconds=3, clock_text="0:03", clock_red=True)
    gone = FrameRead(ok=False, reason="layout_unknown", profile_id=MADDEN27_STANDARD.profile_id)
    script = [ok] * 20 + [gone] * absent_frames

    class R:
        def read(self, img):
            return script.pop(0)

        def reference_band(self, img):
            return np.zeros((30, 640, 3), np.uint8), "ok"

    core = AgentCore(None, None, AgentConfig(), Prompt(quiet=True), scorebug=R())
    core.mid, core.start_ms = "m", 0
    core._issue = lambda phase, t: core.challenges.setdefault(phase, {"t": t})
    core.final_screen_shadow = shadow
    img = frame()
    for i in range(20 + absent_frames):
        core.on_frame(Frame((i + 1) * 250, img, "h"))
    return core


def test_shadow_yes_never_sets_final_screen_basis():
    logs = []
    d = fj.JevFinalScreenDetector("k", http_post=FakeHTTP(), log=logs.append, min_interval_s=0)
    core = _run_core(d.submit, 140)  # 35 s absent -> scorebug_gone
    d.drain()
    d.close()
    assert logs and all(r["jev_final_screen"]["answer"] == "yes" for r in logs)
    assert core.final is not None and core.final.basis == "scorebug_gone"
    assert not any(r.get("obs") == "final_screen" for r in core.timeline)


def test_shadow_yes_cannot_make_short_absence_final():
    d = fj.JevFinalScreenDetector("k", http_post=FakeHTTP(), min_interval_s=0)
    core = _run_core(d.submit, 40)  # 10 s absent: not final without a real detector
    d.drain()
    d.close()
    assert core.final is None and not any(r.get("obs") == "final_screen" for r in core.timeline)


def test_shadow_hook_exception_does_not_break_capture():
    def boom(img, t):
        raise RuntimeError("x")
    core = _run_core(boom, 140)
    assert core.final is not None


def test_shadow_only_called_while_absent():
    seen = []
    _run_core(lambda img, t: seen.append(t), 8)
    assert len(seen) == 8 and min(seen) > 20 * 250


def test_session_log_threadsafe_and_labels(tmp_path):
    from qor_receipt.agent.runner import SessionLog

    log = SessionLog(tmp_path / "s.jsonl")
    d = fj.JevFinalScreenDetector("k", http_post=FakeHTTP(), log=log, min_interval_s=0)
    ths = [threading.Thread(target=lambda: log({"x": 1})) for _ in range(5)]
    [t.start() for t in ths]
    d.submit(frame(), 10)
    d.drain()
    [t.join() for t in ths]
    d.close()
    log({"jev_final_screen": {"answer": "unknown", "frame_sha256": None, "model": fj.MODEL, "p_yes": None}})
    log.close()
    log({"after": "close"})  # must not raise
    n = fj.export_labels(str(tmp_path / "s.jsonl"), str(tmp_path / "labels.jsonl"))
    assert n == 1
    row = json.loads((tmp_path / "labels.jsonl").read_text())
    assert row["label"] is True and row["model"] == "jev-1.14.0" and row["t_ms"] == 10
