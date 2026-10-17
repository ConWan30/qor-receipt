# QorTime official referee service — "verified uploads"

QorCap records on the player's own laptop, so everything it signs locally is **SELF_ATTESTED**: the laptop's
"referee" key sits next to the player. Verified uploads let that match earn **VERIFIED** from the official
QorTime referee (`qortime-v1`, pubkey `a5aa0c91…feea`) — but only after the referee has re-checked the bundle
itself.

Service: `https://qortime-referee-production.up.railway.app` (Railway, project `zesty-mindfulness`, service
`qortime-referee`, Dockerfile `services/referee/Dockerfile`). Code: `qor_receipt/review/` (engine, checks) and
`qor_receipt/referee_service/app.py` (HTTP). Client: `qorcap submit` / "Submit for Verification" in QorCap ≥ 0.3.9.

## Where the signing key lives

- The official Ed25519 seed is a **Railway service variable** `QORTIME_REFEREE_SEED` on `qortime-referee`
  (sealed; set from the operator box without being printed). It is read once at start-up into memory; it is not
  in git, not in the image, never logged and never returned by any endpoint (`/v1/referee` returns only the pubkey).
- The offline copy stays at `/workspace/qortime-keys/qortime-referee-v1.key` (0600) on the operator box.
- **Not** on any player laptop. The laptop keeps only its own device key and local capture-referee key.
- Who can sign as the official referee: this service process, and anyone with write access to the Railway project
  or the operator box. That is a deliberate trade (an always-on referee needs an online key). If the Railway
  project is ever compromised: rotate per `docs/TRUSTED_REFEREES.md` (new seed → new allowlist entry → old one
  `revoked`, so its receipts drop to SELF_ATTESTED). A dedicated online key (`qortime-online-v1`) with the offline
  `qortime-v1` kept cold is the recommended next step.

## Flow

```
laptop (QorCap)                                   referee service (official key)
1 POST /v1/submissions  zip of small files ─────▶ recompute manifest chain, checkpoint chain (frames_hash ==
  (manifest, checkpoints, countersigned,           JCS(qorcap)), inputs chunk chain + checkpoint binding, input
   inputs, haptics, input_proof, receipt.json,     proof file hashes, receipt.json signatures/timing under the
   official_challenges, anchors)                   laptop's own keys.  Reject on any error (HTTP 422).
                                    ◀──────────── commit = H(all of the above); THEN draw the plan:
                                                   init segment(s), recorded tail (last 240 s), segments around
                                                   each challenge's OPTIONS press, 16 random media segments
2 PUT /v1/submissions/{id}/segments/{seq} ─────▶ stream-hash each fMP4 segment; must equal the committed
  (raw bytes from video.mp4 at manifest offset)    sha256 + length (stored only if needed for decoding)
3 POST …/review ───────────────────────────────▶ decode tail with ffmpeg → QorCap's own ReaderCore (pinned
                                                   sidecar: scorebug + end-of-game menu) from scratch, no laptop
                                                   timeline; compare with the claimed final; re-run challenge
                                                   patterns from raw inputs.jsonl + pause seen on its own decode;
                                                   motion correlation over decoded seconds; then sign (or refuse)
4 POST …/player-signature (device key) ────────▶ receipt complete; segments deleted
```

`--full` (or `mode=full`) requires every segment (1–3 GB). Same checks; the plan's decode set is unchanged, the
rest is hashed in-stream and discarded.

## Receipt format

Same `qor-receipt/v0`, with:

- `referee = {pubkey: official, key_kind: "qortime-official-review"}` — the official signature covers everything,
  including the new `review` block.
- `review = {type: "qortime-bundle-review/v1", capture_referee, checks, scope, not_proven, final_reread, anchors, …}`.
  `checks.*.state ∈ pass | partial | fail`.
- `checkpoints` are the laptop's live countersigned chain; in review mode both verifiers check them under
  `review.capture_referee.pubkey` (still gap ≤ 10 s, skew ≤ 3 s).
- `challenges[*].issuer` = signed `qortime-challenge/v1` token; `result` is the referee's own re-run.
- `signals.motion_permille` is computed by the referee (`signals.motion_basis` says how).

## Verdict rules (Python `qor_receipt/receipt.py` = JS `verify/verify.js`)

| condition | status |
|---|---|
| any check `fail`, review block missing/forged, chain sig bad under capture key, any normal FAIL rule | FAIL |
| referee key not on the allowlist / revoked (e.g. a forged review by any other key) | SELF_ATTESTED |
| official key, but any check `partial`, or a phase without an **official** passed challenge, or motion < 300‰ | PARTIAL |
| official key + every check `pass` + kickoff/halftime/end official challenges re-verified + motion ok | VERIFIED |

Verifier output adds `verification_basis: "official-post-hoc-review"` and `review.{scope, video_method,
segments_verified, segments_total, not_proven}`; the browser verifier prints the basis line.

## What VERIFIED means here (honest trust level)

Proven by the referee itself:
- the bundle's hash chains are internally consistent and bind video segment hashes, controller log and checkpoints;
- the laptop holds video bytes matching the committed manifest — **all** of them (`full`), or, in spot-check
  mode, every requested segment; a laptop missing a fraction *f* of the segments passes the random part with
  probability (1−f)^16 (10 % missing → 0.185; 50 % → 1.5e-5). Unsampled content is not inspected;
- the final score comes from the referee's own read of the recorded end-of-game frames;
- with official challenges: the pattern was pressed after the referee issued the nonce (unknowable before), and
  the challenge's chain-head anchor shows the earlier video already existed then. Anchors (`/v1/anchors`, every
  60 s during Start Match) timestamp the chain the same way.

Not proven (also embedded in every receipt as `review.not_proven`):
- who held the controller;
- that the laptop's countersignatures were live (its key is on the player's PC) — official anchors/challenges are
  what tie the chain to real time;
- that footage wasn't fabricated/edited before QorCap hashed it, outside anchored windows;
- QorCap's human-likeness / input-video alignment model is not re-run (hashes and counts are);
- delta corroboration for a one-legal-step end-menu delta still uses QorCap's existing evidence rules
  (haptics / inputs from the committed log; score-clip hashes as claimed in bundle.json — clip bytes not uploaded).

## Operations

```
GET  /health                 {"ok":true,"allowlisted":true}
GET  /v1/referee             pubkey, key_id, policy, final_reader/ffmpeg availability
POST /v1/challenges          {"phase":"kickoff|halftime|end","role":"home","match_id":"…","anchor":{"chain_head":"…"}}
POST /v1/anchors             {"match_id":"…","anchor":{"chain_head":"…","segment_seq":N,"checkpoint_head":"…"}}
POST /v1/submissions?mode=spot-check|full     body: application/zip
```

Env: `QORTIME_REFEREE_SEED` (secret), `QORTIME_REFEREE_ID`, `QORCAP_SIDECAR` (baked into the image at a pinned
QorCap SHA), `QORTIME_RANDOM_SEGMENTS` (16), `QORTIME_TAIL_MS` (240000), `QORTIME_MAX_ACTIVE` (4),
`QORTIME_SUBMIT_TOKEN` (optional shared secret for submissions; unset = open).

In-flight submissions live in `/tmp` and expire after 1 h (a restart drops them; the client just re-submits).
The service has no volume, so nothing is persisted.

### Deployment record (2026-10-16 CDT)

- Railway service `qortime-referee` `fefa2ecd-b255-4ee8-ba71-6607f24ccc22` runs in project `zesty-mindfulness`
  (`d2fab6f8-…`), environment `production`. Source is `ConWan30/qortime@main`. The Dockerfile path is set to
  `services/referee/Dockerfile` (service config plus `RAILWAY_DOCKERFILE_PATH`). Watch patterns are `qor_receipt/**`,
  `services/referee/**` and `pyproject.toml`. The healthcheck is `/health`.
- `QORTIME_REFEREE_SEED` was set with the Railway MCP `set-variables` call. The value was substituted from
  the operator box file `qortime-keys/qortime-referee-v1.key` at call time, so it was never printed, logged or committed.
  `QORTIME_REFEREE_ID=qortime-v1` and `QORTIME_MAX_ACTIVE=4`. `QORTIME_SUBMIT_TOKEN` is unset, so submissions are open.
  The size caps and the max-active limit bound abuse.
- After deploy, `GET /v1/referee` reported pubkey `a5aa0c91…feea`, `allowlisted: true`, `final_reader: true` and
  `ffmpeg: true`.
- To rotate, set a new seed the same way, add the new allowlist entry and retire the old one (`TRUSTED_REFEREES.md`).

Cost: one small always-on Railway container (≈0.25–0.5 vCPU idle-light, 512 MB–1 GB RAM while decoding).
Ingress is free on Railway; a spot-check is ~150–300 MB per match (mostly the 240 s tail), `--full` 1–3 GB.
Review CPU is ~10–60 s per match. Egress is tiny (JSON). Stripe is not involved.

Local run:

```
pip install -e ".[referee,vision]"
QORTIME_REFEREE_SEED=<hex> QORCAP_SIDECAR=/path/to/qorcap/sidecar qortime referee-service --port 8080
```
