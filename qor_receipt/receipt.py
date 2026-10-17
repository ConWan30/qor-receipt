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
from qor_receipt.challenge import DEFAULT_LEN, PHASES, pattern_for
from qor_receipt.checkpoints import verify_chain
from qor_receipt.jcs import canonicalize
from qor_receipt.keys import KeyProvider, kid, verify  # noqa: F401

VERIFIED, PARTIAL, FAIL = "VERIFIED", "PARTIAL", "FAIL"
DUAL, SINGLE = "dual-attested", "single-attested"
MOTION_MIN_PERMILLE = 300
RESULT_METHODS = frozenset({"agreed_scorebug_final", "end_menu_read", "last_scorebug_read_unconfirmed"})
ROLES = frozenset({"home", "away"})
# Referee kinds that mean "the same machine signed as referee" — never VERIFIED.
LOCAL_REFEREE_PREFIXES = ("local-self-attested",)
# key_kind values that claim hardware/attestation we cannot check in v0 → PARTIAL.
UNVERIFIED_KIND_CLAIMS = frozenset({"tpm"})


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


def _is_nonneg_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


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

        players = r.get("players")
        if not isinstance(players, list) or not players:
            errors.append("no_players")
            players = []
        roles_seen: set[str] = set()
        for p in players:
            role = p.get("role")
            if role not in ROLES:
                errors.append(f"bad_player_role:{role!r}")
                continue
            if role in roles_seen:
                errors.append(f"duplicate_player_role:{role}")
            roles_seen.add(role)
            ps = (sigs.get("players") or {}).get(role)
            if not ps:
                errors.append(f"missing_player_signature:{role}")
            elif ps.get("key_id") != kid(p["pubkey"]) or not verify(p["pubkey"], ps["sig"], msg):
                errors.append(f"bad_player_signature:{role}")
            kind = p.get("key_kind")
            if kind in UNVERIFIED_KIND_CLAIMS:
                partial.append(f"{role}:key_kind_claim_unverified:{kind}")

        m = r["match"]
        if m.get("mode") == "dual" and roles_seen != {"home", "away"}:
            errors.append("dual_missing_roles")

        ref_kind = str(ref.get("key_kind") or "")
        if any(ref_kind.startswith(p) for p in LOCAL_REFEREE_PREFIXES):
            partial.append("referee_local_self_attested")
        if ref_kind in UNVERIFIED_KIND_CLAIMS:
            partial.append(f"referee_key_kind_claim_unverified:{ref_kind}")
        # Same key signing as both referee and a player = self-attested, not independent.
        for p in players:
            if p.get("role") in ROLES and p.get("pubkey", "").lower() == ref.get("pubkey", "").lower():
                partial.append("referee_same_as_player")
                break

        for p in players:
            role = p.get("role")
            if role not in ROLES:
                continue
            errors += [f"{role}:{e}" for e in verify_chain(
                r["checkpoints"].get(role, []), match_id=m["match_id"], role=role, referee_pub=ref["pubkey"],
                start_ms=m["started_ms"], end_ms=m["ended_ms"])]
            for ph in PHASES:
                cs = [c for c in r["challenges"] if c["role"] == role and c["phase"] == ph]
                if not cs:
                    partial.append(f"{role}:challenge_missing:{ph}")
                for c in cs:
                    pat = c.get("pattern") or []
                    if not isinstance(pat, list) or len(pat) < DEFAULT_LEN + 1:
                        errors.append(f"{role}:{ph}:pattern_too_short")
                    elif c["pattern"] != pattern_for(c["nonce"], len(c["pattern"]) - 1):
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
        if not _is_nonneg_int(res.get("home_score")) or not _is_nonneg_int(res.get("away_score")):
            errors.append("score_not_nonneg_int")
        else:
            winner = "home" if res["home_score"] > res["away_score"] else "away"
            if res.get("winner") != winner:
                errors.append("winner_inconsistent")
        if not isinstance(res.get("quarter"), int) or isinstance(res.get("quarter"), bool) or res["quarter"] < 4:
            errors.append("quarter_not_final")
        if res.get("method") not in RESULT_METHODS:
            errors.append("bad_result_method")
        tier = compute_tier(r)
        if r.get("trust_tier") != tier:
            errors.append("trust_tier_claim_wrong")
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        errors.append(f"malformed:{type(e).__name__}")
        tier = None
    status = FAIL if errors else (PARTIAL if partial else VERIFIED)
    return {"status": status, "trust_tier": tier, "errors": errors, "partial": partial}
