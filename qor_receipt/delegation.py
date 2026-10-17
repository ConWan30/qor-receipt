"""Delegated referee keys: an offline ROOT key certifies a short-lived ONLINE service key.

The root key (``qortime-v1``, on the trusted-referee allowlist with ``can_delegate``) never runs online. It signs a
``qortime-referee-delegation/v1`` certificate binding a service Ed25519 key to:

  scope            ["official-review"]        what the service key may sign (review receipts + their challenge tokens)
  not_before_ms /  not_after_ms               validity window (rotation every 30-90 days)
  allowed_statuses ["VERIFIED", "PARTIAL"]   highest verdicts a receipt signed under this cert may reach
  serial                                     unique, monotonically increasing; what revocation names

A receipt carries the cert inside ``referee.delegation`` (so the service's own signature covers it too). Verifiers
accept a service-signed receipt only if the cert is root-signed, the receipt's ``review.reviewed_ms`` lies inside the
window, and the serial / key is not on the root-signed revocation list. Receipts signed directly by an allowlisted
key (legacy, root-signed) keep verifying exactly as before.

Signed bytes are RFC 8785 JCS of the object without ``sig`` (same canonicalization as receipts). Ed25519 only.
"""
from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from qor_receipt.jcs import canonicalize
from qor_receipt.keys import kid, verify

DELEGATION_TYPE = "qortime-referee-delegation/v1"
REVOCATIONS_TYPE = "qortime-referee-revocations/v1"
SCOPE_REVIEW = "official-review"
DEFAULT_ALLOWED = ["VERIFIED", "PARTIAL"]
FUTURE_SLACK_MS = 10 * 60_000
MAX_LIFETIME_MS = 120 * 86_400_000  # hard ceiling regardless of what the root signs

REVOCATIONS = Path(__file__).resolve().parent / "data" / "referee_revocations.json"


def payload(obj: dict[str, Any]) -> bytes:
    return canonicalize({k: v for k, v in obj.items() if k != "sig"})


def make_delegation(root, *, root_id: str, delegate_pubkey: str, delegate_id: str, serial: int,
                    not_before_ms: int, not_after_ms: int, allowed_statuses=None, issued_ms: int | None = None,
                    scope=None) -> dict[str, Any]:
    """Root-side: build and sign a delegation cert (``root`` is a KeyProvider holding the root key)."""
    cert = {"type": DELEGATION_TYPE, "serial": int(serial), "root_id": root_id, "root_key_id": root.key_id,
            "delegate_id": delegate_id, "delegate_pubkey": delegate_pubkey.lower(), "delegate_key_id": kid(delegate_pubkey),
            "scope": list(scope or [SCOPE_REVIEW]), "allowed_statuses": list(allowed_statuses or DEFAULT_ALLOWED),
            "not_before_ms": int(not_before_ms), "not_after_ms": int(not_after_ms),
            "issued_ms": int(issued_ms if issued_ms is not None else time.time() * 1000)}
    cert["sig"] = root.sign(payload(cert))
    return cert


def make_revocations(root, *, root_id: str, version: int, revoked: list[dict[str, Any]],
                     issued_ms: int | None = None) -> dict[str, Any]:
    doc = {"type": REVOCATIONS_TYPE, "root_id": root_id, "root_key_id": root.key_id, "version": int(version),
           "issued_ms": int(issued_ms if issued_ms is not None else time.time() * 1000),
           "revoked": sorted(revoked, key=lambda r: int(r.get("serial", 0)))}
    doc["sig"] = root.sign(payload(doc))
    return doc


@lru_cache(maxsize=8)
def _load(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_revocations(path: str | None = None) -> dict[str, Any] | None:
    p = Path(path) if path else REVOCATIONS
    return _load(str(p)) if p.exists() else None


def clear_cache() -> None:
    _load.cache_clear()


def _roots(allowlist: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in (allowlist.get("referees") or [])
            if r.get("status") == "active" and r.get("can_delegate") is True and r.get("pubkey")]


def _root_for(key_id: Any, allowlist: dict[str, Any]) -> dict[str, Any] | None:
    for r in _roots(allowlist):
        if kid(str(r["pubkey"]).lower()) == key_id:
            return r
    return None


def revocations_problem(doc: dict[str, Any] | None, allowlist: dict[str, Any]) -> str | None:
    """None if the revocation list is present and root-signed; else a reason (fail closed: no delegation trusted)."""
    if doc is None:
        return "revocations_missing"
    if not isinstance(doc, dict) or doc.get("type") != REVOCATIONS_TYPE or not isinstance(doc.get("sig"), str):
        return "revocations_malformed"
    root = _root_for(doc.get("root_key_id"), allowlist)
    if root is None or not verify(str(root["pubkey"]).lower(), doc["sig"], payload(doc)):
        return "revocations_bad_signature"
    return None


def check_delegation(cert: Any, signer_pubkey: str, at_ms: Any, *, allowlist: dict[str, Any],
                     revocations: dict[str, Any] | None, scope: str = SCOPE_REVIEW,
                     now_ms: int | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Return (info, problems). ``info`` (non-None only when problems is empty) describes the trusted delegate."""
    probs: list[str] = []
    if not isinstance(cert, dict) or cert.get("type") != DELEGATION_TYPE or not isinstance(cert.get("sig"), str):
        return None, ["delegation_malformed"]
    root = _root_for(cert.get("root_key_id"), allowlist)
    if root is None:
        return None, ["delegation_root_not_trusted"]
    if not verify(str(root["pubkey"]).lower(), cert["sig"], payload(cert)):
        return None, ["delegation_bad_signature"]
    dpub = str(cert.get("delegate_pubkey") or "").lower()
    if dpub != str(signer_pubkey or "").lower() or cert.get("delegate_key_id") != kid(dpub):
        probs.append("delegation_key_mismatch")
    if dpub == str(root["pubkey"]).lower():
        probs.append("delegation_to_root")
    if scope not in (cert.get("scope") or []):
        probs.append("delegation_scope")
    nb, na = cert.get("not_before_ms"), cert.get("not_after_ms")
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (nb, na)) or na <= nb:
        probs.append("delegation_window_malformed")
    elif na - nb > MAX_LIFETIME_MS:
        probs.append("delegation_lifetime_too_long")
    elif not isinstance(at_ms, int) or isinstance(at_ms, bool):
        probs.append("delegation_signing_time_missing")
    else:
        if at_ms < nb:
            probs.append("delegation_not_yet_valid")
        if at_ms > na:
            probs.append("delegation_expired")
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if at_ms > now + FUTURE_SLACK_MS:
            probs.append("delegation_signing_time_in_future")
    rp = revocations_problem(revocations, allowlist)
    if rp:
        probs.append(rp)
    else:
        for row in revocations.get("revoked") or []:  # type: ignore[union-attr]
            if row.get("serial") == cert.get("serial") or str(row.get("delegate_pubkey", "")).lower() == dpub:
                probs.append(f"delegation_revoked:{cert.get('serial')}")
                break
    if probs:
        return None, probs
    return {"id": cert.get("delegate_id"), "status": "active", "delegated_by": root.get("id"),
            "serial": cert.get("serial"), "allowed_statuses": list(cert.get("allowed_statuses") or []),
            "not_after_ms": na}, []
