import type { PlaySlot, ScheduleDocument, ScheduleSlot } from "@vds/sdk-ts";
import { describe, expect, it } from "vitest";
import { plannedPower } from "../src/power/plan";
import { neededSlots, resolveAt, slotAssets } from "../src/timeline";

const H = 3_600_000;
const T0 = Date.parse("2026-10-23T00:00:00Z");
const iso = (ms: number): string => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "+00:00");

const play = (a: number, b: number, kind: "schedule" | "override", pl: string, extra: object = {}): PlaySlot => ({
  start: iso(T0 + a * H),
  end: iso(T0 + b * H),
  kind: "play",
  layout: { zones: [{ id: "z", x: 0, y: 0, w: 100, h: 100, playlist: pl }] },
  source: { schedule_id: kind === "override" ? null : "sch", kind, override_id: kind === "override" ? "ov1" : null, ...extra },
});
const off = (a: number, b: number, extra: object = {}): ScheduleSlot =>
  ({ start: iso(T0 + a * H), end: iso(T0 + b * H), kind: "off", mode: "black", ...extra }) as ScheduleSlot;

function doc(slots: ScheduleSlot[], power: ScheduleDocument["power"] = undefined): ScheduleDocument {
  const pls = ["prog", "forced"].map((k) => [k, { items: [{ assets: [k + "-a"] }] }]);
  return {
    format: 1, revision: "r", server_time: iso(T0),
    screen: { id: "s", name: "n", orientation: "landscape" },
    timezone: "UTC", valid_from: iso(T0), valid_until: iso(T0 + 48 * H),
    slots, playlists: Object.fromEntries(pls), assets: {}, content_types: {}, power,
  } as unknown as ScheduleDocument;
}

// Programmation 00-48 sauf forçage 10-12 ; hors ligne : seul nowMs avance.
const D = doc([play(0, 10, "schedule", "prog"), play(10, 12, "override", "forced"), play(12, 48, "schedule", "prog")]);
const src = (nowMs: number) => (resolveAt(D, nowMs)!.slot as PlaySlot).source;

describe("forçage joué comme un créneau ordinaire", () => {
  it("bascule à ends_at sans serveur puis retour à la programmation", () => {
    expect(src(T0 + 10 * H - 1).kind).toBe("schedule");
    expect(src(T0 + 10 * H).override_id).toBe("ov1");
    expect(src(T0 + 12 * H - 1).kind).toBe("override");
    expect(src(T0 + 12 * H).kind).toBe("schedule");
    expect(neededSlots(D, T0 + 11 * H)).toHaveLength(2);
    expect(slotAssets(D, neededSlots(D, T0 + 11 * H))).toEqual(["forced-a", "prog-a"]);
  });
  it("forçage terminé et document expiré : retour au motif programmé", () => {
    const r = resolveAt(D, T0 + 3 * 24 * H + 11 * H)!;
    expect(r.stale).toBe(true);
    expect((r.slot as PlaySlot).source.kind).toBe("schedule");
  });
});

describe("alimentation et forçage", () => {
  // Programmation éteinte 08-18 ; forçage 10-12 rallume ; off forcé 20-22.
  const P = doc([], [
    { from: iso(T0), to: iso(T0 + 8 * H), state: "on" },
    { from: iso(T0 + 8 * H), to: iso(T0 + 10 * H), state: "off" },
    { from: iso(T0 + 10 * H), to: iso(T0 + 12 * H), state: "on" },
    { from: iso(T0 + 12 * H), to: iso(T0 + 18 * H), state: "off" },
    { from: iso(T0 + 18 * H), to: iso(T0 + 20 * H), state: "on" },
    { from: iso(T0 + 20 * H), to: iso(T0 + 22 * H), state: "off" },
    { from: iso(T0 + 22 * H), to: iso(T0 + 48 * H), state: "on" },
  ]);
  it("rallume pendant le forçage, ré-éteint à la fin, off forcé", () => {
    expect(plannedPower(P, T0 + 9 * H)).toBe("off");
    expect(plannedPower(P, T0 + 11 * H)).toBe("on");
    expect(plannedPower(P, T0 + 12 * H)).toBe("off");
    expect(plannedPower(P, T0 + 21 * H)).toBe("off");
    expect(plannedPower(P, T0 + 22 * H)).toBe("on");
  });
});

describe("tolérance et confidentialité", () => {
  it("champs inconnus tolérés", () => {
    const d = doc([
      play(0, 24, "override", "forced", { futur: { x: 1 } }),
      off(24, 48, { override_id: "ov2", inconnu: true }),
    ]);
    (d.slots[0] as unknown as Record<string, unknown>).nouveau = [1];
    expect(resolveAt(d, T0 + H)?.slot.kind).toBe("play");
    expect(resolveAt(d, T0 + 25 * H)?.slot.kind).toBe("off");
    expect(() => slotAssets(d, d.slots)).not.toThrow();
  });
  it("aucun identifiant de forçage dans le code d'affichage", async () => {
    const fs = await import("node:fs");
    for (const f of ["engine.ts", "screens.ts", "main.ts", "degraded.ts"]) {
      const code = fs.readFileSync(`${process.cwd()}/src/${f}`, "utf8");
      expect(code, f).not.toMatch(/override_id|source\.kind/);
    }
  });
});
