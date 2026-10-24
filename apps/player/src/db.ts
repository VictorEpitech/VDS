import type { ScheduleDocument } from "@vds/sdk-ts";
import { DB_NAME, DB_VERSION, LOG_RING, STORE_KV, STORE_LOG, STORE_SCHEDULE } from "./shared";

export const OPEN_TIMEOUT_MS = 3_000;

// Repli mémoire : IndexedDB absent, bloqué ou muet (TV, contexte non sécurisé). Sans persistance ni hors ligne.
const memKv = new Map<string, unknown>();
let memSchedule: ScheduleDocument | undefined;
let memLog: LogEntry[] = [];
let opened: Promise<IDBDatabase | null> | null = null;

let memOnly = false;
export function isMemoryOnly(): boolean {
  return memOnly;
}

/** Réinitialise l'état interne (tests). */
export function resetDbForTests(): void {
  opened = null;
  memOnly = false;
  memKv.clear();
  memSchedule = undefined;
  memLog = [];
}

function fallback(): null {
  memOnly = true;
  return null;
}

/** Ouvre la base ; renvoie null (repli mémoire) si indisponible ou sans réponse sous OPEN_TIMEOUT_MS. */
export function openDb(): Promise<IDBDatabase | null> {
  opened ??= new Promise<IDBDatabase | null>((resolve) => {
    let done = false;
    const finish = (db: IDBDatabase | null): void => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(db ?? fallback());
    };
    const timer = setTimeout(() => finish(null), OPEN_TIMEOUT_MS);
    try {
      if (typeof indexedDB === "undefined" || !indexedDB) return finish(null);
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        for (const name of [STORE_KV, STORE_SCHEDULE]) {
          if (!db.objectStoreNames.contains(name)) db.createObjectStore(name);
        }
        if (!db.objectStoreNames.contains(STORE_LOG)) db.createObjectStore(STORE_LOG, { autoIncrement: true });
      };
      req.onsuccess = () => finish(req.result);
      req.onerror = () => finish(null);
      req.onblocked = () => finish(null);
    } catch {
      finish(null);
    }
  });
  return opened;
}

const TX_TIMEOUT_MS = 5_000;

/** Exécute une transaction ; `mem` est le repli si la base est absente ou si l'opération échoue/pend. */
async function run<T>(
  store: string,
  mode: IDBTransactionMode,
  fn: (s: IDBObjectStore) => IDBRequest<T>,
  mem: () => unknown,
): Promise<T> {
  const db = await openDb();
  if (!db) return mem() as T;
  try {
    return await new Promise<T>((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("idb_timeout")), TX_TIMEOUT_MS);
      const tx = db.transaction(store, mode);
      const req = fn(tx.objectStore(store));
      tx.oncomplete = () => {
        clearTimeout(t);
        resolve(req.result);
      };
      tx.onerror = () => {
        clearTimeout(t);
        reject(tx.error ?? new Error("idb_tx"));
      };
      tx.onabort = () => {
        clearTimeout(t);
        reject(tx.error ?? new Error("idb_abort"));
      };
    });
  } catch {
    return mem() as T;
  }
}

export async function kvGet<T>(key: string): Promise<T | undefined> {
  return (await run(STORE_KV, "readonly", (s) => s.get(key), () => memKv.get(key))) as T | undefined;
}

export async function kvSet(key: string, value: unknown): Promise<void> {
  memKv.set(key, value);
  await run(STORE_KV, "readwrite", (s) => s.put(value, key), () => undefined);
}

export async function kvDel(key: string): Promise<void> {
  memKv.delete(key);
  await run(STORE_KV, "readwrite", (s) => s.delete(key), () => undefined);
}

const ACTIVE = "active";

export async function scheduleGet(): Promise<ScheduleDocument | undefined> {
  return (await run(STORE_SCHEDULE, "readonly", (s) => s.get(ACTIVE), () => memSchedule)) as ScheduleDocument | undefined;
}

export async function schedulePut(doc: ScheduleDocument): Promise<void> {
  memSchedule = doc;
  await run(STORE_SCHEDULE, "readwrite", (s) => s.put(doc, ACTIVE), () => undefined);
}

export interface LogEntry {
  at: number;
  code: string;
  item_id?: string;
}

export async function logAdd(entry: LogEntry): Promise<void> {
  const db = await openDb();
  if (!db) {
    memLog.push(entry);
    if (memLog.length > LOG_RING) memLog = memLog.slice(-LOG_RING);
    return;
  }
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
  memKv.clear();
  memSchedule = undefined;
  memLog = [];
  const db = await openDb();
  if (!db) return;
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction([STORE_KV, STORE_SCHEDULE, STORE_LOG], "readwrite");
    tx.objectStore(STORE_KV).clear();
    tx.objectStore(STORE_SCHEDULE).clear();
    tx.objectStore(STORE_LOG).clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("idb_clear"));
  });
}
