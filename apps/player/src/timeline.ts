import type { PlaySlot, ScheduleDocument, ScheduleSlot } from "@vds/sdk-ts";

export const DAY_MS = 86_400_000;

export interface Resolved {
  slot: ScheduleSlot;
  index: number;
  /** Programmation expirée : motif de J+1 rejoué à l'identique (mêmes heures locales). */
  stale: boolean;
}

const fmtCache = new Map<string, Intl.DateTimeFormat>();

function formatter(tz: string): Intl.DateTimeFormat {
  let f = fmtCache.get(tz);
  if (!f) {
    try {
      f = new Intl.DateTimeFormat("en-GB", {
        timeZone: tz,
        hourCycle: "h23",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      f = formatter("UTC");
    }
    fmtCache.set(tz, f);
  }
  return f;
}

/** Heure « murale » (champs locaux lus comme UTC) d'un instant dans un fuseau. */
export function wallOf(ms: number, tz: string): number {
  const parts: Record<string, number> = {};
  for (const p of formatter(tz).formatToParts(new Date(ms))) {
    if (p.type !== "literal") parts[p.type] = Number(p.value);
  }
  return Date.UTC(parts.year!, parts.month! - 1, parts.day!, parts.hour!, parts.minute!, parts.second!);
}

/** Heure murale lue dans une chaîne ISO avec décalage (`2026-10-23T08:00:00+02:00`), décalage ignoré. */
export function parseWall(iso: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/.exec(iso);
  if (!m) return NaN;
  return Date.UTC(+m[1]!, +m[2]! - 1, +m[3]!, +m[4]!, +m[5]!, +(m[6] ?? 0));
}

export function resolveAt(doc: ScheduleDocument, nowMs: number): Resolved | null {
  const from = Date.parse(doc.valid_from);
  const until = Date.parse(doc.valid_until);
  if (nowMs >= from && nowMs < until) {
    const index = doc.slots.findIndex((s) => Date.parse(s.start) <= nowMs && nowMs < Date.parse(s.end));
    const slot = doc.slots[index];
    return slot ? { slot, index, stale: false } : null;
  }
  // Hors validité : on projette l'instant sur la dernière journée du document (heure murale).
  const w = wallOf(nowMs, doc.timezone);
  const vuW = parseWall(doc.valid_until);
  const n = Math.floor((w - (vuW - DAY_MS)) / DAY_MS);
  const projected = w - n * DAY_MS;
  const index = doc.slots.findIndex((s) => parseWall(s.start) <= projected && projected < parseWall(s.end));
  const slot = doc.slots[index];
  return slot ? { slot, index, stale: true } : null;
}

/** Créneaux dont les médias doivent être en cache avant activation : en cours et suivant. */
export function neededSlots(doc: ScheduleDocument, nowMs: number): ScheduleSlot[] {
  const cur = resolveAt(doc, nowMs);
  const i = cur ? cur.index : 0;
  return doc.slots.slice(i, i + 2);
}

export function slotAssets(doc: ScheduleDocument, slots: ScheduleSlot[]): string[] {
  const keys = new Set<string>();
  for (const s of slots) {
    if (s.kind !== "play") continue;
    for (const z of (s as PlaySlot).layout.zones) {
      for (const it of doc.playlists[z.playlist]?.items ?? []) it.assets.forEach((a) => keys.add(a));
    }
  }
  return [...keys];
}

export function allAssets(doc: ScheduleDocument): string[] {
  return slotAssets(doc, doc.slots);
}
