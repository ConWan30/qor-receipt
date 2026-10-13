"""QorTime CLI (commands: `qortime` or `qor-receipt`).

  qor-receipt verify RECEIPT.json [--referee-pub HEX]     exit 0 VERIFIED, 1 PARTIAL, 2 FAIL
  qor-receipt emit --demo OUT.json [--mode dual|single]   build a fully synthetic signed receipt
  qor-receipt pattern NONCE                               show the button pattern for a nonce
  qor-receipt agent --device "USB3.0 Video" --referee URL  live capture agent (Windows, capture card + DualSense USB)
  qor-receipt agent --replay FRAMES_DIR --hid-log FILE    offline replay through an in-process referee
  qor-receipt agent-fixture OUT_DIR                       write a synthetic replay session
"""
from __future__ import annotations

import argparse
import json
import sys

from qor_receipt.challenge import pattern_for
from qor_receipt.receipt import FAIL, PARTIAL, verify_receipt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="qortime", description="QorTime: signed, verifiable console match results")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("receipt")
    v.add_argument("--referee-pub")
    e = sub.add_parser("emit")
    e.add_argument("--demo", required=True, metavar="OUT")
    e.add_argument("--mode", choices=["dual", "single"], default="dual")
    p = sub.add_parser("pattern")
    p.add_argument("nonce")
    g = sub.add_parser("agent", help="capture agent (live or --replay)")
    g.add_argument("--device", default="USB3.0 Video", help="DirectShow capture device name (or index)")
    g.add_argument("--referee", help="referee base URL (live)")
    g.add_argument("--role", choices=["home", "away"], default="home")
    g.add_argument("--player-id", default="local:player")
    g.add_argument("--match-id", help="join an existing match (player pubkey already registered)")
    g.add_argument("--key", help="player key file (default ~/.qor-receipt/player.key; replay: ephemeral)")
    g.add_argument("--out", help="receipt JSON path")
    g.add_argument("--obs-text", help="also write the challenge prompt to this text file (OBS Text source)")
    g.add_argument("--window", action="store_true", help="show the prompt in a small always-on-top window")
    g.add_argument("--pause-config", help="JSON overriding pause-detector thresholds")
    g.add_argument("--cp-interval", type=float, default=4.0, help="checkpoint interval seconds")
    g.add_argument("--replay", metavar="FRAMES_DIR")
    g.add_argument("--hid-log", metavar="FILE")
    g.add_argument("--print-pubkey", action="store_true")
    fx = sub.add_parser("agent-fixture")
    fx.add_argument("out")
    a = ap.parse_args(argv)
    if a.cmd == "agent":
        return _agent(a)
    if a.cmd == "agent-fixture":
        from qor_receipt.agent.fixture import make_session

        s = make_session(a.out)
        print(json.dumps({"frames_dir": s["frames_dir"], "hid_log": s["hid_log"]}))
        return 0
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


def _agent(a) -> int:
    from pathlib import Path

    from qor_receipt.agent.pause_detect import PauseThresholds
    from qor_receipt.agent.runner import AgentConfig

    cfg = AgentConfig(role=a.role, player_id=a.player_id, cp_interval_ms=int(a.cp_interval * 1000),
                      pause=PauseThresholds.load(a.pause_config))
    key = None
    if a.key or not a.replay:
        from qor_receipt.keys import FileKey

        key = FileKey(Path(a.key) if a.key else Path.home() / ".qor-receipt" / "player.key")
    if a.print_pubkey:
        print(key.pubkey_hex if key else "replay uses an ephemeral key")
        return 0
    if a.replay:
        from qor_receipt.agent.runner import run_replay

        if not a.hid_log:
            print("--replay needs --hid-log", file=sys.stderr)
            return 2
        r = run_replay(a.replay, a.hid_log, cfg=cfg, key=key, quiet=False)
        out = a.out or "replay-receipt.json"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(r["receipt"], fh, indent=2)
        print(json.dumps({"wrote": out, "referee_pub": r["referee_pub"], "verify": r["verify"], "stats": r["stats"]}))
        return {FAIL: 2, PARTIAL: 1}.get(r["verify"]["status"], 0)
    if not a.referee:
        print("live mode needs --referee URL (or use --replay)", file=sys.stderr)
        return 2
    from qor_receipt.agent.runner import run_live

    out = Path(a.out or f"qor-receipt-{__import__('time').strftime('%Y%m%d-%H%M%S')}.json")
    r = run_live(device=a.device, referee=a.referee, cfg=cfg, out=out, key=key, match_id=a.match_id,
                 obs_text=a.obs_text, window=a.window)
    if r is None:
        return 1
    return {FAIL: 2, PARTIAL: 1}.get(r["verify"]["status"], 0)


if __name__ == "__main__":
    sys.exit(main())
