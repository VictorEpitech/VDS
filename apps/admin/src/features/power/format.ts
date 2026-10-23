import type { Rule } from "../../lib/queries";
import { dayRanges } from "../schedule/rules";

/** « lun-ven 08:00-18:00 » : jours en plage consécutive selon la langue courante. */
export function rulesText(rules: readonly Rule[], locale: string, allDay: string): string {
  if (rules.length === 0) return allDay;
  const day = (i: number) => new Intl.DateTimeFormat(locale, { weekday: "short", timeZone: "UTC" }).format(Date.UTC(2024, 0, 1 + i));
  return rules
    .map((r) => {
      const days = dayRanges(r.weekdays)
        .map(([a, b]) => (a === b ? day(a) : `${day(a)}-${day(b)}`))
        .join(", ");
      return `${days} ${r.start}-${r.end}`;
    })
    .join(" ; ");
}

/** Libellé localisé d'un calendrier (`name` par langue), à défaut sa clé. */
export function calendarName(name: Record<string, unknown>, locale: string, key: string): string {
  const v = name[locale] ?? name.fr ?? name.en;
  return typeof v === "string" && v ? v : key;
}
