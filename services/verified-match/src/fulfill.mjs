import { isValidReceiptId } from "./receipt-id.mjs";

export const INTEGRATION_IDENTIFIER = "qortime_verified_match_hnqwlxpt";

export const FULFILL_EVENTS = new Set([
  "checkout.session.completed",
  "checkout.session.async_payment_succeeded",
]);

const SITE_DEFAULT = "https://conwan30.github.io/qortime";

export function keyClass(key) {
  if (!key) return "missing";
  const live = "sk_" + "live_";
  const liveRestricted = "rk_" + "live_";
  const test = "sk_" + "test_";
  const testRestricted = "rk_" + "test_";
  if (key.startsWith(live) || key.startsWith(liveRestricted)) return "live";
  if (key.startsWith(test) || key.startsWith(testRestricted)) return "test";
  return "unrecognized";
}

export function siteOrigin(env) {
  const raw = (env.SITE_ORIGIN || SITE_DEFAULT).trim().replace(/\/$/, "");
  if (!/^https:\/\/[a-z0-9.-]+(?::\d+)?(?:\/[A-Za-z0-9._~/-]*)?$/i.test(raw)) {
    return SITE_DEFAULT;
  }
  return raw;
}

export function buildCheckoutParams(receiptId, { priceId, site }) {
  const root = site.replace(/\/$/, "");
  return {
    mode: "payment",
    ui_mode: "hosted_page",
    origin_context: "web",
    adaptive_pricing: { enabled: true },
    line_items: [{ price: priceId, quantity: 1 }],
    client_reference_id: receiptId,
    metadata: {
      receipt_id: receiptId,
      product: "verified_match",
      mode: "test",
    },
    payment_intent_data: {
      metadata: {
        receipt_id: receiptId,
        product: "verified_match",
      },
    },
    success_url: `${root}/verify/?checkout=success&receipt_id=${encodeURIComponent(receiptId)}&session_id={CHECKOUT_SESSION_ID}`,
    cancel_url: `${root}/?checkout=cancel#verified-match`,
    integration_identifier: INTEGRATION_IDENTIFIER,
  };
}

export function sessionReceiptId(session) {
  const fromMeta = session?.metadata?.receipt_id;
  if (typeof fromMeta === "string" && fromMeta.length > 0) return fromMeta;
  const fromRef = session?.client_reference_id;
  if (typeof fromRef === "string" && fromRef.length > 0) return fromRef;
  return null;
}

export function lineItemsIncludePrice(items, priceId) {
  return items.some((item) => {
    const price = item?.price;
    const id = typeof price === "string" ? price : price?.id;
    return id === priceId;
  });
}

export function decideFulfillment(event, session, { priceOk }) {
  const known =
    FULFILL_EVENTS.has(event.type) || event.type === "checkout.session.async_payment_failed";
  if (!known) return { action: "ignore", reason: "event" };
  if (session?.livemode === true || event.livemode === true) {
    return { action: "ignore", reason: "livemode" };
  }
  const receiptId = sessionReceiptId(session);
  if (!isValidReceiptId(receiptId)) return { action: "ignore", reason: "receipt_id" };
  if (event.type === "checkout.session.async_payment_failed") {
    return { action: "fail", reason: "async_failed", receiptId };
  }
  if (session.mode && session.mode !== "payment") {
    return { action: "ignore", reason: "mode", receiptId };
  }
  if (session.payment_status === "unpaid") {
    return { action: "pending", reason: "unpaid", receiptId };
  }
  if (!priceOk) return { action: "ignore", reason: "price", receiptId };
  return { action: "record", receiptId };
}

export function isCheckoutUrl(url) {
  if (typeof url !== "string") return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && parsed.hostname === "checkout.stripe.com";
  } catch {
    return false;
  }
}
