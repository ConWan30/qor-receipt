"""RFC 8785 (JCS) canonical JSON, restricted profile.

Receipts carry only strings, integers (|n| < 2**53), booleans, null, arrays and
objects. Floats are refused, so number serialisation is trivially identical in
Python and JS. Keys are sorted by UTF-16 code units, as RFC 8785 requires.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_SAFE = 2**53 - 1


def _key(k: str) -> list[int]:
    b = k.encode("utf-16-be")
    return [int.from_bytes(b[i : i + 2], "big") for i in range(0, len(b), 2)]


def _enc(v: Any, out: list[str]) -> None:
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, int):
        if abs(v) > MAX_SAFE:
            raise ValueError("integer outside JS safe range")
        out.append(str(v))
    elif isinstance(v, float):
        raise ValueError("floats are not allowed in qor-receipt canonical JSON")
    elif isinstance(v, str):
        out.append(json.dumps(v, ensure_ascii=False))
    elif isinstance(v, (list, tuple)):
        out.append("[")
        for i, x in enumerate(v):
            if i:
                out.append(",")
            _enc(x, out)
        out.append("]")
    elif isinstance(v, dict):
        if not all(isinstance(k, str) for k in v):
            raise ValueError("object keys must be strings")
        out.append("{")
        for i, k in enumerate(sorted(v, key=_key)):
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _enc(v[k], out)
        out.append("}")
    else:
        raise ValueError(f"unsupported type {type(v).__name__}")


def canonicalize(v: Any) -> bytes:
    out: list[str] = []
    _enc(v, out)
    return "".join(out).encode("utf-8")


def sha256_hex(v: Any) -> str:
    return hashlib.sha256(canonicalize(v)).hexdigest()
