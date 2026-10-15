import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

function chainLock() {
  let tail = Promise.resolve();
  return function lock(fn) {
    const run = tail.then(fn, fn);
    tail = run.then(
      () => undefined,
      () => undefined,
    );
    return run;
  };
}

export function memoryStore() {
  const rows = new Map();
  const lock = chainLock();
  return {
    async get(id) {
      return rows.get(id) ?? null;
    },
    async save(record) {
      return lock(async () => {
        const existing = rows.get(record.receipt_id);
        if (existing?.paid) return existing;
        rows.set(record.receipt_id, record);
        return record;
      });
    },
  };
}

export function fileStore(filePath) {
  const lock = chainLock();

  async function readAll() {
    try {
      const text = await readFile(filePath, "utf8");
      const data = JSON.parse(text);
      if (!data || typeof data !== "object" || Array.isArray(data)) return {};
      return data;
    } catch (err) {
      if (err && err.code === "ENOENT") return {};
      throw err;
    }
  }

  return {
    async get(id) {
      const all = await readAll();
      return all[id] ?? null;
    },
    async save(record) {
      return lock(async () => {
        const all = await readAll();
        const existing = all[record.receipt_id];
        if (existing?.paid) return existing;
        all[record.receipt_id] = record;
        await mkdir(dirname(filePath), { recursive: true });
        const tmp = `${filePath}.tmp`;
        await writeFile(tmp, `${JSON.stringify(all, null, 2)}\n`);
        await rename(tmp, filePath);
        return record;
      });
    },
  };
}

export function toPublic(record, receiptId) {
  if (!record) {
    return {
      receipt_id: receiptId,
      paid: false,
      tier: null,
      mode: "test",
      livemode: false,
      payment_status: null,
      recorded_at: null,
    };
  }
  const paid = record.paid === true;
  return {
    receipt_id: record.receipt_id,
    paid,
    tier: paid ? "verified-match" : null,
    mode: "test",
    livemode: false,
    payment_status: record.payment_status ?? null,
    recorded_at: record.recorded_at ?? null,
  };
}
