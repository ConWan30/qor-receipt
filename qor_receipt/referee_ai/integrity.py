"""Integrity layer: plain code, no models.

* ``verify_bundle``: Python port of QorCap ``qorcap verify-bundle`` (src/bundle.rs + src/evidence.rs, qorcap
  v0.2.2): re-hash every video segment against ``manifest.jsonl``, re-link the segment chain
  ``head_n = SHA-256(JCS({prev, seq, sha256}))``, re-check ``checkpoints.jsonl`` (QorTime checkpoint hash +
  ``frames_hash == SHA-256(JCS(qorcap))`` + known chain head), plus the bundle.json heads, clip hashes, the
  draft-receipt hash, and the signed ``receipt.json`` via ``verify_receipt`` pinned to ``referee.pub``.
* ``check_progression``: scores only move by legal football steps, never backwards; quarters never go
  backwards; the clock never runs backwards within a quarter.
* ``check_final``: the final screen / claimed result must equal the last scorebug score.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from qor_receipt.jcs import sha256_hex

GENESIS = "0" * 64
LEGAL_STEPS = {1, 2, 3, 6, 7, 8}   # PAT/1pt safety, 2pt/safety, FG, TD, TD+PAT, TD+2pt
CLOCK_SLACK_S = 1                   # reader jitter tolerance


def link(prev: str, seq: int, sha: str) -> str:
    return sha256_hex({"prev": prev, "seq": seq, "sha256": sha})


def read_jsonl(p: Path, errors: list[str], warnings: list[str]) -> list[dict[str, Any]]:
    """Same salvage rule as QorCap: skip up to 3 corrupt lines (with a warning), fail beyond that."""
    if not p.exists():
        return []
    out, bad = [], 0
    for i, line in enumerate((x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()), 1):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1
            warnings.append(f"{p.name}:{i}: corrupt JSONL line skipped")
    if bad > 3:
        errors.append(f"{p.name}: {bad} corrupt lines")
    return out


def _file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_bundle(d: str | Path, *, referee_pub: str | None = None) -> dict[str, Any]:
    d = Path(d)
    errors: list[str] = []
    warnings: list[str] = []
    info: dict[str, Any] = {}
    bj_path = d / "bundle.json"
    if not bj_path.exists():
        return {"ok": False, "errors": ["bundle.json missing (not a QorCap bundle)"], "warnings": [], "info": {}}
    bundle = json.loads(bj_path.read_text(encoding="utf-8"))
    if bundle.get("schema") != "qorcap-bundle/v0":
        errors.append(f"unexpected bundle schema {bundle.get('schema')!r}")
    # segments
    man = read_jsonl(d / "manifest.jsonl", errors, warnings)
    head, nseg, expect_off, heads = GENESIS, 0, {}, {GENESIS}
    for m in (x for x in man if x.get("type") == "segment"):
        seq, f, off, ln = m.get("seq"), m.get("file", ""), int(m.get("offset", 0)), int(m.get("bytes", 0))
        tag = f"segment {seq} ({f}@{off})"
        if seq != nseg:
            errors.append(f"{tag}: seq gap (expected {nseg})")
        if off != expect_off.get(f, 0):
            errors.append(f"{tag}: offset gap (expected {expect_off.get(f, 0)})")
        expect_off[f] = off + ln
        try:
            with open(d / f, "rb") as fh:
                fh.seek(off)
                buf = fh.read(ln)
            if len(buf) != ln:
                errors.append(f"{tag}: video truncated")
            elif hashlib.sha256(buf).hexdigest() != m.get("sha256"):
                errors.append(f"{tag}: SHA-256 mismatch (video bytes altered)")
        except OSError as e:
            errors.append(f"{tag}: cannot read video bytes: {e.strerror}")
        if m.get("prev") != head:
            errors.append(f"{tag}: prev link broken")
        head = link(head, seq if isinstance(seq, int) else -1, m.get("sha256", ""))
        if m.get("chain_head") != head:
            errors.append(f"{tag}: chain head mismatch")
        heads.add(head)
        nseg += 1
    for f, end in expect_off.items():
        real = (d / f).stat().st_size if (d / f).exists() else 0
        if real != end:
            errors.append(f"{f}: {real} bytes on disk but manifest covers {end} (unhashed tail or truncation)")
    # checkpoints
    cps = read_jsonl(d / "checkpoints.jsonl", errors, warnings)
    prev = GENESIS
    for i, c in enumerate(cps):
        body = {k: c.get(k) for k in ("seq", "t_ms", "frames_hash", "input_hash", "prev")}
        if c.get("seq") != i:
            errors.append(f"checkpoint {i}: seq gap")
        if c.get("prev") != prev:
            errors.append(f"checkpoint {i}: prev link broken")
        if sha256_hex(body) != c.get("hash"):
            errors.append(f"checkpoint {i}: hash mismatch")
        if "qorcap" in c and c.get("frames_hash") != sha256_hex(c["qorcap"]):
            errors.append(f"checkpoint {i}: qorcap evidence does not match frames_hash (score/clock state edited)")
        ch = (c.get("qorcap") or {}).get("chain_head")
        if ch is not None and ch not in heads:
            errors.append(f"checkpoint {i}: refers to unknown video chain head")
        prev = c.get("hash", "")
    # bundle.json claims
    if bundle.get("chain_head") not in (None, head):
        errors.append("bundle.json chain_head != recomputed segment chain head")
    if cps and bundle.get("checkpoint_head") not in (None, cps[-1].get("hash")):
        errors.append("bundle.json checkpoint_head != last checkpoint hash")
    for c in bundle.get("clips") or []:
        p = d / "clips" / Path(str(c.get("file", "")).replace("\\", "/")).name
        if not p.exists():
            warnings.append(f"clip {c.get('name')}: file missing")
        elif c.get("sha256") and _file_sha(p) != c["sha256"]:
            errors.append(f"clip {c.get('name')}: SHA-256 mismatch")
    draft = d / "receipt.draft.json"
    if draft.exists() and bundle.get("receipt_draft_sha256"):
        # QorCap engine.rs: receipt_draft_sha256 = SHA-256(JCS(draft receipt))
        if sha256_hex(json.loads(draft.read_text(encoding="utf-8"))) != bundle["receipt_draft_sha256"]:
            errors.append("receipt.draft.json JCS SHA-256 != bundle.json receipt_draft_sha256")
    # signed receipt
    receipt = None
    rp = d / "receipt.json"
    if rp.exists():
        from qor_receipt.receipt import FAIL, verify_receipt

        receipt = json.loads(rp.read_text(encoding="utf-8"))
        pub = referee_pub or ((d / "referee.pub").read_text().strip() if (d / "referee.pub").exists() else None)
        rv = verify_receipt(receipt, referee_pub=pub)
        info["receipt_verify"] = rv
        if rv["status"] == FAIL:
            errors += [f"receipt: {e}" for e in rv["errors"]]
    else:
        warnings.append("receipt.json missing (unsigned bundle; only receipt.draft.json)")
    info.update({"segments": nseg, "chain_head": head, "checkpoints": len(cps),
                 "checkpoint_head": cps[-1].get("hash") if cps else None})
    return {"ok": not errors, "errors": errors, "warnings": warnings, "info": info, "bundle": bundle,
            "receipt": receipt, "checkpoints": cps}


def _fmt(s: dict) -> str:
    q = s.get("quarter")
    c = s.get("clock_s")
    clk = f" Q{q} {c // 60}:{c % 60:02d}" if q and c is not None else (f" Q{q}" if q else "")
    return f"{s.get('away')}-{s.get('home')}{clk}"


def check_progression(states: list[dict[str, Any]]) -> list[str]:
    """``states``: time-ordered dicts with away, home, quarter, clock_s (any may be None), t_ms, src."""
    errs: list[str] = []
    last: dict[str, Any] | None = None
    for s in states:
        if s.get("away") is None or s.get("home") is None:
            continue
        if last is not None:
            where = f"{last.get('src', '?')} {_fmt(last)} -> {s.get('src', '?')} {_fmt(s)}"
            for team in ("away", "home"):
                dlt = s[team] - last[team]
                if dlt < 0:
                    errs.append(f"{team} score went backwards ({where})")
                elif dlt and dlt not in LEGAL_STEPS:
                    errs.append(f"{team} score jumped by {dlt}, not a legal scoring step ({where})")
            lq, q = last.get("quarter"), s.get("quarter")
            if lq and q:
                if q < lq:
                    errs.append(f"quarter went backwards ({where})")
                elif q == lq and last.get("clock_s") is not None and s.get("clock_s") is not None \
                        and s["clock_s"] > last["clock_s"] + CLOCK_SLACK_S:
                    errs.append(f"game clock ran backwards within Q{q} ({where})")
        last = {**(last or {}), **{k: v for k, v in s.items() if v is not None}}
    return errs


def check_final(final: dict[str, Any] | None, last_state: dict[str, Any] | None, *, what: str = "final") -> list[str]:
    if not final or not last_state or last_state.get("away") is None:
        return []
    if (final.get("away"), final.get("home")) != (last_state.get("away"), last_state.get("home")):
        return [f"{what} {final.get('away')}-{final.get('home')} != last scorebug score {last_state.get('away')}-{last_state.get('home')}"]
    return []
