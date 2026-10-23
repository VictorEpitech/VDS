import type { ScreenState } from "../../components/StatusBadge";
import type { Incident, Screen, ScreenPower } from "../../lib/queries";

export type DisplayState = ScreenState | "revoked";

/** Éteint par le planning à l'instant donné (créneau `off` résolu du plan d'alimentation). */
export function isScheduledOff(power: ScreenPower | undefined, now: Date): boolean {
  const t = now.getTime();
  return (power?.slots ?? []).some((s) => s.state === "off" && new Date(s.from).getTime() <= t && t < new Date(s.to).getTime());
}

/**
 * État affiché d'un écran, comme le serveur : `scheduled_off` n'est jamais une alerte ;
 * un échec d'alimentation ouvert prime sur en ligne / hors ligne.
 */
export function displayState(screen: Screen, power: ScreenPower | undefined, incidents: readonly Incident[], now: Date): DisplayState {
  const d = screen.device;
  if (!d) return "pairing";
  if (d.presence === "revoked") return "revoked";
  if (d.presence === "unseen") return "pairing";
  if (isScheduledOff(power, now)) return "scheduled_off";
  if (incidents.some((i) => i.kind === "power.failed" && i.subject_id === screen.id && !i.resolved_at)) return "power_failed";
  return d.presence === "online" ? "online" : "offline";
}
