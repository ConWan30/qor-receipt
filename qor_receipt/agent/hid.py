"""DualSense / DualSense Edge HID (USB report 0x01; BT 0x31 tolerated) -> button edges + decimated sticks.

Offsets follow Qoresence ``sync/hid_report.py`` (QorTroller map): lx=1 ly=2 rx=3 ry=4 l2=5 r2=6,
counter=7, buttons 8 (hat low nibble, face high nibble), 9, 10. Bluetooth 0x31 shifts +1.

Qoresence learned that processing every 1 kHz USB report caused slowdowns, so (like
QORESENCE_HID_ANALOG_HZ=125) analog stick samples are decimated to ``analog_hz`` while EVERY
button edge is kept. Timestamps come from the agent's monotonic clock.
"""
from __future__ import annotations

import os
import threading
from collections import deque
from typing import Any

SONY_VID = 0x054C
DUALSENSE_PIDS = {0x0CE6: "DualSense", 0x0DF2: "DualSense Edge"}

FACE = {0x10: "SQUARE", 0x20: "CROSS", 0x40: "CIRCLE", 0x80: "TRIANGLE"}
B1 = {0x01: "L1", 0x02: "R1", 0x04: "L2", 0x08: "R2", 0x10: "CREATE", 0x20: "OPTIONS", 0x40: "L3", 0x80: "R3"}
B2 = {0x01: "PS", 0x02: "TOUCHPAD", 0x04: "MUTE", 0x10: "FN_L", 0x20: "FN_R", 0x40: "PADDLE_L", 0x80: "PADDLE_R"}
HAT = {0: ("UP",), 1: ("UP", "RIGHT"), 2: ("RIGHT",), 3: ("DOWN", "RIGHT"), 4: ("DOWN",),
       5: ("DOWN", "LEFT"), 6: ("LEFT",), 7: ("UP", "LEFT")}
_BIT_FOR = {**{v: (0, k) for k, v in FACE.items()}, **{v: (1, k) for k, v in B1.items()}, **{v: (2, k) for k, v in B2.items()}}
_HAT_FOR = {v: k for k, v in HAT.items()}


def default_analog_hz() -> float:
    v = os.environ.get("QOR_HID_ANALOG_HZ") or os.environ.get("QORESENCE_HID_ANALOG_HZ") or "125"
    try:
        return float(v)
    except ValueError:
        return 125.0


def parse_report(raw: bytes) -> dict[str, Any] | None:
    """-> {"buttons": frozenset[str], "sticks": (lx, ly, rx, ry), "l2", "r2"} or None if not a DualSense input report."""
    if len(raw) >= 64 and raw[0] == 0x01:
        o = 0
    elif len(raw) >= 78 and raw[0] == 0x31:
        o = 1
    else:
        return None
    b0, b1, b2 = raw[8 + o], raw[9 + o], raw[10 + o]
    names = set(HAT.get(b0 & 0x0F, ()))
    names |= {n for bit, n in FACE.items() if b0 & bit}
    names |= {n for bit, n in B1.items() if b1 & bit}
    names |= {n for bit, n in B2.items() if b2 & bit}
    return {"buttons": frozenset(names), "sticks": (raw[1 + o], raw[2 + o], raw[3 + o], raw[4 + o]),
            "l2": raw[5 + o], "r2": raw[6 + o]}


def pack_usb_report(buttons: set[str] | frozenset[str] = frozenset(), sticks=(128, 128, 128, 128),
                    l2: int = 0, r2: int = 0, counter: int = 0) -> bytes:
    """Inverse of parse_report for USB 0x01 (fixtures/tests)."""
    r = bytearray(64)
    r[0] = 0x01
    r[1:5] = bytes(sticks)
    r[5], r[6], r[7] = l2, r2, counter & 0xFF
    dirs = tuple(sorted(b for b in buttons if b in ("UP", "DOWN", "LEFT", "RIGHT")))
    hat = 8
    if dirs:
        hat = next(k for k, v in HAT.items() if tuple(sorted(v)) == dirs)
    r[8] = hat
    for b in buttons:
        if b in _BIT_FOR:
            i, bit = _BIT_FOR[b]
            r[8 + i] |= bit
    return bytes(r)


class HidDecoder:
    """Stateful: report -> events. Every button edge; sticks at most ``analog_hz`` and only on change."""

    def __init__(self, analog_hz: float | None = None, stick_deadzone: int = 8) -> None:
        hz = default_analog_hz() if analog_hz is None else analog_hz
        self.period_ms = 1000.0 / hz if hz > 0 else 0.0
        self.dz = stick_deadzone
        self.prev: frozenset[str] = frozenset()
        self.last_stick_ms: float | None = None
        self.last_sticks: tuple[int, ...] | None = None
        self.reports_in = 0
        self.events_out = 0

    def _norm(self, s: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(128 if abs(v - 128) <= self.dz else v for v in s)

    def feed(self, raw: bytes, t_ms: int) -> list[dict[str, Any]]:
        self.reports_in += 1
        p = parse_report(raw)
        if p is None:
            return []
        out: list[dict[str, Any]] = []
        cur = p["buttons"]
        for b in sorted(cur - self.prev):
            out.append({"t_ms": t_ms, "button": b, "down": True})
        for b in sorted(self.prev - cur):
            out.append({"t_ms": t_ms, "button": b, "down": False})
        self.prev = cur
        s = self._norm(p["sticks"])
        due = self.last_stick_ms is None or t_ms - self.last_stick_ms >= self.period_ms
        if s != self.last_sticks and due:
            out.append({"t_ms": t_ms, "sticks": list(s), "l2": p["l2"], "r2": p["r2"]})
            self.last_sticks, self.last_stick_ms = s, t_ms
        self.events_out += len(out)
        return out


def find_dualsense() -> dict | None:
    import hid  # hidapi

    devs = [d for d in hid.enumerate(SONY_VID) if d.get("product_id") in DUALSENSE_PIDS]
    # USB interface first (the PC path); the BT link belongs to the PS5.
    devs.sort(key=lambda d: (d.get("bus_type", 1) != 1, d.get("interface_number", 0) not in (0, 3)))
    return devs[0] if devs else None


class DualSenseReader:
    """Background thread reading the USB DualSense/Edge; events land in ``self.events`` (thread-safe deque)."""

    def __init__(self, clock, analog_hz: float | None = None) -> None:
        self.clock = clock
        self.decoder = HidDecoder(analog_hz)
        self.events: deque[dict[str, Any]] = deque()
        self._stop = threading.Event()
        self._t: threading.Thread | None = None
        self.device_name = ""
        self.error: str | None = None

    def start(self) -> None:
        info = find_dualsense()
        if info is None:
            raise RuntimeError("no DualSense / DualSense Edge on USB (VID 054C, PID 0CE6/0DF2)")
        self.device_name = DUALSENSE_PIDS.get(info["product_id"], "DualSense")
        self._t = threading.Thread(target=self._run, args=(info["path"],), name="qor-hid", daemon=True)
        self._t.start()

    def _run(self, path: bytes) -> None:
        import hid

        dev = hid.device()
        try:
            dev.open_path(path)
            dev.set_nonblocking(False)
            while not self._stop.is_set():
                data = dev.read(128, 100)  # ms timeout
                if not data:
                    continue
                raw = bytes(data)
                t_ms = self.clock.now_ms()
                for e in self.decoder.feed(raw, t_ms):
                    self.events.append(e)
        except Exception as e:  # surfaced by the runner
            self.error = f"{type(e).__name__}: {e}"
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def drain(self) -> list[dict[str, Any]]:
        out = []
        while self.events:
            out.append(self.events.popleft())
        return out

    def stop(self) -> None:
        self._stop.set()
        if self._t:
            self._t.join(timeout=1.0)
