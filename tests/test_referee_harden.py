"""Referee hardening: delegated online keys (root-signed certs + revocation), transparency log, install-key submits."""
from __future__ import annotations

import copy
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import review_fixture as F  # noqa: E402
from test_referee_service import Env, _resign  # noqa: E402

from qor_receipt import delegation as D  # noqa: E402
from qor_receipt import receipt as R  # noqa: E402
from qor_receipt import translog as T  # noqa: E402
from qor_receipt.keys import SoftwareKey  # noqa: E402
from qor_receipt.referee_service import submit_auth as A  # noqa: E402
from qor_receipt.trusted_referees import clear_cache  # noqa: E402

DAY = 86_400_000
needs_ffmpeg = pytest.mark.skipif(not F.have_ffmpeg(), reason="ffmpeg needed for the synthetic fMP4 bundle")


@pytest.fixture(autouse=True)
def _reset():
    yield
    clear_cache()
    D.clear_cache()


class Trust:
    """A root key on a test allowlist + a root-signed revocation list."""

    def __init__(self, tmp: Path, revoked: list[dict] | None = None, can_delegate: bool = True):
        self.tmp, self.root = tmp, SoftwareKey()
        self.allow = tmp / "allow_root.json"
        self.allow.write_text(json.dumps({"schema": "qortime-trusted-referees/v1", "version": 1, "referees": [
            {"id": "test-root", "pubkey": self.root.pubkey_hex, "key_id": self.root.key_id, "status": "active",
             "can_delegate": can_delegate}]}))
        self.revs = tmp / "revocations.json"
        self.write_revs(revoked or [])
        clear_cache()

    def write_revs(self, revoked, signer=None):
        doc = D.make_revocations(signer or self.root, root_id="test-root", version=1, revoked=revoked, issued_ms=F.W0)
        self.revs.write_text(json.dumps(doc))
        D.clear_cache()

    def cert(self, svc: SoftwareKey, serial=1, nb=F.W0 - DAY, na=F.W0 + 30 * DAY, **kw):
        return D.make_delegation(self.root, root_id="test-root", delegate_pubkey=svc.pubkey_hex,
                                 delegate_id=f"test-online-s{serial}", serial=serial, not_before_ms=nb,
                                 not_after_ms=na, issued_ms=F.W0 - DAY, **kw)

    def verify(self, rec, pin=None):
        return R.verify_receipt(rec, referee_pub=pin, allowlist_path=str(self.allow), revocations_path=str(self.revs))


def _delegated(tmp_path, **cert_kw):
    e = Env(tmp_path)
    t = Trust(tmp_path)
    e.ref.delegation = t.cert(e.key, **cert_kw)
    e.ref.log = T.TransparencyLog(tmp_path / "log.jsonl", signer=e.key, delegation=e.ref.delegation,
                                  now=lambda: e.clock["t"])
    sub = e.submit()
    e.upload_all(sub)
    out = e.ref.review(sub)
    return e, t, sub, out


# ---------------------------------------------------------------- delegation (Python verifier)
@needs_ffmpeg
def test_delegated_receipt_verifies_and_is_logged(tmp_path):
    e, t, sub, out = _delegated(tmp_path)
    assert out["state"] == "awaiting_player_signature", out.get("review", {}).get("post_sign_errors")
    rec = e.player_sign(sub)["receipt"]
    assert rec["referee"]["delegation"]["delegate_pubkey"] == e.key.pubkey_hex
    v = t.verify(rec, pin=e.key.pubkey_hex)
    assert v["status"] == "VERIFIED", v
    assert v["referee_id"] == "test-online-s1" and v["delegation"]["serial"] == 1
    assert t.verify(rec)["status"] == "PARTIAL"  # unpinned: same rule as legacy receipts
    # transparency log: entry exists and inclusion verifies against the signed head
    bh = T.body_sha256(rec)
    seq = e.ref.log.find(bh)
    assert seq == out["log_entry"]["seq"] == 0
    head = e.ref.log.signed_head()
    assert T.verify_head(head, e.key.pubkey_hex)
    assert T.verify_inclusion(bh, e.ref.log.slice(seq, 99), head) == []
    # vector for verify/test.mjs
    vec = {"receipt": rec, "referee_pub": e.key.pubkey_hex, "allowlist": json.loads(t.allow.read_text()),
           "revocations": json.loads(t.revs.read_text()), "root_seed_note": "test-only keys",
           "revoked_list": D.make_revocations(t.root, root_id="test-root", version=2, issued_ms=F.W0,
                                              revoked=[{"serial": 1, "delegate_pubkey": e.key.pubkey_hex,
                                                        "reason": "test", "revoked_ms": F.W0}]),
           "log_entries": e.ref.log.slice(0, 99), "log_head": head}
    out_p = Path(__file__).parent / "vectors" / "delegated_review_receipt.json"
    if not out_p.exists():
        out_p.write_text(json.dumps(vec, indent=1))
    v2 = json.loads(out_p.read_text())
    (tmp_path / "a2.json").write_text(json.dumps(v2["allowlist"]))
    (tmp_path / "r2.json").write_text(json.dumps(v2["revocations"]))
    clear_cache()
    D.clear_cache()
    assert R.verify_receipt(v2["receipt"], referee_pub=v2["referee_pub"], allowlist_path=str(tmp_path / "a2.json"),
                            revocations_path=str(tmp_path / "r2.json"))["status"] == "VERIFIED"


@needs_ffmpeg
def test_expired_and_not_yet_valid_delegation(tmp_path):
    e, t, sub, _ = _delegated(tmp_path)
    rec = e.player_sign(sub)["receipt"]
    for nb, na, why in ((F.W0 - 40 * DAY, F.W0 - DAY, "delegation_expired"),
                        (F.W0 + DAY, F.W0 + 30 * DAY, "delegation_not_yet_valid")):
        r = copy.deepcopy(rec)
        r["referee"]["delegation"] = t.cert(e.key, nb=nb, na=na)
        r = _resign(r, e.key, e.fx["device"])
        v = t.verify(r, pin=e.key.pubkey_hex)
        assert v["status"] == "SELF_ATTESTED" and why in v["partial"], v


@needs_ffmpeg
def test_service_refuses_to_sign_with_expired_delegation(tmp_path):
    e, t, sub, out = _delegated(tmp_path, nb=F.W0 - 40 * DAY, na=F.W0 - DAY)
    assert out["state"] == "refused" and "delegation" in json.dumps(out["review"]["post_sign_errors"])
    assert e.ref.log.entries == []


@needs_ffmpeg
def test_revoked_delegation_and_bad_revocation_list(tmp_path):
    e, t, sub, _ = _delegated(tmp_path)
    rec = e.player_sign(sub)["receipt"]
    t.write_revs([{"serial": 1, "delegate_pubkey": e.key.pubkey_hex, "reason": "compromise", "revoked_ms": F.W0}])
    v = t.verify(rec, pin=e.key.pubkey_hex)
    assert v["status"] == "SELF_ATTESTED" and "delegation_revoked:1" in v["partial"]
    t.write_revs([], signer=SoftwareKey())  # a revocation list not signed by the root -> fail closed
    v = t.verify(rec, pin=e.key.pubkey_hex)
    assert v["status"] == "SELF_ATTESTED" and "revocations_bad_signature" in v["partial"]
    t.revs.unlink()
    D.clear_cache()
    assert "revocations_missing" in t.verify(rec, pin=e.key.pubkey_hex)["partial"]


@needs_ffmpeg
def test_forged_scope_key_and_root_rules(tmp_path):
    e, t, sub, _ = _delegated(tmp_path)
    rec = e.player_sign(sub)["receipt"]
    other = SoftwareKey()
    cases = {
        "delegation_scope": t.cert(e.key, scope=["something-else"]),
        "delegation_key_mismatch": t.cert(other),
        "delegation_lifetime_too_long": t.cert(e.key, na=F.W0 + 400 * DAY),
    }
    for why, cert in cases.items():
        r = copy.deepcopy(rec)
        r["referee"]["delegation"] = cert
        r = _resign(r, e.key, e.fx["device"])
        v = t.verify(r, pin=e.key.pubkey_hex)
        assert v["status"] == "SELF_ATTESTED" and why in v["partial"], (why, v)
    # cert signed by a key that is not a can_delegate root
    r = copy.deepcopy(rec)
    fake = D.make_delegation(other, root_id="test-root", delegate_pubkey=e.key.pubkey_hex, delegate_id="x", serial=9,
                             not_before_ms=F.W0 - DAY, not_after_ms=F.W0 + DAY)
    r["referee"]["delegation"] = fake
    v = t.verify(_resign(r, e.key, e.fx["device"]), pin=e.key.pubkey_hex)
    assert v["status"] == "SELF_ATTESTED" and "delegation_root_not_trusted" in v["partial"]
    # tampered cert (widened window) -> bad root signature
    r = copy.deepcopy(rec)
    r["referee"]["delegation"]["not_after_ms"] += DAY
    v = t.verify(_resign(r, e.key, e.fx["device"]), pin=e.key.pubkey_hex)
    assert "delegation_bad_signature" in v["partial"]
    # root without can_delegate cannot delegate
    t2 = Trust(tmp_path / "t2" if (tmp_path / "t2").mkdir() is None else tmp_path, can_delegate=False)
    r = copy.deepcopy(rec)
    r["referee"]["delegation"] = t2.cert(e.key)
    v = t2.verify(_resign(r, e.key, e.fx["device"]), pin=e.key.pubkey_hex)
    assert "delegation_root_not_trusted" in v["partial"]


@needs_ffmpeg
def test_allowed_statuses_caps_verdict(tmp_path):
    e, t, sub, _ = _delegated(tmp_path)
    rec = e.player_sign(sub)["receipt"]
    r = copy.deepcopy(rec)
    r["referee"]["delegation"] = t.cert(e.key, allowed_statuses=["PARTIAL"])
    v = t.verify(_resign(r, e.key, e.fx["device"]), pin=e.key.pubkey_hex)
    assert v["status"] == "PARTIAL" and "delegation_caps_status:VERIFIED" in v["partial"]


@needs_ffmpeg
def test_legacy_root_signed_receipt_still_verified(tmp_path):
    """Receipts signed directly by the allowlisted root (pre-delegation) keep verifying."""
    e = Env(tmp_path)  # Env signs directly with an allowlisted key, no delegation
    sub = e.submit()
    e.upload_all(sub)
    e.ref.review(sub)
    rec = e.player_sign(sub)["receipt"]
    assert "delegation" not in rec["referee"]
    t = Trust(tmp_path, revoked=[{"serial": 1, "delegate_pubkey": "00" * 32, "reason": "x", "revoked_ms": F.W0}])
    v = R.verify_receipt(rec, referee_pub=e.key.pubkey_hex, allowlist_path=e.allow, revocations_path=str(t.revs))
    assert v["status"] == "VERIFIED" and "delegation" not in v


def test_shipped_trust_files_are_consistent():
    """Built-in allowlist marks qortime-v1 as the delegating root; revocation list is root-signed; JS twins match."""
    from qor_receipt.trusted_referees import load_allowlist

    allow = load_allowlist()
    root = next(r for r in allow["referees"] if r["id"] == "qortime-v1")
    assert root["can_delegate"] is True and root["pubkey"].startswith("a5aa0c91")
    revs = D.load_revocations()
    assert D.revocations_problem(revs, allow) is None
    root_dir = Path(__file__).resolve().parents[1]
    assert json.loads((root_dir / "verify" / "trusted_referees.json").read_text()) == allow
    assert json.loads((root_dir / "verify" / "referee_revocations.json").read_text()) == revs


# ---------------------------------------------------------------- transparency log
def test_translog_chain_tamper_and_persistence(tmp_path):
    k = SoftwareKey()
    p = tmp_path / "log.jsonl"
    log = T.TransparencyLog(p, signer=k)
    for i in range(5):
        log.append("receipt", f"{i:064x}")
    assert T.verify_entries(log.entries) == []
    again = T.TransparencyLog(p, signer=k)  # reload from disk
    assert again.head == log.head and len(again.entries) == 5
    h = again.signed_head()
    assert T.verify_head(h, k.pubkey_hex) and not T.verify_head(dict(h, size=4), k.pubkey_hex)
    assert T.verify_inclusion(f"{2:064x}", again.slice(2, 9), h) == []
    assert "inclusion_head_mismatch" in T.verify_inclusion(f"{2:064x}", again.slice(2, 2), h)
    bad = copy.deepcopy(again.entries)
    bad[1]["body_sha256"] = "f" * 64  # rewrite history
    assert "hash:1" in T.verify_entries(bad)
    lines = p.read_text().splitlines()
    lines[2] = json.dumps(dict(json.loads(lines[2]), ts_ms=1))
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(RuntimeError):
        T.TransparencyLog(p)


# ---------------------------------------------------------------- install-key submissions (HTTP)
def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(f"match-x/{k}", v)
    return buf.getvalue()


def _signed_post(c, key, body, files, *, mode="spot-check", nonce=None, pow_=None, ts=None, head=None):
    import hashlib

    if nonce is None:
        ch = c.get(f"/v1/submit/challenge?pub={key.pubkey_hex}").json()
        nonce = ch["nonce"]
        pow_ = pow_ or A.solve_pow(nonce, key.pubkey_hex, ch["pow_bits"])
    path = f"/v1/submissions?mode={mode}"
    hdr = A.sign_request(key, method="POST", path=path, body=body, nonce=nonce, pow_=pow_, ts_ms=ts,
                         bundle_head=head or hashlib.sha256(files["manifest.jsonl"]).hexdigest())
    return c.post(path, content=body, headers={"Content-Type": "application/zip", "X-Qor-Submit": hdr}), nonce, pow_, hdr


@needs_ffmpeg
def test_submit_auth_http(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from qor_receipt.referee_service.app import create_app

    monkeypatch.setenv("QORTIME_SUBMIT_TOKEN", "admin-secret")
    e = Env(tmp_path, tail_ms=8_000, k=3)
    g = A.SubmitGuard(state_dir=tmp_path / "state")
    g.pow_unknown, g.pow_known, g.q_unknown = 8, 4, (2, 3)
    c = TestClient(create_app(e.ref, guard=g))
    files = e.fx["files"]
    body = _zip(files)
    dev = e.fx["device"]
    # v0.3.9 client (no header) -> clear upgrade error, before any body handling
    r = c.post("/v1/submissions", content=body)
    assert r.status_code == 426 and "0.3.10" in r.json()["detail"]["errors"][0]
    # admin override still works
    r = c.post("/v1/submissions", content=body, headers={"Authorization": "Bearer admin-secret"})
    assert r.status_code == 200, r.text
    # signed by the recorder (device) key -> accepted
    r, nonce, pow_, hdr = _signed_post(c, dev, body, files)
    assert r.status_code == 200, r.text
    # exact replay -> 409
    r2 = c.post("/v1/submissions?mode=spot-check", content=body, headers={"X-Qor-Submit": hdr})
    assert r2.status_code == 409 and r2.json()["detail"]["error"] == "replay"
    # bad PoW
    ch = c.get(f"/v1/submit/challenge?pub={dev.pubkey_hex}").json()
    bad = next(str(i) for i in range(10_000) if not A.pow_ok(ch["nonce"], dev.pubkey_hex, str(i), ch["pow_bits"]))
    r, *_ = _signed_post(c, dev, body, files, nonce=ch["nonce"], pow_=bad)
    assert r.status_code == 401 and r.json()["detail"]["error"] == "bad_pow"
    # nonce issued for another key
    other = SoftwareKey()
    ch = c.get(f"/v1/submit/challenge?pub={other.pubkey_hex}").json()
    r, *_ = _signed_post(c, dev, body, files, nonce=ch["nonce"], pow_=A.solve_pow(ch["nonce"], dev.pubkey_hex, 8))
    assert r.json()["detail"]["error"] == "bad_nonce"
    # body swapped after signing / wrong bundle head / not the recorder
    r, *_ = _signed_post(c, other, body, files)
    assert r.status_code == 403 and r.json()["detail"]["error"] == "recorder_mismatch"
    third = SoftwareKey()
    r, *_ = _signed_post(c, third, body, files, head="0" * 64)
    assert r.json()["detail"]["error"] == "bundle_head_mismatch"
    # per-key rate limit (unknown quota 2/hour already used by dev: ok submit + bad_pow + recorder... count = accepted
    # prechecks). Fresh key: 2 pass prechecks, the 3rd is rate limited.
    k4 = SoftwareKey()
    codes = [_signed_post(c, k4, body, files)[0].json()["detail"]["error"] for _ in range(3)]
    assert codes == ["recorder_mismatch", "recorder_mismatch", "rate_limited"]
    # stale timestamp
    r, *_ = _signed_post(c, third, body, files, ts=1)
    assert r.json()["detail"]["error"] == "stale_request"


def test_nonce_expiry_global_cap_and_promotion(tmp_path):
    clock = {"t": F.W0}
    k = SoftwareKey()
    g = A.SubmitGuard(now=lambda: clock["t"], state_dir=tmp_path)
    g.pow_unknown, g.pow_known, g.q_global = 6, 2, (2, 10)

    def hdr():
        ch = g.challenge(k.pubkey_hex)
        return A.sign_request(k, method="POST", path="/p", body=b"x", bundle_head="h", nonce=ch["nonce"],
                              pow_=A.solve_pow(ch["nonce"], k.pubkey_hex, ch["pow_bits"]), ts_ms=clock["t"])
    old = hdr()
    clock["t"] += g.nonce_ttl + 1
    with pytest.raises(A.AuthError) as ei:
        g.precheck(old)
    assert ei.value.code in ("nonce_expired", "stale_request")
    g.precheck(hdr())
    g.precheck(hdr())
    with pytest.raises(A.AuthError) as ei:
        g.precheck(hdr())
    assert ei.value.code in ("global_cap", "rate_limited")
    assert not g.is_known(k.pubkey_hex) and g.challenge(k.pubkey_hex)["pow_bits"] == 6
    g.promote(k.pubkey_hex)
    assert A.SubmitGuard(state_dir=tmp_path).is_known(k.pubkey_hex)  # persisted
    assert g.challenge(k.pubkey_hex)["pow_bits"] == 2


def test_delegation_status_and_env_loading(monkeypatch, tmp_path):
    import base64

    from qor_receipt.referee_service.app import _key_from_env

    svc = SoftwareKey.from_seed_hex("11" * 32)
    t = Trust(tmp_path)
    cert = t.cert(svc)
    monkeypatch.setenv("QORTIME_SERVICE_SEED", "11" * 32)
    monkeypatch.setenv("QORTIME_DELEGATION", base64.b64encode(json.dumps(cert).encode()).decode())
    k, c2 = _key_from_env()
    assert k.pubkey_hex == svc.pubkey_hex and c2 == cert
    monkeypatch.delenv("QORTIME_DELEGATION")
    with pytest.raises(SystemExit):
        _key_from_env()
