# Referee key hierarchy, transparency log and signed submissions

Hardening of the official referee service (`docs/REFEREE_SERVICE.md`). Three changes:

1. **Offline root, online delegate.** `qortime-v1` (pubkey `a5aa0c91…feea`) is now an **offline root**. It lives only on the
   operator box and never on Railway. The service signs with its own short-lived **online key**. The root certifies that
   key in a delegation certificate.
2. **Transparency log.** Every receipt the service signs is first appended to an append-only, hash-chained log. The
   service publishes the log, and its head is anchored in this repo every 6 h.
3. **Signed submissions.** `POST /v1/submissions` no longer accepts anonymous uploads. Each QorCap install signs its
   request with the same Ed25519 device key that recorded the bundle. A single-use nonce, a small proof-of-work and
   per-key and global quotas sit in front of it. No accounts are involved.

Only standard primitives are used: Ed25519 (RFC 8032), SHA-256, HMAC-SHA256, RFC 8785 JCS canonical JSON and hashcash-style
proof-of-work. Nothing is home-made beyond the message formats.

## 1. Delegation certificates

```json
{"type":"qortime-referee-delegation/v1","serial":1,"root_id":"qortime-v1","root_key_id":"53eea1b5d23b1ff0",
 "delegate_id":"qortime-online-s1","delegate_pubkey":"…","delegate_key_id":"…",
 "scope":["official-review"],"allowed_statuses":["VERIFIED","PARTIAL"],
 "not_before_ms":…,"not_after_ms":…,"issued_ms":…,"sig":"<Ed25519 by root over JCS(cert without sig)>"}
```

- **Where it goes.** The receipt embeds the cert at `referee.delegation`, so the service's receipt signature covers it as
  well. The service also returns it at `/v1/referee` and `/health`.
- **What verifiers check.** The Python verifier (`qor_receipt/delegation.py` and `receipt.py`) and the JS verifier
  (`verify/verify.js`) apply the same rules. A receipt whose key is not on the allowlist is trusted only when all of these
  hold:
  - The cert is signed by an **active `can_delegate` root** on the built-in allowlist.
  - `delegate_pubkey` equals the receipt's referee key.
  - The scope includes `official-review` and `key_kind` is `qortime-official-review`.
  - The cert window is no longer than 120 days.
  - `review.reviewed_ms` falls inside `[not_before_ms, not_after_ms]` and is not more than 10 minutes in the future.
  - The serial and key are absent from the root-signed revocation list.
  - The final verdict is capped by `allowed_statuses`.
  
  Any failure means SELF_ATTESTED, never VERIFIED. Challenge tokens signed by a validated delegate also count as official.
- **Old receipts.** Receipts signed directly by an allowlisted key (the legacy receipts signed by `qortime-v1` itself)
  verify exactly as before.
- **Expiry vs revocation.** A cert that has simply expired leaves its receipts valid, because each was signed inside the
  window. **Revocation** (`qor_receipt/data/referee_revocations.json` and `verify/referee_revocations.json`, root-signed,
  versioned) removes trust from *every* receipt under that serial. Use it only on compromise. A missing or badly signed
  revocation list fails closed, so no delegated receipt is trusted.
- **Service behaviour.** The service refuses to start with a cert that is invalid for its key. It also refuses to sign
  once the cert is outside its window, so rotation is forced.
- **Publication.** Certs are published in `trust/delegations/serial-N.json`.

### Rotation (operator box only)

```
python scripts/referee_keys.py delegate --root /workspace/qortime-keys/qortime-referee-v1.key \
       --keys-dir /workspace/qortime-keys/online --days 60
# -> qortime-keys/online/qortime-online-sN.seed (0600, bare hex, never printed)
#    qortime-keys/online/qortime-online-sN.delegation.json  +  trust/delegations/serial-N.json (public)
# Railway: QORTIME_SERVICE_SEED <- the .seed file, QORTIME_DELEGATION <- the .delegation.json file
#          (substituted at call time; nothing echoed). Redeploy; check /health delegation.valid + days_left.
python scripts/referee_keys.py revoke --root … --serial N --reason "compromise"   # only on compromise
```

## 2. Transparency log

- **Entry format.** `{"type":"qortime-log-entry/v1","seq","ts_ms","kind":"receipt","body_sha256","submission","match_id","signer_key_id","prev","hash"}`
  - `hash` = SHA-256(JCS(entry without `hash`)).
  - `prev` = the previous entry's `hash`. The first entry's `prev` is 64 zeros.
  - `body_sha256` = SHA-256 of the receipt's signed bytes. It is independent of the player co-signature.
- **Write order.** The service appends and fsyncs the entry *before* it releases the receipt. The log is stored on the
  Railway volume at `/data/translog.jsonl`.
- **Endpoints.**
  - `GET /v1/log/head` returns the signed head `{size, head, ts_ms, pubkey, key_id, delegation_serial, sig}`.
  - `GET /v1/log?start=&limit=` returns a range of entries.
  - `GET /v1/log/inclusion/{body_sha256}` returns the entries from the receipt up to the head, plus the signed head.
- **Anchoring.** The `.github/workflows/anchor-log.yml` workflow runs every 6 h. It runs `scripts/translog.py anchor`,
  which checks four things: the head signature, that the signer's delegation is valid, continuity with the last anchored
  head (the old head must still be in the log at the same position), and the chain. It then commits
  `transparency/anchors.jsonl`. A rewound or rewritten log makes the job fail, and nothing is committed.
- **Inclusion check (optional).** `python scripts/translog.py inclusion receipt.official.json` runs the check in Python.
  `verifyInclusion()` does the same in `verify.js`.
- **What it buys.** A stolen online key can still sign. If the thief skips the log, the receipt has no inclusion proof.
  If the thief writes to the log, the entry is public and the operator can see it. A log rewrite breaks continuity with
  the anchors in git.
- **Limits.**
  - Inclusion is optional in the verifiers, so offline verification cannot require it.
  - The chain is linear, so an inclusion proof is O(n). A Merkle tree (RFC 6962/9162) is the upgrade path.
  - There is no third-party witness or gossip yet. The git anchors are the external witness, and they are only as
    frequent as the cron.

## 3. Signed, rate-limited submissions

1. The client fetches a nonce: `GET /v1/submit/challenge?pub=<install key>` returns `{nonce, expires_ms (5 min), pow_bits}`.
   - The nonce is stateless and bound to the key: `ts‖rand‖HMAC(server_secret, ts‖rand‖pub)[:16]`.
2. The client solves the proof-of-work: find `pow` such that `SHA-256("qortime-pow/v1|"+nonce+"|"+pub+"|"+pow)` has at
   least `pow_bits` leading zero bits.
   - Unknown keys need 20 bits, about 1 s in CPython. Known keys need 12.
3. The client sends `X-Qor-Submit: base64url({v:1,pub,ts_ms,nonce,pow,bundle_head,sig})`.
   - `sig` = Ed25519 over `JCS({type:"qortime-submit/v1",method,path,pub,ts_ms,nonce,pow,body_sha256,bundle_head})`.
   - `bundle_head` = SHA-256 of `manifest.jsonl`, the committed video chain.

The server checks run from cheap to expensive, and all of them run before any video is accepted:

1. Header present. A v0.3.9 client gets **HTTP 426** and a plain "QorCap 0.3.10 or newer is required" message.
2. Clock skew within ±5 min.
3. Nonce MAC, expiry and binding to this key.
4. **Single use.** A replay gets HTTP 409.
5. Proof-of-work.
6. **Per-key quota.** Unknown keys get 3/h and 6/day. Known keys get 10/h and 40/day. Over quota gets HTTP 429.
7. **Global cap.** 60/h and 400/day.
8. Read the size-capped meta zip.
9. Signature over the body hash.
10. `bundle_head` matches the uploaded manifest.
11. **Recorder binding.** The submit key must be one of the bundle's `receipt.json` player (device) keys. This is on by
    default (`QORTIME_REQUIRE_RECORDER_MATCH=1`).
12. The existing metadata re-check.

A key becomes "known" after a completed submission, meaning the referee countersigned and the device co-signed. The known
list is persisted on the volume.

`QORTIME_SUBMIT_TOKEN` (Bearer) is an admin override that skips all of the above. The live helpers (`/v1/challenges` and
`/v1/anchors`, used during Start Match) stay unauthenticated so v0.3.9 recordings keep working. They sit behind a global
cap of 1200/h.

## Prior art (novelty check, 2026-10-16)

Every building block is established. This design is an application of them, not a new primitive:

- **Sigstore.** An offline TUF root, Fulcio short-lived certs, and the Rekor transparency log, with verification that the
  signing time fell inside the cert window. That is the same root → short-lived key → log shape used here. Sigstore uses
  identity-bound ephemeral keys and a trusted timestamp. Here the service attests its own signing time, which is weaker
  (see the risks in `REFEREE_SERVICE.md`).
- **TUF delegations** and **X.509 / CT (RFC 6962/9162).** Delegated roles, signed revocation, and append-only logs with
  public monitoring.
- **Apple App Attest and Play Integrity.** Per-install keys used to authenticate requests and rate-limit per device. Those
  keys are hardware-attested. Ours are software keys with no attestation, so they deter floods and bind the submitter to
  the recorder, but they do not prove a genuine device.
- **HTTP Message Signatures (RFC 9421), DPoP (RFC 9449) and hashcash.** Signed requests with nonce or replay protection,
  and proof-of-work.
- **Gaming.** Braket's dual-client result attestation (both clients hash the result) and vendor offerings that chain
  match records with Merkle anchoring (for example EdSSA).

What is less common in this niche is the specific combination for post-hoc review of a single player's own recording:

- an offline-root-delegated referee key whose verdict level is capped in the cert;
- a public log of every countersigned match receipt, anchored in git;
- submissions signed by the *same* device key that recorded the bundle.

We make no claim to be first.
