"""Regenerate tests/fixtures/contract/*.json: small synthetic QorCap-shaped receipts with known verdicts.

Deterministic seeds (test-only keys, published here on purpose; never used for real matches).
Each fixture mirrors what QorCap's Start Match / Finalize produce (single mode, 'segment' + 'stop' checkpoints).
    python scripts/make_contract_fixtures.py
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from qor_receipt import receipt as R
from qor_receipt.checkpoints import Chain, countersign
from qor_receipt.keys import SoftwareKey

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "contract"
DEV = SoftwareKey.from_seed_hex("11" * 32)
REF = SoftwareKey.from_seed_hex("22" * 32)
FOREIGN = SoftwareKey.from_seed_hex("33" * 32)
T0, MID, ROLE = 1_792_000_000_000, "local-contract-1", "home"


def chain(n=8, step=2000, tail_delay_ms=150, first_delay_ms=500, ref=REF, offline=False):
    """QorCap-shaped chain: start, segments every `step`, then the sealed 'stop' checkpoint.
    The local referee stamps each checkpoint `delay` after its t_ms; the last two get `tail_delay_ms`."""
    ch, out = Chain(), []
    for i in range(n):
        t = T0 + first_delay_ms + i * step
        cp = ch.next(t, f"{i:064x}", f"{i + 100:064x}")
        delay = 0 if offline else (tail_delay_ms if i >= n - 2 else 120)
        out.append(countersign(ref, MID, ROLE, cp, t + delay))
    return out


NONCES = {"kickoff": "00112233445566778899aabbccddeeff", "halftime": "ffeeddccbbaa99887766554433221100",
          "end": "0123456789abcdef0123456789abcdef"}


def challenges(*, window_ms=90_000, answered=True, unanswered=()):
    """QorCap >= 0.3.11: one challenge per phase, opened at a pause-capable moment, answered + re-run at finalize."""
    from qor_receipt.challenge import pattern_for
    out = []
    for i, (ph, n) in enumerate(NONCES.items()):
        issued = T0 + 1_000 + i * 4_000
        press = issued + 2_500
        res = None
        if answered and ph not in unanswered:
            res = {"ok": True, "reason": "ok", "pause_press_ms": press, "done_ms": press + 2_400, "latency_ms": 180,
                   "hid_sha256": f"{i + 7:064x}", "reverified_by": "recorder"}
        out.append({"id": n[:8], "role": ROLE, "phase": ph, "nonce": n, "pattern": pattern_for(n), "issued_ms": issued,
                    "deadline_ms": issued + window_ms, "result": res})
    return out


def receipt(cps, *, ref=REF, ref_pub=None, profile="madden27_standard", method="agreed_scorebug_final", chal=None):
    m = {"match_id": MID, "game": "madden27", "platform": "ps5", "scorebug_profile": profile, "mode": "single",
         "started_ms": T0, "ended_ms": max(cps[-1]["referee_ts_ms"], T0)}
    res = {"home_score": 21, "away_score": 17, "quarter": 4, "final_t_ms": cps[-1]["t_ms"] - 500, "winner": "home",
           "method": method, "final_basis": "clock_zero" if method == "agreed_scorebug_final" else "end_menu"}
    if method == "end_menu_read":
        res["final_confirmed"] = True
    rec = R.build(match=m, result=res,
                  players=[{"role": ROLE, "player_id": "local:player", "pubkey": DEV.pubkey_hex, "key_kind": "software-file"}],
                  referee={"pubkey": ref_pub or ref.pubkey_hex, "key_id": ref.key_id, "key_kind": "local-self-attested"},
                  challenges=chal or [], checkpoints={ROLE: cps}, agreement={"timelines_agree": None, "mismatches": []},
                  signals={"motion_permille": {ROLE: None}})
    return R.sign_player(R.sign_referee(rec, ref), ROLE, DEV)


def cases():
    good = receipt(chain())
    tampered = copy.deepcopy(good); tampered["result"]["home_score"] = 99
    missing = receipt(chain())
    del missing["checkpoints"][ROLE][3]
    missing = R.sign_player(R.sign_referee(missing, REF), ROLE, DEV)
    return {
        "good": (good, REF.pubkey_hex),
        "good_monday_night_end_menu": (receipt(chain(), profile="madden27_monday_night", method="end_menu_read"), REF.pubkey_hex),
        "tampered_score": (tampered, REF.pubkey_hex),
        "foreign_key_pinned_mismatch": (receipt(chain(ref=FOREIGN), ref=FOREIGN), REF.pubkey_hex),
        "foreign_key_claims_official": (receipt(chain(ref=FOREIGN), ref=FOREIGN,
                                                ref_pub="a5aa0c9121db2c517284bf1c49eeec199829ca832a2b665f26e872e01192feea"), None),
        "missing_checkpoint": (missing, REF.pubkey_hex),
        # QorCap <= 0.3.2 Start Match: tail countersigned only after the input-proof work (seen live: +28 s, +113 s)
        "v032_late_stop_tail": (receipt(chain(tail_delay_ms=28_000)), REF.pubkey_hex),
        # QorCap >= 0.3.3: sealed stop checkpoint countersigned within ~150 ms
        "v033_sealed_stop_tail": (receipt(chain(tail_delay_ms=150)), REF.pubkey_hex),
        # recorder took > max_gap to emit its genesis checkpoint (live-match-125430 cp[0])
        "slow_genesis": (receipt(chain(first_delay_ms=10_500)), REF.pubkey_hex),
        # Finalize Last Match without live countersignatures: stamped with QorCap's t_ms
        "finalize_offline": (receipt(chain(offline=True)), REF.pubkey_hex),
        # v0.3.11: three pause-capable challenges (90 s windows) answered and re-run by the recorder at finalize
        "v0311_challenges_answered": (receipt(chain(), chal=challenges()), REF.pubkey_hex),
        # QorCap <= 0.3.10 bug (match 4, challenge 44b8d01f): presses matched but result stayed null
        "v0310_challenge_result_null": (receipt(chain(), chal=challenges(unanswered=("kickoff",))), REF.pubkey_hex),
        # QorCap <= 0.3.10 window (20 s) still verifies
        "v0310_legacy_20s_window": (receipt(chain(), chal=challenges(window_ms=20_000)), REF.pubkey_hex),
        # a window longer than max_window_ms is not a timed challenge
        "v0311_challenge_window_too_long": (receipt(chain(), chal=challenges(window_ms=600_000)), REF.pubkey_hex),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (rec, pub) in cases().items():
        v = R.verify_receipt(rec, referee_pub=pub)
        doc = {"name": name, "referee_pub": pub, "expect": {"status": v["status"], "errors": v["errors"], "partial": v["partial"]},
               "receipt": rec}
        (OUT / f"{name}.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{name:32s} {v['status']:14s} {v['errors'][:4]}")


if __name__ == "__main__":
    main()
