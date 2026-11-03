/**
 * vds-core 1.11.0 (additif) : adaptateur de plateforme du player (module `player-adapter`).
 * Le player noyau n'a jamais de branche par plateforme : tout passe par cette interface.
 */
export type PlayerCapability =
  | "cmd.reload"
  | "cmd.identify"
  | "cmd.screenshot"
  | "cmd.restart.app"
  | "cmd.restart.device"
  | "cmd.cache.resync";

/** Capacités minimales sans adaptateur (un player 1.10 annonce la même chose). */
export const MINIMAL_CAPABILITIES: readonly PlayerCapability[] = ["cmd.reload"];

export interface PlayerAdapter {
  /** "player.android", "player.pi", "player.web". */
  id: string;
  /** Réévalué au démarrage. */
  capabilities(): Promise<PlayerCapability[]>;
  /** Ne résout pas si le redémarrage a lieu. */
  restart(scope: "app" | "device"): Promise<void>;
  /** image/jpeg ; rejette si indisponible. */
  screenshot(opts: { maxEdge: number; quality: number }): Promise<Blob>;
}

let current: PlayerAdapter | null = null;

export function registerPlayerAdapter(a: PlayerAdapter): void {
  current = a;
}

/** Adaptateur enregistré, ou `null` (capacités minimales). */
export function getPlayerAdapter(): PlayerAdapter | null {
  return current;
}

/** Test uniquement. */
export function resetPlayerAdapter(): void {
  current = null;
}
