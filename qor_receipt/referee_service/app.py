"""QorTime official referee service: verified uploads for QorCap bundles.

Run (production, Railway): ``uvicorn qor_receipt.referee_service.app:app --host 0.0.0.0 --port $PORT``

Env:
  QORTIME_REFEREE_SEED      hex Ed25519 seed of the official referee key (secret; never logged or returned)
  QORTIME_REFEREE_ID        allowlist id of that key (default qortime-v1)
  QORCAP_SIDECAR            path to a pinned QorCap sidecar/ (end-of-game + scorebug readers); missing -> fail-closed
  QORTIME_REFEREE_WORKDIR   scratch dir for in-flight submissions (default /tmp/qortime-referee)
  QORTIME_SUBMIT_TOKEN      optional shared secret required on POST /v1/submissions (Bearer); unset = open
  QORTIME_RANDOM_SEGMENTS / QORTIME_TAIL_MS   spot-check policy
  QORTIME_MAX_ACTIVE        max concurrent in-flight submissions (default 4)

Flow (spot-check):
  POST /v1/submissions (zip of bundle meta files)  -> structural re-check, commit, then random plan
  PUT  /v1/submissions/{id}/segments/{seq}          -> raw fMP4 segment bytes, hashed against the committed manifest
  POST /v1/submissions/{id}/review                 -> referee decodes, re-reads final, re-runs challenges, signs
  POST /v1/submissions/{id}/player-signature       -> device key signs the same bytes; receipt complete
Live helpers (optional, during Start Match):
  POST /v1/challenges   signed nonce (+ optional chain-head anchor)     POST /v1/anchors   signed chain-head timestamp
"""
from __future__ import annotations

import hmac
import os
import threading
import time
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from qor_receipt.keys import SoftwareKey
from qor_receipt.review import bundle as B
from qor_receipt.review.engine import FULL, SPOT, Referee
from qor_receipt.review.policy import MAX_META_BYTES, RANDOM_SEGMENTS, TAIL_MS
from qor_receipt.trusted_referees import lookup

VERSION = "0.1.0"


class ChallengeReq(BaseModel):
    phase: str
    role: str = "home"
    match_id: str = ""
    anchor: dict[str, Any] | None = None


class AnchorReq(BaseModel):
    match_id: str = ""
    anchor: dict[str, Any]


class PlayerSig(BaseModel):
    sig: str


def _key_from_env() -> SoftwareKey | None:
    seed = (os.getenv("QORTIME_REFEREE_SEED") or "").strip()
    if not seed:
        return None
    try:
        return SoftwareKey.from_seed_hex(seed)
    except ValueError:
        raise SystemExit("QORTIME_REFEREE_SEED is not a valid hex Ed25519 seed") from None


def create_app(referee: Referee | None = None) -> FastAPI:
    app = FastAPI(title="QorTime official referee", version=VERSION)
    if referee is None:
        key = _key_from_env()
        if key is None:
            raise SystemExit("QORTIME_REFEREE_SEED is not set")
        referee = Referee(key, workdir=os.getenv("QORTIME_REFEREE_WORKDIR", "/tmp/qortime-referee"),
                          random_segments=int(os.getenv("QORTIME_RANDOM_SEGMENTS", RANDOM_SEGMENTS)),
                          tail_ms=int(os.getenv("QORTIME_TAIL_MS", TAIL_MS)),
                          referee_id=os.getenv("QORTIME_REFEREE_ID", "qortime-v1"))
    ref = referee
    submit_token = os.getenv("QORTIME_SUBMIT_TOKEN") or ""
    max_active = int(os.getenv("QORTIME_MAX_ACTIVE", "4"))
    lock = threading.Lock()
    entry = lookup(ref.key.pubkey_hex)
    started = int(time.time())

    def sub_or_404(sid: str, token: str | None):
        s = ref.subs.get(sid)
        if s is None:
            raise HTTPException(404, "no such submission (expired after 1 h, or the service restarted)")
        bearer = (token or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(bearer, s.upload_token):
            raise HTTPException(401, "bad upload token")
        return s

    @app.get("/health")
    def health():
        return {"ok": True, "version": VERSION, "uptime_s": int(time.time()) - started,
                "allowlisted": bool(entry and entry.get("status") == "active")}

    @app.get("/v1/referee")
    def info():
        i = ref.info()
        i["allowlist"] = {"id": entry.get("id"), "status": entry.get("status")} if entry else None
        i["version"] = VERSION
        return i

    @app.post("/v1/challenges")
    def challenge(req: ChallengeReq):
        try:
            return ref.issue_challenge(req.phase, req.role, req.match_id, req.anchor)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/v1/anchors")
    def anchor(req: AnchorReq):
        try:
            return ref.issue_anchor(req.match_id, req.anchor)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/v1/submissions")
    async def submit(request: Request, mode: str = SPOT, authorization: str | None = Header(default=None)):
        if submit_token and not hmac.compare_digest((authorization or "").removeprefix("Bearer ").strip(), submit_token):
            raise HTTPException(401, "submission token required")
        if mode not in (SPOT, FULL):
            raise HTTPException(400, "mode must be spot-check or full")
        ref.expire()
        with lock:
            active = sum(1 for s in ref.subs.values() if s.state in ("awaiting_segments", "awaiting_player_signature"))
        if active >= max_active:
            raise HTTPException(503, "referee busy; try again in a few minutes")
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_META_BYTES:
                raise HTTPException(413, "bundle meta zip too large")
        try:
            files = B.read_meta_zip(bytes(body), MAX_META_BYTES)
        except Exception as e:
            raise HTTPException(400, f"could not read bundle meta zip: {e}") from None
        sub = ref.create(files, mode)
        out = sub.public()
        if sub.state == "rejected":
            raise HTTPException(422, out)
        out["upload_token"] = sub.upload_token
        return out

    @app.get("/v1/submissions/{sid}")
    def status(sid: str, authorization: str | None = Header(default=None)):
        return sub_or_404(sid, authorization).public()

    @app.put("/v1/submissions/{sid}/segments/{seq}")
    async def segment(sid: str, seq: int, request: Request, authorization: str | None = Header(default=None)):
        s = sub_or_404(sid, authorization)
        chunks: list[bytes] = []
        async for c in request.stream():
            chunks.append(c)
        try:
            return ref.put_segment(s, seq, chunks)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    @app.post("/v1/submissions/{sid}/review")
    def review(sid: str, authorization: str | None = Header(default=None)):
        s = sub_or_404(sid, authorization)
        try:
            return ref.review(s)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    @app.post("/v1/submissions/{sid}/player-signature")
    def player_signature(sid: str, req: PlayerSig, authorization: str | None = Header(default=None)):
        s = sub_or_404(sid, authorization)
        try:
            return ref.add_player_signature(s, req.sig)
        except PermissionError as e:
            raise HTTPException(401, str(e)) from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    return app


app = create_app() if os.getenv("QORTIME_REFEREE_AUTOSTART", "1") == "1" and os.getenv("QORTIME_REFEREE_SEED") else None
