import type { PlaylistItem, ScheduleDocument } from "@vds/sdk-ts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssetStore } from "../src/assets";
import { Engine, expired } from "../src/engine";
import { Screens } from "../src/screens";
import { resolveAt } from "../src/timeline";

const T0 = Date.parse("2026-10-23T08:00:00Z");
const H = 3_600_000;
const iso = (ms: number): string => new Date(ms).toISOString();

class Probe extends HTMLElement {
  tag = "";
  configure(props: unknown): void {
    this.tag = (props as { id: string }).id;
    this.dataset.id = this.tag;
  }
  prepare(): Promise<void> {
    return Promise.resolve();
  }
  start(): void {
    this.dataset.started = "1";
  }
  stop(): void {}
}
if (!customElements.get("vds-probe")) customElements.define("vds-probe", Probe);

const item = (id: string, until?: string | null, duration = 10_000): PlaylistItem =>
  ({ id, content_type: "probe", props: { id }, duration_ms: duration, assets: ["a"], until }) as unknown as PlaylistItem;

function doc(items: PlaylistItem[], validUntil = T0 + 48 * H): ScheduleDocument {
  return {
    format: 1, revision: "r", server_time: iso(T0),
    screen: { id: "s", name: "n", orientation: "landscape" },
    timezone: "UTC", valid_from: iso(T0 - 24 * H), valid_until: iso(validUntil),
    slots: [{
      start: iso(T0 - 24 * H), end: iso(validUntil), kind: "play",
      layout: { zones: [{ id: "z", x: 0, y: 0, w: 100, h: 100, playlist: "p" }] },
      source: { schedule_id: "s", kind: "schedule", override_id: null },
    }],
    playlists: { p: { items } }, assets: { a: { sha256: "x", bytes: 1, mime: "image/png" } },
    content_types: { probe: { element: "vds-probe", script: "x.js", version: "1" } },
  } as unknown as ScheduleDocument;
}

const assets = { hasAll: () => true, ensure: async () => {}, lazy: false } as unknown as AssetStore;
let screens: Screens;
let engine: Engine;

const shown = (): (string | undefined)[] =>
  Array.from(document.querySelectorAll<HTMLElement>("[data-started]")).map((e) => e.dataset.id);

async function advance(ms: number): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms);
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
  document.body.innerHTML = '<div id="app"></div>';
  screens = new Screens(document.getElementById("app")!);
  engine = new Engine({
    screens, assets, server: "http://s/", locale: "fr", isOnline: () => false,
    loader: { ensure: async () => {}, create: () => document.createElement("vds-probe") } as never,
  });
});
afterEach(() => {
  engine.stop();
  vi.useRealTimers();
});

describe("until", () => {
  it("expired : borne exclusive, valeur illisible ignorée", () => {
    const u = iso(T0);
    expect(expired(item("x", u), T0 - 1)).toBe(false);
    expect(expired(item("x", u), T0)).toBe(true);
    expect(expired(item("x", null), T0)).toBe(false);
    expect(expired(item("x", "n'importe quoi"), T0)).toBe(false);
  });

  it("saute à la transition un élément dont until est passé, hors ligne", async () => {
    engine.setDoc(doc([item("a", iso(T0 - 1000)), item("b")]));
    engine.start();
    await advance(100);
    expect(shown()).toEqual(["b"]);
  });

  it("arrête immédiatement l'élément courant au tick suivant until", async () => {
    engine.setDoc(doc([item("a", iso(T0 + 3000), 60_000), item("b", null, 60_000)]));
    engine.start();
    await advance(100);
    expect(engine.playing()?.item_id).toBe("a");
    await advance(2000);
    expect(engine.playing()?.item_id).toBe("a");
    await advance(1500); // T0+3,6 s : tick de T0+4 s à venir, until passé à T0+3 s
    await advance(1000);
    expect(engine.playing()?.item_id).toBe("b");
    expect(screens.active).toBe("none");
  });

  it("tous expirés : noir, sans erreur, sans boucle serrée", async () => {
    const d = doc([item("a", iso(T0 - 1)), item("b", iso(T0 - 1))]);
    const create = vi.fn(() => document.createElement("vds-probe"));
    engine = new Engine({
      screens, assets, server: "http://s/", locale: "fr", isOnline: () => false,
      loader: { ensure: async () => {}, create } as never,
    });
    engine.setDoc(d);
    engine.start();
    await advance(60_000);
    expect(screens.active).toBe("black");
    expect(create).not.toHaveBeenCalled();
    expect(engine.takeErrors()).toEqual([]);
    expect(vi.getTimerCount()).toBeLessThan(6);
  });

  it("playlist vide : noir", async () => {
    engine.setDoc(doc([]));
    engine.start();
    await advance(2000);
    expect(screens.active).toBe("black");
  });

  it("l'écran noir se lève quand un élément redevient actif (prolongation)", async () => {
    engine.setDoc(doc([item("a", iso(T0 - 1))]));
    engine.start();
    await advance(1500);
    expect(screens.active).toBe("black");
    engine.setDoc(doc([item("a", null)]));
    await advance(1500);
    expect(screens.active).toBe("none");
    expect(shown()).toEqual(["a"]);
  });

  it("replay J+1 stale : until absolu respecté, document expiré rejoué sans élément périmé", async () => {
    const d = doc([item("a", iso(T0 + 2 * H)), item("b", null)], T0 + 24 * H);
    vi.setSystemTime(T0 + 3 * 24 * H); // bien après valid_until : stale
    expect(resolveAt(d, Date.now())?.stale).toBe(true);
    engine.setDoc(d);
    engine.start();
    await advance(100);
    expect(engine.stale).toBe(true);
    expect(shown()).toEqual(["b"]);
  });
});
