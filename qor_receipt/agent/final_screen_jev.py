"""Jev 1.14 (TypeSafe System One) final-screen detector -- SHADOW MODE ONLY.

Asks one yes/no (Noul) question per sampled frame: "Is this frame Madden's end-of-game final score
screen?". Results are written to the session log as ``{"jev_final_screen": {...}}`` records (model id,
frame sha256, answer, p_yes, confidence, latency) and NEVER feed ``final_screen`` spans, ``detect_final``,
``compute_tier``, score digits, or the signed receipt. They exist to collect labels for a future
deterministic local detector (see :func:`export_labels`).

Contract with the capture loop (``AgentCore.final_screen_shadow``):
* called only while the scorebug is absent (same place ``final_screen_detector`` is called);
* never blocks: frames are handed to one background worker through a 1-slot queue; a busy worker or
  the rate limit (``min_interval_s``) drops the frame;
* every failure (no key, HTTP error, timeout, bad response) fails closed to ``answer="unknown"``.

API facts (docs.typesafe.ai, checked 2026-10-14): ``POST https://api.typesafe.ai/v1/systemone``, model
pinned to ``jev-1.14.0`` (``jev-latest`` still points at 1.13), header ``TypeSafe-Danger-Zone-Images: true``,
images in a top-level ``assets`` map (key = reference string that must appear in state/instructions/
criteria), PNG/baseline JPEG, each side 256..1568 px, 78,400..2,458,624 px total, <=16 MiB per image,
<=32 MiB body, no server-side resize. Noul answers are ``{"type": "noul", "noul": p_yes}`` with no
separate confidence.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import queue
import threading
import time
import uuid
from typing import Any, Callable

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.14.0"
DANGER_ZONE_IMAGES = "TypeSafe-Danger-Zone-Images"
KEY_ENV = "TYPESAFE_API_KEY"
MODE_ENV = "QORTIME_JEV_FINAL_SCREEN"
QUESTION_ID = "is_final_screen"
QUESTION = "Is this frame (`frame`) Madden's end-of-game final score screen?"
CRITERIA_TRUE = ("The Madden NFL end-of-game screen after the game is over: a large FINAL (or 'Final') label "
                 "with both teams' final scores shown prominently, typically on a game-summary / final score "
                 "screen. The normal bottom scorebug strip is not shown.")
CRITERIA_FALSE = ("Anything else: live gameplay, replays, the pause menu, halftime or end-of-quarter stats "
                  "screens, broadcast scoreboard overlays during play, menus, loading screens, or system "
                  "overlays.")

MIN_SIDE, MAX_SIDE = 256, 1568
MIN_PIXELS, MAX_PIXELS = 78_400, 2_458_624
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_BODY_BYTES = 32 * 1024 * 1024
YES_AT, NO_AT = 0.7, 0.3  # Operator confidence gates; middle band = "unsure"


def resolve_mode(env: dict | None = None) -> str:
    """``shadow`` or ``off``. ``QORTIME_JEV_FINAL_SCREEN=shadow|off``; unset -> shadow iff a key is set.
    Shadow without a key is ``off`` (nothing to call)."""
    env = os.environ if env is None else env
    has_key = bool(env.get(KEY_ENV))
    want = (env.get(MODE_ENV) or "").strip().lower()
    if want == "off":
        return "off"
    if want in ("", "shadow"):
        return "shadow" if has_key else "off"
    raise ValueError(f"{MODE_ENV} must be 'shadow' or 'off', got {want!r}")


def prepare_image(img: Any) -> tuple[bytes, str, int, int]:
    """BGR ndarray -> (encoded bytes, format, w, h) inside the System One image limits.

    Extreme aspect ratios (e.g. a 640x30 scorebug strip) are padded with black to <= 4:1 first, then the
    image is scaled so every side is in 256..1568 and the area in 78,400..2,458,624. PNG, falling back to
    baseline JPEG if the PNG is over 16 MiB."""
    import cv2
    import numpy as np

    if img is None or getattr(img, "ndim", 0) not in (2, 3) or img.size == 0:
        raise ValueError("empty image")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    if max(w, h) > 4 * min(w, h):  # pad the short side
        nh, nw = max(h, -(-w // 4)), max(w, -(-h // 4))
        canvas = np.zeros((nh, nw, 3), dtype=img.dtype)
        y0, x0 = (nh - h) // 2, (nw - w) // 2
        canvas[y0:y0 + h, x0:x0 + w] = img
        img, (h, w) = canvas, (nh, nw)
    s = min(1.0, MAX_SIDE / max(w, h), math.sqrt(MAX_PIXELS / (w * h)))
    s = max(s, MIN_SIDE / min(w, h), math.sqrt(MIN_PIXELS / (w * h)))
    nw, nh = int(math.ceil(w * s)), int(math.ceil(h * s))
    nw, nh = min(max(nw, MIN_SIDE), MAX_SIDE), min(max(nh, MIN_SIDE), MAX_SIDE)
    while nw * nh > MAX_PIXELS:
        nw, nh = nw - 1, max(MIN_SIDE, nh - 1)
    if (nw, nh) != (w, h):
        img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".png", img)
    fmt = "png"
    if not ok or len(buf) > MAX_IMAGE_BYTES:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90, cv2.IMWRITE_JPEG_PROGRESSIVE, 0])
        fmt = "jpg"
    if not ok or len(buf) > MAX_IMAGE_BYTES:
        raise ValueError("image too large after encoding")
    return buf.tobytes(), fmt, nw, nh


def _asset(img: Any) -> tuple[str, dict]:
    data, fmt, _, _ = prepare_image(img)
    return f"qf-{uuid.uuid4()}", {"type": "image", "format": fmt, "data_base64": base64.b64encode(data).decode("ascii")}


def build_request(img: Any, *, model: str = MODEL, examples: list[tuple[Any, bool]] | None = None) -> dict:
    """System One JSON body: frame in ``state``, optional example frames in the Noul ``criteria``."""
    ref, asset = _asset(img)
    assets = {ref: asset}
    true_c: Any = CRITERIA_TRUE
    false_c: Any = CRITERIA_FALSE
    ex_t, ex_f = [], []
    for ex_img, is_final in examples or []:
        r, a = _asset(ex_img)
        assets[r] = a
        (ex_t if is_final else ex_f).append(r)
    if ex_t:
        true_c = {"description": CRITERIA_TRUE, "examples": ex_t}
    if ex_f:
        false_c = {"description": CRITERIA_FALSE, "examples": ex_f}
    return {"model": model, "state": {"frame": ref}, "assets": assets,
            "questions": {QUESTION_ID: {"type": "noul", "instructions": QUESTION,
                                        "criteria": {"true": true_c, "false": false_c}}}}


def _httpx_post(url: str, *, headers: dict, content: bytes, timeout: float) -> tuple[int, dict | str]:
    import httpx

    r = httpx.post(url, headers=headers, content=content, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:300]


class JevFinalScreenDetector:
    """Shadow-mode final-screen classifier. Call :meth:`submit` from the capture loop (non-blocking) or
    :meth:`classify` directly (blocking; used by the smoke script and the worker)."""

    def __init__(self, api_key: str | None = None, *, log: Callable[[dict], None] | None = None,
                 model: str = MODEL, min_interval_s: float = 5.0, timeout_s: float = 15.0,
                 examples: list[tuple[Any, bool]] | None = None,
                 http_post: Callable[..., tuple[int, Any]] | None = None,
                 clock: Callable[[], float] = time.monotonic, url: str = API_URL) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get(KEY_ENV)
        self.log = log
        self.model = model
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.examples = examples
        self.http_post = http_post or _httpx_post
        self.clock = clock
        self.url = url
        self._last_submit: float | None = None
        self._q: queue.Queue = queue.Queue(maxsize=1)
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self.stats = {"submitted": 0, "rate_limited": 0, "busy": 0, "calls": 0, "errors": 0}

    # ---- blocking call
    def classify(self, img: Any) -> dict:
        """Return a result record; never raises (fails closed to ``answer="unknown"``)."""
        t0 = time.perf_counter()
        sha = None
        rec: dict = {"model": self.model, "answer": "unknown", "p_yes": None, "confidence": None}
        try:
            import numpy as np
            sha = hashlib.sha256(np.ascontiguousarray(img).tobytes()).hexdigest()
            if not self.api_key:
                raise RuntimeError(f"no {KEY_ENV}")
            body = json.dumps(build_request(img, model=self.model, examples=self.examples),
                              separators=(",", ":")).encode()
            if len(body) > MAX_BODY_BYTES:
                raise ValueError("request body over 32 MiB")
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                       DANGER_ZONE_IMAGES: "true"}
            self.stats["calls"] += 1
            status, data = self.http_post(self.url, headers=headers, content=body, timeout=self.timeout_s)
            if status != 200 or not isinstance(data, dict):
                raise RuntimeError(f"HTTP {status}: {str(data)[:200]}")
            ans = data["answers"][QUESTION_ID]
            p = float(ans["noul"])
            if not 0.0 <= p <= 1.0:
                raise ValueError(f"noul out of range: {p}")
            rec.update(p_yes=round(p, 4), confidence=round(max(p, 1 - p), 4),
                       answer="yes" if p >= YES_AT else "no" if p <= NO_AT else "unsure",
                       model_answered=data.get("model"))
        except Exception as e:  # noqa: BLE001 -- fail closed
            self.stats["errors"] += 1
            rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        rec["frame_sha256"] = sha
        rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return rec

    # ---- non-blocking path
    def submit(self, img: Any, t_ms: int | None = None) -> bool:
        """Queue one frame for the background worker. Returns False if rate-limited or busy."""
        now = self.clock()
        if self._last_submit is not None and now - self._last_submit < self.min_interval_s:
            self.stats["rate_limited"] += 1
            return False
        try:
            self._q.put_nowait((img.copy() if hasattr(img, "copy") else img, t_ms))
        except queue.Full:
            self.stats["busy"] += 1
            return False
        self._last_submit = now
        self.stats["submitted"] += 1
        self._ensure_worker()
        return True

    __call__ = submit  # usable directly as AgentCore.final_screen_shadow(image, t_ms)

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, name="jev-final-screen", daemon=True)
            self._worker.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            img, t_ms = item
            rec = self.classify(img)
            rec["t_ms"] = t_ms
            rec["mode"] = "shadow"
            if self.log:
                try:
                    self.log({"jev_final_screen": rec})
                except Exception:  # noqa: BLE001 -- logging must never kill the worker
                    pass
            self._q.task_done()

    def drain(self, timeout: float = 30.0) -> None:
        """Wait for the queued frame (if any) to finish -- tests and shutdown."""
        end = time.monotonic() + timeout
        while self._q.unfinished_tasks and time.monotonic() < end:
            time.sleep(0.01)

    def close(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout)


def from_env(log: Callable[[dict], None] | None = None, env: dict | None = None, **kw) -> JevFinalScreenDetector | None:
    """Detector for the resolved mode, or None when off."""
    env = os.environ if env is None else env
    if resolve_mode(env) != "shadow":
        return None
    return JevFinalScreenDetector(env.get(KEY_ENV), log=log, **kw)


def export_labels(session_log: str, out: str) -> int:
    """Session log -> training-label JSONL ``{frame_sha256, t_ms, model, p_yes, label}`` (label is
    true/false/null from the gates). Shadow labels are weak labels: review before training."""
    n = 0
    with open(session_log, encoding="utf-8") as fh, open(out, "w", encoding="utf-8") as oh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line).get("jev_final_screen")
            if not r or r.get("answer") == "unknown":
                continue
            label = {"yes": True, "no": False}.get(r["answer"])
            oh.write(json.dumps({"frame_sha256": r["frame_sha256"], "t_ms": r.get("t_ms"), "model": r["model"],
                                 "p_yes": r["p_yes"], "label": label}) + "\n")
            n += 1
    return n
