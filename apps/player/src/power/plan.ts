import type { PowerSlot, ScheduleDocument } from "@vds/sdk-ts";
import { DAY_MS, parseWall, wallOf } from "../timeline";

export type PlannedState = "on" | "off";

function inRange(slots: PowerSlot[], at: number): PlannedState | undefined {
  for (const s of slots) {
    if (Date.parse(s.from) <= at && at < Date.parse(s.to)) return s.state;
  }
  return undefined;
}

/**
 * État voulu à l'instant `nowMs`, calculé localement (aucun serveur requis).
 * Hors de la fenêtre du plan (serveur injoignable depuis plus de 2 jours), le dernier jour est
 * rejoué à l'identique en heure murale, comme la programmation. Aucun plan ou trou = allumé.
 */
export function plannedPower(doc: ScheduleDocument | null, nowMs: number): PlannedState {
  const slots = doc?.power;
  if (!doc || !slots || slots.length === 0) return "on";
  const exact = inRange(slots, nowMs);
  if (exact) return exact;
  const last = slots[slots.length - 1]!;
  if (nowMs < Date.parse(slots[0]!.from) || nowMs >= Date.parse(last.to)) {
    const w = wallOf(nowMs, doc.timezone);
    const n = Math.floor((w - (parseWall(last.to) - DAY_MS)) / DAY_MS);
    const projected = w - n * DAY_MS;
    for (const s of slots) {
      if (parseWall(s.from) <= projected && projected < parseWall(s.to)) return s.state;
    }
  }
  return "on";
}
