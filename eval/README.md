# Reader eval

`labels/frames-2026-10-13.jsonl`: hand labels for 236 Madden NFL 27 capture frames (640x360) from the
2026-10-13 live session (ATL @ CIN): the 233 frames in `/workspace/qrs/` plus `qr_3455.jpg`, `qr_4894.jpg`
and `live-qr-frame.jpg`. The images are **not** committed (21 MB, and the pause/system-overlay frames show
account UI); each row names the file (relative to `--root`) and its sha256.

Labels were made by viewing every frame (zoomed scorebug strips + thumbnails), not from the reader.

| field | meaning |
|---|---|
| `away`, `home` | score in the bottom scorebug strip (left = away = ATL, right = home = CIN); `null` when the strip is absent or its score panel is covered by a banner |
| `quarter`, `clock` | as shown in the strip (`null` = not shown) |
| `clock_red` | game clock digits drawn red (Madden: final ~30 s of a half) |
| `scorebug_visible` | the standard strip is on screen (`"unsure"` = animating in/out). Broadcast team-scoreboard overlays count as `false` |
| `pause_menu` | Madden pause menu (incl. with a PlayStation system overlay) |
| `final_screen` | end-of-game screen (none in this set; the halftime stats screens are `false`) |

Stats: 236 frames; strip visible 162 / absent 71 / unsure 3; score legible 146; pause 35; red clock 16
(Q2 0:27 to 0:09); final screens 0; quarters Q1 93, Q2 68, Q3 1.

Run: `python eval/score_reader.py --root /workspace [--show-wrong] [--json]`
