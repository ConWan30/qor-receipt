"""Score the local scorebug reader against hand labels.

    python eval/score_reader.py [--labels eval/labels/frames-2026-10-13.jsonl] [--root /workspace] [--json]

Frames are not in the repo; each label row names its file (relative to --root) and sha256, which is
checked before scoring. Per field, each frame is one of:
  correct  - reader value == label
  abstain_ok - label says the field is not shown and the reader returned nothing
  refused  - label has a value, reader returned nothing (fail-closed; safe but lossy)
  wrong    - reader returned a value that disagrees with the label (or a value where none is shown)
Fields labelled "unsure" are skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

NOT_VISIBLE = {"layout_unknown", "frame_invalid", "frame_too_small", "frame_aspect"}
HERE = Path(__file__).resolve().parent


def predict(reader, img) -> dict:
    fr = reader.read(img)
    ok = bool(fr.ok)
    creason = (fr.detail or {}).get("clock") if ok else None
    red = getattr(fr, "clock_red", None) if ok else None
    if red is None and ok:
        red = True if creason == "clock_red" else (False if fr.clock_text else None)
    return {
        # "visible" = the strip's logo anchor was found (a covered score panel still counts as visible)
        "scorebug_visible": ok or fr.reason not in NOT_VISIBLE,
        "score": (fr.left_score, fr.right_score) if ok else None,
        "quarter": fr.quarter if ok else None,
        "clock": fr.clock_text if ok else None,
        "clock_red": red,
        "reason": fr.reason if not ok else (fr.detail or {}).get("clock", "ok"),
    }


def truth(lab: dict) -> dict:
    sc = None if lab["away"] is None else (lab["away"], lab["home"])  # left = away (Madden convention)
    return {"scorebug_visible": lab["scorebug_visible"], "score": sc, "quarter": lab["quarter"],
            "clock": lab["clock"], "clock_red": lab["clock_red"]}


def grade(t, p) -> str | None:
    if t == "unsure":
        return None
    if p is None:
        return "abstain_ok" if t is None else "refused"
    return "correct" if p == t else "wrong"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(HERE / "labels" / "frames-2026-10-13.jsonl"))
    ap.add_argument("--root", default="/workspace")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-wrong", action="store_true")
    a = ap.parse_args(argv)
    import cv2

    from qor_receipt._vendor.qoresence.vision.local_scorebug.reader import ScorebugReader

    reader = ScorebugReader()
    fields = ["scorebug_visible", "score", "quarter", "clock", "clock_red"]
    tally = {f: Counter() for f in fields}
    wrong = []
    missing = 0
    for line in Path(a.labels).read_text().splitlines():
        lab = json.loads(line)
        path = Path(a.root) / lab["file"]
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != lab["sha256"]:
            missing += 1
            continue
        p, t = predict(reader, cv2.imread(str(path))), truth(lab)
        for f in fields:
            if f == "scorebug_visible":
                g = None if t[f] == "unsure" else ("correct" if p[f] == t[f] else "wrong")
            else:
                g = grade(t[f], p[f])
            if g:
                tally[f][g] += 1
                if g == "wrong":
                    wrong.append((lab["file"], f, t[f], p[f], p["reason"]))
    out = {"missing_frames": missing, **{f: dict(tally[f]) for f in fields}}
    if a.json:
        print(json.dumps(out, indent=1))
    else:
        print(f"missing/changed frames: {missing}")
        for f in fields:
            c = tally[f]
            n = sum(c.values())
            print(f"{f:17s} n={n:3d}  correct={c['correct']:3d}  abstain_ok={c['abstain_ok']:3d}  refused={c['refused']:3d}  wrong={c['wrong']:3d}")
    if a.show_wrong:
        for w in wrong:
            print("WRONG", *w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
