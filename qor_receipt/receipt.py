"""Build / sign / verify qor-receipt v0.

Signed bytes = JCS(receipt without "signatures"). Every listed player and the
referee sign the same bytes. Verdicts follow the QorTroller PORT-CERT honesty
rule: any broken evidence => FAIL; missing evidence => PARTIAL; VERIFIED only
when everything was checked. The trust tier is computed, never trusted.
"""
from __future__ import annotations

import copy
from typing import Any

from qor_receipt import SCHEMA
from qor_receipt.challenge import PHASES, pattern_for
from qor_receipt.checkpoints import verify_chain
from qor_receipt.jcs import canonicalize
from qor_receipt.keys import KeyProvider, kid, verify  # noqa: F401

VERIFIED, PARTIAL, FAIL = "VERIFIED", "PARTIAL", "FAIL"
DUAL, SINGLE = "dual-attested", "single-attested"
MOTION_MIN_PERMILLE = 300


def signing_bytes(receipt: dict[str, Any]) -> bytes:
    return canonicalize({k: v for k, v in receipt.items() if k != "signatures"})


def compute_tier(body: dict[str, Any]) -> str:
    roles = {p["role"] for p in body.get("players", [])}
    ag = body.get("agreement") or {}
    if body["match"].get("mode") == "dual" and roles == {"home", "away"} and ag.get("timelines_agree") is True:
        return DUAL
    return SINGLE


def build(*, match: dict, result: dict, players: list[dict], referee: dict, challenges: list[dict],
          checkpoints: dict, agreement: dict, signals: dict) -> dict[str, Any]:
    body = {"schema": SCHEMA, "match": match, "result": result, "players": players, "referee": referee,
            "challenges": challenges, "checkpoints": checkpoints, "agreement": agreement, "signals": signals}
    body["trust_tier"] = compute_tier(body)
    body["signatures"] = {"players": {}, "referee": None}
    return body


def sign_player(receipt: dict, role: str, key: KeyProvider) -> dict:
    r = copy.deepcopy(receipt)
    r["signatures"]["players"][role] = {"key_id": key.key_id, "sig": key.sign(signing_bytes(r))}
    return r


def sign_referee(receipt: dict, key: KeyProvider) -> dict:
    r = copy.deepcopy(receipt)
    r["signatures"]["referee"] = {"key_id": key.key_id, "sig": key.sign(signing_bytes(r))}
    return r


def verify_receipt(r: dict[str, Any], *, referee_pub: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    partial: list[str] = []
    try:
        if r.get("schema") != SCHEMA:
            return {"status": FAIL, "trust_tier": None, "errors": ["schema"], "partial": []}
        msg = signing_bytes(r)
        sigs = r.get("signatures") or {}
        ref = r["referee"]
        if referee_pub is None:
            partial.append("referee_key_not_pinned")
        elif referee_pub.lower() != ref["pubkey"].lower():
            errors.append("referee_key_mismatch")
        rs = sigs.get("referee")
        if not rs or rs.get("key_id") != kid(ref["pubkey"]) or not verify(ref["pubkey"], rs["sig"], msg):
            errors.append("bad_referee_signature")
        for p in r["players"]:
            ps = (sigs.get("players") or {}).get(p["role"])
            if not ps:
                errors.append(f"missing_player_signature:{p['role']}")
            elif ps.get("key_id") != kid(p["pubkey"]) or not verify(p["pubkey"], ps["sig"], msg):
                errors.append(f"bad_player_signature:{p['role']}")
        m = r["match"]
        for p in r["players"]:
            role = p["role"]
            errors += [f"{role}:{e}" for e in verify_chain(
                r["checkpoints"].get(role, []), match_id=m["match_id"], role=role, referee_pub=ref["pubkey"],
                start_ms=m["started_ms"], end_ms=m["ended_ms"])]
            for ph in PHASES:
                cs = [c for c in r["challenges"] if c["role"] == role and c["phase"] == ph]
                if not cs:
                    partial.append(f"{role}:challenge_missing:{ph}")
                for c in cs:
                    if c["pattern"] != pattern_for(c["nonce"], len(c["pattern"]) - 1):
                        errors.append(f"{role}:{ph}:pattern_not_from_nonce")
                    res = c.get("result")
                    if res is None:
                        partial.append(f"{role}:challenge_unanswered:{ph}")
                    elif not res.get("ok"):
                        errors.append(f"{role}:{ph}:challenge_failed:{res.get('reason')}")
            mp = (r.get("signals") or {}).get("motion_permille", {}).get(role)
            if mp is None:
                partial.append(f"{role}:motion_signal_missing")
            elif mp < MOTION_MIN_PERMILLE:
                partial.append(f"{role}:motion_correlation_low")
        ag = r.get("agreement") or {}
        if m.get("mode") == "dual" and ag.get("timelines_agree") is not True:
            errors.append("dual_timelines_disagree")
        res = r["result"]
        winner = "home" if res["home_score"] > res["away_score"] else "away"
        if res.get("winner") != winner:
            errors.append("winner_inconsistent")
        tier = compute_tier(r)
        if r.get("trust_tier") != tier:
            errors.append("trust_tier_claim_wrong")
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        errors.append(f"malformed:{type(e).__name__}")
        tier = None
    status = FAIL if errors else (PARTIAL if partial else VERIFIED)
    return {"status": status, "trust_tier": tier, "errors": errors, "partial": partial}
