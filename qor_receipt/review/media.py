"""Decode the video segments the referee actually received, and run its own readers over them.

Segments are fMP4 fragments (QorCap: segment 0 of each file = ftyp+moov init, then moof+mdat fragments). A
contiguous run of fragments prefixed with its file's init segment is a decodable MP4. Frame times are put on
QorCap's monotonic clock (shared with inputs.jsonl) from the manifest's per-segment ``mono_ms`` stamps.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from qor_receipt.review.bundle import Bundle, segment_ranges


def ffmpeg_bin() -> str | None:
    return os.getenv("QORTIME_FFMPEG") or shutil.which("ffmpeg")


def runs(seqs: Iterable[int], b: Bundle) -> list[list[int]]:
    """Group media segment seqs into contiguous runs within the same file (init segments excluded)."""
    by_seq = {int(m["seq"]): m for m in b.segments}
    out: list[list[int]] = []
    for s in sorted(set(seqs)):
        m = by_seq.get(s)
        if m is None or int(m.get("offset", 0)) == 0:
            continue
        if out and out[-1][-1] == s - 1 and by_seq[s - 1]["file"] == m["file"]:
            out[-1].append(s)
        else:
            out.append([s])
    return out


def init_seq_for(b: Bundle, seq: int) -> int | None:
    by_seq = {int(m["seq"]): m for m in b.segments}
    f = by_seq[seq]["file"]
    cands = [int(m["seq"]) for m in b.segments if m["file"] == f and int(m.get("offset", 0)) == 0 and int(m["seq"]) <= seq]
    return max(cands) if cands else None


def decode_run(b: Bundle, run: list[int], read_seg: Callable[[int], bytes | None], *, hz: float,
               size: tuple[int, int] = (1280, 720), gray: bool = False) -> Iterator[tuple[int, Any]]:
    """Yield (mono_ms, frame) for one contiguous run. Frame time = run start (end of the previous segment) + n/hz."""
    import numpy as np

    ff = ffmpeg_bin()
    if not ff:
        raise RuntimeError("ffmpeg not available on the referee")
    init = init_seq_for(b, run[0])
    if init is None:
        return
    parts = [read_seg(init)] + [read_seg(s) for s in run]
    if any(p is None for p in parts):
        return
    start_mono = segment_ranges(b)[run[0]][0]
    w, h = size
    ch = 1 if gray else 3
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
        for p in parts:
            tf.write(p)
        path = tf.name
    try:
        p = subprocess.Popen([ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", path, "-an",
                              "-vf", f"fps={hz},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray" if gray else "bgr24",
                              "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        n, fb = 0, w * h * ch
        try:
            while True:
                buf = p.stdout.read(fb)
                if len(buf) < fb:
                    break
                img = np.frombuffer(buf, np.uint8).reshape((h, w) if gray else (h, w, 3))
                yield start_mono + int(n * 1000 / hz), img
                n += 1
        finally:
            if p.poll() is None:
                p.kill()  # consumer stopped early (e.g. end menu found)
            p.stdout.close()
            p.wait()
        if n == 0:
            raise RuntimeError(f"ffmpeg decoded no frames from segments {run[0]}..{run[-1]}")
    finally:
        os.unlink(path)


# ---------------------------------------------------------------- final re-read (QorCap's own readers)
def sidecar_profiles(sidecar_dir: str | None = None) -> list[str]:
    """Scorebug profiles the referee can read: QorTime's vendored standard pack + packs shipped in the QorCap sidecar."""
    out = ["madden27_standard"]
    d = sidecar_dir or os.getenv("QORCAP_SIDECAR")
    if d and (Path(d) / "qorcap_mnp.py").exists() and (Path(d) / "packs" / "madden27_monday_night_v1.npz").exists():
        out.append("madden27_monday_night")
    if d and (Path(d) / "qorcap_snp.py").exists() and (Path(d) / "packs" / "madden27_sunday_night_v1.npz").exists():
        out.append("madden27_sunday_night")
    if d and (Path(d) / "qorcap_tnp.py").exists() and (Path(d) / "packs" / "madden27_thursday_night_v1.npz").exists():
        out.append("madden27_thursday_night")
    return out


def sidecar_core_factory(sidecar_dir: str | None = None):
    """ReaderCore exactly as QorCap builds it (scorebug + end-of-game menu readers), imported from a pinned QorCap
    sidecar checkout (``$QORCAP_SIDECAR``). Returns None when unavailable (the review then fails closed)."""
    d = sidecar_dir or os.getenv("QORCAP_SIDECAR")
    if not d or not (Path(d) / "qorcap_reader.py").exists():
        return None
    if d not in sys.path:
        sys.path.insert(0, d)
    from qorcap_endmenu import EndMenuReader  # type: ignore
    from qorcap_mnp import AutoProfileReader  # type: ignore
    from qorcap_reader import ReaderCore, madden_final_screen, prep  # type: ignore

    from qor_receipt._vendor.qoresence.vision.local_scorebug.agreement import AgreementWindow
    from qor_receipt._vendor.qoresence.vision.local_scorebug.reader import ScorebugReader
    from qor_receipt.agent.pause_detect import PauseDetector
    from qor_receipt.final_detect import detect_final

    def factory(post):
        return ReaderCore(post, AutoProfileReader(ScorebugReader(), prep=prep), AgreementWindow(), PauseDetector(),
                          detect_final, final_screen_detector=madden_final_screen, end_menu_reader=EndMenuReader())
    return factory


def sidecar_corroboration(bundle_dir: str | None, sidecar_dir: str | None = None) -> list[dict]:
    d = sidecar_dir or os.getenv("QORCAP_SIDECAR")
    if not bundle_dir or not d or not (Path(d) / "qorcap_reader.py").exists():
        return []
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        from qorcap_reader import load_delta_corroboration_from_bundle  # type: ignore
        return load_delta_corroboration_from_bundle(bundle_dir)
    except Exception:
        return []


def reread_final(frames: Iterable[tuple[int, Any]], core_factory, *, corroboration: list[dict] | None = None) -> dict[str, Any]:
    """Feed the referee's decoded tail frames (t_ms = wall clock) through a fresh ReaderCore. No laptop timeline seed."""
    events: list[dict] = []
    core = core_factory(lambda e: events.append(e) or {})
    core.delta_corroboration = list(corroboration or [])
    for t, img in frames:
        core.on_frame(t, img)
        if getattr(core, "end_menu", None) is not None:
            break
    reads = [e for e in events if e.get("type") == "score"]
    return {"final": core.final, "last_read": reads[-1] if reads else None, "reads": len(reads),
            "end_menu": getattr(core, "end_menu", None), "end_menu_rejected": getattr(core, "end_menu_rejected", None),
            "stats": dict(core.stats)}


# ---------------------------------------------------------------- pause onset (challenges) + motion
def pause_onsets(frames: Iterable[tuple[int, Any]], detector=None) -> list[int]:
    """Mono times where the pause menu first appears in the decoded frames."""
    from qor_receipt.agent.pause_detect import PauseDetector

    det = detector or PauseDetector()
    for t, img in frames:
        det.update(t, img)
    return list(det.onsets)


def motion_series(frames: Iterable[tuple[int, Any]], bin_ms: int = 1_000) -> dict[int, int]:
    """Per-bin mean absolute frame difference (gray thumbnails) keyed by bin index on the mono clock."""
    import numpy as np

    prev, acc = None, {}
    for t, img in frames:
        g = img if img.ndim == 2 else img.mean(axis=2)
        g = g.astype(np.int16)
        if prev is not None and prev.shape == g.shape:
            k = t // bin_ms
            s, n = acc.get(k, (0, 0))
            acc[k] = (s + int(np.abs(g - prev).mean() * 100), n + 1)
        prev = g
    return {k: s // max(n, 1) for k, (s, n) in acc.items()}


def input_energy_series(b: Bundle, bin_ms: int = 1_000) -> dict[int, int]:
    """Per-bin count of logged controller reports that changed buttons/triggers/sticks (mono clock)."""
    acc: dict[int, int] = {}
    for r in b.inputs:
        if r.get("type") == "in" and isinstance(r.get("t_us"), int):
            k = (r["t_us"] // 1000) // bin_ms
            acc[k] = acc.get(k, 0) + 1
    return acc
