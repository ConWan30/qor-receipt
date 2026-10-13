"""Key abstraction. Phase 1: software Ed25519 keys. Phase 2: TPM-backed (see docs/PHASE2.md).

Callers only see ``KeyProvider``: ``key_id``, ``pubkey_hex``, ``kind``, ``sign(bytes)``.
The file-backed key reuses the vendored Qoresence ``signing.py`` (0600 hex seed).
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from qor_receipt._vendor.qoresence.truth.signing import key_id_for, load_or_create_signer


class KeyProvider(Protocol):
    kind: str
    key_id: str
    pubkey_hex: str

    def sign(self, data: bytes) -> str: ...


class SoftwareKey:
    """In-memory Ed25519 key (tests, referee dev mode)."""

    kind = "software"

    def __init__(self, priv: Ed25519PrivateKey | None = None) -> None:
        self._priv = priv or Ed25519PrivateKey.generate()
        pub = self._priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.pubkey_hex = pub.hex()
        self.key_id = key_id_for(pub)

    @classmethod
    def from_seed_hex(cls, seed_hex: str) -> "SoftwareKey":
        return cls(Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex)))

    def sign(self, data: bytes) -> str:
        return self._priv.sign(data).hex()


class FileKey:
    """Persistent key file via vendored Qoresence signing (refuses group/other-readable files)."""

    kind = "software-file"

    def __init__(self, path: Path) -> None:
        signer, reason = load_or_create_signer(Path(path))
        if signer is None:
            raise RuntimeError(f"key unavailable: {reason}")
        self._s = signer
        self.pubkey_hex = signer.pubkey_hex
        self.key_id = signer.key_id

    def sign(self, data: bytes) -> str:
        return self._s.sign(data)


class TpmKey:  # pragma: no cover - phase 2
    """Placeholder: non-exportable key in the Windows TPM (Platform Crypto Provider) with attestation."""

    kind = "tpm"

    def __init__(self, *_a, **_k) -> None:
        raise NotImplementedError("TPM keys are phase 2; see docs/PHASE2.md")


def verify(pubkey_hex: str, sig_hex: str, data: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(bytes.fromhex(sig_hex), data)
        return True
    except (InvalidSignature, ValueError):
        return False


def kid(pubkey_hex: str) -> str:
    return key_id_for(bytes.fromhex(pubkey_hex))
