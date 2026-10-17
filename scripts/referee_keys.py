#!/usr/bin/env python3
"""Operator-box tool for the official referee key hierarchy. Run ONLY on the operator box (where the root key lives).

  python scripts/referee_keys.py delegate --root /workspace/qortime-keys/qortime-referee-v1.key \
        --keys-dir /workspace/qortime-keys/online --days 60
      -> new online service key (0600, never printed) + root-signed delegation cert
         (cert published at trust/delegations/serial-<N>.json; env bundle for Railway written next to the key)
  python scripts/referee_keys.py revoke --root ... --serial N --reason "..."
      -> appends to the root-signed revocation list (qor_receipt/data + verify/ twins), bumps its version
  python scripts/referee_keys.py resign-revocations --root ...     (re-sign the current list, e.g. first run)
  python scripts/referee_keys.py sync-js                           (regenerate verify/*.inc.js twins)

Rotation = `delegate` (new serial) -> set Railway QORTIME_SERVICE_SEED / QORTIME_DELEGATION from the files it wrote
(substituted at call time, never echoed) -> redeploy -> check /health -> optionally `revoke` the previous serial
(revoke only on compromise: revoking makes that serial's receipts SELF_ATTESTED; plain expiry keeps them valid).
The root seed is never printed, never sent anywhere, and never set on Railway.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from qor_receipt import delegation as D  # noqa: E402
from qor_receipt.keys import FileKey, SoftwareKey  # noqa: E402
from qor_receipt.trusted_referees import load_allowlist  # noqa: E402

DAY = 86_400_000
REVS = [ROOT_DIR / "qor_receipt" / "data" / "referee_revocations.json", ROOT_DIR / "verify" / "referee_revocations.json"]
ALLOW = [ROOT_DIR / "qor_receipt" / "data" / "trusted_referees.json", ROOT_DIR / "verify" / "trusted_referees.json"]


def root_key(path: str):
    k = FileKey(Path(path))
    allow = load_allowlist()
    row = next((r for r in allow["referees"] if r["pubkey"].lower() == k.pubkey_hex), None)
    if not row or not row.get("can_delegate") or row.get("status") != "active":
        raise SystemExit("that key is not an active can_delegate root on the built-in allowlist")
    return k, row["id"]


def write_json(paths, obj) -> None:
    for p in paths:
        p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def sync_js() -> None:
    for src, const in ((ROOT_DIR / "verify" / "trusted_referees.json", "TRUSTED_REFEREES"),
                       (ROOT_DIR / "verify" / "referee_revocations.json", "REFEREE_REVOCATIONS")):
        obj = json.loads(src.read_text(encoding="utf-8"))
        src.with_suffix(".inc.js").write_text(f"// Auto-generated from {src.name} — do not edit by hand.\n"
                                              f"export const {const} = {json.dumps(obj, indent=2)};\n", encoding="utf-8")


def next_serial() -> int:
    used = [int(p.stem.split("-")[1]) for p in (ROOT_DIR / "trust" / "delegations").glob("serial-*.json")]
    return max(used, default=0) + 1


def cmd_delegate(a) -> None:
    root, root_id = root_key(a.root)
    serial = a.serial or next_serial()
    kd = Path(a.keys_dir)
    kd.mkdir(parents=True, exist_ok=True, mode=0o700)
    seed_path = kd / f"qortime-online-s{serial}.seed"
    if seed_path.exists():
        raise SystemExit(f"{seed_path} exists; refusing to overwrite (never reuse a serial)")
    svc = SoftwareKey()
    fd = os.open(seed_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:  # bare hex, no newline (substituted verbatim into the Railway variable)
        f.write(svc._priv.private_bytes_raw().hex())
    now = int(time.time() * 1000)
    cert = D.make_delegation(root, root_id=root_id, delegate_pubkey=svc.pubkey_hex,
                             delegate_id=f"qortime-online-s{serial}", serial=serial, not_before_ms=now - 60_000,
                             not_after_ms=now + a.days * DAY, issued_ms=now)
    pub = ROOT_DIR / "trust" / "delegations" / f"serial-{serial}.json"
    pub.write_text(json.dumps(cert, indent=2) + "\n", encoding="utf-8")
    (kd / f"qortime-online-s{serial}.delegation.json").write_text(json.dumps(cert, separators=(",", ":")),
                                                                    encoding="utf-8")
    print(json.dumps({"serial": serial, "delegate_pubkey": svc.pubkey_hex, "delegate_key_id": svc.key_id,
                      "not_after_ms": cert["not_after_ms"], "seed_file": str(seed_path),
                      "cert_file": str(pub)}, indent=2))


def cmd_revoke(a, extra=True) -> None:
    root, root_id = root_key(a.root)
    cur = json.loads(REVS[0].read_text()) if REVS[0].exists() else {"version": 0, "revoked": []}
    revoked = list(cur.get("revoked") or [])
    if extra:
        cert = json.loads((ROOT_DIR / "trust" / "delegations" / f"serial-{a.serial}.json").read_text())
        if any(r.get("serial") == a.serial for r in revoked):
            raise SystemExit(f"serial {a.serial} already revoked")
        revoked.append({"serial": a.serial, "delegate_pubkey": cert["delegate_pubkey"], "reason": a.reason,
                        "revoked_ms": int(time.time() * 1000)})
    doc = D.make_revocations(root, root_id=root_id, version=int(cur.get("version", 0)) + 1, revoked=revoked)
    write_json(REVS, doc)
    sync_js()
    print(f"revocation list v{doc['version']}: {len(revoked)} revoked")


def main() -> None:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    d = sp.add_parser("delegate")
    d.add_argument("--root", required=True)
    d.add_argument("--keys-dir", required=True)
    d.add_argument("--days", type=int, default=60)
    d.add_argument("--serial", type=int)
    r = sp.add_parser("revoke")
    r.add_argument("--root", required=True)
    r.add_argument("--serial", type=int, required=True)
    r.add_argument("--reason", required=True)
    rs = sp.add_parser("resign-revocations")
    rs.add_argument("--root", required=True)
    sp.add_parser("sync-js")
    a = ap.parse_args()
    if a.cmd == "delegate":
        if not 1 <= a.days <= 90:
            raise SystemExit("--days must be 1..90")
        cmd_delegate(a)
    elif a.cmd == "revoke":
        cmd_revoke(a)
    elif a.cmd == "resign-revocations":
        cmd_revoke(a, extra=False)
    else:
        sync_js()


if __name__ == "__main__":
    main()
