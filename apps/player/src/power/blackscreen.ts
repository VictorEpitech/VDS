import type { PlayerPowerDriver, PowerState } from "@vds/sdk-ts";

export interface Blackout {
  /** Écran noir plein écran + lecture suspendue (vidéos libérées). */
  setBlackout(on: boolean): void;
}

/**
 * `power.blank` : toujours disponible. Garantit le résultat visible quand tout le reste échoue ;
 * jamais `verified` (rien ne prouve que le rétroéclairage est coupé).
 */
export function createBlackscreenDriver(target: Blackout): PlayerPowerDriver {
  let state: PowerState = "unknown";
  return {
    id: "power.blank",
    available: async () => true,
    setPower: async (s) => {
      target.setBlackout(s === "off");
      state = s === "off" ? "standby" : "on";
      return { ok: true, state, verified: false };
    },
    getState: async () => state,
  };
}
