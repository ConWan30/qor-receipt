// qor-receipt v0 verifier (browser + Node 20). Mirrors qor_receipt/receipt.py exactly.
import * as ed from "./vendor/noble-ed25519.js";
import { TRUSTED_REFEREES } from "./trusted_referees.inc.js";

export const SCHEMA = "qor-receipt/v0";
export const SELF_ATTESTED = "SELF_ATTESTED";
const PHASES = ["kickoff", "halftime", "end"];
const ALPHABET = ["CROSS", "CIRCLE", "SQUARE", "TRIANGLE", "UP", "DOWN", "LEFT", "RIGHT"];
const MAX_GAP_MS = 10000, MAX_SKEW_MS = 3000, MOTION_MIN = 300;
const DEFAULT_LEN = 4;
const RESULT_METHODS = new Set(["agreed_scorebug_final", "end_menu_read", "last_scorebug_read_unconfirmed"]);
const ROLES = new Set(["home", "away"]);
const LOCAL_REFEREE_PREFIXES = ["local-self-attested"];
const UNVERIFIED_KIND_CLAIMS = new Set(["tpm"]);
// Official post-hoc review receipts (qor_receipt/review/policy.py).
export const REVIEW_KEY_KIND = "qortime-official-review";
export const REVIEW_TYPE = "qortime-bundle-review/v1";
export const CHALLENGE_TOKEN_TYPE = "qortime-challenge/v1";
const CHECK_STATES = new Set(["pass", "partial", "fail"]);
const REQUIRED_REVIEW_CHECKS = ["video_bytes", "final_reread", "capture_chain", "bundle_structure"];
const enc = new TextEncoder();

export function loadAllowlist() { return TRUSTED_REFEREES; }
export function lookupReferee(pubkey, allowlist = TRUSTED_REFEREES) {
  if (!pubkey || typeof pubkey !== "string") return null;
  const want = pubkey.toLowerCase();
  return ((allowlist || {}).referees || []).find((r) => String(r.pubkey || "").toLowerCase() === want) || null;
}
export function isTrustedActive(pubkey, allowlist = TRUSTED_REFEREES) {
  const row = lookupReferee(pubkey, allowlist);
  return !!(row && row.status === "active");
}

function cmpUtf16(a, b) {
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


export function challengeEvidenceErrors(c, cps) {
  const errs = [];
  const res = c.result || {};
  const hid = res.hid_sha256;
  if (typeof hid !== "string" || hid.length !== 64 || !/^[0-9a-fA-F]{64}$/.test(hid)) errs.push("challenge_missing_hid_sha256");
  const issued = c.issued_ms, deadline = c.deadline_ms;
  const pause = res.pause_press_ms, done = res.done_ms, lat = res.latency_ms;
  if (pause == null || done == null || lat == null) errs.push("challenge_timing_incomplete");
  else {
    if (!(issued <= pause && pause <= deadline)) errs.push("challenge_pause_outside_window");
    if (done < pause) errs.push("challenge_done_before_pause");
    if (!(lat >= 0 && lat <= 600)) errs.push("challenge_latency_out_of_range");
  }
  const winLo = issued - 3000, winHi = deadline + 5000;
  const bound = (cps || []).some((cp) => typeof cp.referee_ts_ms === "number" && cp.referee_ts_ms >= winLo && cp.referee_ts_ms <= winHi);
  if (!bound) errs.push("challenge_not_bound_to_live_checkpoint");
  return errs;
}


const isHex64 = (v) => typeof v === "string" && /^[0-9a-fA-F]{64}$/.test(v);

// Review receipts: the nonce must come from a token signed by an allowlisted active referee key for exactly this
// nonce / phase / window. Mirrors receipt.official_challenge_ok.
export async function officialChallengeOk(c, allowlist = TRUSTED_REFEREES) {
  const tok = c && c.issuer;
  if (!tok || typeof tok !== "object" || tok.type !== CHALLENGE_TOKEN_TYPE || typeof tok.sig !== "string") return false;
  for (const k of ["nonce", "phase", "issued_ms", "deadline_ms"]) if (tok[k] !== c[k]) return false;
  const payload = enc.encode(canonicalize(without(tok, "sig")));
  for (const row of (allowlist || {}).referees || []) {
    const pub = String(row.pubkey || "").toLowerCase();
    if (row.status === "active" && tok.key_id === (await kid(pub)) && (await edVerify(pub, tok.sig, payload))) return true;
  }
  return false;
}

export function reviewProblems(r, ref) {
  const rv = r.review;
  if (!rv || typeof rv !== "object" || rv.type !== REVIEW_TYPE) return { cap: null, errors: ["review_missing_or_wrong_type"], partial: [] };
  const errors = [], partial = [];
  let cap = (rv.capture_referee || {}).pubkey;
  if (!isHex64(cap)) { errors.push("review_capture_referee_missing"); cap = null; }
  else if (cap.toLowerCase() === String(ref.pubkey || "").toLowerCase()) errors.push("review_capture_referee_is_official_key");
  let checks = rv.checks;
  if (!checks || typeof checks !== "object" || !Object.keys(checks).length) { errors.push("review_checks_missing"); checks = {}; }
  for (const name of REQUIRED_REVIEW_CHECKS) if (!(name in checks)) errors.push(`review_check_missing:${name}`);
  for (const name of Object.keys(checks).sort()) {
    const c = checks[name];
    const st = c && typeof c === "object" ? c.state : undefined;
    if (!CHECK_STATES.has(st)) errors.push(`review_check_bad_state:${name}`);
    else if (st === "fail") errors.push(`review_check_failed:${name}`);
    else if (st === "partial") partial.push(`review_partial:${name}`);
  }
  return { cap, errors, partial };
}

const INPUT_HUMAN_MIN = 400;
const INPUT_ALIGN_EDGE = 50;
const INPUT_HAPTIC_MIN = 1;

export function inputProofAdvisory(signals) {
  if (!signals || typeof signals !== "object") return null;
  const inp = signals.input;
  if (!inp || typeof inp !== "object") return null;
  const notes = [], flags = [];
  const human = inp.human_permille, label = inp.human_label;
  if (typeof human === "number") {
    if (human < INPUT_HUMAN_MIN) { flags.push("human_score_low"); notes.push(`human_permille=${human} below advisory floor ${INPUT_HUMAN_MIN}` + (label ? ` (${label})` : "")); }
    else notes.push(`human_permille=${human}` + (label ? ` (${label})` : ""));
  } else flags.push("human_score_missing");
  const matchP = inp.match_permille, chanceP = inp.chance_permille;
  if (typeof matchP === "number" && typeof chanceP === "number") {
    if (matchP < chanceP + INPUT_ALIGN_EDGE) { flags.push("alignment_not_above_chance"); notes.push(`alignment match=${matchP} chance=${chanceP} (need +${INPUT_ALIGN_EDGE})`); }
    else notes.push(`alignment match=${matchP} vs chance=${chanceP} (ok)`);
  } else flags.push("alignment_missing");
  const he = inp.haptic_events, hc = inp.haptic_high_confidence;
  if (typeof he === "number") {
    if (he <= 0) { flags.push("no_haptic_events"); notes.push("no haptic events recorded"); }
    else if (typeof hc === "number" && hc < INPUT_HAPTIC_MIN) { flags.push("haptic_low_confidence_only"); notes.push(`haptic_events=${he} but high_confidence=${hc}`); }
    else notes.push(`haptic_events=${he} high_confidence=${hc}`);
  }
  if (!inp.inputs_sha256) flags.push("inputs_sha256_missing");
  const bad = ["human_score_low", "alignment_not_above_chance", "no_haptic_events", "haptic_low_confidence_only"];
  const ok = !flags.some((f) => bad.includes(f));
  return { present: true, ok, flags, notes, status: inp.status, human_permille: human, match_permille: matchP,
           chance_permille: chanceP, haptic_events: he, haptic_high_confidence: hc,
           claims: "advisory evidence only — not cryptographic proof of a human player" };
}

export async function verifyReceipt(r, refereePub = null, opts = {}) {
  const allowlist = (opts && opts.allowlist) || TRUSTED_REFEREES;
  let reviewMode = false;
  const errors = [], partial = [];
  let tier = null;
  let trustedRef = false;
  const inputAdvisory = inputProofAdvisory(r && r.signals);
  let refereeEntry = null;
  try {
    if (r.schema !== SCHEMA) return { status: "FAIL", trust_tier: null, errors: ["schema"], partial: [], referee_trust: "unknown" };
    const msg = enc.encode(canonicalize(without(r, "signatures")));
    const sigs = r.signatures || {}, ref = r.referee;
    if (!refereePub) partial.push("referee_key_not_pinned");
    else if (refereePub.toLowerCase() !== ref.pubkey.toLowerCase()) errors.push("referee_key_mismatch");
    const rs = sigs.referee;
    if (!rs || rs.key_id !== (await kid(ref.pubkey)) || !(await edVerify(ref.pubkey, rs.sig, msg))) errors.push("bad_referee_signature");

    refereeEntry = lookupReferee(ref.pubkey, allowlist);
    if (refereeEntry && refereeEntry.status === "active") trustedRef = true;
    else if (refereeEntry && refereeEntry.status === "revoked") partial.push(`referee_revoked:${refereeEntry.id}`);
    else partial.push("referee_not_on_allowlist");

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

    let chainPub = ref.pubkey;
    reviewMode = ref.key_kind === REVIEW_KEY_KIND;
    if (reviewMode) {
      const rp = reviewProblems(r, ref);
      errors.push(...rp.errors);
      partial.push(...rp.partial);
      if (rp.cap) chainPub = rp.cap;
    }

    const m = r.match;
    if (m.mode === "dual" && !(rolesSeen.has("home") && rolesSeen.has("away") && rolesSeen.size === 2)) {
      errors.push("dual_missing_roles");
    }

    const refKind = String(ref.key_kind || "");
    if (LOCAL_REFEREE_PREFIXES.some((p) => refKind.startsWith(p))) {
      partial.push("referee_local_self_attested");
      trustedRef = false;
    }
    if (UNVERIFIED_KIND_CLAIMS.has(refKind)) partial.push(`referee_key_kind_claim_unverified:${refKind}`);
    for (const p of players) {
      if (ROLES.has(p.role) && String(p.pubkey || "").toLowerCase() === String(ref.pubkey || "").toLowerCase()) {
        partial.push("referee_same_as_player");
        trustedRef = false;
        break;
      }
    }
    if (refereePub != null && !isTrustedActive(refereePub, allowlist)) {
      trustedRef = false;
      if (!partial.includes("referee_pin_not_allowlisted")) partial.push("referee_pin_not_allowlisted");
    }

    for (const p of players) {
      const role = p.role;
      if (!ROLES.has(role)) continue;
      for (const e of await verifyChain(r.checkpoints[role], m.match_id, role, chainPub, m.started_ms, m.ended_ms)) errors.push(`${role}:${e}`);
      for (const ph of PHASES) {
        const cs = r.challenges.filter((c) => c.role === role && c.phase === ph);
        if (!cs.length) partial.push(`${role}:challenge_missing:${ph}`);
        for (const c of cs) {
          const pat = c.pattern || [];
          if (!Array.isArray(pat) || pat.length < DEFAULT_LEN + 1) errors.push(`${role}:${ph}:pattern_too_short`);
          else if (canonicalize(c.pattern) !== canonicalize(await patternFor(c.nonce, c.pattern.length - 1))) errors.push(`${role}:${ph}:pattern_not_from_nonce`);
          if (c.result == null) partial.push(`${role}:challenge_unanswered:${ph}`);
          else if (!c.result.ok) errors.push(`${role}:${ph}:challenge_failed:${c.result.reason}`);
          else {
            const evErrs = challengeEvidenceErrors(c, r.checkpoints[role]);
            if (reviewMode && !(await officialChallengeOk(c, allowlist))) evErrs.push("challenge_nonce_not_official");
            for (const e of evErrs) partial.push(`${role}:${ph}:${e}`);
          }
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
    trustedRef = false;
  }
  let status;
  if (errors.length) status = "FAIL";
  else if (!trustedRef) status = SELF_ATTESTED;
  else if (partial.length) status = "PARTIAL";
  else status = "VERIFIED";
  const out = {
    status, trust_tier: tier, errors, partial,
    referee_trust: trustedRef ? "active" : (refereeEntry && refereeEntry.status === "revoked" ? "revoked" : "untrusted"),
    input_advisory: inputAdvisory,
  };
  if (refereeEntry) out.referee_id = refereeEntry.id;
  if (reviewMode && r.review && typeof r.review === "object") {
    const vb = (((r.review.checks || {}).video_bytes || {}).detail) || {};
    out.verification_basis = "official-post-hoc-review";
    out.review = { scope: r.review.scope, video_method: vb.method ?? null, segments_verified: vb.segments_verified ?? null,
                   segments_total: vb.segments_total ?? null, not_proven: r.review.not_proven };
  }
  return out;
}
