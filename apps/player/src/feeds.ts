import type { FeedSnapshot } from "@vds/sdk-ts";
import { request } from "./api";
import { feedDel, feedGet, feedKeys, feedPut } from "./db";

/** Délai réseau d'un `ctx.feed` : au-delà, le dernier état connu (IndexedDB) est servi. */
export const FEED_NET_TIMEOUT_MS = 3_000;
/** Sondage des flux utilisés : jamais sous 300 s (contrat 1.12, le serveur borne `refresh_s` pareil). */
export const FEED_POLL_MS = 300_000;

export interface FeedDeps {
  token: () => string;
}

const ID_RE = /^[a-f0-9]{8,64}$/;

/** Dernier état connu des flux de données : réseau (serveur du player) si possible, sinon IndexedDB. */
export class FeedService {
  private inflight = new Map<string, Promise<FeedSnapshot | null>>();
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private used: string[] = [];
  private stopped = false;

  constructor(private readonly deps: FeedDeps) {}

  /** Réseau avec délai de 3 s puis mise à jour du cache ; sinon valeur en cache ; null si jamais reçu. */
  get(feedId: string): Promise<FeedSnapshot | null> {
    let p = this.inflight.get(feedId);
    if (!p) {
      p = this.load(feedId).finally(() => this.inflight.delete(feedId));
      this.inflight.set(feedId, p);
    }
    return p;
  }

  private async load(feedId: string): Promise<FeedSnapshot | null> {
    if (!ID_RE.test(feedId)) return null; // l'identifiant entre dans l'URL : jamais de valeur arbitraire
    const cached = await feedGet(feedId).catch(() => undefined);
    const snap = (r: { feedId: string; fetchedAt: string; data: unknown } | undefined): FeedSnapshot | null =>
      r ? { feedId: r.feedId, fetchedAt: r.fetchedAt, data: r.data } : null;
    if (typeof navigator !== "undefined" && navigator.onLine === false) return snap(cached);
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), FEED_NET_TIMEOUT_MS);
    try {
      const res = await request(`api/v1/player/feeds/${feedId}`, {
        token: this.deps.token(),
        headers: cached?.etag ? { "If-None-Match": cached.etag } : {},
        signal: ctl.signal,
      });
      if (res.status === 200) {
        const body = (await res.json()) as { feed_id?: unknown; fetched_at?: unknown; data?: unknown };
        if (typeof body.fetched_at !== "string" || !("data" in body)) return snap(cached);
        const rec = { feedId, fetchedAt: body.fetched_at, data: body.data, etag: res.headers.get("ETag") ?? undefined };
        await feedPut(rec).catch(() => {});
        return snap(rec);
      }
      return snap(cached); // 304 (inchangé), 204 (jamais récupéré côté serveur), 404 : état connu
    } catch {
      return snap(cached); // réseau coupé, délai dépassé, corps illisible, jeton révoqué (traité par la synchro)
    } finally {
      clearTimeout(timer);
    }
  }

  /** Précharge les flux de la programmation, un par un, puis purge ceux qui n'y figurent plus. */
  async preload(ids: string[]): Promise<void> {
    this.used = ids;
    for (const id of ids) {
      if (this.stopped) return;
      await this.get(id);
    }
    const keep = new Set(ids);
    for (const k of await feedKeys().catch(() => [])) if (!keep.has(k)) await feedDel(k).catch(() => {});
    this.schedulePoll();
  }

  /** Sondage tant que la programmation référence des flux : un tour toutes les 300 s, séquentiel. */
  private schedulePoll(): void {
    if (this.pollTimer || this.stopped || this.used.length === 0) return;
    this.pollTimer = setTimeout(() => {
      this.pollTimer = null;
      void (async () => {
        for (const id of this.used) {
          if (this.stopped) return;
          await this.get(id);
        }
        this.schedulePoll();
      })();
    }, FEED_POLL_MS);
  }

  stop(): void {
    this.stopped = true;
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.pollTimer = null;
  }
}
