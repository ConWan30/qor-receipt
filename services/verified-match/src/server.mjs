import { createApp } from "./app.mjs";
import { keyClass } from "./fulfill.mjs";
import { liveStripe } from "./stripe-client.mjs";
import { fileStore } from "./store.mjs";

const env = process.env;
const secret = env.STRIPE_SECRET_KEY || "";
const klass = keyClass(secret);
if (klass === "live") {
  console.error("Live Stripe key refused. QorTime Verified Match stays in test mode.");
}
const stripe = klass === "test" ? liveStripe(secret) : null;
const store = fileStore(env.STORE_PATH || "/data/receipts.json");
const port = Number(env.PORT || 8080);

const server = createApp({ env, store, stripe });
server.listen(port, "0.0.0.0", () => {
  console.log(`qortime-verified-match listening on ${port} (test mode)`);
});
