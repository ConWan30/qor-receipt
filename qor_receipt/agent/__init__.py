"""Windows capture agent: capture card + DualSense USB HID -> referee checkpoints, challenges, receipt.

Live:   qor-receipt agent --device "USB3.0 Video" --referee URL
Replay: qor-receipt agent --replay FRAMES_DIR --hid-log HID.jsonl   (no hardware, in-process referee)
"""
