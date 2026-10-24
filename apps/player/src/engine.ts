import type {
  ContentContext,
  LayoutZone,
  Locale,
  PlaySlot,
  PlaylistItem,
  ScheduleDocument,
  VdsContentElement,
} from "@vds/sdk-ts";
import type { AssetStore } from "./assets";
import { resolveServer } from "./api";
import { now } from "./clock";
import type { ContentLoader } from "./content-loader";
import { logAdd } from "./db";
import type { Screens } from "./screens";
import { mark } from "./alive";
import { resolveAt } from "./timeline";

export const PREPARE_TIMEOUT_MS = 10_000;
const MAX_UNBOUNDED_MS = 30 * 60_000;
const IDLE_RETRY_MS = 5_000;
const ERROR_RING = 50;
const MIN_ITEM_MS = 1_000;

export interface PlayerError {
  at: string;
  code: string;
  item_id?: string;
}

interface Prepared {
  el: VdsContentElement;
  item: PlaylistItem;
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const t = setTimeout(done, ms);
    function done(): void {
      clearTimeout(t);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done, { once: true });
  });
}

function withTimeout<T>(p: Promise<T>, ms: number, code: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return Promise.race([
    p,
    new Promise<never>((_, rej) => {
      timer = setTimeout(() => rej(new Error(code)), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}

export interface EngineDeps {
  screens: Screens;
  loader: ContentLoader;
  assets: AssetStore;
  server: string;
  locale: Locale;
  isOnline: () => boolean;
  /** Appelé à chaque tick (1 s), après l'application du créneau : plan d'alimentation hors ligne. */
  onTick?: () => void;
}

class ZoneRunner {
  private ctl = new AbortController();
  private readonly root: HTMLElement;
  current: { item: PlaylistItem; since: number } | null = null;

  constructor(
    readonly key: string,
    private readonly zone: LayoutZone,
    private readonly items: PlaylistItem[],
    private readonly doc: ScheduleDocument,
    private readonly deps: EngineDeps,
    private readonly report: (code: string, itemId?: string) => void,
    stage: HTMLElement,
  ) {
    this.root = document.createElement("div");
    this.root.className = "zone";
    this.root.dataset.zone = zone.id;
    const pct = (v: number): string => `${(v * 100).toFixed(4)}%`;
    this.root.style.cssText = `left:${pct(zone.x)};top:${pct(zone.y)};width:${pct(zone.w)};height:${pct(zone.h)}`;
    stage.append(this.root);
    void this.loop();
  }

  stop(): void {
    this.ctl.abort();
    this.current = null;
    for (const child of Array.from(this.root.children)) {
      (child as Partial<VdsContentElement>).stop?.();
    }
    this.root.remove();
  }

  private context(): ContentContext {
    return {
      locale: this.deps.locale,
      orientation: this.doc.screen.orientation,
      zone: { width: this.root.clientWidth, height: this.root.clientHeight },
      resolveUrl: (rel) => this.deps.assets.urlFor?.(rel) ?? resolveServer(rel, this.deps.server),
    };
  }

  /** Prépare un élément hors écran ; null = élément sauté (jamais de lecture en flux). */
  private async prepare(item: PlaylistItem): Promise<Prepared | null> {
    if (this.ctl.signal.aborted) return null;
    if (item.requires_network && !this.deps.isOnline()) {
      this.report("offline_skipped", item.id);
      return null;
    }
    const { assets } = this.deps;
    if (assets.lazy && !assets.hasAll(item.assets)) {
      // Mode dégradé : téléchargement de l'item (le suivant est préparé pendant que le courant joue).
      try {
        for (const k of item.assets) {
          const ref = this.doc.assets[k];
          if (ref) await withTimeout(assets.ensure(k, ref, this.ctl.signal), 6 * PREPARE_TIMEOUT_MS, "asset_timeout");
        }
      } catch {
        // réseau coupé ou média refusé : item sauté ci-dessous
      }
      if (this.ctl.signal.aborted) return null;
    }
    if (!assets.hasAll(item.assets)) {
      this.report("asset_missing", item.id);
      return null;
    }
    const entry = this.doc.content_types[item.content_type];
    if (!entry) {
      this.report("content_type_unknown", item.id);
      return null;
    }
    let el: VdsContentElement | null = null;
    try {
      await this.deps.loader.ensure(entry);
      el = this.deps.loader.create(entry);
      el.configure(item.props, this.context());
      this.root.append(el);
      await withTimeout(el.prepare(), PREPARE_TIMEOUT_MS, "prepare_timeout");
      return { el, item };
    } catch {
      this.discard(el);
      this.report("prepare_failed", item.id);
      return null;
    }
  }

  private sameExclusive(p: Prepared, nextItem: PlaylistItem): boolean {
    if (!p.el.exclusive) return false;
    const a = this.doc.content_types[p.item.content_type]?.element;
    return a !== undefined && a === this.doc.content_types[nextItem.content_type]?.element;
  }

  private discard(el: VdsContentElement | null): void {
    if (!el) return;
    try {
      el.stop();
    } finally {
      el.remove();
    }
  }

  /** Démarre l'élément puis attend sa fin : durée connue, sinon `vds-ended` (plafond 30 min). */
  private async play(p: Prepared): Promise<boolean> {
    const { el, item } = p;
    const local = new AbortController();
    let failed = false;
    const stopWaiting = (): void => local.abort();
    const onError = (): void => {
      failed = true;
      this.report("playback_error", item.id);
      local.abort();
    };
    this.ctl.signal.addEventListener("abort", stopWaiting, { once: true });
    el.addEventListener("vds-error", onError);
    if (item.duration_ms === null) el.addEventListener("vds-ended", stopWaiting);
    try {
      el.start();
      this.current = { item, since: now() };
      const ms = item.duration_ms === null ? MAX_UNBOUNDED_MS : Math.max(item.duration_ms, MIN_ITEM_MS);
      await sleep(ms, local.signal);
    } finally {
      el.removeEventListener("vds-error", onError);
      el.removeEventListener("vds-ended", stopWaiting);
      this.ctl.signal.removeEventListener("abort", stopWaiting);
    }
    return !failed;
  }

  private async loop(): Promise<void> {
    const { signal } = this.ctl;
    const items = this.items;
    if (items.length === 0) return;
    let idx = 0;
    let shown: Prepared | null = null;
    let next: Promise<Prepared | null> = this.prepare(items[0]!);
    let playedInPass = 0;
    while (!signal.aborted) {
      const p = await next;
      if (signal.aborted) {
        this.discard(p?.el ?? null);
        break;
      }
      const nextIdx = (idx + 1) % items.length;
      if (p) {
        // Bascule sans flash : le nouvel élément passe devant, puis l'ancien est libéré.
        if (shown) {
          const old = shown;
          setTimeout(() => this.discard(old.el), 50);
        }
        shown = p;
        // Le suivant se prépare pendant que celui-ci joue (un seul préchargement à la fois).
        // Élément exclusif (décodeur vidéo) suivi d'un élément du même type : pas de préchargement,
        // l'élément courant est libéré avant de préparer le suivant (jamais deux <video> chargées).
        const serial = this.sameExclusive(p, items[nextIdx]!);
        next = serial ? Promise.resolve(null) : this.prepare(items[nextIdx]!);
        const ok = await this.play(p);
        if (ok) playedInPass++;
        if (!ok || serial) {
          this.discard(p.el);
          shown = null;
        }
        if (serial && !signal.aborted) next = this.prepare(items[nextIdx]!);
      } else {
        next = this.prepare(items[nextIdx]!);
      }
      idx = nextIdx;
      if (idx === 0) {
        if (playedInPass === 0) await sleep(IDLE_RETRY_MS, signal);
        playedInPass = 0;
      }
    }
    this.discard(shown?.el ?? null);
    this.discard((await next.catch(() => null))?.el ?? null);
  }
}

export class Engine {
  private runners = new Map<string, ZoneRunner>();
  private doc: ScheduleDocument | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private slotKey = "";
  private errs: PlayerError[] = [];
  private running = false;
  /** Horodatage (performance.now) du dernier tick : surveillé par le watchdog. */
  lastTick = performance.now();
  stale = false;
  currentKind: "play" | "off" | "none" = "none";

  constructor(private readonly deps: EngineDeps) {}

  setDoc(doc: ScheduleDocument | null): void {
    this.doc = doc;
    this.tick();
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.tick();
  }

  stop(): void {
    this.running = false;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.clearRunners();
    this.slotKey = "";
  }

  takeErrors(): PlayerError[] {
    return this.errs.splice(0, this.errs.length);
  }

  restoreErrors(errs: PlayerError[]): void {
    this.errs = [...errs, ...this.errs].slice(-ERROR_RING);
  }

  playing(): { item_id: string; since: string } | null {
    for (const r of this.runners.values()) {
      if (r.current) return { item_id: r.current.item.id, since: new Date(r.current.since).toISOString() };
    }
    return null;
  }

  /** Écran noir imposé par le plan d'alimentation : lecture suspendue, éléments libérés. */
  private suspended = false;

  setSuspended(on: boolean): void {
    if (this.suspended === on) return;
    this.suspended = on;
    this.tick();
  }

  report = (code: string, itemId?: string): void => {
    const at = new Date(now()).toISOString();
    const entry: PlayerError = itemId ? { at, code, item_id: itemId } : { at, code };
    this.errs.push(entry);
    if (this.errs.length > ERROR_RING) this.errs.shift();
    void logAdd({ at: now(), code, item_id: itemId }).catch(() => {});
  };

  private clearRunners(): void {
    for (const r of this.runners.values()) r.stop();
    this.runners.clear();
  }

  tick = (): void => {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.lastTick = performance.now();
    mark();
    try {
      this.apply();
    } catch {
      this.report("engine_error");
    }
    try {
      this.deps.onTick?.();
    } catch {
      this.report("power_error");
    }
    if (this.running) this.timer = setTimeout(this.tick, 1000);
  };

  private apply(): void {
    const { screens } = this.deps;
    const doc = this.doc;
    if (screens.active === "maintenance") return;
    if (this.suspended) {
      this.clearRunners();
      this.slotKey = "";
      this.currentKind = "off";
      screens.show("black");
      return;
    }
    if (!doc) {
      this.clearRunners();
      this.currentKind = "none";
      return;
    }
    const resolved = resolveAt(doc, now());
    this.stale = resolved?.stale ?? false;
    if (!resolved) {
      this.clearRunners();
      this.slotKey = "";
      this.currentKind = "none";
      screens.show("idle");
      return;
    }
    const { slot } = resolved;
    this.slotKey = `${resolved.stale ? "s" : "d"}:${slot.start}:${slot.kind}`;
    if (slot.kind === "off") {
      this.clearRunners();
      this.currentKind = "off";
      screens.show("black");
      return;
    }
    this.currentKind = "play";
    const zones = (slot as PlaySlot).layout.zones;
    const wanted = new Map<string, { zone: LayoutZone; items: PlaylistItem[] }>();
    for (const z of zones) {
      const items = doc.playlists[z.playlist]?.items ?? [];
      wanted.set(`${z.id}|${z.x}|${z.y}|${z.w}|${z.h}|${JSON.stringify(items)}`, { zone: z, items });
    }
    for (const [key, r] of this.runners) {
      if (!wanted.has(key)) {
        r.stop();
        this.runners.delete(key);
      }
    }
    for (const [key, w] of wanted) {
      if (!this.runners.has(key)) {
        this.runners.set(key, new ZoneRunner(key, w.zone, w.items, doc, this.deps, this.report, screens.stage));
      }
    }
    screens.show(wanted.size > 0 && [...wanted.values()].some((w) => w.items.length > 0) ? "none" : "idle");
  }
}
