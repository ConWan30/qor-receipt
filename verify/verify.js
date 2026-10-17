// qor-receipt v0 verifier (browser + Node 20). Mirrors qor_receipt/receipt.py exactly.
import * as ed from "./vendor/noble-ed25519.js";

export const SCHEMA = "qor-receipt/v0";
const PHASES = ["kickoff", "halftime", "end"];
const ALPHABET = ["CROSS", "CIRCLE", "SQUARE", "TRIANGLE", "UP", "DOWN", "LEFT", "RIGHT"];
const MAX_GAP_MS = 10000, MAX_SKEW_MS = 3000, MOTION_MIN = 300;
const DEFAULT_LEN = 4;
const RESULT_METHODS = new Set(["agreed_scorebug_final", "end_menu_read", "last_scorebug_read_unconfirmed"]);
const ROLES = new Set(["home", "away"]);
const LOCAL_REFEREE_PREFIXES = ["local-self-attested"];
const UNVERIFIED_KIND_CLAIMS = new Set(["tpm"]);
const enc = new TextEncoder();

function cmpUtf16(a, b) { // JS strings are UTF-16: plain code-unit compare = RFC 8785 order
  return a < b ? -1 : a > b ? 1 : 0;
}
export function canonicalize(v) {
  if (v === null) return "null";
  if (v === true) return "true";
  if (v === false) return "false";
  if (typeof v === "number") {
    if (!Number.isSafeInteger(v)) throw new Error("only safe integers allowed");
    return String(v);
  }
  if (typeof v === "string") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(canonicalize).join(",") + "]";
  if (typeof v === "object") {
    return "{" + Object.keys(v).sort(cmpUtf16).map((k) => JSON.stringify(k) + ":" + canonicalize(v[k])).join(",") + "}";
  }
  throw new Error("unsupported type");
}
const hex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
const unhex = (h) => Uint8Array.from(h.match(/../g) || [], (x) => parseInt(x, 16));
async function sha256(bytes) { return new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)); }
export async function sha256Hex(v) { return hex(await sha256(enc.encode(canonicalize(v)))); }
async function edVerify(pub, sig, msgBytes) {
  try { return await ed.verifyAsync(unhex(sig), msgBytes, unhex(pub), { zip215: false }); } catch { return false; }
}
const kid = async (pub) => hex(await sha256(unhex(pub))).slice(0, 16);
const without = (o, k) => Object.fromEntries(Object.entries(o).filter(([x]) => x !== k));

export async function patternFor(nonce, length = 4) {
  const d = await sha256(unhex(nonce));
  const out = [];
  let i = 0;
  while (out.length < length) {
    let b = ALPHABET[d[i % d.length] % ALPHABET.length];
    i++;
    if (out.length && out[out.length - 1] === b) b = ALPHABET[(ALPHABET.indexOf(b) + 1) % ALPHABET.length];
    out.push(b);
  }
  return ["OPTIONS", ...out];
}

async function verifyChain(cps, matchId, role, refPub, startMs, endMs) {
  if (!cps || !cps.length) return ["no_checkpoints"];
  const errs = [];
  let prev = "0".repeat(64), last = startMs;
  for (let i = 0; i < cps.length; i++) {
    const cp = cps[i], tag = `cp[${i}]`;
    if (cp.seq !== i) errs.push(`${tag}:seq_gap`);
    if (cp.prev !== prev) errs.push(`${tag}:prev_mismatch`);
    const body = { seq: cp.seq, t_ms: cp.t_ms, frames_hash: cp.frames_hash, input_hash: cp.input_hash, prev: cp.prev };
    if ((await sha256Hex(body)) !== cp.hash) errs.push(`${tag}:hash_mismatch`);
    const ts = cp.referee_ts_ms;
    const payload = enc.encode(canonicalize({ type: "checkpoint", match_id: matchId, role, hash: cp.hash, referee_ts_ms: ts }));
    if (!(await edVerify(refPub, cp.referee_sig, payload))) errs.push(`${tag}:bad_referee_sig`);
    if (ts < last) errs.push(`${tag}:referee_time_backwards`);
    if (ts - last > MAX_GAP_MS) errs.push(`${tag}:gap_too_long`);
    if (Math.abs(ts - cp.t_ms) > MAX_SKEW_MS) errs.push(`${tag}:clock_skew`);
    prev = cp.hash; last = ts;
  }
  if (endMs - last > MAX_GAP_MS) errs.push("chain_ends_before_match_end");
  return errs;
}

function computeTier(r) {
  const roles = new Set((r.players || []).map((p) => p.role));
  const dual = r.match.mode === "dual" && roles.size === 2 && roles.has("home") && roles.has("away") &&
    (r.agreement || {}).timelines_agree === true;
  return dual ? "dual-attested" : "single-attested";
}

function isNonnegInt(v) {
  return typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
}

export async function verifyReceipt(r, refereePub = null) {
  const errors = [], partial = [];
  let tier = null;
  try {
    if (r.schema !== SCHEMA) return { status: "FAIL", trust_tier: null, errors: ["schema"], partial: [] };
    const msg = enc.encode(canonicalize(without(r, "signatures")));
    const sigs = r.signatures || {}, ref = r.referee;
    if (!refereePub) partial.push("referee_key_not_pinned");
    else if (refereePub.toLowerCase() !== ref.pubkey.toLowerCase()) errors.push("referee_key_mismatch");
    const rs = sigs.referee;
    if (!rs || rs.key_id !== (await kid(ref.pubkey)) || !(await edVerify(ref.pubkey, rs.sig, msg))) errors.push("bad_referee_signature");

    let players = r.players;
    if (!Array.isArray(players) || !players.length) {
      errors.push("no_players");
      players = [];
    }
    const rolesSeen = new Set();
    for (const p of players) {
      const role = p.role;
      if (!ROLES.has(role)) {
        errors.push(`bad_player_role:${JSON.stringify(role)}`);
        continue;
      }
      if (rolesSeen.has(role)) errors.push(`duplicate_player_role:${role}`);
      rolesSeen.add(role);
      const ps = (sigs.players || {})[role];
      if (!ps) errors.push(`missing_player_signature:${role}`);
      else if (ps.key_id !== (await kid(p.pubkey)) || !(await edVerify(p.pubkey, ps.sig, msg))) errors.push(`bad_player_signature:${role}`);
      if (UNVERIFIED_KIND_CLAIMS.has(p.key_kind)) partial.push(`${role}:key_kind_claim_unverified:${p.key_kind}`);
    }

    const m = r.match;
    if (m.mode === "dual" && !(rolesSeen.has("home") && rolesSeen.has("away") && rolesSeen.size === 2)) {
      errors.push("dual_missing_roles");
    }

    const refKind = String(ref.key_kind || "");
    if (LOCAL_REFEREE_PREFIXES.some((p) => refKind.startsWith(p))) partial.push("referee_local_self_attested");
    if (UNVERIFIED_KIND_CLAIMS.has(refKind)) partial.push(`referee_key_kind_claim_unverified:${refKind}`);
    for (const p of players) {
      if (ROLES.has(p.role) && String(p.pubkey || "").toLowerCase() === String(ref.pubkey || "").toLowerCase()) {
        partial.push("referee_same_as_player");
        break;
      }
    }

    for (const p of players) {
      const role = p.role;
      if (!ROLES.has(role)) continue;
      for (const e of await verifyChain(r.checkpoints[role], m.match_id, role, ref.pubkey, m.started_ms, m.ended_ms)) errors.push(`${role}:${e}`);
      for (const ph of PHASES) {
        const cs = r.challenges.filter((c) => c.role === role && c.phase === ph);
        if (!cs.length) partial.push(`${role}:challenge_missing:${ph}`);
        for (const c of cs) {
          const pat = c.pattern || [];
          if (!Array.isArray(pat) || pat.length < DEFAULT_LEN + 1) errors.push(`${role}:${ph}:pattern_too_short`);
          else if (canonicalize(c.pattern) !== canonicalize(await patternFor(c.nonce, c.pattern.length - 1))) errors.push(`${role}:${ph}:pattern_not_from_nonce`);
          if (c.result == null) partial.push(`${role}:challenge_unanswered:${ph}`);
          else if (!c.result.ok) errors.push(`${role}:${ph}:challenge_failed:${c.result.reason}`);
        }
      }
      const mp = ((r.signals || {}).motion_permille || {})[role];
      if (mp == null) partial.push(`${role}:motion_signal_missing`);
      else if (mp < MOTION_MIN) partial.push(`${role}:motion_correlation_low`);
    }
    if (m.mode === "dual" && (r.agreement || {}).timelines_agree !== true) errors.push("dual_timelines_disagree");
    const res = r.result;
    if (!isNonnegInt(res.home_score) || !isNonnegInt(res.away_score)) errors.push("score_not_nonneg_int");
    else if (res.winner !== (res.home_score > res.away_score ? "home" : "away")) errors.push("winner_inconsistent");
    if (typeof res.quarter !== "number" || !Number.isSafeInteger(res.quarter) || res.quarter < 4) errors.push("quarter_not_final");
    if (!RESULT_METHODS.has(res.method)) errors.push("bad_result_method");
    tier = computeTier(r);
    if (r.trust_tier !== tier) errors.push("trust_tier_claim_wrong");
  } catch (e) {
    errors.push(`malformed:${e.name}`);
    tier = null;
  }
  const status = errors.length ? "FAIL" : partial.length ? "PARTIAL" : "VERIFIED";
  return { status, trust_tier: tier, errors, partial };
}
