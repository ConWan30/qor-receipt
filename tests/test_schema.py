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
