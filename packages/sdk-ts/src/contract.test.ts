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

describe("contrat 1.9.0 (additif, compat player 1.8)", () => {
  it("un créneau 1.9 reste lisible par un consommateur 1.8", () => {
    type Slot18 = { layout: { zones: { id: string; x: number; y: number; w: number; h: number; playlist: string }[] } };
    const slot: import("./player").PlaySlot = {
      start: "2026-11-03T08:00:00Z",
      end: "2026-11-03T18:00:00Z",
      kind: "play",
      layout: {
        id: "l1",
        revision: 2,
        zones: [
          {
            id: "main", x: 0, y: 0, w: 1, h: 1, playlist: "p1", z: 0, fit: "cover",
            overrides: [{ override_id: "o1", playlist: "p2", from: "2026-11-03T09:00:00Z", until: "2026-11-03T10:00:00Z" }],
          },
        ],
      },
      source: { schedule_id: "s1", layout_id: "l1" },
    };
    const old: Slot18 = slot;
    expect(old.layout.zones[0].playlist).toBe("p1");
  });
});

describe("contrat 1.10.0 (additif, droits par périmètre)", () => {
  it("les payloads user.* et NodeRef sont typés, owner reste optionnel", () => {
    const scope: import("./events").UserScopeChangedV1 = {
      version: 1,
      user_id: "u1",
      scopes: [{ type: "site", id: "s1" }],
    };
    const role: import("./events").UserRoleChangedV1 = {
      version: 1, user_id: "u1", from_role: "editor", to_role: "admin", source: "provider",
    };
    // un consommateur 1.9 ignore `owner` : un payload sans owner reste valide
    const media: { media_id: string; owner?: import("./events").NodeRef | null } = { media_id: "m1" };
    expect(scope.scopes[0]?.type).toBe("site");
    expect(role.source).toBe("provider");
    expect(media.owner).toBeUndefined();
  });
});

describe("contrat 1.11.0 (additif, commandes player et captures)", () => {
  it("sans adaptateur : capacités minimales ; adaptateur enregistré puis réinitialisé", async () => {
    const a = await import("./adapter");
    a.resetPlayerAdapter();
    expect(a.getPlayerAdapter()).toBeNull();
    expect(a.MINIMAL_CAPABILITIES).toEqual(["cmd.reload"]);
    const adapter: import("./adapter").PlayerAdapter = {
      id: "player.web",
      capabilities: async () => ["cmd.reload", "cmd.identify"],
      restart: async () => undefined,
      screenshot: async () => { throw new Error("unavailable"); },
    };
    a.registerPlayerAdapter(adapter);
    expect(await a.getPlayerAdapter()?.capabilities()).toContain("cmd.identify");
    a.resetPlayerAdapter();
  });

  it("les payloads d'événements sont typés", () => {
    const done: import("./events").PlayerCommandCompletedV1 = {
      version: 1, command_id: "c", screen_id: null, name: "restart", status: "expired", code: null,
    };
    expect(done.status).toBe("expired");
  });
});
