import type { AssetRef } from "@vds/sdk-ts";
import { DeviceRevokedError, NetworkError, errorCode, resolveServer } from "./api";
import { kvDel, kvGet, kvSet } from "./db";
import { CACHE_MEDIA, ERR_DEVICE_REVOKED, KV, NET_HEADER } from "./shared";
import { sha256Stream } from "./sha256";

export class AssetError extends Error {
  constructor(
    readonly code: "asset_http" | "asset_hash" | "asset_size",
    readonly key: string,
  ) {
    super(code);
    this.name = "AssetError";
  }
}

/** Cache des médias (`vds-media-v1`), indexé par clé relative à l'URL serveur. */
export class AssetCache {
  private present = new Set<string>();
  private cache: Cache | null = null;

  constructor(private readonly server: string) {}

  async init(): Promise<void> {
    this.cache = await caches.open(CACHE_MEDIA);
    this.present.clear();
    for (const req of await this.cache.keys()) {
      if (req.url.startsWith(this.server)) this.present.add(req.url.slice(this.server.length));
    }
  }

  has(key: string): boolean {
    return this.present.has(key);
  }

  hasAll(keys: readonly string[]): boolean {
    return keys.every((k) => this.present.has(k));
  }

  keys(): string[] {
    return [...this.present];
  }

  missing(keys: readonly string[]): string[] {
    return keys.filter((k) => !this.present.has(k));
  }

  /** Télécharge `key` s'il manque ou a changé ; sha256 vérifié sur le contenu stocké. */
  async ensure(key: string, ref: AssetRef, signal?: AbortSignal): Promise<void> {
    if (!this.cache) throw new Error("asset_cache_not_ready");
    if (this.present.has(key) && (await kvGet<string>(KV.asset(key))) === ref.sha256) return;
    const url = resolveServer(key, this.server);
    let res: Response;
    try {
      // Le SW ajoute le jeton d'appareil et laisse passer la requête réseau (en-tête NET_HEADER).
      res = await fetch(url, { headers: { [NET_HEADER]: "1" }, cache: "no-store", signal });
    } catch (e) {
      throw new NetworkError(e instanceof Error ? e.message : "fetch");
    }
    if (!res.ok) {
      if (res.status === 401 && (await errorCode(res)) === ERR_DEVICE_REVOKED) throw new DeviceRevokedError();
      throw new AssetError("asset_http", key);
    }
    await this.cache.put(url, res);
    const stored = await this.cache.match(url);
    const bad = async (code: "asset_hash" | "asset_size"): Promise<never> => {
      await this.cache?.delete(url);
      this.present.delete(key);
      await kvDel(KV.asset(key));
      throw new AssetError(code, key);
    };
    if (!stored?.body) return bad("asset_size");
    const sha = await sha256Stream(stored.body);
    if (sha !== ref.sha256) return bad("asset_hash");
    await kvSet(KV.asset(key), sha);
    this.present.add(key);
  }

  /** Supprime les médias non référencés (après bascule de révision). */
  async purge(keep: ReadonlySet<string>): Promise<void> {
    if (!this.cache) return;
    for (const key of [...this.present]) {
      if (keep.has(key)) continue;
      await this.cache.delete(resolveServer(key, this.server));
      await kvDel(KV.asset(key));
      this.present.delete(key);
    }
  }

  async clear(): Promise<void> {
    await caches.delete(CACHE_MEDIA);
    this.present.clear();
    this.cache = await caches.open(CACHE_MEDIA);
  }
}

export async function storageEstimate(): Promise<{ bytes: number; quota: number } | null> {
  try {
    const e = await navigator.storage?.estimate?.();
    if (!e) return null;
    return { bytes: e.usage ?? 0, quota: e.quota ?? 0 };
  } catch {
    return null;
  }
}
