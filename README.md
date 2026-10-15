# QorTime

**Signed, verified match-result receipts for console money-match / escrow platforms**
(Locked, TrustBridge, ArcadeBet, Wagr, and others). First title: **Madden 27 on PS5**.

QorTime (formerly qor-receipt) seals the moment a match ends. Site: https://conwan30.github.io/qortime/ · Verifier: https://conwan30.github.io/qortime/verify/ . The Python package stays `qor_receipt`; both `qortime` and `qor-receipt` commands work.

**Verified Match is early access, Stripe test mode.** $9.99 USD, one-time, for a hosted referee countersignature after a completed match. Business KYC is not done, so this is not a live paid product. Test cards only. Pricing: https://conwan30.github.io/qortime/pricing/ · How it works: https://conwan30.github.io/qortime/how-it-works/ · Details: [docs/STRIPE_VERIFIED_MATCH_TEST.md](docs/STRIPE_VERIFIED_MATCH_TEST.md).

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

## Status

**Real:** receipt format, JCS (Python and JS parity vector), Ed25519 signing and verification (Python
and browser), the checkpoint chain with referee countersign, the challenge pattern and HID/latency verifier,
motion correlation, final detection, dual timeline agreement, the referee API (in-memory), the CLI, the
static verifier, and the tests and CI.

**Windows capture agent: built** (PR #1, `scripts/qor-agent.cmd`): DualSense HID capture, a scorebug
sampler that requires 3-read agreement, and nonce challenges. The reader fix landed in PR #2; in a live
Madden test, of 233 sampled frames 143 were read correctly, 90 were refused, and 0 were read wrong.

**Live:** site at <https://conwan30.github.io/qortime/>, verifier at <https://conwan30.github.io/qortime/verify/>, pricing at <https://conwan30.github.io/qortime/pricing/>, how Verified Match works at <https://conwan30.github.io/qortime/how-it-works/>. Verified Match checkout is Stripe **test mode** until KYC.

**Known gaps:**
- End-of-game: red final-seconds clock is now read (fail-closed), with `scorebug_gone` / `final_screen` fallbacks
  and `result.final_basis`. A Jev final-screen detector runs in shadow mode only (see above). Not yet validated on a real Q4 ending (none captured). Reader eval: `eval/`.
- Halftime challenge and real pause timing are not yet confirmed in a full live match.
- The referee is in-memory only (no persistence or auth beyond player signatures).
- Phase 2 items (TPM, transparency log) are not started ([PHASE2](docs/PHASE2.md)). No platform integration.

## Jev final-screen detector (shadow mode)

`qor_receipt/agent/final_screen_jev.py` asks TypeSafe Jev 1.14 (model pinned to `jev-1.14.0`; `jev-latest`
is still 1.13) one yes/no (Noul) question while the scorebug is absent: is this Madden's
**post-game options menu with the final score**? Per the player (Madden 27): as soon as the game clock hits
0:00, Madden goes straight to an end-of-game options menu (a list of menu options) that shows the final score
(both team names and scores); the bottom scorebug bar is not shown. That menu is the final frame. The criteria
explicitly reject the mid-game pause menu (the most likely false positive: it looks similar, so the criteria
key on FINAL/post-game wording and a 4th-quarter 0:00 clock), halftime screens, touchdown/score graphics and
broadcast scoreboard overlays. It runs in a background worker (at most one call per 5 s, 15 s timeout; a busy worker
or the rate limit drops the frame), resizes frames to the image limits, and treats any error as `unknown`.

**Shadow only.** Results go to the session log as `jev_final_screen` records (model, frame sha256, answer,
p_yes, confidence, latency, plus `last_scorebug` = the last agreed scorebug `{t_ms, quarter, clock_s}` when
one exists, so a later deterministic rule can require "last clock seen was Q4 near 0:00" + "menu with final
score"). They are wired through `AgentCore.final_screen_shadow`, whose return value is
ignored, so they never open a `final_screen` span, never set `final_basis`, and never feed the trust tier,
the score digits, or the signed receipt.

- `QORTIME_JEV_FINAL_SCREEN=shadow|off`. When it is unset: shadow if `TYPESAFE_API_KEY` is set, otherwise off.
- `export_labels(session_log, out)` turns shadow records into weak labels for a future local detector.
- Live smoke: `python scripts/jev_final_screen_smoke.py --root /workspace` (needs the key and the eval images).
  First run (2026-10-14, 13 real negatives: gameplay, absent strip, pause, touchdown, broadcast overlay,
  halftime report/stats): 0 false yes, p_yes 0.01-0.02, median latency about 200 ms. Re-run 2026-10-15 with
the post-game-options-menu criteria on the same 13 frames: 0 false yes; both pause-menu frames `no`
(p_yes 0.02); all others p_yes 0.01-0.02; median 194 ms. **No real final screen has
  been captured yet**, so recall is untested.

## Credits

Vendored from [ConWan30/Qoresence](https://github.com/ConWan30/Qoresence) (MIT). Design ideas (certificate
shape, PORT-CERT VERIFIED/PARTIAL/FAIL honesty, clock-notary io ledger) come from
[ConWan30/QorTroller](https://github.com/ConWan30/QorTroller) at pinned commit `51d1ab3`. It's referenced, not depended on.
MIT licensed.
