"""Synthetic replay session: real Madden 27 scorebug strips on synthetic gameplay, synthetic pause
menus (NEEDS TUNING vs real frames), and raw DualSense USB reports at 250 Hz performing the challenges.

Timeline (s from t0): Q2 0-0 | 10 Q2 14-7 | 18 Q3 22-7 (halftime challenge) | 30 Q4 21-24 0:32 |
40 Q4 21-24 0:00 (FINAL -> end challenge) | 55 end. Kickoff challenge performed at 3 s.
"""
from __future__ import annotations

import json
from pathlib import Path

from qor_receipt.challenge import new_nonce, pattern_for

from .hid import pack_usb_report

STRIPS = Path(__file__).parent / "fixtures" / "strips"
T0 = 1_790_000_000_000
SEGMENTS = [(0, "std_0_0_2nd_0-53.jpg"), (10, "std_14_7_2nd_1-23.jpg"), (18, "std_22_7_3rd_4-00.jpg"),
            (30, "std_21_24_4th_0-32.jpg"), (40, "spliced_21_24_4th_0-00.png")]
END_S = 55
PRESS_S = {"kickoff": 3.0, "halftime": 21.0, "end": 46.0}
STEP_MS = 300
HOLD_MS = 80


def _strip_at(s: float) -> str:
    return [n for t, n in SEGMENTS if s >= t][-1]


def stick_lx(t_ms_rel: int) -> int:
    """Per-second stick deflection pattern (drives on-screen motion)."""
    b = t_ms_rel // 1000
    return 128 + [0, 60, 120, 30, 90, 0, 127, 45][(b * 5 + 3) % 8] * (1 if b % 2 else -1) if b % 8 else 128


def make_session(out: str | Path, *, t0: int = T0, pause_latency_ms: int = 150, nonces: dict | None = None,
                 skip_pause: str | None = None) -> dict:
    import cv2
    import numpy as np

    out = Path(out)
    fdir = out / "frames"
    fdir.mkdir(parents=True, exist_ok=True)
    nonces = nonces or {ph: new_nonce() for ph in PRESS_S}
    pats = {ph: pattern_for(n) for ph, n in nonces.items()}
    presses: list[tuple[int, str]] = []
    pause_windows = []
    for ph, s in PRESS_S.items():
        p0 = t0 + int(s * 1000)
        for i, b in enumerate(pats[ph]):
            presses.append((p0 + i * STEP_MS, b))
        if ph != skip_pause:
            pause_windows.append((p0 + pause_latency_ms, p0 + (len(pats[ph]) - 1) * STEP_MS + 400))

    # ---- HID: 250 Hz USB reports
    rng = np.random.default_rng(7)
    lines = [json.dumps({"challenge": {"phase": ph, "role": "home", "nonce": n}}) for ph, n in nonces.items()]
    for k, t in enumerate(range(t0, t0 + END_S * 1000, 4)):
        held = {b for p, b in presses if p <= t < p + HOLD_MS}
        lx = stick_lx(t - t0) if not any(a <= t < b for a, b in pause_windows) else 128
        rep = pack_usb_report(held, (lx, 128 + int(rng.integers(-3, 4)), 128, 128), counter=k)
        lines.append(json.dumps({"t_ms": t, "report": rep.hex()}))
    (out / "hid.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- frames: 5 fps, 25 fps around challenges
    tex = rng.integers(50, 140, size=(330, 640 + 4096), dtype=np.uint8)
    field = np.dstack([tex * 0.6, tex, tex * 0.5]).astype(np.uint8)
    strips = {n: cv2.imread(str(STRIPS / n)) for _, n in SEGMENTS}
    times = set(range(t0, t0 + END_S * 1000, 200))
    for s in PRESS_S.values():
        p0 = t0 + int(s * 1000)
        times |= set(range(p0 - 400, p0 + 2_200, 40))
    offset, last_t = 0.0, t0
    for t in sorted(times):
        offset += (stick_lx(t - t0) - 128) / 128 * 0.4 * (t - last_t)
        last_t = t
        x = int(offset) % 4096
        img = np.zeros((360, 640, 3), np.uint8)
        img[:330] = field[:, x:x + 640]
        img[330:] = strips[_strip_at((t - t0) / 1000)]
        if any(a <= t < b for a, b in pause_windows):
            img = (img * 0.4).astype(np.uint8)
            img[72:288, 64:256] = 225               # menu panel
            img[90:110, 80:240] = 40                # menu rows
            img[130:150, 80:240] = 40
        cv2.imwrite(str(fdir / f"{t}.png"), img)
    return {"frames_dir": str(fdir), "hid_log": str(out / "hid.jsonl"), "nonces": nonces, "patterns": pats}
