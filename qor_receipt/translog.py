"""Append-only, hash-chained transparency log of every receipt the official referee service signs.

Each entry commits to the previous one (``prev``), so the log head (``size``, ``head``) commits to the whole history.
The service publishes entries and a signed head (``/v1/log/head``); heads are periodically anchored outside the
service (committed to the qortime repo, ``transparency/anchors.jsonl``). A receipt signed by the service key but
never logged — or a log rewritten after an anchor — is therefore detectable by anyone replaying the published log.

Entry hash = SHA-256(JCS(entry without ``hash``)). Logged value: ``body_sha256`` = SHA-256 of the receipt's signed
bytes (JCS of the receipt without ``signatures``), i.e. independent of the later player co-signature.

This is a linear hash chain (inclusion proof = the entries from the receipt to the head, O(n)); fine at referee scale.
A Merkle tree (RFC 6962 / RFC 9162) would give O(log n) proofs and is the upgrade path.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from qor_receipt.jcs import canonicalize
from qor_receipt.keys import kid, verify

ENTRY_TYPE = "qortime-log-entry/v1"
HEAD_TYPE = "qortime-log-head/v1"
GENESIS = "0" * 64


def entry_hash(e: dict[str, Any]) -> str:
    return hashlib.sha256(canonicalize({k: v for k, v in e.items() if k != "hash"})).hexdigest()


def body_sha256(receipt: dict[str, Any]) -> str:
    return hashlib.sha256(canonicalize({k: v for k, v in receipt.items() if k != "signatures"})).hexdigest()


def verify_entries(entries: list[dict[str, Any]], *, start_prev: str = GENESIS, start_seq: int = 0) -> list[str]:
    errs: list[str] = []
    prev, seq = start_prev, start_seq
    for e in entries:
        if e.get("type") != ENTRY_TYPE or e.get("seq") != seq:
            errs.append(f"seq:{seq}")
        if e.get("prev") != prev:
            errs.append(f"prev:{seq}")
        if e.get("hash") != entry_hash(e):
            errs.append(f"hash:{seq}")
        prev, seq = e.get("hash"), seq + 1
    return errs


def head_payload(h: dict[str, Any]) -> bytes:
    return canonicalize({k: v for k, v in h.items() if k != "sig"})


def verify_head(h: dict[str, Any], pubkey_hex: str) -> bool:
    return (isinstance(h, dict) and h.get("type") == HEAD_TYPE and isinstance(h.get("sig"), str)
            and h.get("key_id") == kid(pubkey_hex) and verify(pubkey_hex, h["sig"], head_payload(h)))


def verify_inclusion(body_hash: str, entries: list[dict[str, Any]], head: dict[str, Any]) -> list[str]:
    """``entries`` = the log entries from the one holding ``body_hash`` up to the head (inclusive)."""
    if not entries:
        return ["inclusion_no_entries"]
    first = entries[0]
    errs = []
    if first.get("body_sha256") != body_hash:
        errs.append("inclusion_wrong_entry")
    errs += verify_entries(entries, start_prev=first.get("prev"), start_seq=int(first.get("seq", -1)))
    last = entries[-1]
    if head.get("size") != int(last.get("seq", -2)) + 1 or head.get("head") != last.get("hash"):
        errs.append("inclusion_head_mismatch")
    return errs


class TransparencyLog:
    """JSONL-backed log. Single writer (the service process); appends are fsync'd before a receipt is released."""

    def __init__(self, path: str | Path | None, signer=None, delegation: dict | None = None,
                 now=lambda: int(time.time() * 1000)) -> None:
        self.path = Path(path) if path else None
        self.signer, self.delegation, self.now = signer, delegation, now
        self.entries: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.entries.append(json.loads(line))
            errs = verify_entries(self.entries)
            if errs:
                raise RuntimeError(f"transparency log at {self.path} is corrupt: {errs[:5]}")

    @property
    def head(self) -> str:
        return self.entries[-1]["hash"] if self.entries else GENESIS

    def append(self, kind: str, body_hash: str, **meta: Any) -> dict[str, Any]:
        with self.lock:
            e = {"type": ENTRY_TYPE, "seq": len(self.entries), "ts_ms": self.now(), "kind": kind,
                 "body_sha256": body_hash, "prev": self.head, **meta}
            if self.signer is not None:
                e["signer_key_id"] = self.signer.key_id
            e["hash"] = entry_hash(e)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(e, separators=(",", ":")) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            self.entries.append(e)
            return e

    def signed_head(self) -> dict[str, Any]:
        with self.lock:
            h = {"type": HEAD_TYPE, "size": len(self.entries), "head": self.head, "ts_ms": self.now()}
        if self.signer is not None:
            h["pubkey"] = self.signer.pubkey_hex
            h["key_id"] = self.signer.key_id
            if self.delegation:
                h["delegation_serial"] = self.delegation.get("serial")
            h["sig"] = self.signer.sign(head_payload(h))
        return h

    def find(self, body_hash: str) -> int | None:
        for e in self.entries:
            if e.get("body_sha256") == body_hash:
                return int(e["seq"])
        return None

    def slice(self, start: int, limit: int) -> list[dict[str, Any]]:
        return self.entries[max(0, start): max(0, start) + max(0, limit)]
