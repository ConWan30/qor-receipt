"""Run the AI referee over the 22-frame Madden set + doctored copies.

    python eval/referee_ai_frames.py FRAMES_DIR [--record tests/fixtures/referee_ai/jev_answers.json] [--out DIR]

Needs TYPESAFE_API_KEY for live Jev; without it, pass --replay JEV_ANSWERS.json to reuse recorded answers.
Prints per-frame accuracy (scorebug reader and Jev) and each scenario's verdict vs the expected verdict.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qor_receipt.keys import SoftwareKey  # noqa: E402
from qor_receipt.referee_ai import verdict as vd  # noqa: E402
from qor_receipt.referee_ai.jev import JevJudge, parse  # noqa: E402
from qor_receipt.referee_ai.referee import Frame, build_verdict, decide, judge_frames  # noqa: E402
from qor_receipt.referee_ai.scenarios import GROUND_TRUTH, SCENARIOS  # noqa: E402
from qor_receipt.referee_ai.vision import Observation, ScorebugVision  # noqa: E402


def load(frames_dir: Path, name: str):
    import cv2

    return cv2.imread(str(frames_dir / name))


class RecordingJev(JevJudge):
    """Live Jev that remembers raw answers per frame name (for offline replay)."""

    def __init__(self, store: dict, replay: bool, **kw):
        super().__init__(**kw)
        self.store, self.replay, self.cur = store, replay, None

    @property
    def available(self) -> bool:
        return self.replay or super().available

    def observe(self, img) -> Observation:
        if self.replay:
            a = self.store.get(self.cur)
            return parse(a["answers"]) if a else Observation("jev", False, "not_recorded")
        orig = self._post

        def post(body, key, timeout):
            st, j = orig(body, key, timeout)
            if st == 200:
                self.store[self.cur] = {"model": j.get("model"), "answers": j["answers"], "usage": j.get("usage")}
            return st, j

        self._post = post
        try:
            return super().observe(img)
        finally:
            self._post = orig


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("frames")
    ap.add_argument("--record")
    ap.add_argument("--replay")
    ap.add_argument("--out")
    a = ap.parse_args()
    fd = Path(a.frames)
    store = json.loads(Path(a.replay).read_text()) if a.replay else {}
    jev = RecordingJev(store, bool(a.replay))
    vis = ScorebugVision()
    cache: dict[str, list[Observation]] = {}
    per = {"vision": {"read": 0, "right": 0}, "jev_type": [0, 0], "jev_score_ok": [0, 0], "jev_full_ok": [0, 0]}
    rows = []
    for name, (ft, aw, ho, q, clk) in GROUND_TRUTH.items():
        img = load(fd, name)
        vo = vis.observe(img)
        jev.cur = name
        jo = jev.observe(img)
        cache[name] = [vo, jo]
        if not name.startswith("TAMPER"):
            if vo.ok:
                per["vision"]["read"] += 1
                per["vision"]["right"] += int((vo.away, vo.home, vo.quarter) == (aw, ho, q))
            if ft:
                per["jev_type"][1] += 1
                per["jev_type"][0] += int(jo.frame_type == ft)
            if aw is not None:
                per["jev_score_ok"][1] += 1
                per["jev_score_ok"][0] += int((jo.away, jo.home) == (aw, ho))
                per["jev_full_ok"][1] += 1
                per["jev_full_ok"][0] += int((jo.away, jo.home, jo.quarter, jo.clock_s) == (aw, ho, q, clk))
        rows.append({"frame": name, "truth": [ft, aw, ho, q, clk], "scorebug": vo.to_json(), "jev": jo.to_json()})
    key = SoftwareKey()
    results = {}
    for sname, (names, claim, expect) in SCENARIOS.items():
        frames = [Frame(id=n, img=None, t_ms=i) for i, n in enumerate(names)]
        for f in frames:
            f.obs = list(cache[f.id])
        fr = judge_frames(frames, vision=None, jev=_Stub(), claim=claim)
        verdict, reasons = decide(integrity_errors=[], frame_result=fr, claim=claim)
        v = vd.sign(build_verdict(verdict=verdict, reasons=reasons, claim=claim, frames=frames, frame_result=fr, jev=None,
                                  vision=vis, subject={"kind": "frame-sequence", "scenario": sname}, integrity={"ok": True, "errors": []},
                                  elapsed_ms=0), key)
        results[sname] = {"expected": expect, "verdict": verdict, "pass": verdict == expect, "reasons": reasons}
        if a.out:
            Path(a.out).mkdir(parents=True, exist_ok=True)
            (Path(a.out) / f"verdict_{sname}.json").write_text(json.dumps(v, indent=2, sort_keys=True))
    summary = {"per_frame": per, "scenarios": results, "jev_calls": jev.calls}
    if a.record:
        Path(a.record).write_text(json.dumps(store, indent=1, sort_keys=True))
    if a.out:
        (Path(a.out) / "frames_eval.json").write_text(json.dumps({"rows": rows, **summary}, indent=2))
    print(json.dumps(summary, indent=2))
    return 0 if all(r["pass"] for r in results.values()) else 1


class _Stub:
    """Observations are pre-filled; a non-None judge with available=False makes judge_frames run Jev consensus
    over them without calling the API again."""

    available = False
    model = None
    calls: list = []


if __name__ == "__main__":
    sys.exit(main())
