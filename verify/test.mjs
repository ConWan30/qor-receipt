// node verify/test.mjs : JCS parity + JS verifier against the Python-produced vectors.
import { readFileSync } from "node:fs";
import { canonicalize, sha256Hex, verifyReceipt, challengeEvidenceErrors } from "./verify.js";
const v = JSON.parse(readFileSync(new URL("../tests/vectors/jcs_vector.json", import.meta.url)));
let fails = 0;
const ok = (c, m) => { console.log((c ? "ok   " : "FAIL ") + m); if (!c) fails++; };
ok(canonicalize(v.input) === v.canonical, "jcs canonical parity with python");
ok((await sha256Hex(v.input)) === v.sha256, "jcs sha256 parity with python");
const d = JSON.parse(readFileSync(new URL("../tests/vectors/demo_receipt_dual.json", import.meta.url)));
let r = await verifyReceipt(d.receipt, d.referee_pub);
ok(r.status === "SELF_ATTESTED" && r.trust_tier === "dual-attested", "ephemeral demo referee => SELF_ATTESTED in JS");
ok((await verifyReceipt(d.receipt, null)).status === "SELF_ATTESTED", "unpinned ephemeral referee => SELF_ATTESTED");
const t = structuredClone(d.receipt); t.result.home_score = 99;
ok((await verifyReceipt(t, d.referee_pub)).status === "FAIL", "tampered score => FAIL");
const t2 = structuredClone(d.receipt); t2.checkpoints.home.splice(3, 1);
ok((await verifyReceipt(t2, d.referee_pub)).status === "FAIL", "dropped checkpoint => FAIL");
const t3 = structuredClone(d.receipt); t3.trust_tier = "dual-attested"; t3.match.mode = "single";
ok((await verifyReceipt(t3, d.referee_pub)).status === "FAIL", "tier upgrade => FAIL");
const a = JSON.parse(readFileSync(new URL("../tests/vectors/agent_replay_receipt.json", import.meta.url)));
r = await verifyReceipt(a.receipt, a.referee_pub);
ok(r.status === "SELF_ATTESTED" && r.trust_tier === "single-attested", "agent replay ephemeral referee => SELF_ATTESTED in JS");
const bareOk = { issued_ms: 1000, deadline_ms: 20000, result: { ok: true, reason: "ok" } };
const ev = challengeEvidenceErrors(bareOk, []);
ok(ev.includes("challenge_missing_hid_sha256") && ev.includes("challenge_timing_incomplete")
   && ev.includes("challenge_not_bound_to_live_checkpoint"),
   "challengeEvidenceErrors rejects bare result.ok without HID/timing/checkpoint bind");
const good = {
  issued_ms: 1000, deadline_ms: 20000,
  result: { ok: true, reason: "ok", pause_press_ms: 1500, done_ms: 2000, latency_ms: 80,
            hid_sha256: "a".repeat(64) },
};
ok(challengeEvidenceErrors(good, [{ referee_ts_ms: 1600 }]).length === 0,
   "challengeEvidenceErrors accepts HID+timing+live checkpoint");
process.exit(fails ? 1 : 0);
