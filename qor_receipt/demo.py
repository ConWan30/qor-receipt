"""Synthetic end-to-end match through the real referee service (used by CLI emit, tests, CI)."""
from __future__ import annotations

from typing import Any

from qor_receipt.checkpoints import Chain, hid_events_hash, window_hash
from qor_receipt.jcs import canonicalize, sha256_hex
from qor_receipt.keys import SoftwareKey
from qor_receipt.receipt import signing_bytes


class FakeClock:
    def __init__(self, t: int = 1_790_000_000_000) -> None:
        self.t = t

    def __call__(self) -> int:
        return self.t


def _signed(key: SoftwareKey, role: str, payload: dict) -> dict:
    return {"role": role, "payload": payload, "sig": key.sign(canonicalize(payload))}


def perform(pattern: list[str], t0: int, step: int = 300) -> list[dict[str, Any]]:
    ev = []
    for i, b in enumerate(pattern):
        ev += [{"t_ms": t0 + i * step, "button": b, "down": True},
               {"t_ms": t0 + i * step + 80, "button": b, "down": False}]
    return ev


def timeline(t0: int, final=(24, 17)) -> list[dict[str, Any]]:
    h, a = final
    pts = [(0, 0, 1, 900), (7, 0, 1, 400), (7, 7, 2, 300), (14, 10, 3, 600), (h - 3, a, 4, 200), (h, a, 4, 30)]
    reads, t = [], t0
    for hh, aa, q, c in pts:
        reads.append({"t_ms": t, "home": hh, "away": aa, "quarter": q, "clock_s": c}); t += 60_000
    for i in range(4):
        reads.append({"t_ms": t + i * 1500, "home": h, "away": a, "quarter": 4, "clock_s": 0})
    return reads


def run_match(client, clock: FakeClock, mode: str = "dual", *, pattern_override=None, skip_phase=None, away_final=None):
    roles = ["home", "away"] if mode == "dual" else ["home"]
    keys = {r: SoftwareKey() for r in roles}
    r = client.post("/matches", json={"players": [{"role": ro, "player_id": f"psn:{ro}_player",
                                                    "pubkey": keys[ro].pubkey_hex} for ro in roles]}).json()
    mid = r["match_id"]
    chains = {ro: Chain() for ro in roles}
    t_start = clock.t
    tl = timeline(t_start + 10_000)
    end_t = tl[-1]["t_ms"]
    phase_at = {"kickoff": t_start + 1_000, "halftime": t_start + 150_000, "end": end_t - 3_000}
    cps_t = list(range(t_start + 4_000, end_t + 4_000, 4_000))
    events = sorted([(t, "cp", None) for t in cps_t] + [(t, "ch", p) for p, t in phase_at.items()])
    for t, kind, phase in events:
        clock.t = t
        for ro in roles:
            if kind == "cp":
                hid = [{"t_ms": t - 100, "button": "R2", "down": True}]
                cp = chains[ro].next(t, window_hash([f"frame{t}-{i}".encode() for i in range(3)]), hid_events_hash(hid))
                assert client.post(f"/matches/{mid}/checkpoints", json=_signed(keys[ro], ro, cp)).status_code == 200
            elif phase != skip_phase:
                c = client.post(f"/matches/{mid}/challenges", json={"phase": phase, "role": ro}).json()
                pat = pattern_override or c["pattern"]
                ev = perform(pat, t + 1_500)
                client.post(f"/matches/{mid}/challenges/{c['id']}/response",
                            json=_signed(keys[ro], ro, {"hid_events": ev, "pause_seen_ms": t + 1_500 + 120}))
    clock.t = end_t + 1_000
    energy = [((i * 7) % 11) * 10 for i in range(40)]
    for ro in roles:
        client.post(f"/matches/{mid}/timeline", json=_signed(keys[ro], ro, {
            "reads": timeline(t_start + 10_000, away_final) if (ro == "away" and away_final) else tl, "input_energy": energy, "video_motion": [0] + energy[:-1]}))
    fin = client.post(f"/matches/{mid}/finalize")
    if fin.status_code != 200:
        return fin, keys
    rec = fin.json()
    for ro in roles:
        body_hash = sha256_hex({k: v for k, v in rec.items() if k != "signatures"})
        client.post(f"/matches/{mid}/sign", json=_signed(keys[ro], ro, {
            "receipt_sha256": body_hash, "sig": keys[ro].sign(signing_bytes(rec))}))
    return client.get(f"/matches/{mid}/receipt").json(), keys


def demo_receipt(mode: str = "dual"):
    from fastapi.testclient import TestClient

    from qor_receipt.referee.app import create_app

    clock, ref = FakeClock(), SoftwareKey()
    rec, _ = run_match(TestClient(create_app(ref, clock)), clock, mode)
    return rec, ref.pubkey_hex
