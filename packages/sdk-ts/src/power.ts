// Contrat vds-core 1.2.0 : pilotes d'alimentation côté player.
export type PowerState = "on" | "off" | "standby" | "unknown";

export interface PowerOutcome {
  ok: boolean;
  state: PowerState;
  verified: boolean;
  code?: string;
}

export interface PlayerPowerDriver {
  /** "power.cec", "power.blank" */
  id: string;
  /** Ex. pont VdsNative.cec présent. */
  available(): Promise<boolean>;
  setPower(state: "on" | "off"): Promise<PowerOutcome>;
  getState(): Promise<PowerState>;
}

const drivers = new Map<string, PlayerPowerDriver>();

export function registerPowerDriver(d: PlayerPowerDriver): void {
  drivers.set(d.id, d);
}

export function getPowerDrivers(): PlayerPowerDriver[] {
  return [...drivers.values()];
}

export function clearPowerDrivers(): void {
  drivers.clear();
}
