import type { LayoutZone, PlaylistItem, ScheduleDocument } from "@vds/sdk-ts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssetStore } from "../src/assets";
import { Engine } from "../src/engine";
import { Screens } from "../src/screens";
import { slotAssets } from "../src/timeline";

const T0 = Date.parse("2026-11-03T08:00:00Z");
const H = 3_600_000;
const iso = (ms: number): string => new Date(ms).toISOString();

class Probe extends HTMLElement {
  configure(props: unknown): void {
    this.dataset.id = (props as { id: string }).id;
  }
  prepare(): Promise<void> {
    return Promise.resolve();
  }
  start(): void {
    this.dataset.started = "1";
  }
  stop(): void {
    this.dataset.stopped = "1";
  }
}
class XProbe extends Probe {
  get exclusive(): boolean {
    return true;
  }
}
if (!customElements.get("vds-lprobe")) customElements.define("vds-lprobe", Probe);
if (!customElements.get("vds-lxprobe")) customElements.define("vds-lxprobe", XProbe);

const item = (id: string, type = "probe", extra: Partial<PlaylistItem> = {}): PlaylistItem =>
  ({ id, content_type: type, props: { id }, duration_ms: 10_000, assets: [], ...extra }) as unknown as PlaylistItem;

interface Opts {
  zones: LayoutZone[];
  playlists: Record<string, PlaylistItem[]>;
  background?: { color?: string; asset?: string } | null;
  player?: { max_exclusive_elements: number };
  extra?: Record<string, unknown>;
}
function doc(o: Opts): ScheduleDocument {
  return {
    format: 1, revision: "r", server_time: iso(T0),
    screen: { id: "s", name: "n", orientation: "landscape" },
    timezone: "UTC", valid_from: iso(T0 - H), valid_until: iso(T0 + 48 * H),
    slots: [{
      start: iso(T0 - H), end: iso(T0 + 48 * H), kind: "play",
      layout: { zones: o.zones, ...(o.background !== undefined ? { background: o.background } : {}) },
      source: { schedule_id: "s" },
    }],
    playlists: Object.fromEntries(Object.entries(o.playlists).map(([k, items]) => [k, { items }])),
    assets: { bg: { sha256: "x", bytes: 1, mime: "image/png" } },
    content_types: {
      probe: { element: "vds-lprobe", script: "x.js", version: "1" },
      xprobe: { element: "vds-lxprobe", script: "x.js", version: "1" },
    },
    ...(o.player ? { player: o.player } : {}),
    ...o.extra,
  } as unknown as ScheduleDocument;
}
const zone = (id: string, playlist: string, extra: Partial<LayoutZone> = {}): LayoutZone =>
  ({ id, x: 0, y: 0, w: 1, h: 1, playlist, ...extra }) as LayoutZone;

const assets = { hasAll: () => true, ensure: async () => {}, lazy: false, urlFor: (k: string) => `/m/${k}` } as unknown as AssetStore;
let screens: Screens;
let engine: Engine;
const started = (z?: string): (string | undefined)[] =>
  Array.from(document.querySelectorAll<HTMLElement>(`${z ? `[data-zone="${z}"] ` : ""}[data-started]`))
    .filter((e) => !e.dataset.stopped)
    .map((e) => e.dataset.id);
const advance = async (ms: number): Promise<void> => {
  await vi.advanceTimersByTimeAsync(ms);
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
  document.body.innerHTML = '<div id="app"></div>';
  screens = new Screens(document.getElementById("app")!);
  engine = new Engine({
    screens, assets, server: "http://s/", locale: "fr", isOnline: () => false,
    loader: { ensure: async () => {}, create: (e: { element: string }) => document.createElement(e.element) } as never,
  });
  engine.start();
});
afterEach(() => {
  engine.stop();
  vi.useRealTimers();
});

describe("scène multi-zones", () => {
  it("calques : géométrie en %, z-index, fit, fond couleur ; valeurs hors bornes ramenées", async () => {
    engine.setDoc(doc({
      zones: [
        zone("main", "a", { z: 0, fit: "cover" }),
        zone("bandeau", "b", { x: 0, y: 0.9, w: 1, h: 0.1, z: 2 }),
        zone("logo", "c", { x: 0.8, y: 0.05, w: 5, h: 0.2, z: 1, fit: "stretch" }),
      ],
      playlists: { a: [item("a1")], b: [item("b1")], c: [item("c1")] },
      background: { color: "#112233" },
    }));
    await advance(50);
    const z = (id: string): HTMLElement => document.querySelector<HTMLElement>(`[data-zone="${id}"]`)!;
    expect(z("bandeau").style.top).toBe("90%");
    expect(z("bandeau").style.height).toBe("10%");
    expect(z("bandeau").style.zIndex).toBe("2");
    expect(z("main").dataset.fit).toBe("cover");
    expect(z("logo").dataset.fit).toBe("stretch");
    expect(z("logo").style.width).toBe("20%");
    expect(started().sort()).toEqual(["a1", "b1", "c1"]);
    expect(document.querySelector<HTMLElement>(".bg")!.style.backgroundColor).not.toBe("");
  });

  it("couleur de fond invalide ignorée, aucune chaîne libre en CSS", async () => {
    engine.setDoc(doc({ zones: [zone("main", "a")], playlists: { a: [item("a1")] }, background: { color: "red;x:y" } }));
    await advance(50);
    expect(document.querySelector(".bg")).toBeNull();
  });

  it("fond image : élément <img> (cover), pré-cache inclus dans slotAssets", async () => {
    const d = doc({ zones: [zone("main", "a")], playlists: { a: [item("a1")] }, background: { asset: "bg" } });
    expect(slotAssets(d, d.slots)).toContain("bg");
    engine.setDoc(d);
    await advance(50);
    expect(document.querySelector(".bg img")?.getAttribute("src")).toBe("/m/bg");
  });

  it("aucune information technique : pas de nom de zone ni de texte visible", async () => {
    engine.setDoc(doc({ zones: [zone("secret_zone", "a")], playlists: { a: [item("a1")] } }));
    await advance(50);
    expect(document.getElementById("app")!.textContent).toBe("");
  });

  it("une playlist par zone : curseurs indépendants", async () => {
    engine.setDoc(doc({
      zones: [zone("main", "a"), zone("side", "b", { w: 0.5 })],
      playlists: { a: [item("a1", "probe", { duration_ms: 3000 }), item("a2", "probe", { duration_ms: 3000 })], b: [item("b1")] },
    }));
    await advance(50);
    expect(started("main")).toEqual(["a1"]);
    await advance(3500);
    expect(started("main")).toContain("a2");
    expect(started("side")).toEqual(["b1"]);
  });
});

describe("fenêtres de zone et forçage de zone (hors ligne)", () => {
  it("la zone apparaît à from et disparaît à until, sur l'horloge locale", async () => {
    engine.setDoc(doc({
      zones: [zone("main", "a"), zone("pop", "b", { from: iso(T0 + 5000), until: iso(T0 + 15_000) })],
      playlists: { a: [item("a1")], b: [item("b1")] },
    }));
    await advance(2000);
    expect(started()).toEqual(["a1"]);
    await advance(5000);
    expect(started("pop")).toEqual(["b1"]);
    await advance(10_000);
    expect(started("pop")).toEqual([]);
    expect(started("main")).toEqual(["a1"]);
  });

  it("forçage : bascule au début de la playlist forcée puis retour ; l'autre zone n'est pas touchée", async () => {
    engine.setDoc(doc({
      zones: [
        zone("main", "a"),
        zone("bandeau", "b", { overrides: [{ override_id: "o", playlist: "f", from: iso(T0 + 4000), until: iso(T0 + 12_000) }] }),
      ],
      playlists: { a: [item("a1", "probe", { duration_ms: 600_000 })], b: [item("b1")], f: [item("f1"), item("f2")] },
    }));
    await advance(1500);
    const mainEl = document.querySelector('[data-zone="main"] [data-started]');
    expect(started("bandeau")).toEqual(["b1"]);
    await advance(4000);
    expect(started("bandeau")).toEqual(["f1"]);
    await advance(8000);
    expect(started("bandeau")).toEqual(["b1"]);
    expect(document.querySelector('[data-zone="main"] [data-started]')).toBe(mainEl);
    expect(mainEl && (mainEl as HTMLElement).dataset.stopped).toBeUndefined();
  });

  it("nouveau document : la zone inchangée garde son lecteur, la zone modifiée est recréée", async () => {
    const mk = (b: string): ScheduleDocument =>
      doc({ zones: [zone("main", "a"), zone("side", b)], playlists: { a: [item("a1")], b: [item("b1")], c: [item("c1")] } });
    engine.setDoc(mk("b"));
    await advance(100);
    const before = document.querySelector('[data-zone="main"] [data-started]');
    engine.setDoc(mk("c"));
    await advance(1500);
    expect(document.querySelector('[data-zone="main"] [data-started]')).toBe(before);
    expect(started("side")).toEqual(["c1"]);
  });
});

describe("ressources exclusives", () => {
  const two = (player?: { max_exclusive_elements: number }): ScheduleDocument =>
    doc({
      zones: [zone("main", "a"), zone("side", "b", { w: 0.5 })],
      playlists: { a: [item("v1", "xprobe")], b: [item("v2", "xprobe")] },
      ...(player ? { player } : {}),
    });

  it("par défaut une seule vidéo à la fois, la seconde est sautée", async () => {
    engine.setDoc(two());
    await advance(500);
    expect(started()).toEqual(["v1"]);
    expect(engine.takeErrors().some((e) => e.code === "exclusive_limit")).toBe(true);
  });

  it("player.max_exclusive_elements = 2 : les deux jouent", async () => {
    engine.setDoc(two({ max_exclusive_elements: 2 }));
    await advance(500);
    expect(started().sort()).toEqual(["v1", "v2"]);
  });

  it("le décodeur est rendu quand la zone est forcée : l'autre zone peut le reprendre", async () => {
    engine.setDoc(two());
    await advance(500);
    expect(started("side")).toEqual([]);
    engine.setDoc(doc({
      zones: [zone("main", "a", { until: iso(T0 + 3000) }), zone("side", "b", { w: 0.5 })],
      playlists: { a: [item("v1", "xprobe")], b: [item("v2", "xprobe")] },
    }));
    await advance(9000);
    expect(started("side")).toEqual(["v2"]);
  });
});

describe("repli document 1.8", () => {
  it("zone main seule, sans z/fit/fenêtres ni player : lecture normale", async () => {
    engine.setDoc(doc({ zones: [zone("main", "a")], playlists: { a: [item("a1")] } }));
    await advance(100);
    expect(started()).toEqual(["a1"]);
    expect(document.querySelector(".bg")).toBeNull();
    expect(document.querySelector<HTMLElement>('[data-zone="main"]')!.dataset.fit).toBe("contain");
  });
});
