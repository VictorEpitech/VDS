import { createHash } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssetCache } from "../src/assets";

const SERVER = "http://s/";
const K = "media/r/original";
const bytes = (n: number, seed: number): Uint8Array => new Uint8Array(n).fill(seed);
const ref = (b: Uint8Array) => ({ sha256: createHash("sha256").update(b).digest("hex"), bytes: b.length, mime: "image/png" });

// Cache API minimale en mémoire (jsdom n'en fournit pas).
function fakeCaches(): { store: Map<string, Uint8Array> } {
  const store = new Map<string, Uint8Array>();
  const cache = {
    put: async (u: string, r: Response) => void store.set(u, new Uint8Array(await r.arrayBuffer())),
    match: async (u: string) => (store.has(u) ? new Response(store.get(u)) : undefined),
    delete: async (u: string | Request) => store.delete(typeof u === "string" ? u : u.url),
    keys: async () => [...store.keys()].map((url) => ({ url }) as Request),
  };
  vi.stubGlobal("caches", { open: async () => cache, delete: async () => true });
  return { store };
}

let served: Uint8Array;
beforeEach(() => {
  vi.stubGlobal("fetch", async () => new Response(served));
});
afterEach(() => vi.unstubAllGlobals());

describe("cache média : identité (clé, sha256)", () => {
  it("remplacement : l'ancien octet reste jusqu'à la vérification, puis bascule sans clé d'attente", async () => {
    const { store } = fakeCaches();
    const v1 = bytes(40, 1);
    const v2 = bytes(50, 2);
    const c = new AssetCache(SERVER);
    await c.init();
    served = v1;
    await c.ensure(K, ref(v1));
    expect(c.hasAll([K], { [K]: ref(v1) })).toBe(true);
    expect(c.missing([K], { [K]: ref(v2) })).toEqual([K]);
    served = bytes(50, 9); // corrompu
    await expect(c.ensure(K, ref(v2))).rejects.toMatchObject({ code: "asset_hash" });
    expect(store.get(SERVER + K)).toEqual(v1);
    expect(c.hasAll([K], { [K]: ref(v1) })).toBe(true);
    served = v2;
    await c.ensure(K, ref(v2));
    expect(store.get(SERVER + K)).toEqual(v2);
    expect([...store.keys()]).toEqual([SERVER + K]);
    expect(c.hasAll([K], { [K]: ref(v2) })).toBe(true);
    expect(c.hasAll([K], { [K]: ref(v1) })).toBe(false);
  });

  it("redémarrage : l'identité est relue depuis IndexedDB, les restes d'attente sont purgés", async () => {
    const { store } = fakeCaches();
    const v1 = bytes(40, 1);
    const a = new AssetCache(SERVER);
    await a.init();
    served = v1;
    await a.ensure(K, ref(v1));
    store.set(`${SERVER}${K}?vds-staging=1`, bytes(3, 3));
    const b = new AssetCache(SERVER);
    await b.init();
    expect(b.keys()).toEqual([K]);
    expect(b.hasAll([K], { [K]: ref(v1) })).toBe(true);
    expect(store.size).toBe(1);
  });
});
