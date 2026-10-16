"""Judgment layer: TypeSafe Jev System One with image input.

API facts (docs.typesafe.ai/images.md, checked 2026-10-16): POST /v1/systemone, header
``TypeSafe-Danger-Zone-Images: true``, images in a top-level ``assets`` map whose key must be referenced in
state/instructions/criteria, PNG/JPEG, sides 280..1568 px. Images are "Dreamer-ready" (beta).

Jev returns typed answers only (Choice probabilities + confidence), so a score "read" is a Choice over every
legal value. Measured on 22 real Madden frames (jev-vision-test/REPORT.md): scores ~all right on visible
scorebugs, but it hallucinates scores at conf <= 0.68 when the bar is hidden and it reads doctored frames at
face value (conf ~1.0). Hence: accept only conf >= 0.9 AND 2-3 frame agreement; never use Jev for tampering.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable

from qor_receipt.referee_ai.vision import Observation

API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.14.0"
KEY_ENV = "TYPESAFE_API_KEY"
ACCEPT_PERMILLE = 900
MIN_AGREE = 2
PRICE_PER_MTOK_USD = 0.042

FRAME_TYPES = {
    "live": "Live gameplay: players on the field, the broadcast scorebug bar visible at the bottom.",
    "cutscene": "A replay, cinematic cutscene, celebration, touchdown/stat graphic, or the MADDEN logo wipe.",
    "menu": "A pause menu or other in-game menu (RESUME, CALL TIMEOUT...) while the game is in progress.",
    "final": "The post-game options menu (FINISH GAME, VIEW HIGHLIGHTS...) shown right after the game ends, with the final score.",
    "black": "An all-black, blank, or no-signal frame.",
}
QUARTERS = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "OT": 5}
SB = "the Madden scorebug or score display in `frame`"


def _vals(rng) -> dict[str, str]:
    d = {str(v): f"The value shown is {v}." for v in rng}
    d["none"] = "Not visible or cannot be read."
    return d


def questions() -> dict[str, Any]:
    return {
        "frame_type": {"type": "choice", "instructions": "What kind of Madden NFL video frame is `frame`?", "criteria": FRAME_TYPES},
        "away_score": {"type": "choice", "instructions": f"In {SB}, what is the score of the LEFT (away) team?", "criteria": _vals(range(50))},
        "home_score": {"type": "choice", "instructions": f"In {SB}, what is the score of the RIGHT (home) team?", "criteria": _vals(range(50))},
        "quarter": {"type": "choice", "instructions": f"In {SB}, which quarter is shown?",
                    "criteria": {**{k: f"{k} quarter" for k in QUARTERS}, "none": "No quarter visible."}},
        "clock_minutes": {"type": "choice", "instructions": f"In {SB}, the GAME clock next to the quarter reads M:SS (ignore the separate :SS play clock). What is M?", "criteria": _vals(range(16))},
        "clock_seconds": {"type": "choice", "instructions": f"In {SB}, the GAME clock next to the quarter reads M:SS (ignore the separate :SS play clock). What is SS?", "criteria": _vals([f"{s:02d}" for s in range(60)])},
    }


def build_request(img_bytes: bytes, fmt: str, model: str = DEFAULT_MODEL) -> dict[str, Any]:
    ref = f"qr-{uuid.uuid4()}"
    return {"model": model, "state": {"frame": ref},
            "assets": {ref: {"type": "image", "format": fmt, "data_base64": base64.b64encode(img_bytes).decode("ascii")}},
            "questions": questions()}


def _urllib_post(body: dict, key: str, timeout: float) -> tuple[int, Any]:
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "TypeSafe-Danger-Zone-Images": "true"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300].decode("utf-8", "replace")


MIN_SIDE_DOCS = 280  # images.md says 280 (the QorTime shadow helper uses 256)


def _pad_min(img):
    """Pad with black so both sides are >= 280 px (the API never resizes and rejects smaller)."""
    import numpy as np

    h, w = img.shape[:2]
    if h >= MIN_SIDE_DOCS and w >= MIN_SIDE_DOCS:
        return img
    nh, nw = max(h, MIN_SIDE_DOCS), max(w, MIN_SIDE_DOCS)
    out = np.zeros((nh, nw) + img.shape[2:], dtype=img.dtype)
    y0, x0 = (nh - h) // 2, (nw - w) // 2
    out[y0:y0 + h, x0:x0 + w] = img
    return out


def parse(answers: dict[str, Any]) -> Observation:
    def ch(k):
        a = answers.get(k) or {}
        c = a.get("choice")
        return (None if c in (None, "none") else c), int(round(float(a.get("confidence") or 0) * 1000))

    ft, ftc = ch("frame_type")
    aw, awc = ch("away_score")
    ho, hoc = ch("home_score")
    q, qc = ch("quarter")
    m, mc = ch("clock_minutes")
    s, sc = ch("clock_seconds")
    clock = int(m) * 60 + int(s) if m is not None and s is not None else None
    return Observation("jev", True, "ok", frame_type=ft, away=None if aw is None else int(aw),
                       home=None if ho is None else int(ho), quarter=QUARTERS.get(q) if q else None, clock_s=clock,
                       conf={"frame_type": ftc, "away": awc, "home": hoc, "quarter": qc, "clock": min(mc, sc)})


class JevJudge:
    """One request per frame (all questions share the image in ``state``). Fails closed to ok=False."""

    def __init__(self, *, model: str = DEFAULT_MODEL, key: str | None = None, timeout: float = 30.0,
                 post: Callable[[dict, str, float], tuple[int, Any]] | None = None) -> None:
        self.model = model
        self._key = key if key is not None else os.environ.get(KEY_ENV, "")
        self.timeout = timeout
        self._post = post or _urllib_post
        self.calls: list[dict[str, Any]] = []   # latency / usage log (no key, no image)

    @property
    def available(self) -> bool:
        return bool(self._key)

    def observe(self, img) -> Observation:
        if not self.available:
            return Observation("jev", False, "no_api_key")
        from qor_receipt.agent.final_screen_jev import prepare_image

        try:
            data, fmt, _, _ = prepare_image(_pad_min(img))
        except Exception as e:  # noqa: BLE001
            return Observation("jev", False, f"image_rejected:{e}")
        t = time.perf_counter()
        try:
            status, j = self._post(build_request(data, fmt, self.model), self._key, self.timeout)
        except Exception as e:  # noqa: BLE001
            return Observation("jev", False, f"transport:{type(e).__name__}")
        ms = int((time.perf_counter() - t) * 1000)
        if status != 200 or not isinstance(j, dict) or "answers" not in j:
            self.calls.append({"status": status, "latency_ms": ms})
            return Observation("jev", False, f"http_{status}")
        usage = j.get("usage") or {}
        self.calls.append({"status": 200, "latency_ms": ms, "model": j.get("model"), "input_tokens": usage.get("input_tokens")})
        o = parse(j["answers"])
        o.raw = {"model": j.get("model"), "latency_ms": ms}
        return o


def consensus(obs: list[Observation], *, accept: int = ACCEPT_PERMILLE, min_agree: int = MIN_AGREE) -> dict[str, Any]:
    """Per field: accept a value only if >= min_agree frames read it at conf >= accept and no confident frame
    read something else. Otherwise the field is ``None`` (abstain) with a reason."""
    fields = {"frame_type": "frame_type", "away": "away", "home": "home", "quarter": "quarter", "clock_s": "clock"}
    out: dict[str, Any] = {"accepted": {}, "abstained": {}}
    good = [o for o in obs if o.ok]
    for attr, ck in fields.items():
        conf_vals = [getattr(o, attr) for o in good if getattr(o, attr) is not None and o.conf.get(ck, 0) >= accept]
        distinct = set(conf_vals)
        if len(distinct) == 1 and len(conf_vals) >= min_agree:
            out["accepted"][attr] = conf_vals[0]
        elif len(distinct) > 1:
            out["abstained"][attr] = f"confident frames disagree: {sorted(distinct, key=str)}"
        else:
            out["abstained"][attr] = f"{len(conf_vals)}/{len(obs)} frames at conf>={accept / 1000:.1f} (need {min_agree})"
    return out
