/// <reference lib="webworker" />
import { parseRange } from "../range";
import { CACHE_MEDIA, CACHE_SHELL_PREFIX, DB_NAME, DB_VERSION, KV, NET_HEADER, STORE_KV, STORE_LOG, STORE_SCHEDULE } from "../shared";

declare const self: ServiceWorkerGlobalScope;
declare const __SW_VERSION__: string;

const SHELL = `${CACHE_SHELL_PREFIX}${__SW_VERSION__}`;
const NETWORK_TIMEOUT_MS = 3000;

/** URL du serveur (préfixe compris) : le scope est `<base>/player/`. */
const SERVER = new URL("..", self.registration.scope).href;
const SCOPE = self.registration.scope;

// --- jeton d'appareil (IndexedDB partagée avec la page) ----------------------
function readToken(): Promise<string | null> {
  return new Promise((resolve) => {
    const open = indexedDB.open(DB_NAME, DB_VERSION);
    open.onupgradeneeded = () => {
      const db = open.result;
      for (const name of [STORE_KV, STORE_SCHEDULE]) {
        if (!db.objectStoreNames.contains(name)) db.createObjectStore(name);
      }
      if (!db.objectStoreNames.contains(STORE_LOG)) db.createObjectStore(STORE_LOG, { autoIncrement: true });
    };
    open.onerror = () => resolve(null);
    open.onsuccess = () => {
      const db = open.result;
      const get = db.transaction(STORE_KV, "readonly").objectStore(STORE_KV).get(KV.token);
      get.onsuccess = () => {
        db.close();
        resolve(typeof get.result === "string" ? get.result : null);
      };
      get.onerror = () => {
        db.close();
        resolve(null);
      };
    };
  });
}

// --- installation : application, config.js, thème --------------------------
async function addAll(cache: Cache, urls: string[], optional: boolean): Promise<void> {
  await Promise.all(
    urls.map(async (u) => {
      try {
        const res = await fetch(u, { cache: "reload", credentials: "omit" });
        if (res.ok) await cache.put(u, res);
        else if (!optional) throw new Error(`precache ${res.status}`);
      } catch (e) {
        if (!optional) throw e;
      }
    }),
  );
}

function refsIn(text: string, pattern: RegExp, base: string): string[] {
  const out: string[] = [];
  for (const m of text.matchAll(pattern)) {
    const ref = m[1];
    if (!ref || ref.startsWith("data:")) continue;
    try {
      const u = new URL(ref, base);
      if (u.origin === location.origin) out.push(u.href);
    } catch {
      // référence invalide ignorée
    }
  }
  return out;
}

async function precache(): Promise<void> {
  const cache = await caches.open(SHELL);
  const indexRes = await fetch(SCOPE, { cache: "reload", credentials: "omit" });
  if (!indexRes.ok) throw new Error("precache_index");
  const html = await indexRes.clone().text();
  await cache.put(SCOPE, indexRes);
  await addAll(cache, refsIn(html, /(?:src|href)="([^"]+)"/g, SCOPE), false);
  // Thème : feuille, polices, logo, icônes (best effort : le player reste lisible sans).
  const configUrl = new URL("config.js", SERVER).href;
  const cfg = await cache.match(configUrl);
  const css = cfg ? refsIn(await cfg.text(), /"cssUrl":\s*"([^"]+)"/g, SERVER)[0] : undefined;
  if (css) {
    await addAll(cache, [css], true);
    const cssRes = await cache.match(css);
    if (cssRes) await addAll(cache, [...new Set(refsIn(await cssRes.text(), /url\(["']?([^)"']+)["']?\)/g, css))], true);
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil(precache().then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const name of await caches.keys()) {
        if (name.startsWith(CACHE_SHELL_PREFIX) && name !== SHELL) await caches.delete(name);
      }
      await self.clients.claim();
    })(),
  );
});

// --- stratégies --------------------------------------------------------------
function timeout(ms: number): Promise<never> {
  return new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), ms));
}

/** Réseau d'abord (version à jour), cache si le réseau échoue ou tarde ; sans cache on attend le réseau. */
async function networkFirst(req: Request, key: string): Promise<Response> {
  const cache = await caches.open(SHELL);
  const net = fetch(req.mode === "navigate" ? key : req, { credentials: "omit" }).then(async (res) => {
    if (res.ok) await cache.put(key, res.clone());
    return res;
  });
  const cached = await cache.match(key);
  if (!cached) return net;
  net.catch(() => undefined);
  try {
    const res = await Promise.race([net, timeout(NETWORK_TIMEOUT_MS)]);
    return res.ok ? res : cached;
  } catch {
    return cached;
  }
}

function slice(res: Response, header: string | null): Promise<Response> | Response {
  if (!header) return res;
  return res.blob().then((blob) => {
    const range = parseRange(header, blob.size);
    if (range === null) return res;
    const headers = new Headers({ "Accept-Ranges": "bytes", "Content-Type": res.headers.get("Content-Type") ?? blob.type });
    if (range === "unsatisfiable") {
      headers.set("Content-Range", `bytes */${blob.size}`);
      return new Response(null, { status: 416, headers });
    }
    headers.set("Content-Range", `bytes ${range.start}-${range.end}/${blob.size}`);
    headers.set("Content-Length", String(range.end - range.start + 1));
    return new Response(blob.slice(range.start, range.end + 1), { status: 206, headers });
  });
}

/** Médias : cache uniquement (jamais de lecture en flux) ; absent = 404, l'élément est sauté. */
async function media(req: Request): Promise<Response> {
  if (req.headers.has(NET_HEADER)) {
    const token = await readToken();
    const headers = new Headers({ Accept: "*/*" });
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(req.url, { headers, cache: "no-store", credentials: "omit" });
  }
  const cache = await caches.open(CACHE_MEDIA);
  const hit = await cache.match(req.url);
  if (!hit) return new Response(null, { status: 404 });
  return slice(hit, req.headers.get("Range"));
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || !req.url.startsWith(SERVER)) return;
  const rel = req.url.slice(SERVER.length);
  if (rel.startsWith("media/")) {
    event.respondWith(media(req));
  } else if (rel.startsWith("player/") || rel === "config.js" || rel.startsWith("modules/")) {
    const key = req.mode === "navigate" ? SCOPE : req.url;
    event.respondWith(networkFirst(req, key));
  }
  // api/… : réseau direct (jeton posé par la page).
});
