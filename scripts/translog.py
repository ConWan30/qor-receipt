#!/usr/bin/env python3
"""Transparency-log tools for the official referee service (stdlib + qor_receipt).

  python scripts/translog.py anchor  [--url URL]      verify the live signed head + chain continuity since the last
                                                     anchor, then append it to transparency/anchors.jsonl
  python scripts/translog.py inclusion RECEIPT.json [--url URL]
                                                     check that a receipt was logged (and the path to the head verifies)

`anchor` exits non-zero (and writes nothing) if the head signature, the signer's delegation, or continuity with the
last anchored head fails — e.g. the log was rewound or rewritten. A scheduled GitHub Action runs it and commits the
new anchor, so the qortime git history is the external witness of the log head.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from qor_receipt import delegation as D  # noqa: E402
from qor_receipt import translog as T  # noqa: E402
from qor_receipt.trusted_referees import load_allowlist, lookup  # noqa: E402

URL = "https://qortime-referee-production.up.railway.app"
ANCHORS = ROOT_DIR / "transparency" / "anchors.jsonl"


def get(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "qortime-anchor"}), timeout=60) as r:
        return json.loads(r.read())


def signer_ok(info: dict, pub: str) -> str | None:
    ent = lookup(pub)
    if ent and ent.get("status") == "active":
        return None
    cert = info.get("delegation")
    now = int(time.time() * 1000)
    ok, probs = D.check_delegation(cert, pub, now, allowlist=load_allowlist(), revocations=D.load_revocations(), now_ms=now)
    return None if ok else f"log signer not trusted: {probs}"


def fetch_entries(base: str, start: int, end: int) -> list[dict]:
    out: list[dict] = []
    while start + len(out) < end:
        page = get(f"{base}/v1/log?start={start + len(out)}&limit=2000")["entries"]
        if not page:
            break
        out += page
    return out[: end - start]


def cmd_anchor(base: str) -> int:
    info = get(f"{base}/v1/referee")
    head = get(f"{base}/v1/log/head")
    pub = head.get("pubkey") or info.get("pubkey")
    if pub != info.get("pubkey") or not T.verify_head(head, pub):
        print("FAIL: log head signature invalid")
        return 1
    why = signer_ok(info, pub)
    if why:
        print("FAIL:", why)
        return 1
    last = None
    if ANCHORS.exists():
        lines = [ln for ln in ANCHORS.read_text().splitlines() if ln.strip()]
        last = json.loads(lines[-1]) if lines else None
    size = int(head["size"])
    if last and size < int(last["size"]):
        print(f"FAIL: log rewound: anchored size {last['size']} > live size {size}")
        return 1
    start = max(0, int(last["size"]) - 1) if last and int(last["size"]) > 0 else 0
    entries = fetch_entries(base, start, size)
    if len(entries) != size - start:
        print("FAIL: could not fetch all log entries")
        return 1
    if entries:
        errs = T.verify_entries(entries, start_prev=entries[0].get("prev"), start_seq=start)
        if start == 0 and entries[0].get("prev") != T.GENESIS:
            errs.append("genesis")
        if last and int(last["size"]) > 0 and entries[0].get("hash") != last["head"]:
            errs.append("anchored head no longer in the log (history rewritten)")
        if entries[-1]["hash"] != head["head"]:
            errs.append("head mismatch")
        if errs:
            print("FAIL:", errs[:5])
            return 1
    elif size == 0 and head["head"] != T.GENESIS:
        print("FAIL: empty log with non-genesis head")
        return 1
    if last and last["size"] == size and last["head"] == head["head"]:
        print(f"unchanged: size {size} head {head['head'][:16]}")
        return 0
    row = {"anchored_ms": int(time.time() * 1000), "url": base, "size": size, "head": head["head"],
           "head_ts_ms": head.get("ts_ms"), "signer_key_id": head.get("key_id"),
           "delegation_serial": head.get("delegation_serial"), "head_sig": head.get("sig")}
    ANCHORS.parent.mkdir(parents=True, exist_ok=True)
    with open(ANCHORS, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(f"anchored: size {size} head {head['head'][:16]}")
    return 0


def cmd_inclusion(base: str, receipt: str) -> int:
    r = json.loads(Path(receipt).read_text(encoding="utf-8"))
    bh = T.body_sha256(r)
    try:
        p = get(f"{base}/v1/log/inclusion/{bh}")
    except Exception as e:
        print(f"NOT LOGGED (or log unreachable): {e}")
        return 1
    errs = T.verify_inclusion(bh, p["entries"], p["head"])
    if not T.verify_head(p["head"], p["head"].get("pubkey", "")):
        errs.append("bad head signature")
    print("INCLUDED" if not errs else f"FAIL {errs}", f"seq={p.get('seq')} body_sha256={bh}")
    return 0 if not errs else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["anchor", "inclusion"])
    ap.add_argument("receipt", nargs="?")
    ap.add_argument("--url", default=URL)
    a = ap.parse_args()
    base = a.url.rstrip("/")
    return cmd_anchor(base) if a.cmd == "anchor" else cmd_inclusion(base, a.receipt)


if __name__ == "__main__":
    sys.exit(main())
