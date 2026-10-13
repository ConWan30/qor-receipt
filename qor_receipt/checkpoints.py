"""Rolling hash checkpoints (frames + input), countersigned by the referee.

Every few seconds the agent hashes the frames it captured and the HID events it
logged in that window, chains it onto the previous checkpoint and sends it to
the referee. The referee stamps its own clock and countersigns. A receipt built
after the fact cannot carry referee signatures with past timestamps, so
post-hoc fabrication fails even if the player's key is stolen/extracted.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable

from qor_receipt.jcs import canonicalize, sha256_hex
from qor_receipt.keys import KeyProvider, verify

GENESIS = "0" * 64
DEFAULT_MAX_GAP_MS = 10_000      # referee must have seen a checkpoint at least this often
DEFAULT_MAX_SKEW_MS = 3_000      # agent clock vs referee clock


def window_hash(items: Iterable[bytes]) -> str:
    h = hashlib.sha256()
    for it in items:
        h.update(hashlib.sha256(it).digest())
    return h.hexdigest()


def hid_events_hash(events: list[dict[str, Any]]) -> str:
    return sha256_hex(events)


def make_checkpoint(seq: int, t_ms: int, frames_hash: str, input_hash: str, prev: str) -> dict[str, Any]:
    body = {"seq": seq, "t_ms": t_ms, "frames_hash": frames_hash, "input_hash": input_hash, "prev": prev}
    body["hash"] = sha256_hex(body)
    return body


def body_of(cp: dict[str, Any]) -> dict[str, Any]:
    return {k: cp[k] for k in ("seq", "t_ms", "frames_hash", "input_hash", "prev")}


def countersign_payload(match_id: str, role: str, cp_hash: str, referee_ts_ms: int) -> bytes:
    return canonicalize({"type": "checkpoint", "match_id": match_id, "role": role,
                         "hash": cp_hash, "referee_ts_ms": referee_ts_ms})


def countersign(referee: KeyProvider, match_id: str, role: str, cp: dict[str, Any], referee_ts_ms: int) -> dict[str, Any]:
    out = dict(cp)
    out["referee_ts_ms"] = referee_ts_ms
    out["referee_sig"] = referee.sign(countersign_payload(match_id, role, cp["hash"], referee_ts_ms))
    return out


class Chain:
    """Agent-side builder."""

    def __init__(self) -> None:
        self.seq = 0
        self.prev = GENESIS

    def next(self, t_ms: int, frames_hash: str, input_hash: str) -> dict[str, Any]:
        cp = make_checkpoint(self.seq, t_ms, frames_hash, input_hash, self.prev)
        self.seq += 1
        self.prev = cp["hash"]
        return cp


def verify_chain(cps: list[dict[str, Any]], *, match_id: str, role: str, referee_pub: str,
                 start_ms: int, end_ms: int, max_gap_ms: int = DEFAULT_MAX_GAP_MS,
                 max_skew_ms: int = DEFAULT_MAX_SKEW_MS) -> list[str]:
    errs: list[str] = []
    if not cps:
        return ["no_checkpoints"]
    prev, last_ts = GENESIS, start_ms
    for i, cp in enumerate(cps):
        tag = f"cp[{i}]"
        try:
            if cp["seq"] != i:
                errs.append(f"{tag}:seq_gap")
            if cp["prev"] != prev:
                errs.append(f"{tag}:prev_mismatch")
            if sha256_hex(body_of(cp)) != cp["hash"]:
                errs.append(f"{tag}:hash_mismatch")
            ts = cp["referee_ts_ms"]
            if not verify(referee_pub, cp["referee_sig"], countersign_payload(match_id, role, cp["hash"], ts)):
                errs.append(f"{tag}:bad_referee_sig")
            if ts < last_ts:
                errs.append(f"{tag}:referee_time_backwards")
            if ts - last_ts > max_gap_ms:
                errs.append(f"{tag}:gap_too_long")
            if abs(ts - cp["t_ms"]) > max_skew_ms:
                errs.append(f"{tag}:clock_skew")
            prev, last_ts = cp["hash"], ts
        except (KeyError, TypeError, ValueError):
            errs.append(f"{tag}:malformed")
    if end_ms - last_ts > max_gap_ms:
        errs.append("chain_ends_before_match_end")
    return errs
