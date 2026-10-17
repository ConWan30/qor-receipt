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

## Online copy: the referee service (verified uploads)

Since 2026-10-16 the `qortime-v1` seed is also held by the official referee service as the Railway service
variable `QORTIME_REFEREE_SEED` (service `qortime-referee`). It countersigns uploaded QorCap bundles only after
re-checking them (`docs/REFEREE_SERVICE.md`); those receipts carry `referee.key_kind: qortime-official-review`
and a signed `review` block. Rotating the key means updating that variable too (step 1b): set the new seed on the
service, redeploy, then revoke the old entry here.
