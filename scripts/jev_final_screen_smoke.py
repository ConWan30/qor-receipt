#!/usr/bin/env python3
"""Live smoke for the Jev 1.14 final-screen shadow detector on real eval frames.

Needs TYPESAFE_API_KEY (never printed) and the unlabelled-in-git eval images (see eval/README.md).
The 2026-10-13 label set has NO final screens, so this measures false positives on the hardest
negatives (halftime stats, broadcast scoreboard, touchdown graphic, pause menu, plain absent-strip
gameplay, normal gameplay). Prints one JSON line per frame and a summary.

  python scripts/jev_final_screen_smoke.py --root /workspace [--per-kind 2] [--file qrs/x.jpg ...]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qor_receipt.agent.final_screen_jev import KEY_ENV, JevFinalScreenDetector  # noqa: E402

LABELS = Path(__file__).resolve().parents[1] / "eval/labels/frames-2026-10-13.jsonl"


def kind(r: dict) -> str:
    n = (r.get("notes") or "").lower()
    if r.get("final_screen") is True:
        return "final_screen"
    if r.get("pause_menu"):
        return "pause_menu"
    for k in ("halftime stats", "halftime report", "broadcast", "touchdown"):
        if k in n:
            return k.replace(" ", "_")
    return "absent_strip" if r.get("scorebug_visible") is False else "gameplay"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/workspace")
    ap.add_argument("--per-kind", type=int, default=1)
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--timeout", type=float, default=30.0)
    a = ap.parse_args()
    if not os.environ.get(KEY_ENV):
        print(f"SKIP: {KEY_ENV} not set")
        return 2
    rows = [json.loads(line) for line in LABELS.read_text().splitlines() if line.strip()]
    if a.file:
        picks = [r for r in rows if r["file"] in a.file]
    else:
        seen: dict[str, int] = {}
        picks = []
        for r in rows:
            k = kind(r)
            if seen.get(k, 0) < a.per_kind and (Path(a.root) / r["file"]).exists():
                seen[k] = seen.get(k, 0) + 1
                picks.append(r)
    det = JevFinalScreenDetector(timeout_s=a.timeout)
    out = []
    for r in picks:
        img = cv2.imread(str(Path(a.root) / r["file"]))
        if img is None:
            print(json.dumps({"file": r["file"], "error": "missing"}))
            continue
        rec = det.classify(img)
        res = {"file": r["file"], "kind": kind(r), "label_final": r.get("final_screen"), "answer": rec["answer"],
               "p_yes": rec["p_yes"], "latency_ms": rec["latency_ms"], "model": rec.get("model_answered"),
               **({"error": rec["error"]} if "error" in rec else {})}
        out.append(res)
        print(json.dumps(res))
    ok = [o for o in out if o["p_yes"] is not None]
    fp = [o for o in ok if o["answer"] == "yes" and not o["label_final"]]
    lat = sorted(o["latency_ms"] for o in ok)
    print(json.dumps({"frames": len(out), "answered": len(ok), "false_yes": len(fp),
                      "latency_ms_median": lat[len(lat) // 2] if lat else None}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
