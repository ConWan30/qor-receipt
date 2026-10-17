from __future__ import annotations

import base64
import copy
import html
import json
from pathlib import Path


def _embed(out: Path, rel: str | None) -> str | None:
    if not rel:
        return None
    p = out / rel
    if not p.exists():
        return None
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


def write_viewer(idx: dict, out: Path) -> Path:
    data = copy.deepcopy(idx)
    for e in data["events"]:
        for f in e.get("frames") or []:
            f["src"] = _embed(out, f["file"])
        e["contact_src"] = _embed(out, e.get("contact_sheet"))
    blob = json.dumps(data).replace("</", "<\\/")
    title = html.escape(f"QorTime · what the AI saw · {idx.get('match_id') or 'match'}")
    p = out / "viewer.html"
    p.write_text(TEMPLATE.replace("__TITLE__", title).replace("__DATA__", blob), encoding="utf-8")
    return p


TEMPLATE = (Path(__file__).with_name("viewer_template.html")).read_text(encoding="utf-8")
