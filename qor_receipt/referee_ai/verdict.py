"""Signed referee verdict, receipt-friendly: JCS bytes (no floats), Ed25519, same key_id rule as qor-receipt."""
from __future__ import annotations

import copy
import hashlib
from typing import Any

from qor_receipt.jcs import canonicalize
from qor_receipt.keys import KeyProvider, kid, verify

SCHEMA = "qortime-referee-verdict/v0"
ACCEPT, DISPUTE, ABSTAIN = "accept", "dispute", "abstain"
FILENAME = "referee_verdict.json"


def signing_bytes(v: dict[str, Any]) -> bytes:
    return canonicalize({k: x for k, x in v.items() if k != "signature"})


def sign(v: dict[str, Any], key: KeyProvider) -> dict[str, Any]:
    out = copy.deepcopy(v)
    out["referee_ai"] = {"pubkey": key.pubkey_hex, "key_id": key.key_id, "key_kind": key.kind}
    out.pop("signature", None)
    out["signature"] = {"key_id": key.key_id, "sig": key.sign(signing_bytes(out))}
    return out


def verify_verdict(v: dict[str, Any], *, pub: str | None = None) -> list[str]:
    errs: list[str] = []
    try:
        if v.get("schema") != SCHEMA:
            errs.append("schema")
        if v.get("verdict") not in (ACCEPT, DISPUTE, ABSTAIN):
            errs.append("verdict_value")
        ra = v["referee_ai"]
        if pub is not None and pub.lower() != ra["pubkey"].lower():
            errs.append("key_mismatch")
        s = v["signature"]
        if s.get("key_id") != kid(ra["pubkey"]) or not verify(ra["pubkey"], s["sig"], signing_bytes(v)):
            errs.append("bad_signature")
    except (KeyError, TypeError, ValueError) as e:
        errs.append(f"malformed:{type(e).__name__}")
    return errs


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
