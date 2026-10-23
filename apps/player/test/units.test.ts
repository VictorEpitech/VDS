import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import type { PlaySlot, ScheduleDocument, ScheduleSlot } from "@vds/sdk-ts";
import { describe, expect, it } from "vitest";
import en from "../src/i18n/en.json";
import fr from "../src/i18n/fr.json";
import { parseRange } from "../src/range";
import { Sha256 } from "../src/sha256";
import { neededSlots, parseWall, resolveAt, slotAssets, wallOf } from "../src/timeline";

describe("sha256", () => {
  it.each([0, 1, 55, 56, 63, 64, 65, 119, 120, 1000, 100_003])("longueur %i", (n) => {
    const data = new Uint8Array(n).map((_, i) => (i * 31 + 7) & 0xff);
    const expected = createHash("sha256").update(data).digest("hex");
    expect(new Sha256().update(data).hex()).toBe(expected);
    // Morceaux de tailles irrégulières.
    const h = new Sha256();
    for (let i = 0; i < n; i += 37) h.update(data.subarray(i, i + 37));
    expect(h.hex()).toBe(expected);
  });
});

describe("range", () => {
  it("formes usuelles", () => {
    expect(parseRange(null, 100)).toBeNull();
    expect(parseRange("bytes=0-", 100)).toEqual({ start: 0, end: 99 });
    expect(parseRange("bytes=10-19", 100)).toEqual({ start: 10, end: 19 });
    expect(parseRange("bytes=90-500", 100)).toEqual({ start: 90, end: 99 });
    expect(parseRange("bytes=-10", 100)).toEqual({ start: 90, end: 99 });
    expect(parseRange("bytes=100-", 100)).toBe("unsatisfiable");
    expect(parseRange("bytes=5-2", 100)).toBe("unsatisfiable");
    expect(parseRange("garbage", 100)).toBeNull();
  });
});

function slot(start: string, end: string, kind: "off" | "play", playlist = "pl"): ScheduleSlot {
  if (kind === "off") return { start, end, kind: "off", mode: "black" };
  const s: PlaySlot = {
    start,
    end,
    kind: "play",
    layout: { zones: [{ id: "main", x: 0, y: 0, w: 1, h: 1, playlist }] },
    source: { schedule_id: "s" },
  };
  return s;
}

function doc(): ScheduleDocument {
  return {
    format: 1,
    revision: "r",
    server_time: "2026-10-24T10:00:00Z",
    screen: { id: "x", name: "x", orientation: "landscape" },
    timezone: "Europe/Paris",
    valid_from: "2026-10-24T00:00:00+02:00",
    valid_until: "2026-10-26T00:00:00+01:00", // changement d'heure le 25 octobre 2026
    slots: [
      slot("2026-10-24T00:00:00+02:00", "2026-10-24T08:00:00+02:00", "off"),
      slot("2026-10-24T08:00:00+02:00", "2026-10-25T08:00:00+01:00", "play", "a"),
      slot("2026-10-25T08:00:00+01:00", "2026-10-26T00:00:00+01:00", "off"),
    ],
    playlists: {
      a: {
        items: [
          { id: "i", content_type: "content.image", duration_ms: 1000, props: {}, assets: ["media/1/original"], requires_network: false },
        ],
      },
    },
    assets: {},
    content_types: {},
  };
}

describe("timeline", () => {
  it("créneau courant en instants absolus", () => {
    const d = doc();
    expect(resolveAt(d, Date.parse("2026-10-24T05:00:00+02:00"))?.slot.kind).toBe("off");
    expect(resolveAt(d, Date.parse("2026-10-24T09:00:00+02:00"))?.slot.kind).toBe("play");
    expect(resolveAt(d, Date.parse("2026-10-25T08:00:00+01:00"))?.slot.kind).toBe("off");
  });

  it("lit l'heure murale d'une chaîne ISO et d'un instant", () => {
    expect(parseWall("2026-10-24T08:30:00+02:00")).toBe(Date.UTC(2026, 9, 24, 8, 30));
    expect(wallOf(Date.parse("2026-10-24T08:30:00+02:00"), "Europe/Paris")).toBe(Date.UTC(2026, 9, 24, 8, 30));
    expect(wallOf(Date.parse("2026-10-25T08:30:00+01:00"), "Europe/Paris")).toBe(Date.UTC(2026, 9, 25, 8, 30));
  });

  it("au-delà de la validité : rejoue le motif de J+1 aux mêmes heures locales", () => {
    const d = doc();
    // Motif de J+1 (25 octobre, jour de changement d'heure) : play jusqu'à 08:00 locale, off ensuite.
    const early = resolveAt(d, Date.parse("2026-10-27T03:00:00+01:00"));
    expect(early?.stale).toBe(true);
    expect(early?.slot.kind).toBe("play");
    expect(resolveAt(d, Date.parse("2026-10-27T12:00:00+01:00"))?.slot.kind).toBe("off");
    // Horloge en retard avant valid_from : même projection.
    expect(resolveAt(d, Date.parse("2026-10-20T12:00:00+02:00"))?.stale).toBe(true);
  });

  it("créneaux et médias requis", () => {
    const d = doc();
    const slots = neededSlots(d, Date.parse("2026-10-24T05:00:00+02:00"));
    expect(slots).toHaveLength(2);
    expect(slotAssets(d, slots)).toEqual(["media/1/original"]);
  });
});

describe("i18n des écrans système", () => {
  const flat = (o: unknown, p = ""): string[] =>
    Object.entries(o as Record<string, unknown>).flatMap(([k, v]) =>
      typeof v === "object" && v ? flat(v, `${p}${k}.`) : [`${p}${k}`],
    );

  it("mêmes clés en FR et EN", () => {
    expect(flat(fr).sort()).toEqual(flat(en).sort());
  });

  it("aucune clé inutilisée", () => {
    const src = readdirSync("src")
      .filter((f) => f.endsWith(".ts"))
      .map((f) => readFileSync(`src/${f}`, "utf8"))
      .join("\n");
    for (const key of flat(fr)) expect(src, key).toContain(`"${key}"`);
  });
});

describe("timeline : passage à l'heure d'été (29 mars 2026)", () => {
  it("heure murale correcte avant et après le saut 02:00 -> 03:00", () => {
    expect(wallOf(Date.parse("2026-03-29T01:30:00+01:00"), "Europe/Paris")).toBe(Date.UTC(2026, 2, 29, 1, 30));
    expect(wallOf(Date.parse("2026-03-29T03:30:00+02:00"), "Europe/Paris")).toBe(Date.UTC(2026, 2, 29, 3, 30));
  });

  it("fuseau inconnu : repli sans exception", () => {
    expect(Number.isFinite(wallOf(Date.parse("2026-03-29T01:30:00Z"), "Nope/Zone"))).toBe(true);
  });
});
