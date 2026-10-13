"""qor-receipt CLI.

  qor-receipt verify RECEIPT.json [--referee-pub HEX]     exit 0 VERIFIED, 1 PARTIAL, 2 FAIL
  qor-receipt emit --demo OUT.json [--mode dual|single]   build a fully synthetic signed receipt
  qor-receipt pattern NONCE                               show the button pattern for a nonce
"""
from __future__ import annotations

import argparse
import json
import sys

from qor_receipt.challenge import pattern_for
from qor_receipt.receipt import FAIL, PARTIAL, verify_receipt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="qor-receipt")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("receipt")
    v.add_argument("--referee-pub")
    e = sub.add_parser("emit")
    e.add_argument("--demo", required=True, metavar="OUT")
    e.add_argument("--mode", choices=["dual", "single"], default="dual")
    p = sub.add_parser("pattern")
    p.add_argument("nonce")
    a = ap.parse_args(argv)
    if a.cmd == "verify":
        with open(a.receipt, encoding="utf-8") as fh:
            out = verify_receipt(json.load(fh), referee_pub=a.referee_pub)
        print(json.dumps(out, indent=2))
        return {FAIL: 2, PARTIAL: 1}.get(out["status"], 0)
    if a.cmd == "emit":
        from qor_receipt.demo import demo_receipt

        rec, ref_pub = demo_receipt(a.mode)
        with open(a.demo, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=2)
        print(json.dumps({"wrote": a.demo, "referee_pub": ref_pub}))
        return 0
    print(" ".join(pattern_for(a.nonce)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
