# Capture agent (`qor_receipt/agent/`)

Feeds real data to the referee from a Windows PC: PS5 -> HDMI -> "USB3.0 Video" capture card
(DirectShow, read at 640x360) and the DualSense Edge on USB to the PC (it stays on Bluetooth to the PS5).

| module | role |
| --- | --- |
| `capture.py` | DirectShow device by name (pygrabber), CAP_DSHOW, 1-frame buffer, resize to 640x360, frame-size reset guard, per-frame sha256 |
| `hid.py` | hidapi DualSense/Edge (054C:0CE6/0DF2) USB 0x01 parsing; every button edge kept, sticks decimated to `QOR_HID_ANALOG_HZ` (default 125, same as Qoresence); monotonic-derived timestamps |
| `pause_detect.py` | pause-menu heuristic (dim outside menu region + bright menu panel) or template NCC; thresholds via `--pause-config JSON`. **NEEDS TUNING** |
| `runner.py` | match start, kickoff/halftime(Q3)/end(FINAL) challenges, 4 s rolling checkpoints, scorebug reads ~5/s with agreement, timeline + motion bins, finalize, co-sign, receipt |

Live (on the Windows laptop):

    scripts\qor-agent.cmd http://REFEREE:8000 --window
    # = qortime agent --device "USB3.0 Video" --referee http://REFEREE:8000 --window

Each live run writes `qor-session-*/hid.jsonl` (button/stick events, challenge nonces) and
`qor-session-*/frames/<t_ms>.jpg` (all frames around challenges, ~5/s otherwise) so the match can be replayed:

    qortime agent --replay qor-session-X/frames --hid-log qor-session-X/hid.jsonl

Replay runs the same core against an in-process referee on a virtual clock, re-using the recorded
nonces (proves the pipeline, not freshness). `qortime agent-fixture DIR` writes a synthetic session.

Known live unknowns: pause-menu thresholds/template; real end-of-game scorebug (Madden may turn the
final clock red -> reader `clock_red`, or cut away before 3 agreed 0:00 reads); pattern buttons are
pressed inside the pause menu (CROSS/CIRCLE may select/resume — check what the menu does).
