import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { createApp } from "../src/app.mjs";
import {
  buildCheckoutParams,
  decideFulfillment,
  keyClass,
  lineItemsIncludePrice,
} from "../src/fulfill.mjs";
import { receiptIdFrom } from "../src/receipt-id.mjs";
import { fileStore, memoryStore } from "../src/store.mjs";

const PRICE = "price_1UQruXCepyIJsHZulKEdcVkX";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "../../..");

function listen(server) {
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

function close(server) {
  return new Promise((resolve, reject) => {
    server.close((err) => (err ? reject(err) : resolve()));
  });
}

test("receipt id comes from receipt_id or match.match_id", () => {
  assert.equal(receiptIdFrom({ receipt_id: "abc_1", match: { match_id: "other" } }), "abc_1");
  assert.equal(receiptIdFrom({ match: { match_id: "32b29a8a383c4849" } }), "32b29a8a383c4849");
  assert.equal(receiptIdFrom({ match: { match_id: "has space" } }), null);
  assert.equal(receiptIdFrom(null), null);
});

test("checkout params tie the receipt and omit payment method allowlists and tax", () => {
  const params = buildCheckoutParams("match_42", {
    priceId: PRICE,
    site: "https://conwan30.github.io/qortime",
  });
  assert.equal(params.mode, "payment");
  assert.equal(params.ui_mode, "hosted_page");
  assert.equal(params.origin_context, "web");
  assert.equal(params.client_reference_id, "match_42");
  assert.equal(params.metadata.receipt_id, "match_42");
  assert.equal(params.line_items[0].price, PRICE);
  assert.equal(params.line_items[0].quantity, 1);
  assert.equal(params.adaptive_pricing.enabled, true);
  assert.equal("payment_method_types" in params, false);
  assert.equal("automatic_tax" in params, false);
  assert.match(params.success_url, /^https:\/\/conwan30\.github\.io\/qortime\/verify\/\?checkout=success/);
  assert.match(params.success_url, /session_id=\{CHECKOUT_SESSION_ID\}$/);
  assert.match(params.cancel_url, /checkout=cancel#verified-match$/);
  assert.match(params.integration_identifier, /[a-z]{8}$/);
});

test("key class refuses live prefixes and accepts test prefixes", () => {
  assert.equal(keyClass(""), "missing");
  assert.equal(keyClass("rk_" + "live_" + "example"), "live");
  assert.equal(keyClass("sk_" + "live_" + "example"), "live");
  assert.equal(keyClass("rk_" + "test_" + "example"), "test");
  assert.equal(keyClass("not-a-key"), "unrecognized");
});

test("fulfillment records a paid test session only when the price matches", () => {
  const session = {
    id: "cs_test_123",
    livemode: false,
    mode: "payment",
    payment_status: "paid",
    metadata: { receipt_id: "match_42" },
    client_reference_id: "match_42",
  };
  const paid = decideFulfillment(
    { type: "checkout.session.completed", livemode: false },
    session,
    { priceOk: true },
  );
  assert.deepEqual(paid, { action: "record", receiptId: "match_42" });

  const unpaid = decideFulfillment(
    { type: "checkout.session.completed", livemode: false },
    { ...session, payment_status: "unpaid" },
    { priceOk: true },
  );
  assert.equal(unpaid.action, "pending");

  const wrongPrice = decideFulfillment(
    { type: "checkout.session.async_payment_succeeded", livemode: false },
    session,
    { priceOk: false },
  );
  assert.equal(wrongPrice.action, "ignore");
  assert.equal(wrongPrice.reason, "price");

  const live = decideFulfillment(
    { type: "checkout.session.completed", livemode: true },
    { ...session, livemode: true },
    { priceOk: true },
  );
  assert.equal(live.reason, "livemode");

  assert.equal(lineItemsIncludePrice([{ price: { id: PRICE } }], PRICE), true);
  assert.equal(lineItemsIncludePrice([{ price: "price_other" }], PRICE), false);
});

test("file store keeps the first paid record", async () => {
  const dir = await mkdtemp(join(tmpdir(), "qortime-vm-"));
  const store = fileStore(join(dir, "receipts.json"));
  const first = await store.save({
    receipt_id: "match_42",
    paid: true,
    recorded_at: "2026-10-15T00:00:00.000Z",
  });
  const second = await store.save({
    receipt_id: "match_42",
    paid: true,
    recorded_at: "2026-10-15T01:00:00.000Z",
  });
  assert.equal(second.recorded_at, first.recorded_at);
  const raw = JSON.parse(await readFile(join(dir, "receipts.json"), "utf8"));
  assert.equal(raw.match_42.paid, true);
});

function fakeStripe(overrides = {}) {
  const created = [];
  return {
    created,
    checkout: {
      sessions: {
        async create(params) {
          created.push(params);
          return { id: "cs_test_mock", url: "https://checkout.stripe.com/c/pay/cs_test_mock" };
        },
        async listLineItems() {
          return { data: [{ price: { id: PRICE } }] };
        },
      },
    },
    webhooks: {
      constructEvent(raw, signature, secret) {
        if (signature !== "ok" || secret !== "webhook-secret") {
          throw new Error("bad signature");
        }
        return JSON.parse(raw.toString("utf8"));
      },
    },
    ...overrides,
  };
}

async function withServer(ctx, fn) {
  const server = createApp(ctx);
  const port = await listen(server);
  try {
    await fn(`http://127.0.0.1:${port}`);
  } finally {
    await close(server);
  }
}

test("checkout and receipt routes stay in test mode", async () => {
  const store = memoryStore();
  const stripe = fakeStripe();
  const env = {
    STRIPE_PRICE_ID: PRICE,
    STRIPE_WEBHOOK_SECRET: "webhook-secret",
    SITE_ORIGIN: "https://conwan30.github.io/qortime",
  };
  await withServer({ env, store, stripe, keyClass: "test", now: () => 1_700_000_000_000 }, async (base) => {
    const health = await fetch(`${base}/health`);
    assert.equal(health.status, 200);
    const healthBody = await health.json();
    assert.equal(healthBody.mode, "test");
    assert.equal(healthBody.livemode, false);
    assert.equal(healthBody.stripe_key, "test");

    const missing = await fetch(`${base}/v1/receipts/match_42`);
    assert.equal((await missing.json()).paid, false);

    const bad = await fetch(`${base}/v1/checkout`, {
      method: "POST",
      headers: { "content-type": "application/json", origin: "https://conwan30.github.io" },
      body: JSON.stringify({ receipt_id: "bad id", receipt_sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }),
    });
    assert.equal(bad.status, 400);
    const missingSha = await fetch(`${base}/v1/checkout`, {
      method: "POST",
      headers: { "content-type": "application/json", origin: "https://conwan30.github.io" },
      body: JSON.stringify({ receipt_id: "match_42" }),
    });
    assert.equal(missingSha.status, 400);


    const created = await fetch(`${base}/v1/checkout`, {
      method: "POST",
      headers: { "content-type": "application/json", origin: "https://conwan30.github.io" },
      body: JSON.stringify({ receipt_id: "match_42", receipt_sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }),
    });
    assert.equal(created.status, 200);
    assert.equal(created.headers.get("access-control-allow-origin"), "https://conwan30.github.io");
    const createdBody = await created.json();
    assert.equal(createdBody.url, "https://checkout.stripe.com/c/pay/cs_test_mock");
    assert.equal(stripe.created[0].metadata.receipt_id, "match_42");
    assert.equal(stripe.created[0].metadata.receipt_sha256, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    assert.equal(stripe.created[0].client_reference_id, "match_42");

    const unpaid = await fetch(`${base}/v1/webhook`, {
      method: "POST",
      headers: { "stripe-signature": "ok" },
      body: JSON.stringify({
        type: "checkout.session.completed",
        livemode: false,
        data: {
          object: {
            id: "cs_test_unpaid",
            livemode: false,
            mode: "payment",
            payment_status: "unpaid",
            metadata: { receipt_id: "match_42" },
          },
        },
      }),
    });
    assert.equal((await unpaid.json()).action, "pending");
    assert.equal((await (await fetch(`${base}/v1/receipts/match_42`)).json()).paid, false);

    const paid = await fetch(`${base}/v1/webhook`, {
      method: "POST",
      headers: { "stripe-signature": "ok" },
      body: JSON.stringify({
        type: "checkout.session.completed",
        livemode: false,
        data: {
          object: {
            id: "cs_test_paid",
            livemode: false,
            mode: "payment",
            payment_status: "paid",
            metadata: { receipt_id: "match_42" },
            client_reference_id: "match_42",
          },
        },
      }),
    });
    assert.equal((await paid.json()).action, "record");
    const badge = await (await fetch(`${base}/v1/receipts/match_42`)).json();
    assert.equal(badge.paid, true);
    assert.equal(badge.tier, "verified-match");
    assert.equal(badge.mode, "test");
    assert.equal(badge.livemode, false);
    assert.equal("session_id" in badge, false);

    const rejected = await fetch(`${base}/v1/webhook`, {
      method: "POST",
      headers: { "stripe-signature": "nope" },
      body: "{}",
    });
    assert.equal(rejected.status, 400);
  });
});

test("live keys and live events never mark a receipt paid", async () => {
  const store = memoryStore();
  const stripe = fakeStripe();
  const env = { STRIPE_PRICE_ID: PRICE, STRIPE_WEBHOOK_SECRET: "webhook-secret" };
  await withServer({ env, store, stripe, keyClass: "live" }, async (base) => {
    const checkout = await fetch(`${base}/v1/checkout`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ receipt_id: "match_42", receipt_sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }),
    });
    assert.equal(checkout.status, 403);
    assert.equal(stripe.created.length, 0);
  });

  await withServer({ env, store, stripe, keyClass: "test" }, async (base) => {
    const liveEvent = await fetch(`${base}/v1/webhook`, {
      method: "POST",
      headers: { "stripe-signature": "ok" },
      body: JSON.stringify({
        type: "checkout.session.completed",
        livemode: true,
        data: {
          object: {
            id: "cs_live_nope",
            livemode: true,
            mode: "payment",
            payment_status: "paid",
            metadata: { receipt_id: "match_42" },
          },
        },
      }),
    });
    assert.equal((await liveEvent.json()).reason, "livemode");
    assert.equal((await (await fetch(`${base}/v1/receipts/match_42`)).json()).paid, false);
  });
});

test("launch page and verifier mention the test payment link", async () => {
  const home = await readFile(join(ROOT, "site/index.html"), "utf8");
  const verify = await readFile(join(ROOT, "verify/index.html"), "utf8");
  const config = await readFile(join(ROOT, "verify/verified-match.js"), "utf8");
  assert.match(home, /Buy Verified Match \(test\)/);
  assert.match(home, /https:\/\/buy\.stripe\.com\/test_3cI4gy7he9Uuarqczl00000/);
  assert.match(verify, /Verified Match/);
  assert.match(verify, /verified-match\.js/);
  assert.match(config, /buy\.stripe\.com\/test_3cI4gy7he9Uuarqczl00000/);
  assert.doesNotMatch(home + verify + config, /sk_live_|sk_test_|whsec_/);
});
