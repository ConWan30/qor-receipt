"""Account-free authenticated submissions: per-install Ed25519 keys + stateless nonces + hashcash + rate limits.

Client (QorCap >= 0.3.10):
  1. GET /v1/submit/challenge?pub=<install pubkey>  ->  {nonce, expires_ms, pow_bits}
     nonce = hex(ts_ms(8) || rand(16) || HMAC-SHA256(server_secret, ts||rand||pub)[:16])  (stateless, bound to the key)
  2. find ``pow`` (decimal string) with  SHA-256("qortime-pow/v1|" nonce "|" pub "|" pow)  having >= pow_bits leading
     zero bits (hashcash; ~2^pow_bits hashes).
  3. POST /v1/submissions with header  X-Qor-Submit: base64url(JSON{v:1, pub, ts_ms, nonce, pow, bundle_head, sig})
     sig = Ed25519(install key, JCS({type:"qortime-submit/v1", method, path, pub, ts_ms, nonce, pow,
                                     body_sha256, bundle_head}))
     bundle_head = SHA-256 of the bundle's manifest.jsonl bytes (the committed video chain).

Server order (cheap -> expensive, all before any video is accepted): header parse, timestamp skew, nonce MAC/expiry,
nonce single-use (replay), PoW, per-key + global rate limits, read the small meta zip (size-capped), signature over
the body hash, bundle_head == manifest hash, optional recorder-key match (install key == the bundle's player/device
key), then the normal metadata re-check. ``QORTIME_SUBMIT_TOKEN`` (Bearer) is an admin override that skips all of it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from qor_receipt.jcs import canonicalize
from qor_receipt.keys import verify

SUBMIT_TYPE = "qortime-submit/v1"
POW_PREFIX = "qortime-pow/v1|"
HEADER = "x-qor-submit"
UPGRADE_MSG = ("QorCap 0.3.10 or newer is required: submissions are now signed by this PC's install key "
               "(no account needed). Download the latest QorCap from https://github.com/ConWan30/qorcap/releases")


def pow_ok(nonce: str, pub: str, pow_: str, bits: int) -> bool:
    d = hashlib.sha256(f"{POW_PREFIX}{nonce}|{pub}|{pow_}".encode()).digest()
    n = int.from_bytes(d, "big")
    return n >> (256 - bits) == 0 if bits > 0 else True


def solve_pow(nonce: str, pub: str, bits: int) -> str:
    i = 0
    while not pow_ok(nonce, pub, str(i), bits):
        i += 1
    return str(i)


def request_payload(*, method: str, path: str, pub: str, ts_ms: int, nonce: str, pow_: str, body_sha256: str,
                    bundle_head: str) -> bytes:
    return canonicalize({"type": SUBMIT_TYPE, "method": method.upper(), "path": path, "pub": pub.lower(),
                         "ts_ms": ts_ms, "nonce": nonce, "pow": pow_, "body_sha256": body_sha256,
                         "bundle_head": bundle_head})


def sign_request(key, *, method: str, path: str, body: bytes, bundle_head: str, nonce: str, pow_: str,
                 ts_ms: int | None = None) -> str:
    """Client side: build the X-Qor-Submit header value."""
    ts = int(time.time() * 1000) if ts_ms is None else ts_ms
    sig = key.sign(request_payload(method=method, path=path, pub=key.pubkey_hex, ts_ms=ts, nonce=nonce, pow_=pow_,
                                   body_sha256=hashlib.sha256(body).hexdigest(), bundle_head=bundle_head))
    h = {"v": 1, "pub": key.pubkey_hex, "ts_ms": ts, "nonce": nonce, "pow": pow_, "bundle_head": bundle_head, "sig": sig}
    return base64.urlsafe_b64encode(json.dumps(h, separators=(",", ":")).encode()).decode().rstrip("=")


class AuthError(Exception):
    def __init__(self, status: int, code: str, msg: str):
        super().__init__(msg)
        self.status, self.code, self.msg = status, code, msg


class Window:
    """Sliding-window counter."""

    def __init__(self) -> None:
        self.t: deque[int] = deque()

    def count(self, now: int, span: int) -> int:
        while self.t and self.t[0] <= now - span:
            self.t.popleft()
        return len(self.t)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


class SubmitGuard:
    HOUR, DAY = 3_600_000, 86_400_000

    def __init__(self, *, secret: bytes | None = None, state_dir: str | Path | None = None,
                 now=lambda: int(time.time() * 1000), known_keys: set[str] | None = None,
                 require_recorder_match: bool | None = None) -> None:
        self.secret = secret or secrets.token_bytes(32)
        self.now = now
        self.state_dir = Path(state_dir) if state_dir else None
        self.nonce_ttl = _int_env("QORTIME_NONCE_TTL_MS", 300_000)
        self.skew = _int_env("QORTIME_SUBMIT_SKEW_MS", 300_000)
        self.pow_unknown = _int_env("QORTIME_POW_BITS_UNKNOWN", 20)
        self.pow_known = _int_env("QORTIME_POW_BITS_KNOWN", 12)
        self.q_unknown = (_int_env("QORTIME_RATE_UNKNOWN_HOUR", 3), _int_env("QORTIME_RATE_UNKNOWN_DAY", 6))
        self.q_known = (_int_env("QORTIME_RATE_KNOWN_HOUR", 10), _int_env("QORTIME_RATE_KNOWN_DAY", 40))
        self.q_global = (_int_env("QORTIME_RATE_GLOBAL_HOUR", 60), _int_env("QORTIME_RATE_GLOBAL_DAY", 400))
        self.require_recorder_match = (os.getenv("QORTIME_REQUIRE_RECORDER_MATCH", "1") == "1"
                                       if require_recorder_match is None else require_recorder_match)
        self.lock = threading.Lock()
        self.used: dict[str, int] = {}  # nonce -> expiry
        self.per_key: dict[str, Window] = {}
        self.glob = Window()
        self.known: set[str] = {k.strip().lower() for k in (os.getenv("QORTIME_KNOWN_INSTALL_KEYS") or "").split(",")
                                if k.strip()} | {k.lower() for k in (known_keys or set())}
        if self._known_file and self._known_file.exists():
            try:
                self.known |= {str(k).lower() for k in json.loads(self._known_file.read_text())}
            except ValueError:
                pass

    @property
    def _known_file(self) -> Path | None:
        return self.state_dir / "known_install_keys.json" if self.state_dir else None

    def is_known(self, pub: str) -> bool:
        return pub.lower() in self.known

    def promote(self, pub: str) -> None:
        """A key whose submission completed (referee countersigned + device co-signed) gets the higher quota."""
        pub = pub.lower()
        with self.lock:
            if pub in self.known:
                return
            self.known.add(pub)
            if self._known_file:
                self._known_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._known_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(sorted(self.known)))
                tmp.replace(self._known_file)

    # ------------------------------------------------------------- challenge
    def _mac(self, ts: int, rnd: bytes, pub: str) -> bytes:
        return hmac.new(self.secret, ts.to_bytes(8, "big") + rnd + pub.lower().encode(), hashlib.sha256).digest()[:16]

    def challenge(self, pub: str) -> dict[str, Any]:
        if not _is_hex64(pub):
            raise AuthError(400, "bad_pub", "pub must be a 64-hex Ed25519 public key")
        ts = self.now()
        rnd = secrets.token_bytes(16)
        nonce = (ts.to_bytes(8, "big") + rnd + self._mac(ts, rnd, pub)).hex()
        return {"nonce": nonce, "expires_ms": ts + self.nonce_ttl,
                "pow_bits": self.pow_known if self.is_known(pub) else self.pow_unknown,
                "pow": "sha256('qortime-pow/v1|'+nonce+'|'+pub+'|'+pow) needs pow_bits leading zero bits",
                "known": self.is_known(pub)}

    # ------------------------------------------------------------- pre-body checks
    def precheck(self, header: str | None) -> dict[str, Any]:
        if not header:
            raise AuthError(426, "upgrade_required", UPGRADE_MSG)
        try:
            pad = "=" * (-len(header) % 4)
            h = json.loads(base64.urlsafe_b64decode(header + pad))
            pub, ts, nonce, pow_ = str(h["pub"]).lower(), h["ts_ms"], str(h["nonce"]), str(h["pow"])
            str(h["sig"]), str(h["bundle_head"])
        except Exception:
            raise AuthError(400, "bad_auth_header", "malformed X-Qor-Submit header") from None
        if h.get("v") != 1 or not _is_hex64(pub) or not isinstance(ts, int) or len(pow_) > 24:
            raise AuthError(400, "bad_auth_header", "malformed X-Qor-Submit header")
        now = self.now()
        if abs(now - ts) > self.skew:
            raise AuthError(401, "stale_request", "request timestamp too far from server time; check the PC clock")
        try:
            raw = bytes.fromhex(nonce)
            nts, rnd, mac = int.from_bytes(raw[:8], "big"), raw[8:24], raw[24:]
        except ValueError:
            raise AuthError(401, "bad_nonce", "bad nonce") from None
        if len(raw) != 40 or not hmac.compare_digest(mac, self._mac(nts, rnd, pub)):
            raise AuthError(401, "bad_nonce", "nonce was not issued by this referee for this key")
        if now > nts + self.nonce_ttl:
            raise AuthError(401, "nonce_expired", "nonce expired; fetch a new one from /v1/submit/challenge")
        bits = self.pow_known if self.is_known(pub) else self.pow_unknown
        if not pow_ok(nonce, pub, pow_, bits):
            raise AuthError(401, "bad_pow", f"proof-of-work does not meet {bits} bits")
        with self.lock:
            for k in [k for k, exp in self.used.items() if exp < now]:
                del self.used[k]
            if nonce in self.used:
                raise AuthError(409, "replay", "nonce already used (replayed request)")
            hour, day = self.q_known if self.is_known(pub) else self.q_unknown
            w = self.per_key.setdefault(pub, Window())
            if w.count(now, self.DAY) >= day or sum(1 for t in w.t if t > now - self.HOUR) >= hour:
                raise AuthError(429, "rate_limited", "too many submissions from this install key; try later")
            gh, gd = self.q_global
            if self.glob.count(now, self.DAY) >= gd or sum(1 for t in self.glob.t if t > now - self.HOUR) >= gh:
                raise AuthError(429, "global_cap", "the referee is at its submission cap; try again later")
            self.used[nonce] = nts + self.nonce_ttl
            w.t.append(now)
            self.glob.t.append(now)
        return h

    # ------------------------------------------------------------- post-body checks
    def verify_body(self, h: dict[str, Any], *, method: str, path: str, body: bytes, files: dict[str, bytes]) -> None:
        pub = str(h["pub"]).lower()
        head = hashlib.sha256(files.get("manifest.jsonl", b"")).hexdigest()
        msg = request_payload(method=method, path=path, pub=pub, ts_ms=h["ts_ms"], nonce=h["nonce"], pow_=h["pow"],
                              body_sha256=hashlib.sha256(body).hexdigest(), bundle_head=h["bundle_head"])
        if not verify(pub, h["sig"], msg):
            raise AuthError(401, "bad_signature", "submit signature does not verify under the install key")
        if h["bundle_head"] != head:
            raise AuthError(401, "bundle_head_mismatch", "signed bundle_head does not match the uploaded manifest")
        if self.require_recorder_match:
            try:
                players = json.loads(files.get("receipt.json", b"{}")).get("players") or []
            except ValueError:
                players = []
            if pub not in {str(p.get("pubkey", "")).lower() for p in players if isinstance(p, dict)}:
                raise AuthError(403, "recorder_mismatch",
                                "the install key that signed this submit is not the bundle's recorder (device) key")


def _is_hex64(v: Any) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v.lower())
