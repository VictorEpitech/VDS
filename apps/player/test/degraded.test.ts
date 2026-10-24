import { createHash } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryAssetCache, degradedMode } from "../src/degraded";
import { NetworkError } from "../src/api";

const sha = (b: Uint8Array): string => createHash("sha256").update(b).digest("hex");
const media = (n: number, seed: number): Uint8Array => new Uint8Array(n).fill(seed);

let served: Record<string, Uint8Array>;
let auth: (string | null)[];
let down = false;
let n = 0;

beforeEach(() => {
  served = {};
  auth = [];
  down = false;
  n = 0;
  vi.stubGlobal("fetch", async (url: string, init: { headers: Record<string, string> }) => {
    if (down) throw new TypeError("net");
    auth.push(init.headers.Authorization ?? null);
    const body = served[new URL(url).pathname.slice(1)];
    return body ? new Response(body, { headers: { "Content-Type": "image/png" } }) : new Response("", { status: 404 });
  });
  URL.createObjectURL = (() => `blob:test/${++n}`) as never;
  URL.revokeObjectURL = (() => {}) as never;
});
afterEach(() => vi.unstubAllGlobals());

const ref = (b: Uint8Array) => ({ sha256: sha(b), bytes: b.length, mime: "image/png" });

describe("mode dégradé", () => {
  it("actif seulement sans contexte sécurisé ou sans caches", () => {
    vi.stubGlobal("caches", {});
    expect(degradedMode()).toBe(false);
    vi.stubGlobal("caches", undefined);
    expect(degradedMode()).toBe(true);
    vi.stubGlobal("caches", {});
    Object.defineProperty(window, "isSecureContext", { value: false, configurable: true });
    expect(degradedMode()).toBe(true);
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
  });

  it("télécharge avec Authorization, vérifie le hash, expose une URL blob:", async () => {
    served["media/a/original"] = media(100, 1);
    const c = new MemoryAssetCache("http://s/", () => "tok");
    await c.ensure("media/a/original", ref(served["media/a/original"]!));
    expect(auth).toEqual(["Bearer tok"]);
    expect(c.urlFor("media/a/original")).toMatch(/^blob:/);
    await expect(c.ensure("media/a/original", ref(media(3, 9)))).resolves.toBeUndefined(); // déjà présent
    served["media/b/original"] = media(10, 2);
    await expect(c.ensure("media/b/original", ref(media(10, 3)))).rejects.toMatchObject({ code: "asset_hash" });
    expect(c.has("media/b/original")).toBe(false);
  });

  it("LRU borné : évince hors programmation d'abord, puis le plus ancien", async () => {
    const c = new MemoryAssetCache("http://s/", () => "t", 250);
    for (const k of ["a", "b", "c"]) served[`media/${k}`] = media(100, k.charCodeAt(0));
    await c.ensure("media/a", ref(served["media/a"]!));
    await c.ensure("media/b", ref(served["media/b"]!));
    await c.purge(new Set(["media/a", "media/c"])); // b hors programmation : libéré
    expect(c.keys()).toEqual(["media/a"]);
    await c.ensure("media/b", ref(served["media/b"]!));
    c.urlFor("media/a");
    await c.ensure("media/c", ref(served["media/c"]!));
    expect(c.bytes()).toBeLessThanOrEqual(250);
    expect(c.has("media/c")).toBe(true);
    expect(c.has("media/b")).toBe(false); // hors programmation, évincé avant a
    expect(c.has("media/a")).toBe(true);
  });

  it("réseau coupé : le contenu en mémoire reste, le reste échoue en NetworkError", async () => {
    served["media/a"] = media(10, 1);
    served["media/b"] = media(10, 2);
    const c = new MemoryAssetCache("http://s/", () => "t");
    await c.ensure("media/a", ref(served["media/a"]!));
    down = true;
    expect(c.urlFor("media/a")).not.toBeNull();
    await expect(c.ensure("media/b", ref(served["media/b"]!))).rejects.toBeInstanceOf(NetworkError);
    expect(c.has("media/b")).toBe(false);
  });
});
