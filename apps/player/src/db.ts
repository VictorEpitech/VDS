import type { ScheduleDocument } from "@vds/sdk-ts";
import { DB_NAME, DB_VERSION, LOG_RING, STORE_KV, STORE_LOG, STORE_SCHEDULE } from "./shared";

let opened: Promise<IDBDatabase> | null = null;

export function openDb(): Promise<IDBDatabase> {
  opened ??= new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      for (const name of [STORE_KV, STORE_SCHEDULE]) {
        if (!db.objectStoreNames.contains(name)) db.createObjectStore(name);
      }
      if (!db.objectStoreNames.contains(STORE_LOG)) db.createObjectStore(STORE_LOG, { autoIncrement: true });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => {
      opened = null;
      reject(req.error ?? new Error("idb_open"));
    };
  });
  return opened;
}

async function run<T>(store: string, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb();
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction(store, mode);
    const req = fn(tx.objectStore(store));
    tx.oncomplete = () => resolve(req.result);
    tx.onerror = () => reject(tx.error ?? new Error("idb_tx"));
    tx.onabort = () => reject(tx.error ?? new Error("idb_abort"));
  });
}

export async function kvGet<T>(key: string): Promise<T | undefined> {
  return (await run(STORE_KV, "readonly", (s) => s.get(key))) as T | undefined;
}

export async function kvSet(key: string, value: unknown): Promise<void> {
  await run(STORE_KV, "readwrite", (s) => s.put(value, key));
}

export async function kvDel(key: string): Promise<void> {
  await run(STORE_KV, "readwrite", (s) => s.delete(key));
}

const ACTIVE = "active";

export async function scheduleGet(): Promise<ScheduleDocument | undefined> {
  return (await run(STORE_SCHEDULE, "readonly", (s) => s.get(ACTIVE))) as ScheduleDocument | undefined;
}

export async function schedulePut(doc: ScheduleDocument): Promise<void> {
  await run(STORE_SCHEDULE, "readwrite", (s) => s.put(doc, ACTIVE));
}

export interface LogEntry {
  at: number;
  code: string;
  item_id?: string;
}

export async function logAdd(entry: LogEntry): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_LOG, "readwrite");
    const store = tx.objectStore(STORE_LOG);
    store.add(entry);
    const count = store.count();
    count.onsuccess = () => {
      const extra = count.result - LOG_RING;
      if (extra > 0) {
        const cur = store.openKeyCursor();
        let left = extra;
        cur.onsuccess = () => {
          const c = cur.result;
          if (c && left-- > 0) {
            store.delete(c.primaryKey);
            c.continue();
          }
        };
      }
    };
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("idb_log"));
  });
}

/** Efface jeton, programmation et journal (réponse à une révocation) ; le SW vide ses caches côté page. */
export async function clearAll(): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction([STORE_KV, STORE_SCHEDULE, STORE_LOG], "readwrite");
    tx.objectStore(STORE_KV).clear();
    tx.objectStore(STORE_SCHEDULE).clear();
    tx.objectStore(STORE_LOG).clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("idb_clear"));
  });
}
