import type { PlatformBridge, PlayerPowerDriver, PowerOutcome, PowerState } from "@vds/sdk-ts";

const STATES: readonly string[] = ["on", "off", "standby", "unknown"];

function parse(raw: unknown): PowerOutcome {
  try {
    const o = (typeof raw === "string" ? JSON.parse(raw) : raw) as Partial<PowerOutcome> | null;
    const state = STATES.includes(String(o?.state)) ? (o?.state as PowerState) : "unknown";
    const out: PowerOutcome = { ok: o?.ok === true, state, verified: o?.verified === true };
    if (typeof o?.code === "string") out.code = o.code;
    return out;
  } catch {
    return { ok: false, state: "unknown", verified: false, code: "power.cec.bad_reply" };
  }
}

const UNAVAILABLE: PowerOutcome = { ok: false, state: "unknown", verified: false, code: "power.cec.unavailable" };

/**
 * `power.cec` : ordres CEC via le pont natif `VdsNative.cec` (Android). Rallumage = One Touch Play
 * (Image View On + Active Source), extinction = Standby. Sans pont, `available()` est faux.
 */
export function createCecDriver(bridge: () => PlatformBridge | undefined = () => window.VdsNative): PlayerPowerDriver {
  const call = async (action: "probe" | "on" | "off" | "state"): Promise<PowerOutcome> => {
    const b = bridge();
    if (!b || typeof b.cec !== "function") return UNAVAILABLE;
    try {
      return parse(await b.cec(action));
    } catch {
      return { ok: false, state: "unknown", verified: false, code: "power.cec.failed" };
    }
  };
  return {
    id: "power.cec",
    available: async () => (await call("probe")).ok,
    setPower: (state) => call(state),
    getState: async () => (await call("state")).state,
  };
}
