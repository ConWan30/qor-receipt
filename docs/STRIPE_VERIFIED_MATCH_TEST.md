# QorTime — Verified Match (Stripe test mode)

**Honesty:** Test money only. Business KYC / live account details are not complete.
Do not treat these IDs as production. Do not commit Stripe secret keys. Do not switch this service to live mode.

## Account

| Field | Value |
|---|---|
| Connected Stripe context | `acct_1UQVjjCepyIJsHZu` |
| Account name | New business sandbox |
| Mode | **test** (`livemode: false`) |
| Note | Session connected this sandbox; live-style id `acct_1UQVjXCatAPuUvUA` was named in planning but is not the MCP session account. Stay in test until KYC is done. |

## Catalog (created 2026-10-15 CT)

| Resource | ID |
|---|---|
| Product | `prod_VRlRQfOgasBJJj` |
| Name | Verified Match |
| Price | `price_1UQruXCepyIJsHZulKEdcVkX` |
| Amount | **$9.99 USD** (`unit_amount: 999`, one-time) |
| Payment Link | `plink_1UQrueCepyIJsHZuXcqSnjnH` |
| Payment Link URL | https://buy.stripe.com/test_3cI4gy7he9Uuarqczl00000 |

Product description: QorTime signed Madden match receipt countersigned by a hosted
referee after a completed match (test-mode Verified Match).

## What not to store here

- Secret or restricted API keys (`sk_test_…` / `sk_live_…` / `rk_test_…` / `rk_live_…`)
- Webhook signing secrets (`whsec_…`)

Publishable keys (`pk_test_…`) may be used client-side later. This integration does not put a key in the browser. Checkout is a redirect to Stripe-hosted Checkout or the existing Payment Link.

Prefer a **restricted test key** (`rk_test_…`) with write access to Checkout Sessions over a secret key. The service refuses any live key.

## What shipped

1. **Site CTA** on `site/index.html` (GitHub Pages: https://conwan30.github.io/qortime/). A navy/aqua/gold **Buy Verified Match (test)** button in the hero and in the `#verified-match` section opens the Payment Link above. The label says test. Cancel returns to `/?checkout=cancel#verified-match`.
2. **Receipt-tied Checkout** in `services/verified-match/` (Node 22, Stripe Node SDK 23, API `2026-09-30.endive`). `POST /v1/checkout` creates a hosted Checkout Session for `STRIPE_PRICE_ID` with `metadata.receipt_id`, `client_reference_id`, and `payment_intent_data.metadata.receipt_id`. Success URL: `{SITE}/verify/?checkout=success&receipt_id=…&session_id={CHECKOUT_SESSION_ID}`. Dynamic payment methods stay on (`payment_method_types` is not sent). Tax is off (no registration). Adaptive Pricing is enabled.
3. **Webhook** `POST /v1/webhook` verifies `Stripe-Signature` with `STRIPE_WEBHOOK_SECRET`, then handles:
   - `checkout.session.completed`
   - `checkout.session.async_payment_succeeded`
   - `checkout.session.async_payment_failed`
   A receipt is marked paid only when `livemode` is false, `payment_status` is not `unpaid`, and a line item price equals `STRIPE_PRICE_ID`. Live events are acknowledged and ignored.
4. **Store** is a JSON object at `STORE_PATH` (default `/data/receipts.json`). On Railway, mount a volume at `/data` so a redeploy does not wipe paid receipts.
5. **Verifier badge** on `verify/index.html`. The receipt id is `receipt_id` when present, otherwise `match.match_id`. `GET /v1/receipts/:receipt_id` drives a gold **Verified Match** badge labeled **TEST MODE**. The signature verdict is unchanged. The receipt JSON is not uploaded.

`verify/verified-match.js` holds the public catalog ids and `apiBase`. Set `apiBase` to the deployed service origin (no trailing slash) when the host URL exists. On `localhost` only, `?api=http://127.0.0.1:PORT` overrides it for a local badge check.

## Environment

Set these on the host. Never commit the values.

| Name | Required | Purpose |
|---|---|---|
| `STRIPE_SECRET_KEY` | yes | Restricted test key (`rk_test_…`) with Checkout Sessions write. Live keys are refused. |
| `STRIPE_WEBHOOK_SECRET` | yes | Signing secret for the webhook endpoint. |
| `STRIPE_PRICE_ID` | yes | `price_1UQruXCepyIJsHZulKEdcVkX` |

Optional, not secrets:

| Name | Default |
|---|---|
| `PORT` | `8080` |
| `STORE_PATH` | `/data/receipts.json` |
| `SITE_ORIGIN` | `https://conwan30.github.io/qortime` |

Empty template: `services/verified-match/.env.example`.

## HTTP

| Method | Path | Role |
|---|---|---|
| `GET` | `/health` | Liveness. Reports whether key, webhook, and price are configured. Does not echo secrets. |
| `POST` | `/v1/checkout` | Body `{"receipt_id":"<id>"}`. Response `{"url":"https://checkout.stripe.com/…"}`. CORS allows `https://conwan30.github.io` and localhost. |
| `POST` | `/v1/webhook` | Stripe webhook. Raw body, `Stripe-Signature` required. |
| `GET` | `/v1/receipts/:receipt_id` | Public paid lookup. `paid`, `tier` (`verified-match` or null), `mode: "test"`, `livemode: false`. |

Receipt ids are 1–128 characters from `[A-Za-z0-9._:-]`.

## Deploy (Railway)

The service is `services/verified-match` (Dockerfile, `railway.toml`). Root directory on Railway: `services/verified-match`. Health check: `/health`. Volume mount: `/data`.

```bash
cd services/verified-match
npm ci
npm test
npm start
```

Operator steps still required before a real test card payment can stamp a badge:

1. Put `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and `STRIPE_PRICE_ID` in the Railway service variables (or whatever host you use). Do not commit them.
2. In the Stripe **test** Dashboard, add a webhook endpoint aimed at `https://<service-host>/v1/webhook` for the three `checkout.session.*` events above. Paste its signing secret into `STRIPE_WEBHOOK_SECRET`.
3. Set `apiBase` in `verify/verified-match.js` to `https://<service-host>` and deploy GitHub Pages (push to `main` updates `site/**` and `verify/**`).
4. Keep the service awake. A sleeping host can miss the webhook that paints the badge.

Local webhook forwarding, once the secret is in the environment and not in git:

```bash
stripe listen --forward-to localhost:8080/v1/webhook
```

Use the listen signing secret only in that local environment.

## Test cards

Use Stripe test cards (for example `4242 4242 4242 4242`) from the Dashboard docs. Any future date, any CVC. No real charges in this mode.

## Go live later

Complete Stripe business KYC, then create live-mode products, prices, and a live Payment Link. Point a separate production service at live keys. Do not reuse this test Payment Link, test price, or test webhook secret in production.
