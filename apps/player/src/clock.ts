/** Horloge corrigée de la dérive par rapport au serveur (TV sans NTP fiable). */
let drift = 0;

export function setDrift(ms: number): void {
  drift = Number.isFinite(ms) ? ms : 0;
}

export function getDrift(): number {
  return drift;
}

/** `serverTime` : ISO 8601 reçu du serveur ; `sentAt`/`receivedAt` : horloge locale autour de la requête. */
export function driftFrom(serverTime: string, sentAt: number, receivedAt: number): number | null {
  const t = Date.parse(serverTime);
  if (Number.isNaN(t)) return null;
  return t - (sentAt + receivedAt) / 2;
}

export function now(): number {
  return Date.now() + drift;
}
