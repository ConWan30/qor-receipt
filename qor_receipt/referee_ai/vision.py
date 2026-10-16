"""Seeing layer: frame -> Observation. Pluggable via ``get_vision(name)``."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

READER_SIZE = (640, 360)  # QorTime's scorebug profile is calibrated on 640x360


@dataclass
class Observation:
    """What one source saw in one frame. Confidences are permille ints (receipt JSON has no floats)."""

    source: str
    ok: bool
    reason: str = "ok"
    frame_type: str | None = None      # live | menu | final | black | cutscene
    away: int | None = None
    home: int | None = None
    quarter: int | None = None         # 1-4, 5 = OT
    clock_s: int | None = None
    conf: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in ("source", "ok", "reason", "frame_type", "away", "home", "quarter", "clock_s")}
        d["conf_permille"] = dict(self.conf)
        return {k: v for k, v in d.items() if v is not None}


class Vision(Protocol):
    name: str

    def observe(self, img) -> Observation: ...


class ScorebugVision:
    """Default: QorTime's vendored Qoresence ScorebugReader (Madden: AWAY left, HOME right)."""

    name = "scorebug"

    def __init__(self) -> None:
        from qor_receipt._vendor.qoresence.vision.local_scorebug.reader import ScorebugReader

        self._r = ScorebugReader()

    def observe(self, img) -> Observation:
        import cv2

        if img is None:
            return Observation(self.name, False, "frame_invalid")
        if (img.shape[1], img.shape[0]) != READER_SIZE:
            img = cv2.resize(img, READER_SIZE, interpolation=cv2.INTER_AREA)
        r = self._r.read(img)
        if not r.ok:
            return Observation(self.name, False, r.reason)
        conf = {k: int(round(v * 1000)) for k, v in (("score", r.score_conf), ("clock", r.clock_conf),
                                                    ("quarter", r.quarter_conf)) if v is not None}
        return Observation(self.name, True, "ok", frame_type="live", away=r.left_score, home=r.right_score,
                           quarter=r.quarter, clock_s=r.clock_seconds, conf=conf)


class MetaMuseVision:
    """STUB for Meta Muse Spark (hackathon key expected 2026-10-26). Makes no network calls.

    Interface to implement when the key arrives (keep it; the rest of the referee depends only on it):

        observe(img: np.ndarray BGR) -> Observation(source="meta", ok, reason, frame_type, away, home,
                                                    quarter, clock_s, conf={field: permille})

    * Key from env ``META_MUSE_API_KEY`` (never logged). Model id from ``META_MUSE_MODEL``.
    * ``ok=False`` with a reason on any error/timeout (fail closed; the referee then abstains).
    * Return only what is visible; no inference of hidden scores. Confidences must be calibrated or
      omitted; uncalibrated values must not be passed through as >= 900.
    """

    name = "meta"

    def __init__(self) -> None:
        self.key_present = bool(os.environ.get("META_MUSE_API_KEY"))

    def observe(self, img) -> Observation:
        return Observation(self.name, False, "meta_adapter_not_implemented" if self.key_present else "meta_key_missing")


def get_vision(name: str) -> Vision:
    if name == "scorebug":
        return ScorebugVision()
    if name == "meta":
        return MetaMuseVision()
    raise ValueError(f"unknown vision adapter {name!r} (scorebug | meta)")
