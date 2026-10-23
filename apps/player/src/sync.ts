import type { ScheduleDocument } from "@vds/sdk-ts";
import { SCHEDULE_FORMAT } from "@vds/sdk-ts";
import { AssetError } from "./assets";
import type { AssetCache } from "./assets";
import { NetworkError, request, resolveServer } from "./api";
import { driftFrom, now, setDrift } from "./clock";
import { kvGet, kvSet, schedulePut } from "./db";
import { KV } from "./shared";
import { allAssets, neededSlots, slotAssets } from "./timeline";

export interface SyncDeps {
  token: () => string;
  assets: AssetCache;
  server: string;
  getActive: () => ScheduleDocument | null;
  activate: (doc: ScheduleDocument) => void;
  report: (code: string) => void;
}

/** Récupère la programmation (ETag), télécharge les médias, n'active qu'une fois le créneau en cours et le suivant en cache. */
export class Syncer {
  private busy: Promise<void> | null = null;

  constructor(private readonly deps: SyncDeps) {}

  /** Lance une synchronisation ; les appels simultanés partagent la même exécution. */
  trigger(): Promise<void> {
    this.busy ??= this.run().finally(() => {
      this.busy = null;
    });
    return this.busy;
  }

  private async run(): Promise<void> {
    const { deps } = this;
    const active = deps.getActive();
    const etag = active ? await kvGet<string>(KV.etag) : undefined;
    const sent = Date.now();
    let res: Response;
    try {
      res = await request(`api/v1/player/schedule?format=${SCHEDULE_FORMAT}`, {
        token: deps.token(),
        headers: etag ? { "If-None-Match": etag } : {},
      });
    } catch (e) {
      if (e instanceof NetworkError) return;
      throw e;
    }
    if (res.status === 304) {
      const doc = deps.getActive();
      if (doc) await this.fill(doc);
      return;
    }
    if (!res.ok) return;
    const doc = (await res.json()) as ScheduleDocument;
    if (doc.format !== SCHEDULE_FORMAT || !Array.isArray(doc.slots)) {
      deps.report("schedule_format");
      return;
    }
    const drift = driftFrom(doc.server_time, sent, Date.now());
    if (drift !== null) {
      setDrift(drift);
      await kvSet(KV.drift, drift);
    }
    try {
      await this.prefetchScripts(doc);
      await this.download(doc, slotAssets(doc, neededSlots(doc, now())));
    } catch (e) {
      if (e instanceof NetworkError) return; // l'ancienne révision continue de jouer
      throw e;
    }
    await schedulePut(doc);
    await kvSet(KV.etag, res.headers.get("ETag") ?? `"${doc.revision}"`);
    deps.activate(doc);
    await this.fill(doc);
  }

  /** Médias restants puis purge des médias non référencés. */
  private async fill(doc: ScheduleDocument): Promise<void> {
    try {
      await this.download(doc, allAssets(doc));
    } catch (e) {
      if (e instanceof NetworkError) return;
      throw e;
    }
    await this.deps.assets.purge(new Set(allAssets(doc)));
  }

  private async prefetchScripts(doc: ScheduleDocument): Promise<void> {
    for (const entry of Object.values(doc.content_types)) {
      try {
        const res = await fetch(resolveServer(entry.script, this.deps.server), { cache: "reload" });
        if (!res.ok) this.deps.report("content_script_http");
      } catch (e) {
        throw new NetworkError(e instanceof Error ? e.message : "fetch");
      }
    }
  }

  private async download(doc: ScheduleDocument, keys: string[]): Promise<void> {
    for (const key of keys) {
      const ref = doc.assets[key];
      if (!ref) {
        this.deps.report("asset_unknown");
        continue;
      }
      try {
        await this.deps.assets.ensure(key, ref);
      } catch (e) {
        if (e instanceof AssetError) {
          this.deps.report(e.code);
          continue; // média refusé ou corrompu : l'élément sera sauté, le reste est servi
        }
        throw e;
      }
    }
  }
}
