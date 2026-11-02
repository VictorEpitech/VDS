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

/** Ce que le moteur, la synchronisation et le heartbeat attendent d'un stock de médias. */
export interface AssetStore {
  init(): Promise<void>;
  has(key: string): boolean;
  /** Identité = (clé, sha256) : avec `refs`, une clé dont le sha256 diffère compte comme absente. */
  hasAll(keys: readonly string[], refs?: Readonly<Record<string, AssetRef>>): boolean;
  keys(): string[];
  missing(keys: readonly string[], refs?: Readonly<Record<string, AssetRef>>): string[];
  ensure(key: string, ref: AssetRef, signal?: AbortSignal): Promise<void>;
  purge(keep: ReadonlySet<string>): Promise<void>;
  clear(): Promise<void>;
  /** Mode dégradé : téléchargement à la demande, borné en mémoire. */
  readonly lazy?: boolean;
  /** Mode dégradé : URL `blob:` du média, sinon l'URL serveur (servie par le SW). */
  urlFor?(key: string): string | null;
}

/** Cache des médias (`vds-media-v1`), indexé par clé relative à l'URL serveur. */
export class AssetCache implements AssetStore {
  /** clé -> sha256 vérifié (miroir de IndexedDB `asset:<clé>`). */
  private present = new Map<string, string>();
  private cache: Cache | null = null;

  constructor(private readonly server: string) {}

  async init(): Promise<void> {
    this.present.clear();
    // Cache API absente (contexte non sécurisé) : aucun média local, le player reste neutre.
    if (typeof caches === "undefined") return;
    this.cache = await caches.open(CACHE_MEDIA);
    for (const req of await this.cache.keys()) {
      if (!req.url.startsWith(this.server)) continue;
      if (req.url.includes("vds-staging=1")) {
        await this.cache.delete(req); // reste d'un téléchargement interrompu
        continue;
      }
      const key = req.url.slice(this.server.length);
      // Octets sans empreinte vérifiée (IndexedDB vidée) : jamais jouables avec une référence.
      this.present.set(key, (await kvGet<string>(KV.asset(key))) ?? "");
    }
  }

  has(key: string): boolean {
    return this.present.has(key);
  }

  hasAll(keys: readonly string[], refs?: Readonly<Record<string, AssetRef>>): boolean {
    return keys.every((k) => this.matches(k, refs));
  }

  private matches(key: string, refs?: Readonly<Record<string, AssetRef>>): boolean {
    const sha = this.present.get(key);
    if (sha === undefined) return false;
    const ref = refs?.[key];
    return !ref || ref.sha256 === sha;
  }

  keys(): string[] {
    return [...this.present.keys()];
  }

  missing(keys: readonly string[], refs?: Readonly<Record<string, AssetRef>>): string[] {
    return keys.filter((k) => !this.matches(k, refs));
  }

  /** Télécharge `key` s'il manque ou a changé ; sha256 vérifié sur le contenu stocké. */
  async ensure(key: string, ref: AssetRef, signal?: AbortSignal): Promise<void> {
    if (!this.cache) throw new Error("asset_cache_not_ready");
    if (this.present.get(key) === ref.sha256) return;
    const url = resolveServer(key, this.server);
    let res: Response;
    try {
      // Le SW ajoute le jeton d'appareil et laisse passer la requête réseau (en-tête NET_HEADER).
      // Le jeton est aussi transmis explicitement : le SW ne peut pas le lire si IndexedDB est indisponible (repli mémoire).
      const headers: Record<string, string> = { [NET_HEADER]: "1" };
      const token = await kvGet<string>(KV.token);
      if (token) headers.Authorization = `Bearer ${token}`;
      res = await fetch(url, { headers, cache: "no-store", signal });
    } catch (e) {
      throw new NetworkError(e instanceof Error ? e.message : "fetch");
    }
    if (!res.ok) {
      if (res.status === 401 && (await errorCode(res)) === ERR_DEVICE_REVOKED) throw new DeviceRevokedError();
      throw new AssetError("asset_http", key);
    }
    // Écriture sur une clé d'attente : l'octet courant (autre sha256, même clé) reste jouable
    // jusqu'à la vérification du nouveau (remplacement de fichier, architecture-mediatheque §4).
    const staging = `${url}${url.includes("?") ? "&" : "?"}vds-staging=1`;
    const cache = this.cache;
    await cache.put(staging, res);
    const bad = async (code: "asset_hash" | "asset_size"): Promise<never> => {
      await cache.delete(staging);
      throw new AssetError(code, key);
    };
    const stored = await cache.match(staging);
    if (!stored?.body) return bad("asset_size");
    const sha = await sha256Stream(stored.body);
    if (sha !== ref.sha256) return bad("asset_hash");
    const verified = await cache.match(staging);
    if (!verified) return bad("asset_size");
    await cache.put(url, verified);
    await cache.delete(staging);
    await kvSet(KV.asset(key), sha);
    this.present.set(key, sha);
  }

  /** Supprime les médias non référencés (après bascule de révision). */
  async purge(keep: ReadonlySet<string>): Promise<void> {
    if (!this.cache) return;
    for (const key of [...this.present.keys()]) {
      if (keep.has(key)) continue;
      await this.cache.delete(resolveServer(key, this.server));
      await kvDel(KV.asset(key));
      this.present.delete(key);
    }
  }

  async clear(): Promise<void> {
    this.present.clear();
    if (typeof caches === "undefined") return;
    await caches.delete(CACHE_MEDIA);
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
