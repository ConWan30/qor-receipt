"""QorCap <-> QorTime contract tests: schema/contracts/compat.json is the single source of truth.

Every constant duplicated in code (Python verifier, JS verifier, review bundle loader, allowlists) must match it,
and the golden fixtures (tests/fixtures/contract) must give the recorded verdicts. verify/contract.test.mjs
asserts the browser verifier gives the same verdicts on the same files.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from qor_receipt import SCHEMA
from qor_receipt import checkpoints as C
from qor_receipt import receipt as R
from qor_receipt.review import bundle as B
from qor_receipt.review import policy as P

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "schema" / "contracts"
COMPAT = json.loads((CONTRACTS / "compat.json").read_text(encoding="utf-8"))
FIX = sorted((ROOT / "tests" / "fixtures" / "contract").glob("*.json"))


def test_python_constants_match_compat():
    assert SCHEMA in COMPAT["receipt_schemas"]
    assert C.DEFAULT_MAX_GAP_MS == COMPAT["checkpoint_timing"]["max_gap_ms"]
    assert C.DEFAULT_MAX_SKEW_MS == COMPAT["checkpoint_timing"]["max_skew_ms"]
    assert C.GENESIS == B.GENESIS == COMPAT["hash_chain"]["genesis"]
    assert set(R.RESULT_METHODS) == set(COMPAT["result_methods"])
    assert list(B.META_FILES) == COMPAT["bundle_files"]["meta"]
    assert list(B.REQUIRED_FILES) == COMPAT["bundle_files"]["required"]
    assert {R.VERIFIED, R.PARTIAL, R.SELF_ATTESTED, R.FAIL} == set(COMPAT["verdicts"])
    assert P.REVIEW_TYPE == COMPAT["review_type"] and P.CHALLENGE_TOKEN_TYPE == COMPAT["challenge_token_type"]


def test_receipt_schema_enums_match_compat():
    s = json.loads((ROOT / "schema" / "receipt.v0.json").read_text(encoding="utf-8"))
    res = s["properties"]["result"]["properties"]
    assert set(res["method"]["enum"]) == set(COMPAT["result_methods"])
    assert set(res["final_basis"]["enum"]) == set(COMPAT["final_basis"])
    assert s["properties"]["schema"]["const"] in COMPAT["receipt_schemas"]


def test_js_constants_match_compat():
    js = (ROOT / "verify" / "verify.js").read_text(encoding="utf-8")
    m = re.search(r"MAX_GAP_MS = (\d+), MAX_SKEW_MS = (\d+)", js)
    assert m and int(m[1]) == COMPAT["checkpoint_timing"]["max_gap_ms"] and int(m[2]) == COMPAT["checkpoint_timing"]["max_skew_ms"]
    assert f'SCHEMA = "{COMPAT["receipt_schemas"][0]}"' in js
    assert f'REVIEW_TYPE = "{COMPAT["review_type"]}"' in js


def test_allowlists_identical_and_official_key():
    a = json.loads((ROOT / "qor_receipt" / "data" / "trusted_referees.json").read_text(encoding="utf-8"))
    b = json.loads((ROOT / "verify" / "trusted_referees.json").read_text(encoding="utf-8"))
    assert a == b
    inc = (ROOT / "verify" / "trusted_referees.inc.js").read_text(encoding="utf-8")
    off = COMPAT["official_referee"]
    assert off["pubkey"] in inc
    assert a["schema"] in COMPAT["trusted_referees_schemas"]
    row = next(r for r in a["referees"] if r["id"] == off["id"])
    assert row["pubkey"] == off["pubkey"] and row["key_id"] == off["key_id"] and row["status"] == "active"
    from qor_receipt.keys import kid
    assert kid(off["pubkey"]) == off["key_id"]


def test_contract_json_schemas_valid_and_allowlist_conforms():
    js = pytest.importorskip("jsonschema")
    for p in CONTRACTS.glob("*.v*.json"):
        js.Draft202012Validator.check_schema(json.loads(p.read_text(encoding="utf-8")))
    js.validate(json.loads((ROOT / "qor_receipt" / "data" / "trusted_referees.json").read_text(encoding="utf-8")),
                json.loads((CONTRACTS / "trusted-referees.v1.json").read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", FIX, ids=[p.stem for p in FIX])
def test_golden_fixture_verdicts(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    v = R.verify_receipt(doc["receipt"], referee_pub=doc["referee_pub"])
    assert v["status"] == doc["expect"]["status"]
    assert v["errors"] == doc["expect"]["errors"]
    js = pytest.importorskip("jsonschema")
    js.validate(doc["receipt"], json.loads((ROOT / "schema" / "receipt.v0.json").read_text(encoding="utf-8")))


def test_golden_fixtures_cover_required_cases_and_are_reproducible():
    spec = importlib.util.spec_from_file_location("mcf", ROOT / "scripts" / "make_contract_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cases = mod.cases()
    assert {p.stem for p in FIX} == set(cases)
    for name, (rec, _pub) in cases.items():
        assert json.loads((ROOT / "tests" / "fixtures" / "contract" / f"{name}.json").read_text())["receipt"] == rec, name
    want = {"good": "SELF_ATTESTED", "tampered_score": "FAIL", "foreign_key_pinned_mismatch": "FAIL",
            "foreign_key_claims_official": "FAIL", "missing_checkpoint": "FAIL", "v032_late_stop_tail": "FAIL",
            "v033_sealed_stop_tail": "SELF_ATTESTED", "slow_genesis": "FAIL", "finalize_offline": "SELF_ATTESTED"}
    for n, s in want.items():
        assert json.loads((ROOT / "tests" / "fixtures" / "contract" / f"{n}.json").read_text())["expect"]["status"] == s, n


def test_stop_tail_rule_is_the_verifier_rule():
    """The real-match failure signature (cp[n-2] gap+skew, cp[n-1] skew) is exactly a late-stamped stop tail."""
    e = json.loads((ROOT / "tests/fixtures/contract/v032_late_stop_tail.json").read_text())["expect"]["errors"]
    assert e == ["home:cp[6]:gap_too_long", "home:cp[6]:clock_skew", "home:cp[7]:clock_skew"]


def test_js_verifier_same_verdicts_on_golden_fixtures():
    """Runs verify/contract.test.mjs (browser verifier) over the same fixtures. Node is on GitHub's ubuntu runners."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    p = subprocess.run([node, str(ROOT / "verify" / "contract.test.mjs")], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
