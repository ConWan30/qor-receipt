"""End-of-game detection from AGREED scorebug reads (+ optional visibility observations).

Input reads are already multi-frame-agreed (see vendored Qoresence ``AgreementWindow``;
``agreed_reads_from_frames`` adapts it). A timeline is a time-sorted list mixing:

* score reads ``{"t_ms","home","away","quarter","clock_s"[,"clock_red"]}``
* observation spans ``{"t_ms","until_ms","obs": "scorebug_absent" | "final_screen"[, "n"]}``: the agent saw
  no scorebug strip (and no pause menu) / a final-score screen continuously over [t_ms, until_ms]
  on ``n`` sampled frames.

A game is final on exactly one ``final_basis`` (checked in this order):

``clock_zero``
    The last score reads show quarter >= 4, clock 0:00, and the same score on ``need`` consecutive
    reads spanning ``min_span_ms``. If any of those 0:00 reads came from Madden's red final-seconds
    clock (blurrier at 360p), ``need_red`` reads are required instead.
``final_screen``
    Q4 low clock (below), then a final-screen span of >= ``screen_need`` frames over >= ``screen_span_ms``.
``scorebug_gone``
    Q4 low clock (below), then the scorebug strip is continuously absent (pause menu breaks the span)
    for >= ``gone_ms``, starting within ``gone_lead_ms`` of the last score read. Long enough to outlast
    a touchdown celebration / replay (a PAT would bring the strip back with a new score read).

"Q4 low clock": the last score read is quarter >= 4, its score was held on >= ``need`` reads, and the
most recent read with a known clock shows <= ``low_clock_s`` seconds and is the latest-clocked read of
that same score/quarter. Anything later than the evidence (a new score read) cancels a fallback.
All bases also require a non-tied score (ties go to OT). Ambiguous evidence -> not final (fail closed).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

BASES = ("clock_zero", "scorebug_gone", "final_screen")


@dataclass
class Final:
    final: bool
    reason: str
    home: int | None = None
    away: int | None = None
    quarter: int | None = None
    t_ms: int | None = None
    reads_used: int = 0
    basis: str | None = None


def is_obs(r: dict[str, Any]) -> bool:
    return "obs" in r


def score_reads(reads: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in reads if not is_obs(r)]


def _clock_zero(sr, need, need_red, min_span_ms):
    last = sr[-1]
    run = [r for r in sr if r["quarter"] == last["quarter"] and r.get("clock_s") == 0
           and (r["home"], r["away"]) == (last["home"], last["away"])]
    want = need_red if any(r.get("clock_red") for r in run) else need
    if len(run) < want or run[-1]["t_ms"] - run[0]["t_ms"] < min_span_ms:
        return None, "not_stable"
    return run, "ok"


def _q4_low(sr, need, low_clock_s):
    last = sr[-1]
    tail = []  # the same-score run at the end of the timeline
    for r in reversed(sr):
        if (r["home"], r["away"], r["quarter"]) != (last["home"], last["away"], last["quarter"]):
            break
        tail.append(r)
    if len(tail) < need:
        return False, "not_stable"
    clocked = [r for r in sr if r.get("clock_s") is not None]
    if not clocked or clocked[-1]["t_ms"] < tail[-1]["t_ms"]:
        return False, "no_low_clock"
    if clocked[-1]["clock_s"] > low_clock_s:
        return False, "clock_not_low"
    return True, "ok"


def detect_final(reads: list[dict[str, Any]], need: int = 3, min_span_ms: int = 3_000, *, need_red: int = 5,
                 low_clock_s: int = 5, gone_ms: int = 30_000, gone_lead_ms: int = 15_000,
                 screen_need: int = 3, screen_span_ms: int = 3_000) -> Final:
    """reads: timeline sorted by t_ms (score reads, optionally mixed with obs spans)."""
    sr = score_reads(reads)
    if not sr:
        return Final(False, "no_reads")
    last = sr[-1]
    if last["quarter"] < 4:
        return Final(False, "not_fourth_quarter")

    def done(basis, n):
        if last["home"] == last["away"]:
            return Final(False, "tied_overtime_expected")
        return Final(True, "final", last["home"], last["away"], last["quarter"], last["t_ms"], n, basis)

    reason = "clock_running"
    if last.get("clock_s") == 0:
        run, why = _clock_zero(sr, need, need_red, min_span_ms)
        if run is not None:
            return done("clock_zero", len(run))
        reason = why
    low, why = _q4_low(sr, need, low_clock_s)
    if not low:
        return Final(False, reason)
    spans = [o for o in reads if is_obs(o) and o["t_ms"] > last["t_ms"]]
    for o in spans:
        if (o["obs"] == "final_screen" and o.get("n", 0) >= screen_need
                and o["until_ms"] - o["t_ms"] >= screen_span_ms):
            return done("final_screen", len(sr))
    for o in spans:
        if (o["obs"] == "scorebug_absent" and o["t_ms"] - last["t_ms"] <= gone_lead_ms
                and o["until_ms"] - o["t_ms"] >= gone_ms):
            return done("scorebug_gone", len(sr))
    return Final(False, reason if reason == "not_stable" else "awaiting_final_evidence")


def timelines_agree(a: list[dict[str, Any]], b: list[dict[str, Any]], tol_ms: int = 5_000) -> list[str]:
    """Dual-attested mode: every (score, quarter) state change seen by one agent must be seen by
    the other within tol_ms, in the same order. Returns mismatches (empty = agree). Obs spans are ignored."""
    def changes(rs):
        out, prev = [], None
        for r in score_reads(rs):
            k = (r["home"], r["away"], r["quarter"])
            if k != prev:
                out.append((k, r["t_ms"]))
                prev = k
        return out
    ca, cb = changes(a), changes(b)
    errs = []
    if [k for k, _ in ca] != [k for k, _ in cb]:
        errs.append("state_sequence_differs")
    else:
        for (k, ta), (_, tb) in zip(ca, cb):
            if abs(ta - tb) > tol_ms:
                errs.append(f"state_{k}_time_differs")
    return errs


def weakest_basis(bases: Iterable[str | None]) -> str | None:
    """Dual mode: the receipt records the weaker of the two agents' bases."""
    order = {"clock_zero": 0, "final_screen": 1, "scorebug_gone": 2}
    bs = [b for b in bases if b]
    return max(bs, key=order.__getitem__) if bs else None


def agreed_reads_from_frames(frames: Iterable[tuple[int, Any, str]], session_id: str = "m") -> list[dict[str, Any]]:
    """frames: (clock_ns, FrameRead, crop_hash). Uses vendored Qoresence AgreementWindow (needs [vision])."""
    from qor_receipt._vendor.qoresence.vision.local_scorebug.agreement import AgreementWindow

    w, out = AgreementWindow(), []
    for ns, fr, ch in frames:
        res = w.offer(fr, clock_ns=ns, crop_hash=ch, session_id=session_id)
        if res.agreed and res.read is not None:
            out.append({"t_ms": ns // 1_000_000, "away": res.read.left_score, "home": res.read.right_score,  # Madden: away team on the LEFT
                        "quarter": res.read.quarter, "clock_s": res.read.clock_seconds,
                        "clock_red": bool(getattr(res.read, "clock_red", False))})
    return out
