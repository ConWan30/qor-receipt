import { createServer } from "node:http";
import {
  buildCheckoutParams,
  decideFulfillment,
  isCheckoutUrl,
  keyClass,
  lineItemsIncludePrice,
  siteOrigin,
} from "./fulfill.mjs";
import { isValidReceiptId } from "./receipt-id.mjs";
import { toPublic } from "./store.mjs";

const CHECKOUT_LIMIT = 8 * 1024;
const WEBHOOK_LIMIT = 1024 * 1024;
const PRICE_ID = /^price_[A-Za-z0-9]+$/;

function json(res, status, body, extraHeaders = {}) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "content-length": Buffer.byteLength(payload),
    ...extraHeaders,
  });
  res.end(payload);
}

function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(Object.assign(new Error("body too large"), { statusCode: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

function clientIp(req) {
  const forwarded = req.headers["x-forwarded-for"];
  if (typeof forwarded === "string" && forwarded.length > 0) {
    return forwarded.split(",")[0].trim();
  }
  return req.socket?.remoteAddress || "unknown";
}

function rateLimiter() {
  const hits = new Map();
  return function limited(ip, now) {
    const windowMs = 60_000;
    const max = 20;
    const row = hits.get(ip);
    if (!row || now - row.start >= windowMs) {
      hits.set(ip, { start: now, count: 1 });
      return false;
    }
    row.count += 1;
    return row.count > max;
  };
}


function allowOrigin(req, env) {
  const origin = req.headers.origin;
  if (typeof origin !== "string" || origin.length === 0) return null;
  let siteHost = "";
  try {
    siteHost = new URL(siteOrigin(env)).origin;
  } catch {
    siteHost = "";
  }
  const local = /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/;
  if (origin === "https://conwan30.github.io" || origin === siteHost || local.test(origin)) {
    return origin;
  }
  return null;
}

function corsHeaders(req, env) {
  const origin = allowOrigin(req, env);
  if (!origin) return {};
  return {
    "access-control-allow-origin": origin,
    vary: "Origin",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "Content-Type",
  };
}

function safeError(err) {
  const message = err instanceof Error ? err.message : "error";
  if (/sk_|rk_|whsec_/.test(message)) return "stripe request failed";
  return message;
}

function configuredPrice(env) {
  const priceId = (env.STRIPE_PRICE_ID || "").trim();
  if (!PRICE_ID.test(priceId)) return null;
  return priceId;
}

function resolvedKeyClass(ctx) {
  if (ctx.keyClass) return ctx.keyClass;
  return keyClass(ctx.env.STRIPE_SECRET_KEY || "");
}

async function priceMatches(stripe, session, priceId) {
  const listed = await stripe.checkout.sessions.listLineItems(session.id, { limit: 20 });
  const rows = Array.isArray(listed?.data) ? listed.data : [];
  return lineItemsIncludePrice(rows, priceId);
}

export async function handle(req, res, ctx) {
  const env = ctx.env || {};
  const cors = corsHeaders(req, env);
  const url = new URL(req.url || "/", "http://127.0.0.1");
  const method = req.method || "GET";

  if (method === "OPTIONS") {
    res.writeHead(204, cors);
    res.end();
    return;
  }

  if (method === "GET" && url.pathname === "/health") {
    const klass = resolvedKeyClass(ctx);
    json(
      res,
      200,
      {
        ok: true,
        service: "qortime-verified-match",
        mode: "test",
        livemode: false,
        stripe_key: klass === "test" ? "test" : klass,
        webhook: env.STRIPE_WEBHOOK_SECRET ? "configured" : "missing",
        price: configuredPrice(env) ? "configured" : "missing",
      },
      cors,
    );
    return;
  }

  if (method === "GET" && url.pathname === "/") {
    json(
      res,
      200,
      {
        service: "qortime-verified-match",
        mode: "test",
        endpoints: ["/health", "/v1/checkout", "/v1/webhook", "/v1/receipts/:receipt_id"],
      },
      cors,
    );
    return;
  }

  const receiptMatch = url.pathname.match(/^\/v1\/receipts\/([^/]+)$/);
  if (method === "GET" && receiptMatch) {
    let receiptId = "";
    try {
      receiptId = decodeURIComponent(receiptMatch[1]);
    } catch {
      json(res, 400, { error: "Invalid receipt id" }, cors);
      return;
    }
    if (!isValidReceiptId(receiptId)) {
      json(res, 400, { error: "Invalid receipt id" }, cors);
      return;
    }
    const record = await ctx.store.get(receiptId);
    json(res, 200, toPublic(record, receiptId), cors);
    return;
  }

  if (method === "POST" && url.pathname === "/v1/checkout") {
    const now = ctx.now ? ctx.now() : Date.now();
    const isLimited = ctx.limited || rateLimiter();
    if (isLimited(clientIp(req), now)) {
      json(res, 429, { error: "Too many checkout attempts. Wait a minute and try again." }, cors);
      return;
    }
    const klass = resolvedKeyClass(ctx);
    if (klass === "live") {
      json(res, 403, { error: "Live mode is refused. This service accepts test keys only." }, cors);
      return;
    }
    if (klass !== "test" || !ctx.stripe) {
      json(res, 503, { error: "Test checkout is not configured yet." }, cors);
      return;
    }
    const priceId = configuredPrice(env);
    if (!priceId) {
      json(res, 503, { error: "STRIPE_PRICE_ID is not set." }, cors);
      return;
    }
    let body;
    try {
      body = JSON.parse((await readBody(req, CHECKOUT_LIMIT)).toString("utf8") || "{}");
    } catch (err) {
      const status = err && err.statusCode === 413 ? 413 : 400;
      json(res, status, { error: status === 413 ? "Body too large" : "Expected JSON" }, cors);
      return;
    }
    const receiptId = body?.receipt_id;
    if (!isValidReceiptId(receiptId)) {
      json(res, 400, { error: "receipt_id must be 1–128 letters, numbers, or . _ : -" }, cors);
      return;
    }
    const params = buildCheckoutParams(receiptId, { priceId, site: siteOrigin(env) });
    let session;
    try {
      const minute = Math.floor(now / 60_000);
      session = await ctx.stripe.checkout.sessions.create(params, {
        idempotencyKey: `qortime_vm_${receiptId}_${minute}`,
      });
    } catch (err) {
      console.error("checkout failed", safeError(err));
      json(res, 502, { error: "Could not create the test checkout session." }, cors);
      return;
    }
    if (!isCheckoutUrl(session?.url)) {
      json(res, 502, { error: "Stripe did not return a hosted checkout URL." }, cors);
      return;
    }
    json(res, 200, { url: session.url }, cors);
    return;
  }

  if (method === "POST" && url.pathname === "/v1/webhook") {
    if (!env.STRIPE_WEBHOOK_SECRET || !ctx.stripe) {
      json(res, 503, { error: "Webhook is not configured yet." }, cors);
      return;
    }
    if (resolvedKeyClass(ctx) === "live") {
      json(res, 200, { received: true, action: "ignore", reason: "live_key" }, cors);
      return;
    }
    const signature = req.headers["stripe-signature"];
    let raw;
    try {
      raw = await readBody(req, WEBHOOK_LIMIT);
    } catch (err) {
      const status = err && err.statusCode === 413 ? 413 : 400;
      json(res, status, { error: status === 413 ? "Body too large" : "Unreadable body" }, cors);
      return;
    }
    let event;
    try {
      event = ctx.stripe.webhooks.constructEvent(raw, signature, env.STRIPE_WEBHOOK_SECRET);
    } catch (err) {
      console.error("webhook signature rejected", safeError(err));
      json(res, 400, { error: "Invalid webhook signature" }, cors);
      return;
    }
    const session = event.data?.object ?? {};
    const priceId = configuredPrice(env);
    let priceOk = false;
    const needsPrice =
      event.type === "checkout.session.completed" ||
      event.type === "checkout.session.async_payment_succeeded";
    if (needsPrice && priceId && session.id && session.livemode !== true && event.livemode !== true) {
      try {
        priceOk = await priceMatches(ctx.stripe, session, priceId);
      } catch (err) {
        console.error("line item lookup failed", safeError(err));
        json(res, 500, { error: "Could not confirm the checkout price." }, cors);
        return;
      }
    }
    const decision = decideFulfillment(event, session, { priceOk });
    if (decision.action === "record") {
      await ctx.store.save({
        receipt_id: decision.receiptId,
        paid: true,
        mode: "test",
        livemode: false,
        tier: "verified-match",
        payment_status: session.payment_status || "paid",
        price_id: priceId,
        session_id: session.id,
        recorded_at: new Date(ctx.now ? ctx.now() : Date.now()).toISOString(),
      });
    } else if (decision.action === "fail") {
      await ctx.store.save({
        receipt_id: decision.receiptId,
        paid: false,
        mode: "test",
        livemode: false,
        tier: null,
        payment_status: "failed",
        session_id: session.id,
        recorded_at: new Date(ctx.now ? ctx.now() : Date.now()).toISOString(),
      });
    }
    json(res, 200, { received: true, action: decision.action, reason: decision.reason || null }, cors);
    return;
  }

  json(res, 404, { error: "Not found" }, cors);
}

export function createApp(ctx) {
  const appCtx = { limited: rateLimiter(), ...ctx };
  return createServer((req, res) => {
    handle(req, res, appCtx).catch((err) => {
      console.error("request failed", safeError(err));
      if (res.headersSent) return;
      json(res, 500, { error: "Internal error" });
    });
  });
}
