/** Présentation seule : dates et heures dans le fuseau du site (aucune règle de diffusion ici). */

const dtfCache = new Map<string, Intl.DateTimeFormat>();
function dtf(tz: string): Intl.DateTimeFormat {
  let f = dtfCache.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-CA", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
    dtfCache.set(tz, f);
  }
  return f;
}

export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function safeZone(tz: string | undefined | null): string {
  if (!tz) return browserTimeZone();
  try {
    dtf(tz);
    return tz;
  } catch {
    return browserTimeZone();
  }
}

export interface Wall {
  y: number;
  mo: number;
  d: number;
  h: number;
  mi: number;
  s: number;
}

export function wallClock(ms: number, tz: string): Wall {
  const o: Record<string, number> = {};
  for (const p of dtf(tz).formatToParts(ms)) if (p.type !== "literal") o[p.type] = Number(p.value);
  return { y: o.year!, mo: o.month!, d: o.day!, h: o.hour!, mi: o.minute!, s: o.second! };
}

const pad = (n: number) => String(n).padStart(2, "0");
export const dateKey = (w: Pick<Wall, "y" | "mo" | "d">): string => `${w.y}-${pad(w.mo)}-${pad(w.d)}`;

/** Décalage (ms) du fuseau à un instant donné. */
function offsetMs(ms: number, tz: string): number {
  const w = wallClock(Math.floor(ms / 1000) * 1000, tz);
  return Date.UTC(w.y, w.mo - 1, w.d, w.h, w.mi, w.s) - Math.floor(ms / 1000) * 1000;
}

/** Instant UTC (ms) d'une heure murale (date `YYYY-MM-DD`, heure `HH:MM`) dans le fuseau. */
export function zonedToUtc(date: string, time: string, tz: string): number {
  const [y, mo, d] = date.split("-").map(Number) as [number, number, number];
  const [h, mi] = time.split(":").map(Number) as [number, number];
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  const first = guess - offsetMs(guess, tz);
  return guess - offsetMs(first, tz);
}

export const todayIn = (tz: string, now = Date.now()): string => dateKey(wallClock(now, tz));

export function addDays(date: string, n: number): string {
  const [y, mo, d] = date.split("-").map(Number) as [number, number, number];
  const t = new Date(Date.UTC(y, mo - 1, d + n));
  return dateKey({ y: t.getUTCFullYear(), mo: t.getUTCMonth() + 1, d: t.getUTCDate() });
}

/** Premier jour de la semaine (0 = dimanche) selon la langue : lundi en FR. */
export const firstWeekday = (lang: string): number => (lang.startsWith("fr") ? 1 : 0);

export function weekStart(date: string, first: number): string {
  const [y, mo, d] = date.split("-").map(Number) as [number, number, number];
  const dow = new Date(Date.UTC(y, mo - 1, d)).getUTCDay();
  return addDays(date, -((dow - first + 7) % 7));
}

/** Bornes UTC (ms) d'une journée murale ; 23 h ou 25 h les jours de changement d'heure. */
export function dayBounds(date: string, tz: string): [number, number] {
  return [zonedToUtc(date, "00:00", tz), zonedToUtc(addDays(date, 1), "00:00", tz)];
}

export const isDate = (v: string | null | undefined): v is string => !!v && /^\d{4}-\d{2}-\d{2}$/.test(v);
export const isLocalInstant = (v: string | null | undefined): v is string => !!v && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(v);

/** `2026-11-02T10:30` (heure murale du site) -> instant UTC en ms. */
export function localInstantToUtc(local: string, tz: string): number {
  const [date, time] = local.split("T") as [string, string];
  return zonedToUtc(date, time, tz);
}

export function utcToLocalInstant(ms: number, tz: string): string {
  const w = wallClock(ms, tz);
  return `${dateKey(w)}T${pad(w.h)}:${pad(w.mi)}`;
}

export function timeFormatter(lang: string, tz: string) {
  const time = new Intl.DateTimeFormat(lang, { timeStyle: "short", timeZone: tz });
  const day = new Intl.DateTimeFormat(lang, { weekday: "long", day: "numeric", month: "long", timeZone: tz });
  const dayShort = new Intl.DateTimeFormat(lang, { weekday: "short", day: "numeric", month: "short", timeZone: tz });
  const full = new Intl.DateTimeFormat(lang, { dateStyle: "full", timeStyle: "short", timeZone: tz });
  const dayOfDate = (date: string) => new Intl.DateTimeFormat(lang, { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" }).format(Date.parse(`${date}T12:00:00Z`));
  return {
    time: (ms: number | string) => time.format(typeof ms === "string" ? Date.parse(ms) : ms),
    day: (ms: number | string) => day.format(typeof ms === "string" ? Date.parse(ms) : ms),
    dayShort: (ms: number | string) => dayShort.format(typeof ms === "string" ? Date.parse(ms) : ms),
    full: (ms: number | string) => full.format(typeof ms === "string" ? Date.parse(ms) : ms),
    dayOfDate,
  };
}
export type Fmt = ReturnType<typeof timeFormatter>;
