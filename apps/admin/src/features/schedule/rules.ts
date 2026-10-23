import type { Rule } from "../../lib/queries";

/** Masque hebdomadaire : lundi = 1 ... dimanche = 64 (contrat de l'API). */
export const DAY_BITS = [1, 2, 4, 8, 16, 32, 64] as const;
export const WEEKDAYS = 31;
export const ALL_DAYS = 127;

export const hasDay = (mask: number, index: number) => (mask & DAY_BITS[index]!) !== 0;
export const toggleDay = (mask: number, index: number) => mask ^ DAY_BITS[index]!;

export function defaultRule(): Rule {
  return { weekdays: WEEKDAYS, start: "08:00", end: "18:00" };
}

export type RuleProblem = "days" | "range" | null;

/** Au moins un jour, début strictement avant la fin ("HH:MM", comparaison lexicographique valide). */
export function ruleProblem(r: Rule): RuleProblem {
  if (r.weekdays <= 0) return "days";
  if (!/^\d{2}:\d{2}$/.test(r.start) || !/^\d{2}:\d{2}$/.test(r.end) || r.start >= r.end) return "range";
  return null;
}

/** Libellé court d'un jeu de jours : indices 0..6 consécutifs réduits en plage. */
export function dayRanges(mask: number): [number, number][] {
  const out: [number, number][] = [];
  let start = -1;
  for (let i = 0; i <= 7; i++) {
    const on = i < 7 && hasDay(mask, i);
    if (on && start < 0) start = i;
    if (!on && start >= 0) {
      out.push([start, i - 1]);
      start = -1;
    }
  }
  return out;
}
