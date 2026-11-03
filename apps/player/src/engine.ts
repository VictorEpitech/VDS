import type {
  ContentContext,
  FeedSnapshot,
  LayoutZone,
  Locale,
  PlaySlot,
  PlaylistItem,
  ScheduleDocument,
  VdsContentElement,
  ZoneOverrideWindow,
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
const FEED_CODES = new Set(["feed_unavailable", "feed_stale"]);

export interface PlayerError {
  at: string;
  code: string;
  item_id?: string;
}

interface Prepared {
  el: VdsContentElement;
  item: PlaylistItem;
}

/** Instant `until` de l'élément (ms), null si absent ; valeur illisible = ignorée (jamais de saut à tort). */
export function untilMs(item: PlaylistItem): number | null {
  if (!item.until) return null;
  const t = Date.parse(item.until);
  return Number.isNaN(t) ? null : t;
}

export function expired(item: PlaylistItem, at: number): boolean {
  const u = untilMs(item);
  return u !== null && at >= u;
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
  /** Flux de données (météo, RSS) : alimente `ctx.feed`. */
  feeds?: { get(feedId: string): Promise<FeedSnapshot | null> };
  /** Appelé à chaque tick (1 s), après l'application du créneau : plan d'alimentation hors ligne. */
  onTick?: () => void;
}

/** Ressources exclusives (décodeur vidéo) : au plus `max` éléments tenus à la fois, toutes zones confondues. */
export class ExclusiveBudget {
  max = 1;
  private held = new WeakSet<object>();
  private used = 0;

  acquire(el: object): boolean {
    if (this.held.has(el)) return true;
    if (this.used >= this.max) return false;
    this.held.add(el);
    this.used++;
    return true;
  }

  release(el: object): void {
    if (!this.held.delete(el)) return;
    this.used--;
  }
}

const num = (v: unknown, lo: number, hi: number, def: number): number =>
  typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : def;
const COLOR_RE = /^#[0-9a-fA-F]{6}$/;
const FITS = new Set(["contain", "cover", "stretch"]);

function ms(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

/** Lecteur d'une playlist dans un conteneur de zone (curseur propre, préchargement d'un seul élément). */
class PlaylistLoop {
  private ctl = new AbortController();
  current: { item: PlaylistItem; since: number } | null = null;
  /** Interrompt l'attente de l'élément courant (fin de validité atteinte). */
  private cut: (() => void) | null = null;
  /** Conteneur propre à ce lecteur : retiré d'un bloc à l'arrêt, un élément tardif ne reste jamais affiché. */
  private readonly root: HTMLElement;

  constructor(
    private readonly zoneId: string,
    zoneRoot: HTMLElement,
    private readonly items: PlaylistItem[],
    private readonly doc: ScheduleDocument,
    private readonly deps: EngineDeps,
    private readonly report: (code: string, itemId?: string) => void,
    private readonly budget: ExclusiveBudget,
  ) {
    this.root = document.createElement("div");
    this.root.className = "play";
    zoneRoot.append(this.root);
    void this.loop();
  }

  stop(): void {
    this.ctl.abort();
    this.current = null;
    for (const child of Array.from(this.root.children)) {
      (child as Partial<VdsContentElement>).stop?.();
      this.budget.release(child);
    }
    this.root.remove();
  }

  /** Contrôle à chaque tick : l'élément courant dont `until` est dépassé s'arrête immédiatement. */
  checkExpiry(at: number): void {
    const cur = this.current;
    if (cur && expired(cur.item, at)) this.cut?.();
  }

  private context(): ContentContext {
    return {
      locale: this.deps.locale,
      orientation: this.doc.screen.orientation,
      zone: { width: this.root.clientWidth, height: this.root.clientHeight, id: this.zoneId },
      resolveUrl: (rel) => this.deps.assets.urlFor?.(rel) ?? resolveServer(rel, this.deps.server),
      now,
      ...(this.deps.feeds ? { feed: (id: string) => this.deps.feeds!.get(id) } : {}),
    };
  }

  /** Prépare un élément hors écran ; null = élément sauté (jamais de lecture en flux). */
  private async prepare(item: PlaylistItem): Promise<Prepared | null> {
    if (this.ctl.signal.aborted) return null;
    if (expired(item, now())) return null; // expiré : sauté sans bruit (état attendu, pas une erreur)
    if (item.requires_network && !this.deps.isOnline()) {
      this.report("offline_skipped", item.id);
      return null;
    }
    const { assets } = this.deps;
    if (assets.lazy && !assets.hasAll(item.assets, this.doc.assets)) {
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
    if (!assets.hasAll(item.assets, this.doc.assets)) {
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
      if (el.exclusive && !this.budget.acquire(el)) {
        // Décodeur déjà tenu par une autre zone : sauté comme une erreur, sans message au public.
        el = null;
        this.report("exclusive_limit", item.id);
        return null;
      }
      el.configure(item.props, this.context());
      this.root.append(el);
      await withTimeout(el.prepare(), PREPARE_TIMEOUT_MS, "prepare_timeout");
      return { el, item };
    } catch (e) {
      this.discard(el);
      // Flux absent ou trop ancien : code précis remonté au serveur, jamais affiché au public.
      const msg = e instanceof Error ? e.message : "";
      this.report(FEED_CODES.has(msg) ? msg : "prepare_failed", item.id);
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
      this.budget.release(el);
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
    if (expired(item, now())) return false; // expiré pendant la préparation
    this.ctl.signal.addEventListener("abort", stopWaiting, { once: true });
    el.addEventListener("vds-error", onError);
    if (item.duration_ms === null) el.addEventListener("vds-ended", stopWaiting);
    try {
      el.start();
      this.current = { item, since: now() };
      const ms = item.duration_ms === null ? MAX_UNBOUNDED_MS : Math.max(item.duration_ms, MIN_ITEM_MS);
      this.cut = stopWaiting;
      await sleep(ms, local.signal);
    } finally {
      this.cut = null;
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
    let slept = false;
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
        if (!ok || serial || expired(p.item, now())) {
          this.discard(p.el);
          shown = null;
        }
        if (serial && !signal.aborted) next = this.prepare(items[nextIdx]!);
      } else {
        // Fin d'une passe où rien n'a pu jouer : attendre avant de réessayer (ex. décodeur tenu par une autre zone).
        if (nextIdx === 0 && playedInPass === 0) {
          await sleep(IDLE_RETRY_MS, signal);
          slept = true;
        }
        if (!signal.aborted) next = this.prepare(items[nextIdx]!);
      }
      idx = nextIdx;
      if (idx === 0) {
        if (playedInPass === 0 && !slept) await sleep(IDLE_RETRY_MS, signal);
        slept = false;
        playedInPass = 0;
      }
    }
    this.discard(shown?.el ?? null);
    this.discard((await next.catch(() => null))?.el ?? null);
  }
}

interface ZoneModel {
  zone: LayoutZone;
  base: PlaylistItem[];
  overrides: { win: ZoneOverrideWindow; from: number; until: number; items: PlaylistItem[] }[];
  from: number | null;
  until: number | null;
}

/** Conteneur d'une zone : géométrie, fenêtre d'affichage et forçages suivis sur l'horloge locale (hors ligne). */
class ZoneRunner {
  private readonly root: HTMLElement;
  private loop: PlaylistLoop | null = null;
  private modeKey = "";

  constructor(
    private readonly m: ZoneModel,
    private readonly doc: ScheduleDocument,
    private readonly deps: EngineDeps,
    private readonly report: (code: string, itemId?: string) => void,
    private readonly budget: ExclusiveBudget,
    stage: HTMLElement,
  ) {
    const z = m.zone;
    this.root = document.createElement("div");
    this.root.className = "zone";
    this.root.dataset.zone = z.id;
    this.root.dataset.fit = FITS.has(z.fit as string) ? (z.fit as string) : "contain";
    const x = num(z.x, 0, 1, 0);
    const y = num(z.y, 0, 1, 0);
    const pct = (v: number): string => `${(v * 100).toFixed(4)}%`;
    const st = this.root.style;
    st.left = pct(x);
    st.top = pct(y);
    st.width = pct(num(z.w, 0, 1 - x, 1 - x));
    st.height = pct(num(z.h, 0, 1 - y, 1 - y));
    st.zIndex = String(Math.trunc(num(z.z, 0, 1000, 0)));
    stage.append(this.root);
  }

  private mode(at: number): { key: string; items: PlaylistItem[] } | null {
    const { from, until } = this.m;
    if ((from !== null && at < from) || (until !== null && at >= until)) return null;
    for (const o of this.m.overrides) {
      if (o.from <= at && at < o.until) return { key: `ov:${o.win.from}:${o.win.until}`, items: o.items };
    }
    return { key: "base", items: this.m.base };
  }

  /** Flux de données (météo, RSS) : alimente `ctx.feed`. */
  feeds?: { get(feedId: string): Promise<FeedSnapshot | null> };
  /** Appelé à chaque tick : bascule de lecteur à `from`, `until` et aux bornes des forçages ; sinon ne touche à rien. */
  update(at: number): void {
    const mode = this.mode(at);
    const key = mode?.key ?? "hidden";
    if (key !== this.modeKey) {
      this.loop?.stop();
      this.loop = null;
      this.modeKey = key;
      if (mode && mode.items.length > 0) {
        this.loop = new PlaylistLoop(this.m.zone.id, this.root, mode.items, this.doc, this.deps, this.report, this.budget);
      }
    }
    this.loop?.checkExpiry(at);
  }

  hasContent(at: number): boolean {
    const mode = this.mode(at);
    return !!mode && mode.items.some((i) => !expired(i, at));
  }

  current(): { item: PlaylistItem; since: number } | null {
    return this.loop?.current ?? null;
  }

  stop(): void {
    this.loop?.stop();
    this.loop = null;
    this.root.remove();
  }
}

export class Engine {
  private runners = new Map<string, ZoneRunner>();
  private budget = new ExclusiveBudget();
  private sigs = new Map<string, string>();
  private bg: { key: string; el: HTMLElement } | null = null;
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
      const c = r.current();
      if (c) return { item_id: c.item.id, since: new Date(c.since).toISOString() };
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
    this.sigs.clear();
    this.setBackground(null, null);
  }

  /** Fond sous toutes les zones : couleur validée par regex, puis image (cover). Jamais de chaîne libre en CSS. */
  private setBackground(bg: PlaySlot["layout"]["background"] | undefined, doc: ScheduleDocument | null): void {
    const color = bg?.color && COLOR_RE.test(bg.color) ? bg.color : "";
    const asset = bg?.asset && doc?.assets[bg.asset] && this.deps.assets.hasAll([bg.asset], doc.assets) ? bg.asset : "";
    const key = `${color}|${asset}`;
    if (!color && !asset) {
      this.bg?.el.remove();
      this.bg = null;
      return;
    }
    if (this.bg?.key === key) return;
    this.bg?.el.remove();
    const el = document.createElement("div");
    el.className = "bg";
    if (color) el.style.backgroundColor = color;
    if (asset) {
      const img = document.createElement("img");
      img.alt = "";
      img.src = this.deps.assets.urlFor?.(asset) ?? resolveServer(asset, this.deps.server);
      el.append(img);
    }
    this.deps.screens.stage.prepend(el);
    this.bg = { key, el };
  }

  private model(doc: ScheduleDocument, z: LayoutZone, stale: boolean): ZoneModel {
    const items = (key: string): PlaylistItem[] => doc.playlists[key]?.items ?? [];
    const overrides: ZoneModel["overrides"] = [];
    // Document expiré (motif rejoué) : les fenêtres datées appartiennent à des jours révolus, on les ignore.
    for (const w of stale ? [] : (z.overrides ?? [])) {
      const from = ms(w.from);
      const until = ms(w.until);
      if (from === null || until === null) continue;
      overrides.push({ win: w, from, until, items: items(w.playlist) });
    }
    return { zone: z, base: items(z.playlist), overrides, from: stale ? null : ms(z.from), until: stale ? null : ms(z.until) };
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
    const layout = (slot as PlaySlot).layout;
    const zones = layout.zones;
    const maxExcl = doc.player?.max_exclusive_elements;
    this.budget.max = typeof maxExcl === "number" && maxExcl >= 1 ? Math.min(4, Math.trunc(maxExcl)) : 1;
    this.setBackground(layout.background, doc);
    // Réconciliation par id de zone : une zone dont (playlists, fit, géométrie, fenêtres, forçages) est inchangée
    // garde son lecteur et son curseur ; seules les zones modifiées sont recréées.
    const wanted = new Map<string, ZoneModel>();
    const sigs = new Map<string, string>();
    for (const z of zones) {
      const m = this.model(doc, z, resolved.stale);
      wanted.set(z.id, m);
      sigs.set(
        z.id,
        JSON.stringify([z.x, z.y, z.w, z.h, z.z, z.fit, z.from, z.until, m.base, m.overrides.map((o) => [o.win, o.items])]),
      );
    }
    for (const [id, r] of this.runners) {
      if (!wanted.has(id) || this.sigs.get(id) !== sigs.get(id)) {
        r.stop();
        this.runners.delete(id);
        this.sigs.delete(id);
      }
    }
    for (const [id, m] of wanted) {
      if (!this.runners.has(id)) {
        this.runners.set(id, new ZoneRunner(m, doc, this.deps, this.report, this.budget, screens.stage));
        this.sigs.set(id, sigs.get(id)!);
      }
    }
    const at = now();
    for (const r of this.runners.values()) r.update(at);
    // Aucun élément actif (playlist vide ou tous `until` dépassés) : noir, jamais de logo ni d'erreur.
    const active = [...this.runners.values()].some((r) => r.hasContent(at));
    screens.show(active ? "none" : "black");
  }
}
