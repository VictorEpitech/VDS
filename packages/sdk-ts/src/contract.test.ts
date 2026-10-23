import { describe, expect, it } from "vitest";
import {
  clearPowerDrivers,
  getPowerDrivers,
  registerPowerDriver,
  CONTENT_API_VERSION, CONTENT_EVENTS, SCHEDULE_FORMAT, getPlatformBridge
} from "./index";

describe("contrat vds-core 1.2.0", () => {
  it("expose les versions de contrat", () => {
    expect(CONTENT_API_VERSION).toBe(1);
    expect(SCHEDULE_FORMAT).toBe(1);
  });

  it("nomme les événements de contenu", () => {
    expect(CONTENT_EVENTS).toEqual({ ended: "vds-ended", error: "vds-error" });
  });

  it("renvoie un pont web sans effet hors Android", () => {
    (globalThis as { window?: unknown }).window = {};
    const bridge = getPlatformBridge();
    expect(bridge.info().platform).toBe("web");
    expect(() => {
      bridge.ping();
      bridge.reload();
    }).not.toThrow();
  });

  it("délègue au pont natif quand il existe", () => {
    const native = {
      ping: () => {},
      info: () => ({ platform: "android", model: "", app_version: "", os_version: "" }),
      reload: () => {}
    };
    (globalThis as { window?: unknown }).window = { VdsNative: native };
    expect(getPlatformBridge()).toBe(native);
  });

  it("enregistre un pilote d'alimentation joueur", async () => {
    clearPowerDrivers();
    registerPowerDriver({
      id: "power.blank",
      available: async () => true,
      setPower: async (state) => ({ ok: true, state, verified: false }),
      getState: async () => "unknown"
    });
    const [d] = getPowerDrivers();
    expect(d?.id).toBe("power.blank");
    expect(await d?.setPower("off")).toEqual({ ok: true, state: "off", verified: false });
    clearPowerDrivers();
  });
});
