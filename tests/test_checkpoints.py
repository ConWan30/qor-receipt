from qor_receipt.checkpoints import Chain, countersign, verify_chain
from qor_receipt.keys import SoftwareKey

REF = SoftwareKey()


def chain(n=5, t0=1000, step=4000, sign_key=REF, ts_shift=0):
    c, out = Chain(), []
    for i in range(n):
        t = t0 + i * step
        out.append(countersign(sign_key, "m1", "home", c.next(t, f"f{i}".rjust(64, "0"), f"i{i}".rjust(64, "0")), t + ts_shift))
    return out


def v(cps, **kw):
    return verify_chain(cps, match_id="m1", role="home", referee_pub=REF.pubkey_hex, start_ms=0,
                        end_ms=cps[-1]["referee_ts_ms"] + 1000 if cps else 0, **kw)


def test_good():
    assert v(chain()) == []


def test_reorder_drop_edit():
    c = chain()
    assert any("prev_mismatch" in e for e in v([c[0], c[2], c[1], c[3]]))
    assert any("seq_gap" in e for e in v(c[:2] + c[3:]))
    c2 = chain(); c2[2]["input_hash"] = "f" * 64
    assert "cp[2]:hash_mismatch" in v(c2)


def test_forged_countersig():
    errs = v(chain(sign_key=SoftwareKey()))
    assert all(f"cp[{i}]:bad_referee_sig" in errs for i in range(5))


def test_posthoc_fabrication_detected():
    # attacker re-signs a fabricated chain but cannot get referee timestamps for the past
    c = chain(ts_shift=600_000)
    assert any("clock_skew" in e for e in v(c))


def test_gaps():
    assert any("gap_too_long" in e for e in v(chain(step=15_000)))
    c = chain()
    assert "chain_ends_before_match_end" in verify_chain(c, match_id="m1", role="home", referee_pub=REF.pubkey_hex,
                                                         start_ms=0, end_ms=10**7)
    assert verify_chain([], match_id="m1", role="home", referee_pub=REF.pubkey_hex, start_ms=0, end_ms=1) == ["no_checkpoints"]


def test_wrong_match_or_role_binding():
    c = chain()
    assert verify_chain(c, match_id="other", role="home", referee_pub=REF.pubkey_hex, start_ms=0, end_ms=c[-1]["referee_ts_ms"])
    assert verify_chain(c, match_id="m1", role="away", referee_pub=REF.pubkey_hex, start_ms=0, end_ms=c[-1]["referee_ts_ms"])
