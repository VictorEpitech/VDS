/** Aides de la médiathèque : mode d'affichage mémorisé, état d'expiration, date + heure locales. */
import type { IconName } from "../components/Icon";

export type ViewMode = "grid" | "table";
const VIEW_KEY = "vds.media.view";

export function readView(): ViewMode {
  try {
    return window.localStorage.getItem(VIEW_KEY) === "table" ? "table" : "grid";
  } catch {
    return "grid";
  }
}

export function storeView(mode: ViewMode): void {
  try {
    window.localStorage.setItem(VIEW_KEY, mode);
  } catch {
    /* stockage indisponible : le choix vaut pour la session */
  }
}

/** Seuil indicatif « expire bientôt » (défaut de core.media.expiring_soon_days) pour la teinte du badge. */
export const EXPIRING_SOON_DAYS = 7;

export type ExpiryKind = "expired" | "soon" | "later" | "none";

export function expiryKind(item: { expired: boolean; expires_at: string | null }, now = Date.now()): ExpiryKind {
  if (item.expired) return "expired";
  if (!item.expires_at) return "none";
  return new Date(item.expires_at).getTime() - now <= EXPIRING_SOON_DAYS * 86_400_000 ? "soon" : "later";
}

export const EXPIRY_ICON: Record<Exclude<ExpiryKind, "none">, IconName> = { expired: "failed", soon: "warning", later: "calendar" };
export const EXPIRY_TONE: Record<Exclude<ExpiryKind, "none">, string> = { expired: "offline", soon: "pairing", later: "scheduled_off" };

const pad = (n: number) => String(n).padStart(2, "0");

/** Instant ISO → champs date (AAAA-MM-JJ) et heure (HH:MM) locaux. */
export function toParts(iso: string | null): { date: string; time: string } {
  if (!iso) return { date: "", time: "23:59" };
  const d = new Date(iso);
  return { date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, time: `${pad(d.getHours())}:${pad(d.getMinutes())}` };
}

/** Champs date + heure locaux → instant ISO ; null si la date est vide ou invalide. */
export function fromParts(date: string, time: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  const d = new Date(`${date}T${/^\d{2}:\d{2}$/.test(time) ? time : "23:59"}`);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export function formatBytes(n: number, lang: string): string {
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${new Intl.NumberFormat(lang, { maximumFractionDigits: v < 10 && i > 0 ? 1 : 0 }).format(v)} ${units[i]}`;
}

export function formatDateTime(iso: string, lang: string): string {
  return new Intl.DateTimeFormat(lang, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}
