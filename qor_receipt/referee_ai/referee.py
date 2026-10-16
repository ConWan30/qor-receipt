"""Orchestration: integrity -> seeing -> Jev judgment -> verdict.

Verdict rules (plain reasons are always listed):
* dispute: any integrity error (hash/chain/signature, illegal score step, score/quarter/clock backwards, final
  != last score), or an *accepted* reading (vision agreement or Jev consensus) contradicting the claim.
* accept: integrity clean AND the claimed final score is confirmed by at least one seeing source's
  multi-frame agreement (Jev: conf >= 0.9 on >= 2 frames; scorebug reader: >= 2 identical ok reads).
* abstain: everything else (not enough confident evidence). Abstaining is a normal outcome.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qor_receipt.referee_ai import integrity as integ
from qor_receipt.referee_ai.jev import ACCEPT_PERMILLE, JevJudge, consensus
from qor_receipt.referee_ai.verdict import ABSTAIN, ACCEPT, DISPUTE, SCHEMA
from qor_receipt.referee_ai.vision import Observation

FINAL_TYPES = {"final"}


@dataclass
class Frame:
    """One evidence frame: BGR image + where it came from (+ sha256 of the encoded frame for the record)."""

    id: str
    img: Any
    t_ms: int | None = None
    sha256: str | None = None
    obs: list[Observation] = field(default_factory=list)


def _state(o: Observation, f: Frame, *, confident_only: bool) -> dict[str, Any] | None:
    """Observation -> progression state. Jev fields count only at conf >= 0.9 (single-frame plausibility)."""
    if not o.ok:
        return None
    if o.source == "jev" and confident_only:
        g = lambda v, k: v if v is not None and o.conf.get(k, 0) >= ACCEPT_PERMILLE else None  # noqa: E731
        s = {"away": g(o.away, "away"), "home": g(o.home, "home"), "quarter": g(o.quarter, "quarter"), "clock_s": g(o.clock_s, "clock")}
        if s["away"] is None or s["home"] is None:
            s["away"] = s["home"] = None
    else:
        s = {"away": o.away, "home": o.home, "quarter": o.quarter, "clock_s": o.clock_s}
    if all(v is None for v in s.values()):
        return None
    return {**s, "t_ms": f.t_ms, "src": f"{f.id}/{o.source}"}


def judge_frames(frames: list[Frame], *, vision=None, jev: JevJudge | None = None,
                 claim: dict[str, Any] | None = None, jev_window: int = 3) -> dict[str, Any]:
    """Seeing + Jev + frame-level integrity over a time-ordered frame list. ``claim``: {away, home} final."""
    for f in frames:
        if vision is not None:
            f.obs.append(vision.observe(f.img))
        if jev is not None and jev.available:
            f.obs.append(jev.observe(f.img))
    errors: list[str] = []
    states: list[dict[str, Any]] = []
    for f in frames:
        for o in f.obs:
            s = _state(o, f, confident_only=True)
            if s:
                states.append(s)
    errors += integ.check_progression(states)
    last = next((s for s in reversed(states) if s.get("away") is not None and s.get("src", "").split("/")[0]
                 not in _final_ids(frames)), None)
    finals = [f for f in frames if _is_final(f)]
    fin_states = [s for s in states if s["src"].split("/")[0] in {f.id for f in finals}]
    if fin_states and last:
        errors += integ.check_final(fin_states[-1], last, what=f"final screen ({fin_states[-1]['src']})")
    # multi-frame consensus on the last window of frames that show a score
    scored = [f for f in frames if any(o.ok and o.away is not None for o in f.obs)]
    window = (finals or scored)[-jev_window:]
    jev_c = consensus([o for f in window for o in f.obs if o.source == "jev"]) if jev is not None else None
    # seeing-adapter agreement: the two most recent frames of the window must both read the same score
    vis_obs = [o for f in window[-2:] for o in f.obs if o.source != "jev" and o.ok and o.away is not None]
    vis_pairs = [(o.away, o.home) for o in vis_obs]
    vis_agree = vis_pairs[-1] if len(vis_pairs) >= 2 and len(set(vis_pairs[-2:])) == 1 else None
    confirmed, reasons = [], []
    if jev_c is not None:
        a = jev_c["accepted"]
        if "away" in a and "home" in a:
            confirmed.append(("jev", (a["away"], a["home"])))
    if vis_agree:
        confirmed.append(("scorebug", vis_agree))
    if claim:
        cpair = (claim.get("away"), claim.get("home"))
        for src, pair in confirmed:
            if pair != cpair:
                errors.append(f"{src} multi-frame reading {pair[0]}-{pair[1]} contradicts claimed final {cpair[0]}-{cpair[1]}")
            else:
                reasons.append(f"{src} confirms final {pair[0]}-{pair[1]} on >= 2 frames")
    return {"errors": errors, "confirmed": confirmed, "reasons": reasons, "jev_consensus": jev_c,
            "states": states, "window": [f.id for f in window]}


def _is_final(f: Frame) -> bool:
    return any(o.source == "jev" and o.ok and o.frame_type in FINAL_TYPES and o.conf.get("frame_type", 0) >= ACCEPT_PERMILLE
               for o in f.obs)


def _final_ids(frames: list[Frame]) -> set[str]:
    return {f.id for f in frames if _is_final(f)}


def decide(*, integrity_errors: list[str], frame_result: dict[str, Any] | None, claim: dict | None) -> tuple[str, list[str]]:
    errs = list(integrity_errors) + list((frame_result or {}).get("errors", []))
    if errs:
        return DISPUTE, errs
    fr = frame_result or {}
    if claim and any(p == (claim.get("away"), claim.get("home")) for _, p in fr.get("confirmed", [])):
        return ACCEPT, ["integrity checks passed"] + fr.get("reasons", [])
    why = ["integrity checks passed", "no seeing source confirmed the final score on >= 2 confident frames"]
    jc = fr.get("jev_consensus")
    if jc:
        why += [f"jev abstained on {k}: {v}" for k, v in jc["abstained"].items() if k in ("away", "home")]
    return ABSTAIN, why


# ---------------------------------------------------------------------------------------------- bundles

def sample_bundle_frames(d: Path, bundle: dict, timeline: list[dict], *, per_point: int = 3, step_ms: int = 700,
                         max_frames: int = 24) -> list[Frame]:
    """Decode evidence frames from the bundle's own video: ``per_point`` frames after each timeline score
    change and before the end. Video time is mapped to wall time from the manifest (last segment wall_ms minus
    video duration), so positions are approximate (+-1 segment)."""
    import cv2

    man = integ.read_jsonl(d / "manifest.jsonl", [], [])
    segs = [m for m in man if m.get("type") == "segment" and m.get("file") == "video.mp4"]
    vp = d / "video.mp4"
    if not segs or not vp.exists():
        return []
    cap = cv2.VideoCapture(str(vp))
    n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS) or 30.0
    dur_ms = int(n / fps * 1000) if n > 0 else 0
    start_wall = segs[-1]["wall_ms"] - dur_ms
    points, last = [], None
    for ev in timeline:
        k = (ev.get("away"), ev.get("home"), ev.get("quarter"))
        if k != last:
            points.append(ev["t_ms"])
            last = k
    fin = (bundle.get("final") or {}).get("t_ms")
    if fin:
        points.append(fin)
    want: list[int] = []
    for p in points:
        want += [p + 300 + i * step_ms for i in range(per_point)]
    want = sorted({w for w in want if start_wall <= w <= start_wall + dur_ms})[-max_frames:] if dur_ms else []
    frames: list[Frame] = []
    for w in want:
        cap.set(cv2.CAP_PROP_POS_MSEC, w - start_wall)
        ok, img = cap.read()
        if ok:
            import hashlib

            _, enc = cv2.imencode(".png", img)
            frames.append(Frame(id=f"video@{w - start_wall}ms", img=img, t_ms=w, sha256=hashlib.sha256(enc.tobytes()).hexdigest()))
    cap.release()
    return frames


def referee_bundle(d: str | Path, *, vision=None, jev: JevJudge | None = None, referee_pub: str | None = None,
                   max_frames: int = 24) -> dict[str, Any]:
    d = Path(d)
    t0 = time.time()
    iv = integ.verify_bundle(d, referee_pub=referee_pub)
    errors = list(iv["errors"])
    bundle = iv.get("bundle") or {}
    tl = integ.read_jsonl(d / "timeline.jsonl", errors, iv["warnings"])
    tl_scores = [t for t in tl if "away" in t and "home" in t]
    cp_states = [dict((c.get("qorcap") or {}).get("score") or {}, t_ms=c.get("t_ms"), src=f"checkpoint {c.get('seq')}")
                 for c in iv.get("checkpoints") or [] if (c.get("qorcap") or {}).get("score")]
    tl_states = [{**{k: t.get(k) for k in ("away", "home", "quarter", "clock_s")}, "t_ms": t.get("t_ms"), "src": "timeline"} for t in tl_scores]
    errors += integ.check_progression(cp_states)
    errors += integ.check_progression(tl_states)
    final = bundle.get("final")
    last = (cp_states or tl_states or [None])[-1]
    errors += integ.check_final(final, last, what="bundle.json final")
    rec = iv.get("receipt") or {}
    claim = None
    if rec.get("result"):
        claim = {"away": rec["result"]["away_score"], "home": rec["result"]["home_score"]}
        errors += integ.check_final(claim, last, what="receipt.json result")
    elif final:
        claim = {"away": final.get("away"), "home": final.get("home")}
    frames = sample_bundle_frames(d, bundle, tl_scores, max_frames=max_frames) if (vision or jev) else []
    fr = judge_frames(frames, vision=vision, jev=jev, claim=claim) if frames else None
    verdict, reasons = decide(integrity_errors=errors, frame_result=fr, claim=claim)
    rv = (iv["info"].get("receipt_verify") or {})
    return build_verdict(
        verdict=verdict, reasons=reasons, claim=claim, frames=frames, frame_result=fr, jev=jev, vision=vision,
        subject={"kind": "qorcap-bundle", "schema": bundle.get("schema"), "match_id": bundle.get("match_id"),
                 "chain_head": iv["info"].get("chain_head"), "checkpoint_head": iv["info"].get("checkpoint_head"),
                 "receipt_sha256": _sha(d / "receipt.json"), "bundle_json_sha256": _sha(d / "bundle.json")},
        integrity={"ok": not errors, "errors": errors, "warnings": iv["warnings"], "segments": iv["info"].get("segments"),
                   "checkpoints": iv["info"].get("checkpoints"), "receipt_status": rv.get("status"),
                   "receipt_partial": rv.get("partial", [])},
        elapsed_ms=int((time.time() - t0) * 1000))


def _sha(p: Path) -> str | None:
    from qor_receipt.referee_ai.verdict import sha256_file

    return sha256_file(p) if p.exists() else None


def build_verdict(*, verdict, reasons, claim, frames, frame_result, jev, vision, subject, integrity, elapsed_ms) -> dict:
    ev = []
    for f in frames:
        ev.append({"id": f.id, "t_ms": f.t_ms, "sha256": f.sha256, "observations": [o.to_json() for o in f.obs]})
    calls = jev.calls if jev is not None else []
    toks = sum(c.get("input_tokens") or 0 for c in calls)
    return {
        "schema": SCHEMA, "verdict": verdict, "reasons": reasons, "claim": claim, "subject": subject,
        "integrity": integrity,
        "seeing": {"adapter": getattr(vision, "name", None)},
        "judgment": {"engine": "typesafe-jev" if jev is not None else None, "model": getattr(jev, "model", None),
                     "available": bool(jev is not None and jev.available), "accept_permille": ACCEPT_PERMILLE,
                     "min_agree_frames": 2, "consensus": (frame_result or {}).get("jev_consensus"),
                     "calls": len(calls), "input_tokens": toks,
                     "latency_ms_median": sorted(c["latency_ms"] for c in calls)[len(calls) // 2] if calls else None},
        "evidence": ev, "created_ms": int(time.time() * 1000), "elapsed_ms": elapsed_ms,
    }


def dumps(v: dict) -> str:
    return json.dumps(v, indent=2, sort_keys=True)
