"""Play-by-play match index + burst frames + "what the AI saw" viewer for a QorCap bundle.

`qortime index BUNDLE [--video V] [--out DIR]` -> DIR/match_index.json, DIR/events/<id>/frame_XX.jpg,
DIR/events/<id>/contact.jpg, DIR/viewer.html. Inspired by claude-eyes (ffmpeg contact sheets, cut detection,
burst frames around moments, cached index, viewer HTML). Everything is derived from bundle files; a field the
bundle does not carry is emitted as null and listed in ``missing``. Controller inputs and haptics are advisory
evidence, never proof.
"""
from qor_receipt.match_index.build import build_index  # noqa: F401
