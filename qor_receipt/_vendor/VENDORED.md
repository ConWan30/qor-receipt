# Vendored code (MIT, (c) ConWan30 / Qoresence)

Copied verbatim from https://github.com/ConWan30/Qoresence at commit `a062c9df7d6fb96c3a2e14821e229092e76c2d1c`
(only change: `qoresence.` imports rewritten to `qor_receipt._vendor.qoresence.`).

| file | upstream path |
|---|---|
| truth/signing.py | qoresence/truth/signing.py (Ed25519 file key) |
| truth/chain.py, truth/model.py | qoresence/truth/chain.py (+ its model dependency) |
| sync/digit_integrity.py | qoresence/sync/digit_integrity.py (agreement dependency) |
| vision/local_scorebug/{reader,agreement,profile,glyphs}.py + packs/ | qoresence/vision/local_scorebug/ (profile MADDEN27_STANDARD) |

Ideas/schema (not code) borrowed from https://github.com/ConWan30/QorTroller pinned at
`51d1ab3ff9fc31503ba556149cb69281971d68fa`: scripts/match_certificate.py (certificate shape),
l9_presence/port_cert.py (VERIFIED/PARTIAL/FAIL honesty: missing evidence => PARTIAL, never a false VERIFIED),
compose/clock-notary io_ledger (rolling, countersigned ledger). QorTroller is NOT a dependency.

Vision modules need the `[vision]` extra (numpy, opencv-python-headless).

## Local changes
- 2026-10-13 `local_scorebug/reader.py`: game clock is best-effort (a blank/red clock no longer
  blanks the whole read; score + quarter gates unchanged). Live Madden 27 capture got a sure clock on
  ~10% of frames, so the agreement window never reached 3-in-a-row. Upstream this to Qoresence.
- 2026-10-13 `local_scorebug/reader.py`: red final-seconds clock is read (`read_red_clock=True` default) from
  contrast-stretched luma instead of HSV value, same clock glyph gates; `FrameRead.clock_red` flag added.
  On the 16 labelled red frames: 5 read correctly, 11 refused, 0 wrong. Upstream this to Qoresence.
