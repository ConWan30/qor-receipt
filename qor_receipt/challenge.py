"""Nonce challenges -> DualSense button patterns, and the HID/vision verifier.

The referee issues a random nonce for three phases: ``kickoff`` (game start), ``halftime`` (second half) and
``end`` (4th quarter). The nonce maps
deterministically to: OPTIONS (pause), then N face/d-pad buttons. The player
performs it on the DualSense Edge (Bluetooth to the PS5). The same controller
is also on USB to the PC, so the agent's HID log records the presses, and the
capture card shows the pause menu opening. The verifier requires:

1. HID log contains OPTIONS then exactly the pattern (next distinct presses),
   all after issue time and before the deadline.
2. Vision saw the pause menu open 0..max_latency ms after the OPTIONS press
   (before the press => video not live / not this console; too late => relayed).

A pre-recorded or replayed stream cannot contain a nonce issued just now.

v0.3.11 (pause-capable windows). In online Madden 27 a player cannot pause during a kickoff or a kickoff return:
OPTIONS only opens the pause menu once a play has been called (pre-snap). So the recorder asks for a phase's nonce
only at a pause-capable moment (after the kickoff of that half/quarter and a play call, see QorCap's
``qorcap_playcall``), and the window is ``DEFAULT_DEADLINE_MS`` = 90 s so a quick snap by the opponent does not
burn it: the player pauses at the next pre-snap. Phase names are unchanged for receipt compatibility.
Inside the window the response is the first OPTIONS press that is followed by exactly the pattern, so an
OPTIONS press that did nothing (live play) does not void the challenge. Security is unchanged: the pattern is
unknown before ``issued_ms``, every press must fall inside [issued_ms, deadline_ms], the pause menu must appear
0..600 ms after THAT press on video, and windows longer than ``MAX_WINDOW_MS`` are refused by the verifiers.
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
DEFAULT_DEADLINE_MS = 90_000   # v0.3.11: was 20 s (passable only if the prompt landed pre-snap)
LEGACY_DEADLINE_MS = 20_000
MAX_WINDOW_MS = 120_000          # verifiers refuse longer windows (partial: challenge_window_too_long)
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


def response_start(pattern: list[str], presses: list[dict[str, Any]]) -> int | None:
    """Index (into time-sorted down presses) of the OPTIONS press that answers the challenge: the first one followed
    by exactly the pattern; else the first OPTIONS press (so the failure reason stays pattern_mismatch); else None."""
    first = None
    for i, e in enumerate(presses):
        if e["button"] != PAUSE:
            continue
        if first is None:
            first = i
        if [x["button"] for x in presses[i : i + len(pattern)]] == pattern:
            return i
    return first


def response_press_ms(pattern: list[str], hid_events: list[dict[str, Any]], *, issued_ms: int,
                      deadline_ms: int) -> int | None:
    """t_ms of the answering OPTIONS press inside the window (what the video pause frame is matched against)."""
    presses = sorted((e for e in hid_events if e.get("down", True) and issued_ms <= e["t_ms"] <= deadline_ms),
                     key=lambda e: e["t_ms"])
    i = response_start(pattern, presses)
    return presses[i]["t_ms"] if i is not None else None


def verify_response(pattern: list[str], hid_events: list[dict[str, Any]], *, issued_ms: int,
                    deadline_ms: int, pause_seen_ms: int | None,
                    min_latency_ms: int = MIN_LATENCY_MS, max_latency_ms: int = MAX_LATENCY_MS) -> ChallengeResult:
    """hid_events: [{"t_ms": int, "button": str, "down": bool}] in capture order."""
    presses = sorted((e for e in hid_events if e.get("down") and issued_ms <= e["t_ms"] <= deadline_ms),
                     key=lambda e: e["t_ms"])
    start = response_start(pattern, presses)
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
