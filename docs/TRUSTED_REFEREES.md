# Trusted referee allowlist

`qortime verify` (Python CLI and the browser verifier) grants **VERIFIED** only when the receipt's referee Ed25519 public key is listed in the built-in allowlist:

- Python: `qor_receipt/data/trusted_referees.json`
- Browser: `verify/trusted_referees.json` + `verify/trusted_referees.inc.js` (generated twin)

Unknown, revoked, local-self-attested, or URL-pinned (`?referee=`) keys that are not on the list yield **SELF_ATTESTED** at most — never VERIFIED.

## Official key material

The matching **private** key for `qortime-v1` is generated on the operator box and stored **outside git**, e.g.:

```
/workspace/qortime-keys/qortime-referee-v1.key   # mode 0600, hex seed
```

Never commit, paste, or log that seed. Only the pubkey belongs in the allowlist JSON.

## Rotation / revocation

1. Generate `qortime-referee-vN.key` on the operator box.
2. Append a new allowlist entry with `"status": "active"`.
3. Set the previous entry to `"status": "revoked"` (keep the pubkey so old receipts become SELF_ATTESTED, not mysterious FAIL).
4. Bump `version` / `updated` in the JSON and republish the site verifier.
5. Never reuse a revoked seed.

QorCap Start Match continues to use a **local** `referee.key` (`key_kind: local-self-attested`). Those receipts are intentionally SELF_ATTESTED until an independent QorTime referee co-signs.

## Online signing: delegated service key (since 2026-10-16)

`qortime-v1` is now marked `"can_delegate": true, "role": "root"` and **no longer runs online**. The referee service signs
with a short-lived online key certified by a root-signed `qortime-referee-delegation/v1` cert embedded in each receipt
(`referee.delegation`). Verifiers trust such receipts only while the cert is valid at signing time and not revoked
(`referee_revocations.json`, root-signed; JS twin `verify/referee_revocations.json` + `.inc.js`). Details, rotation and
revocation: `docs/REFEREE_KEYS.md`. Receipts signed directly by `qortime-v1` before the switch stay VERIFIED.
