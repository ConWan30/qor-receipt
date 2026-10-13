"""Capture-card frames: OpenCV DirectShow by device name, resized to 640x360, with a frame-size reset guard.

Mirrors Qoresence ``lobes/streamer.py``: resolve the DirectShow index from the device *name*
(pygrabber), open with CAP_DSHOW and a 1-frame buffer, and when the native frame size changes
(re-plug, HDCP/resolution renegotiation) flag ``reset`` so motion/pause baselines are not diffed
across sizes.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

W, H = 640, 360
FRAME_RE = re.compile(r"^(\d+)\.(png|jpg|jpeg|bmp)$", re.I)


@dataclass
class Frame:
    t_ms: int
    image: Any  # np.ndarray HxWx3 BGR, 640x360
    sha256: str
    reset: bool = False


def frame_hash(img: Any) -> str:
    h = hashlib.sha256()
    h.update(f"{img.shape[1]}x{img.shape[0]}x{img.shape[2] if img.ndim == 3 else 1}:".encode())
    h.update(img.tobytes())
    return h.hexdigest()


class SizeGuard:
    """Resize to W x H; report a reset whenever the native size changes."""

    def __init__(self) -> None:
        self.native: tuple[int, int] | None = None
        self.resets = 0

    def apply(self, img: Any) -> tuple[Any, bool]:
        import cv2

        size = (int(img.shape[1]), int(img.shape[0]))
        reset = self.native is not None and size != self.native
        if reset:
            self.resets += 1
        self.native = size
        if size != (W, H):
            img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA if size[0] > W else cv2.INTER_LINEAR)
        return img, reset


def list_dshow_devices() -> list[str]:
    try:
        from pygrabber.dshow_graph import FilterGraph
    except ImportError:
        return []
    try:
        return list(FilterGraph().get_input_devices())
    except Exception:
        return []


def resolve_device(name: str, names: list[str] | None = None) -> int | None:
    """Exact (case-insensitive) name match first, then substring. Never an OBS virtual camera."""
    names = list_dshow_devices() if names is None else names
    low = name.strip().lower()
    cands = [(i, n) for i, n in enumerate(names) if "obs" not in n.lower()]
    for i, n in cands:
        if n.strip().lower() == low:
            return i
    for i, n in cands:
        if low in n.lower():
            return i
    return None


class DShowCapture:
    """Live capture card. ``read()`` -> Frame or None. Re-opens on repeated read failures."""

    def __init__(self, device: str, clock, fps: int = 30) -> None:
        self.device, self.clock, self.fps = device, clock, fps
        self.cap = None
        self.guard = SizeGuard()
        self.fails = 0

    def open(self) -> None:
        import cv2

        if self.device.isdigit():
            idx = int(self.device)
        else:
            idx = resolve_device(self.device)
            if idx is None:
                raise RuntimeError(f"capture device {self.device!r} not found; DirectShow devices: {list_dshow_devices()}")
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
        if not cap.isOpened():
            raise RuntimeError(f"could not open capture device {self.device!r} (index {idx})")
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        self.cap = cap

    def read(self) -> Frame | None:
        if self.cap is None:
            self.open()
        ok, img = self.cap.read()
        t_ms = self.clock.now_ms()
        if not ok or img is None:
            self.fails += 1
            if self.fails >= 30:  # ~1 s of failures: drop and re-open (next read)
                self.close()
                self.fails = 0
                time.sleep(0.5)
            return None
        self.fails = 0
        img, reset = self.guard.apply(img)
        return Frame(t_ms, img, frame_hash(img), reset)

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


def replay_frames(frames_dir: str | Path) -> Iterator[Frame]:
    """Frames named ``<t_ms>.png|jpg`` (epoch ms), in time order, through the same size guard."""
    import cv2

    guard = SizeGuard()
    files = sorted((int(m.group(1)), p) for p in Path(frames_dir).iterdir() if (m := FRAME_RE.match(p.name)))
    for t_ms, p in files:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            continue
        img, reset = guard.apply(img)
        yield Frame(t_ms, img, frame_hash(img), reset)
