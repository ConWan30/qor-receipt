import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")
ROOT = Path(__file__).resolve().parent.parent


def test_demo_receipts_match_schema(ref):
    from qor_receipt.demo import run_match

    schema = json.loads((ROOT / "schema" / "receipt.v0.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    for mode in ("dual", "single"):
        client, clock, _ = ref
        rec, _ = run_match(client, clock, mode)
        jsonschema.validate(rec, schema)
    jsonschema.validate(json.loads((ROOT / "verify" / "sample-receipt.json").read_text()), schema)


def test_end_menu_read_result_matches_schema():
    import copy

    schema = json.loads((ROOT / "schema" / "receipt.v0.json").read_text())
    rec = copy.deepcopy(json.loads((ROOT / "verify" / "sample-receipt.json").read_text()))
    rec["result"].update(home_score=20, away_score=0, winner="home", method="end_menu_read", final_basis="end_menu",
                         final_confirmed=True, scorebug_last={"home_score": 17, "away_score": 0, "quarter": 4, "t_ms": 1},
                         notes=["final play score not shown on scorebug"])
    jsonschema.validate(rec, schema)
    rec["result"]["method"] = "made_up"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(rec, schema)


def test_review_receipt_matches_schema():
    schema = json.loads((ROOT / "schema" / "receipt.v0.json").read_text())
    v = json.loads((ROOT / "tests" / "vectors" / "review_receipt.json").read_text())
    jsonschema.validate(v["receipt"], schema)
