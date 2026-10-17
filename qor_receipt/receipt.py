"""Build / sign / verify qor-receipt v0.

Signed bytes = JCS(receipt without "signatures"). Every listed player and the
referee sign the same bytes. Verdicts follow the QorTroller PORT-CERT honesty
rule: any broken evidence => FAIL; missing evidence => PARTIAL; VERIFIED only
when everything was checked **and** the referee pubkey is on the built-in
trusted-referee allowlist. Unknown / local / URL-pinned keys are at most
SELF_ATTESTED. The trust tier is computed, never trusted.
"""
from __future__ import annotations

import copy
from typing import Any

from qor_receipt import SCHEMA
from qor_receipt.challenge import DEFAULT_LEN, PHASES, pattern_for
from qor_receipt.checkpoints import verify_chain
from qor_receipt.jcs import canonicalize
from qor_receipt.keys import KeyProvider, kid, verify  # noqa: F401
from qor_receipt.trusted_referees import is_trusted_active, lookup

VERIFIED, PARTIAL, SELF_ATTESTED, FAIL = "VERIFIED", "PARTIAL", "SELF_ATTESTED", "FAIL"
DUAL, SINGLE = "dual-attested", "single-attested"
MOTION_MIN_PERMILLE = 300
RESULT_METHODS = frozenset({"agreed_scorebug_final", "end_menu_read", "last_scorebug_read_unconfirmed"})
ROLES = frozenset({"home", "away"})
LOCAL_REFEREE_PREFIXES = ("local-self-attested",)
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



def _challenge_evidence_errors(c: dict[str, Any], cps: list[dict[str, Any]]) -> list[str]:
    """Evidence required when result.ok is claimed. Without HID bytes in the receipt we cannot
    re-run verify_response; we require a HID commitment, sane timing, and a countersigned
    checkpoint whose referee clock falls inside the challenge window (referee was live)."""
    errs: list[str] = []
    res = c.get("result") or {}
    hid = res.get("hid_sha256")
    if not isinstance(hid, str) or len(hid) != 64 or any(ch not in "0123456789abcdef" for ch in hid.lower()):
        errs.append("challenge_missing_hid_sha256")
    try:
        issued, deadline = int(c["issued_ms"]), int(c["deadline_ms"])
        pause = res.get("pause_press_ms")
        done = res.get("done_ms")
        lat = res.get("latency_ms")
    except (KeyError, TypeError, ValueError):
        return errs + ["challenge_timing_malformed"]
    if pause is None or done is None or lat is None:
        errs.append("challenge_timing_incomplete")
    else:
        if not (issued <= int(pause) <= deadline):
            errs.append("challenge_pause_outside_window")
        if int(done) < int(pause):
            errs.append("challenge_done_before_pause")
        if not (0 <= int(lat) <= 600):
            errs.append("challenge_latency_out_of_range")
    # Live referee: at least one countersigned checkpoint stamped during the challenge window.
    win_lo, win_hi = issued - 3_000, deadline + 5_000
    if not any(isinstance(cp.get("referee_ts_ms"), int) and win_lo <= cp["referee_ts_ms"] <= win_hi for cp in (cps or [])):
        errs.append("challenge_not_bound_to_live_checkpoint")
    return errs


# Advisory thresholds for signals.input (QorCap input_proof) — never gates VERIFIED alone.
INPUT_HUMAN_MIN = 400          # human_permille: below = "machine-like / low"
INPUT_ALIGN_EDGE = 50          # match_permille must beat chance_permille by this margin
INPUT_HAPTIC_MIN = 1           # at least one high-confidence haptic if haptics claimed


def _input_proof_advisory(signals: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fold QorCap ``signals.input`` into verify as signed advisory evidence (not proof).

    Returns None when no input block is present. Never raises; missing fields → caution notes.
    """
    if not isinstance(signals, dict):
        return None
    inp = signals.get("input")
    if not isinstance(inp, dict) or not inp:
        return None
    notes: list[str] = []
    flags: list[str] = []
    human = inp.get("human_permille")
    label = inp.get("human_label")
    if isinstance(human, int):
        if human < INPUT_HUMAN_MIN:
            flags.append("human_score_low")
            notes.append(f"human_permille={human} below advisory floor {INPUT_HUMAN_MIN}"
                         + (f" ({label})" if label else ""))
        else:
            notes.append(f"human_permille={human}" + (f" ({label})" if label else ""))
    else:
        flags.append("human_score_missing")
    match_p, chance_p = inp.get("match_permille"), inp.get("chance_permille")
    if isinstance(match_p, int) and isinstance(chance_p, int):
        if match_p < chance_p + INPUT_ALIGN_EDGE:
            flags.append("alignment_not_above_chance")
            notes.append(f"alignment match={match_p} chance={chance_p} (need +{INPUT_ALIGN_EDGE})")
        else:
            notes.append(f"alignment match={match_p} vs chance={chance_p} (ok)")
    else:
        flags.append("alignment_missing")
    he = inp.get("haptic_events")
    hc = inp.get("haptic_high_confidence")
    if isinstance(he, int):
        if he <= 0:
            flags.append("no_haptic_events")
            notes.append("no haptic events recorded")
        elif isinstance(hc, int) and hc < INPUT_HAPTIC_MIN:
            flags.append("haptic_low_confidence_only")
            notes.append(f"haptic_events={he} but high_confidence={hc}")
        else:
            notes.append(f"haptic_events={he} high_confidence={hc}")
    # Signed hashes present? advisory only
    if not inp.get("inputs_sha256"):
        flags.append("inputs_sha256_missing")
    ok = not any(f in flags for f in (
        "human_score_low", "alignment_not_above_chance", "no_haptic_events", "haptic_low_confidence_only"))
    return {
        "present": True,
        "ok": ok,
        "flags": flags,
        "notes": notes,
        "status": inp.get("status"),
        "human_permille": human,
        "match_permille": match_p,
        "chance_permille": chance_p,
        "haptic_events": he,
        "haptic_high_confidence": hc,
        "claims": "advisory evidence only — not cryptographic proof of a human player",
    }


def verify_receipt(r: dict[str, Any], *, referee_pub: str | None = None,
                   allowlist_path: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    partial: list[str] = []
    trusted_ref = False
    input_advisory = _input_proof_advisory(r.get("signals") if isinstance(r, dict) else None)
    referee_entry: dict[str, Any] | None = None
    try:
        if r.get("schema") != SCHEMA:
            return {"status": FAIL, "trust_tier": None, "errors": ["schema"], "partial": [],
                    "referee_trust": "unknown"}
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

        referee_entry = lookup(ref["pubkey"], path=allowlist_path)
        if referee_entry and referee_entry.get("status") == "active":
            trusted_ref = True
        elif referee_entry and referee_entry.get("status") == "revoked":
            partial.append(f"referee_revoked:{referee_entry.get('id')}")
        else:
            partial.append("referee_not_on_allowlist")

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
            trusted_ref = False  # local referee is never the official allowlisted operator key in spirit
        if ref_kind in UNVERIFIED_KIND_CLAIMS:
            partial.append(f"referee_key_kind_claim_unverified:{ref_kind}")
        for p in players:
            if p.get("role") in ROLES and p.get("pubkey", "").lower() == ref.get("pubkey", "").lower():
                partial.append("referee_same_as_player")
                trusted_ref = False
                break

        # Pin must be an allowlisted active key for VERIFIED. A URL/?referee= pin of an
        # unknown key never upgrades trust (loud warning is UI-side).
        if referee_pub is not None and not is_trusted_active(referee_pub, path=allowlist_path):
            trusted_ref = False
            if "referee_pin_not_allowlisted" not in partial:
                partial.append("referee_pin_not_allowlisted")

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
                    else:
                        # Do not trust bare result.ok: require committed evidence + live referee window.
                        ev_errs = _challenge_evidence_errors(c, r["checkpoints"].get(role, []))
                        if ev_errs:
                            for e in ev_errs:
                                partial.append(f"{role}:{ph}:{e}")
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
        trusted_ref = False

    if errors:
        status = FAIL
    elif not trusted_ref:
        status = SELF_ATTESTED
    elif partial:
        status = PARTIAL
    else:
        status = VERIFIED

    out = {"status": status, "trust_tier": tier, "errors": errors, "partial": partial,
           "referee_trust": ("active" if trusted_ref else ("revoked" if referee_entry and referee_entry.get("status") == "revoked" else "untrusted")),
           "input_advisory": input_advisory}
    if referee_entry:
        out["referee_id"] = referee_entry.get("id")
    return out
