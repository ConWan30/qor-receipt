# QorTime

**Signed, verified match-result receipts for console money-match / escrow platforms**
(Locked, TrustBridge, ArcadeBet, Wagr, and others). First title: **Madden 27 on PS5**.

QorTime (formerly qor-receipt) seals the moment a match ends. Site: https://conwan30.github.io/qortime/ · Verifier: https://conwan30.github.io/qortime/verify/ . The Python package stays `qor_receipt`; both `qortime` and `qor-receipt` commands work.

A platform holding escrow gets a small JSON receipt saying *who played, the final score, how it
was observed*, signed by each player's agent and countersigned by an independent **referee**
service. Anyone can check it offline (CLI) or in a browser ([verifier page](https://conwan30.github.io/qortime/verify/)).
The verdict is **VERIFIED / PARTIAL / FAIL** plus a trust tier: **dual-attested** (both players ran
agents and their timelines agree) or **single-attested**.

## Rig (per player)

```
PS5 --HDMI--> USB capture card --> Windows PC (qor agent: scorebug reader + HID log)
DualSense Edge --Bluetooth--> PS5      (plays the game)
DualSense Edge --USB-------> PC        (same controller, also cabled to the PC: the agent logs its HID reports)
```

Both links are intended. The USB link is what lets the agent prove the inputs, so don't unplug it.

## How a receipt is made trustworthy

| Weak spot | Mitigation in v0 |
|---|---|
| Replay / wrong console / pre-recorded video | Referee **nonce challenges** at kickoff, halftime and end. The nonce maps to `OPTIONS` + 4 buttons. Verifier needs the pattern in the HID log **and** the pause menu on video 0–600 ms after the press. Input-to-video **motion correlation** is an extra signal. |
| Signing key sits on the player's PC | Agent sends **rolling hash checkpoints** (frames + input) every ~4 s. The referee timestamps and countersigns each one. A receipt fabricated afterwards can't carry referee signatures from the past. Keys go through `KeyProvider`, so a TPM can drop in later ([PHASE2](docs/PHASE2.md)). |
| Fake video source | The same live challenge, plus **dual mode**: both agents submit, and score/quarter timelines must agree. Single-agent matches get a lower tier. |

Residual risks are written down plainly in [docs/RISKS.md](docs/RISKS.md) (account sharing, both players colluding, and others).

## Layout

```
spec/match-receipt-v0.md      the format and verification rules
schema/receipt.v0.json        JSON Schema (JCS canonical, Ed25519)
qor_receipt/receipt.py        build / sign / verify
qor_receipt/challenge.py      nonce -> button pattern, HID + vision verifier, motion correlation
qor_receipt/checkpoints.py    hash chain + referee countersign
qor_receipt/referee/app.py    FastAPI referee: start, challenge, checkpoint, timeline, finalize, sign
qor_receipt/final_detect.py   end-of-game from agreed scorebug reads; dual timeline agreement
qor_receipt/cli.py            qortime emit | verify | pattern
qor_receipt/_vendor/          vendored Qoresence code (scorebug reader/agreement, signing, chain), see VENDORED.md
verify/                       static verifier (index.html + verify.js + vendored @noble/ed25519)
```

## Quick start

```bash
pip install -e ".[dev,vision]"
pytest -q && node verify/test.mjs
qortime emit --demo r.json            # synthetic match through the real referee code
qortime verify r.json --referee-pub <hex>
QOR_REFEREE_SEED=<64 hex> uvicorn qor_receipt.referee.app:app   # run a referee
```

## Status (v0 foundation)

**Real:** receipt format, JCS (Python and JS parity vector), Ed25519 signing and verification (Python
and browser), the checkpoint chain with referee countersign, the challenge pattern and HID/latency verifier,
motion correlation, final detection, dual timeline agreement, the referee API (in-memory), the CLI, the
static verifier, and the tests and CI.

**Not yet built:** the Windows capture agent, i.e. live capture-card frames → vendored `ScorebugReader`,
DualSense HID reader, and pause-menu detector. Today tests feed synthetic HID logs, frames and timelines.
The referee also has no persistence or auth beyond player signatures, there's no TPM yet, and there's no
platform integration.

## Credits

Vendored from [ConWan30/Qoresence](https://github.com/ConWan30/Qoresence) (MIT). Design ideas (certificate
shape, PORT-CERT VERIFIED/PARTIAL/FAIL honesty, clock-notary io ledger) come from
[ConWan30/QorTroller](https://github.com/ConWan30/QorTroller) at pinned commit `51d1ab3`. It's referenced, not depended on.
MIT licensed.
