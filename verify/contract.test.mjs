// node verify/contract.test.mjs : the browser verifier must give the same verdict as the Python one
// on every golden contract fixture (tests/fixtures/contract, see scripts/make_contract_fixtures.py).
import { readFileSync, readdirSync } from "node:fs";
import { verifyReceipt } from "./verify.js";
const dir = new URL("../tests/fixtures/contract/", import.meta.url);
let fails = 0, n = 0;
for (const f of readdirSync(dir).filter((x) => x.endsWith(".json")).sort()) {
  const d = JSON.parse(readFileSync(new URL(f, dir)));
  const r = await verifyReceipt(d.receipt, d.referee_pub);
  const same = r.status === d.expect.status && JSON.stringify([...r.errors].sort()) === JSON.stringify([...d.expect.errors].sort());
  console.log((same ? "ok   " : "FAIL ") + `${d.name}: js=${r.status} py=${d.expect.status}` + (same ? "" : ` js_errors=${JSON.stringify(r.errors)}`));
  n++; if (!same) fails++;
}
if (n < 9) { console.log(`FAIL only ${n} fixtures`); fails++; }
process.exit(fails ? 1 : 0);
