"""End-of-game detection from AGREED scorebug reads.

Input reads are already multi-frame-agreed (see vendored Qoresence
``AgreementWindow``; ``agreed_reads_from_frames`` adapts it). A game is final
when the last agreed reads show quarter >= 4 (5 = OT), clock 0:00, the same
score on ``need`` consecutive agreed reads spanning ``min_span_ms``, and the
score is not tied (Madden regular play has no ties in money matches).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass
class Final:
    final: bool
    reason: str
    home: int | None = None
    away: int | None = None
    quarter: int | None = None
    t_ms: int | None = None
    reads_used: int = 0


def detect_final(reads: list[dict[str, Any]], need: int = 3, min_span_ms: int = 3_000) -> Final:
    """reads: [{"t_ms","home","away","quarter","clock_s"}] sorted by t_ms."""
    if not reads:
        return Final(False, "no_reads")
    last = reads[-1]
    if last["quarter"] < 4:
        return Final(False, "not_fourth_quarter")
    if last["clock_s"] != 0:
        return Final(False, "clock_running")
    run = [r for r in reads if r["quarter"] == last["quarter"] and r["clock_s"] == 0
           and (r["home"], r["away"]) == (last["home"], last["away"])]
    if len(run) < need or run[-1]["t_ms"] - run[0]["t_ms"] < min_span_ms:
        return Final(False, "not_stable")
    if last["home"] == last["away"]:
        return Final(False, "tied_overtime_expected")
    return Final(True, "final", last["home"], last["away"], last["quarter"], last["t_ms"], len(run))


def timelines_agree(a: list[dict[str, Any]], b: list[dict[str, Any]], tol_ms: int = 5_000) -> list[str]:
    """Dual-attested mode: every (score, quarter) state change seen by one agent must be seen by
    the other within tol_ms, in the same order. Returns mismatches (empty = agree)."""
    def changes(rs):
        out, prev = [], None
        for r in rs:
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


def agreed_reads_from_frames(frames: Iterable[tuple[int, Any, str]], session_id: str = "m") -> list[dict[str, Any]]:
    """frames: (clock_ns, FrameRead, crop_hash). Uses vendored Qoresence AgreementWindow (needs [vision])."""
    from qor_receipt._vendor.qoresence.vision.local_scorebug.agreement import AgreementWindow

    w, out = AgreementWindow(), []
    for ns, fr, ch in frames:
        res = w.offer(fr, clock_ns=ns, crop_hash=ch, session_id=session_id)
        if res.agreed and res.read is not None:
            out.append({"t_ms": ns // 1_000_000, "home": res.read.left_score, "away": res.read.right_score,
                        "quarter": res.read.quarter, "clock_s": res.read.clock_seconds})
    return out
