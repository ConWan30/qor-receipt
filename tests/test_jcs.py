import json
from pathlib import Path

import pytest

from qor_receipt.jcs import canonicalize, sha256_hex

V = json.loads((Path(__file__).parent / "vectors" / "jcs_vector.json").read_text(encoding="utf-8"))


def test_parity_vector():
    assert canonicalize(V["input"]).decode() == V["canonical"]
    assert sha256_hex(V["input"]) == V["sha256"]


def test_key_order_independent():
    assert canonicalize({"b": 1, "a": 2}) == canonicalize({"a": 2, "b": 1}) == b'{"a":2,"b":1}'


@pytest.mark.parametrize("bad", [1.5, 2**53, {1: "x"}, object()])
def test_refuses(bad):
    with pytest.raises(ValueError):
        canonicalize(bad)
