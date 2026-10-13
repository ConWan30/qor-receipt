"""Nonce challenges -> DualSense button patterns, and the HID/vision verifier.

The referee issues a random nonce at kickoff, halftime and end. The nonce maps
deterministically to: OPTIONS (pause), then N face/d-pad buttons. The player
performs it on the DualSense Edge (Bluetooth to the PS5). The same controller
is also on USB to the PC, so the agent's HID log records the presses, and the
capture card shows the pause menu opening. The verifier requires:

1. HID log contains OPTIONS then exactly the pattern (next distinct presses),
   all after issue time and before the deadline.
2. Vision saw the pause menu open 0..max_latency ms after the OPTIONS press
   (before the press => video not live / not this console; too late => relayed).

A pre-recorded or replayed stream cannot contain a nonce issued just now.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any

PAUSE = "OPTIONS"
ALPHABET = ["CROSS", "CIRCLE", "SQUARE", "TRIANGLE", "UP", "DOWN", "LEFT", "RIGHT"]
PHASES = ("kickoff", "halftime", "end")
DEFAULT_LEN = 4
DEFAULT_DEADLINE_MS = 20_000
MIN_LATENCY_MS = 0
MAX_LATENCY_MS = 600


def new_nonce() -> str:
    return secrets.token_hex(16)


def pattern_for(nonce_hex: str, length: int = DEFAULT_LEN) -> list[str]:
    """Deterministic: sha256(nonce) bytes mod 8. No immediate repeats (easier to perform/detect)."""
    d = hashlib.sha256(bytes.fromhex(nonce_hex)).digest()
    out: list[str] = []
    i = 0
    while len(out) < length:
        b = ALPHABET[d[i % len(d)] % len(ALPHABET)]
        i += 1
        if out and out[-1] == b:
            b = ALPHABET[(ALPHABET.index(b) + 1) % len(ALPHABET)]
        out.append(b)
    return [PAUSE] + out


@dataclass
class ChallengeResult:
    ok: bool
    reason: str
    pause_press_ms: int | None = None
    done_ms: int | None = None
    latency_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, "pause_press_ms": self.pause_press_ms,
                "done_ms": self.done_ms, "latency_ms": self.latency_ms}


def verify_response(pattern: list[str], hid_events: list[dict[str, Any]], *, issued_ms: int,
                    deadline_ms: int, pause_seen_ms: int | None,
                    min_latency_ms: int = MIN_LATENCY_MS, max_latency_ms: int = MAX_LATENCY_MS) -> ChallengeResult:
    """hid_events: [{"t_ms": int, "button": str, "down": bool}] in capture order."""
    presses = sorted((e for e in hid_events if e.get("down") and issued_ms <= e["t_ms"] <= deadline_ms),
                     key=lambda e: e["t_ms"])
    start = next((i for i, e in enumerate(presses) if e["button"] == PAUSE), None)
    if start is None:
        return ChallengeResult(False, "no_pause_press")
    seq = [e["button"] for e in presses[start : start + len(pattern)]]
    if seq != pattern:
        return ChallengeResult(False, "pattern_mismatch", presses[start]["t_ms"])
    p_ms = presses[start]["t_ms"]
    done = presses[start + len(pattern) - 1]["t_ms"]
    if pause_seen_ms is None:
        return ChallengeResult(False, "pause_not_seen_on_video", p_ms, done)
    lat = pause_seen_ms - p_ms
    if lat < min_latency_ms:
        return ChallengeResult(False, "video_before_input", p_ms, done, lat)
    if lat > max_latency_ms:
        return ChallengeResult(False, "video_latency_too_high", p_ms, done, lat)
    return ChallengeResult(True, "ok", p_ms, done, lat)


def motion_correlation_permille(input_energy: list[int], video_motion: list[int], max_lag: int = 3) -> int:
    """Best Pearson correlation (x1000, int) between per-bin stick/trigger activity and frame-diff
    motion energy, video lagging input by 0..max_lag bins. A supporting signal, not a proof."""
    best = -1000
    for lag in range(0, max_lag + 1):
        x = input_energy[: len(input_energy) - lag] if lag else input_energy
        y = video_motion[lag : lag + len(x)]
        n = min(len(x), len(y))
        if n < 8:
            continue
        x, y = x[:n], y[:n]
        mx, my = sum(x) / n, sum(y) / n
        sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
        sxx = sum((a - mx) ** 2 for a in x)
        syy = sum((b - my) ** 2 for b in y)
        if sxx == 0 or syy == 0:
            continue
        best = max(best, int(round(1000 * sxy / (sxx * syy) ** 0.5)))
    return best
