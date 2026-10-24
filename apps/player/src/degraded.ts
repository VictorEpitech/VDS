import type { AssetRef } from "@vds/sdk-ts";
import { AssetError } from "./assets";
import type { AssetStore } from "./assets";
import { DeviceRevokedError, NetworkError, errorCode, resolveServer } from "./api";
import { ERR_DEVICE_REVOKED } from "./shared";
import { sha256Stream } from "./sha256";

/** Budget mémoire des médias en mode dégradé (octets). */
export const DEGRADED_BUDGET = 128 * 1024 * 1024;

/**
 * Mode dégradé (test / LAN en HTTP) : ni Service Worker ni Cache API.
 * Actif seulement hors contexte sécurisé ou sans `caches` ; le chemin sécurisé n'est jamais désactivé.
 */
export function degradedMode(): boolean {
  return (typeof window !== "undefined" && window.isSecureContext === false) || typeof caches === "undefined";
}

interface Entry {
  blob: Blob;
  url: string;
  bytes: number;
  sha: string;
}

/**
 * Médias gardés en mémoire (Blob + URL `blob:`), LRU borné. Pas de hors ligne : si le réseau tombe,
 * ce qui est déjà en mémoire continue de jouer. Téléchargement à la demande (`lazy`) : créneau courant
 * et suivant à la synchronisation, puis item suivant préchargé par le moteur.
 */
export class MemoryAssetCache implements AssetStore {
  readonly lazy = true;
  /** Ordre d'insertion = ordre LRU (le plus ancien en tête). */
  private entries = new Map<string, Entry>();
  private inflight = new Map<string, Promise<void>>();
  private keep: ReadonlySet<string> = new Set();
  private total = 0;

  constructor(
    private readonly server: string,
    private readonly token: () => string,
    private readonly budget: number = DEGRADED_BUDGET,
  ) {}

  init(): Promise<void> {
    return Promise.resolve();
  }

  has(key: string): boolean {
    return this.entries.has(key);
  }

  hasAll(keys: readonly string[]): boolean {
    return keys.every((k) => this.entries.has(k));
  }

  keys(): string[] {
    return [...this.entries.keys()];
  }

  missing(keys: readonly string[]): string[] {
    return keys.filter((k) => !this.entries.has(k));
  }

  bytes(): number {
    return this.total;
  }

  /** URL `blob:` d'un média en mémoire (marqué récemment utilisé), sinon null. */
  urlFor(key: string): string | null {
    const e = this.entries.get(key);
    if (!e) return null;
    this.entries.delete(key);
    this.entries.set(key, e);
    return e.url;
  }

  ensure(key: string, ref: AssetRef, signal?: AbortSignal): Promise<void> {
    if (this.entries.has(key)) {
      this.urlFor(key);
      return Promise.resolve();
    }
    let p = this.inflight.get(key);
    if (!p) {
      p = this.download(key, ref, signal).finally(() => this.inflight.delete(key));
      this.inflight.set(key, p);
    }
    return p;
  }

  private async download(key: string, ref: AssetRef, signal?: AbortSignal): Promise<void> {
    let res: Response;
    try {
      const token = this.token();
      res = await fetch(resolveServer(key, this.server), {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        cache: "no-store",
        signal,
      });
    } catch (e) {
      throw new NetworkError(e instanceof Error ? e.message : "fetch");
    }
    if (!res.ok) {
      if (res.status === 401 && (await errorCode(res)) === ERR_DEVICE_REVOKED) throw new DeviceRevokedError();
      throw new AssetError("asset_http", key);
    }
    let blob: Blob;
    try {
      blob = await res.blob();
    } catch (e) {
      throw new NetworkError(e instanceof Error ? e.message : "body");
    }
    if (blob.size === 0 || blob.size > this.budget) throw new AssetError("asset_size", key);
    if ((await sha256Stream(new Response(blob).body as ReadableStream<Uint8Array>)) !== ref.sha256) {
      throw new AssetError("asset_hash", key);
    }
    this.makeRoom(blob.size);
    // Le type MIME du serveur (ou du descripteur) permet à <img>/<video> de décoder le blob.
    const typed = blob.type ? blob : blob.slice(0, blob.size, ref.mime);
    this.entries.set(key, { blob: typed, url: URL.createObjectURL(typed), bytes: blob.size, sha: ref.sha256 });
    this.total += blob.size;
  }

  /** Évince d'abord les médias hors programmation courante, puis les moins récemment utilisés. */
  private makeRoom(incoming: number): void {
    while (this.total + incoming > this.budget && this.entries.size > 0) {
      let victim: string | undefined;
      for (const k of this.entries.keys()) {
        if (!this.keep.has(k)) {
          victim = k;
          break;
        }
      }
      victim ??= this.entries.keys().next().value;
      if (victim === undefined) return;
      this.drop(victim);
    }
  }

  private drop(key: string): void {
    const e = this.entries.get(key);
    if (!e) return;
    URL.revokeObjectURL(e.url);
    this.entries.delete(key);
    this.total -= e.bytes;
  }

  /** Libère les médias hors programmation courante ; `keep` guide aussi l'éviction LRU. */
  purge(keep: ReadonlySet<string>): Promise<void> {
    this.keep = keep;
    for (const k of [...this.entries.keys()]) if (!keep.has(k)) this.drop(k);
    return Promise.resolve();
  }

  clear(): Promise<void> {
    for (const k of [...this.entries.keys()]) this.drop(k);
    this.keep = new Set();
    return Promise.resolve();
  }
}
