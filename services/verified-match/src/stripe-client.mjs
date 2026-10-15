import Stripe from "stripe";

export function liveStripe(secretKey) {
  return new Stripe(secretKey, {
    apiVersion: "2026-09-30.endive",
    appInfo: {
      name: "qortime-verified-match",
      version: "0.1.0",
    },
  });
}
