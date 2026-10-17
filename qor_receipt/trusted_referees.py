"""Built-in, versioned allowlist of QorTime trusted referee public keys.

VERIFIED requires the receipt's referee pubkey to appear here with status
``active``. Unknown or revoked keys can at most yield SELF_ATTESTED.
The matching private keys live only on the operator box (never in git).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent / "data" / "trusted_referees.json"


@lru_cache(maxsize=4)
def load_allowlist(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else DATA
    return json.loads(p.read_text(encoding="utf-8"))


def clear_cache() -> None:
    load_allowlist.cache_clear()


def lookup(pubkey_hex: str, *, path: str | None = None) -> dict[str, Any] | None:
    """Return the allowlist entry for ``pubkey_hex`` (case-insensitive), or None."""
    if not pubkey_hex or not isinstance(pubkey_hex, str):
        return None
    want = pubkey_hex.lower()
    for row in load_allowlist(path).get("referees") or []:
        if str(row.get("pubkey", "")).lower() == want:
            return row
    return None


def is_trusted_active(pubkey_hex: str, *, path: str | None = None) -> bool:
    row = lookup(pubkey_hex, path=path)
    return bool(row and row.get("status") == "active")


def allowlist_pubkeys(*, path: str | None = None) -> list[str]:
    return [str(r["pubkey"]).lower() for r in (load_allowlist(path).get("referees") or []) if r.get("pubkey")]
