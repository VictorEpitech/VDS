import { clearPowerDrivers, getPowerDrivers, registerPowerDriver } from "@vds/sdk-ts";
import type { PlatformBridge, PlayerPowerDriver, PowerOutcome, ScheduleDocument } from "@vds/sdk-ts";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createBlackscreenDriver } from "../src/power/blackscreen";
import { createCecDriver } from "../src/power/cec";
import { MAX_RETRIES, PowerController, RETRY_AFTER_MS } from "../src/power/controller";
import { plannedPower } from "../src/power/plan";

const H = 3_600_000;
const T0 = Date.parse("2026-10-23T00:00:00Z");
const iso = (ms: number): string => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "+00:00");

function docWith(power: ScheduleDocument["power"], timezone = "UTC"): ScheduleDocument {
  return {
    format: 1,
    revision: "r",
    server_time: iso(T0),
    screen: { id: "s", name: "n", orientation: "landscape" },
    timezone,
    valid_from: iso(T0),
    valid_until: iso(T0 + 48 * H),
    slots: [],
    playlists: {},
    assets: {},
    content_types: {},
    power,
  };
}
// J : on 00-08, off 08-18, on 18-24 ; J+1 : off 00-06, on 06-24.
const PLAN: ScheduleDocument["power"] = [
  { from: iso(T0), to: iso(T0 + 8 * H), state: "on" },
  { from: iso(T0 + 8 * H), to: iso(T0 + 18 * H), state: "off" },
  { from: iso(T0 + 18 * H), to: iso(T0 + 24 * H), state: "on" },
  { from: iso(T0 + 24 * H), to: iso(T0 + 30 * H), state: "off" },
  { from: iso(T0 + 30 * H), to: iso(T0 + 48 * H), state: "on" },
];

describe("plan d'alimentation", () => {
  it("état au créneau courant ; allumé sans plan", () => {
    const doc = docWith(PLAN);
    expect(plannedPower(doc, T0 + 7 * H)).toBe("on");
    expect(plannedPower(doc, T0 + 8 * H)).toBe("off"); // `from` inclus
    expect(plannedPower(doc, T0 + 18 * H - 1)).toBe("off");
    expect(plannedPower(doc, T0 + 18 * H)).toBe("on"); // `to` exclu
    expect(plannedPower(docWith(undefined), T0)).toBe("on");
    expect(plannedPower(docWith([]), T0)).toBe("on");
    expect(plannedPower(null, T0)).toBe("on");
  });
  it("document expiré : rejoue le dernier jour aux mêmes heures locales", () => {
    const doc = docWith(PLAN);
    // 3 jours plus tard, 03:00 -> motif de J+1 : éteint ; 12:00 -> allumé.
    expect(plannedPower(doc, T0 + 3 * 24 * H + 3 * H)).toBe("off");
    expect(plannedPower(doc, T0 + 3 * 24 * H + 12 * H)).toBe("on");
  });
});

class FakeDriver implements PlayerPowerDriver {
  calls: string[] = [];
  results: PowerOutcome[] = [];
  constructor(
    readonly id: string,
    private avail = true,
  ) {}
  available = async (): Promise<boolean> => this.avail;
  setPower = async (s: "on" | "off"): Promise<PowerOutcome> => {
    this.calls.push(s);
    return this.results.shift() ?? { ok: true, state: s === "on" ? "on" : "standby", verified: true };
  };
  getState = async (): Promise<"unknown"> => "unknown";
}

async function flush(): Promise<void> {
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

describe("PowerController (hors ligne, horloge simulée)", () => {
  function setup(drivers: PlayerPowerDriver[]) {
    const clock = { t: T0 + 7 * H };
    const report = vi.fn();
    const c = new PowerController({ now: () => clock.t, getDoc: () => docWith(PLAN), drivers: () => drivers, report });
    return { clock, report, c };
  }

  it("applique CEC puis écran noir aux bascules, une seule fois par état", async () => {
    const cec = new FakeDriver("power.cec");
    const blank = new FakeDriver("power.blank");
    const { clock, c } = setup([blank, cec]);
    c.tick();
    await flush();
    expect(cec.calls).toEqual(["on"]);
    expect(blank.calls).toEqual(["on"]);
    for (let i = 0; i < 5; i++) {
      clock.t += 60_000;
      c.tick();
      await flush();
    }
    expect(cec.calls).toEqual(["on"]); // pas de répétition
    clock.t = T0 + 8 * H;
    c.tick();
    await flush();
    expect(cec.calls).toEqual(["on", "off"]);
    expect(blank.calls).toEqual(["on", "off"]);
    expect(c.state).toBe("off");
  });

  it("pilote indisponible ignoré ; l'écran noir suffit", async () => {
    const cec = new FakeDriver("power.cec", false);
    const blank = new FakeDriver("power.blank");
    const { clock, c } = setup([cec, blank]);
    clock.t = T0 + 9 * H;
    c.tick();
    await flush();
    expect(cec.calls).toEqual([]);
    expect(blank.calls).toEqual(["off"]);
  });

  it("échec CEC : l'écran noir est quand même appliqué, CEC retenté 3 fois puis abandonné", async () => {
    const cec = new FakeDriver("power.cec");
    cec.results = Array.from({ length: 5 }, () => ({ ok: false, state: "unknown", verified: false, code: "power.cec.failed" }));
    const blank = new FakeDriver("power.blank");
    const { clock, report, c } = setup([cec, blank]);
    clock.t = T0 + 9 * H;
    c.tick();
    await flush();
    expect(blank.calls).toEqual(["off"]);
    expect(report).toHaveBeenCalledWith("power.cec.failed");
    for (let i = 0; i < 6; i++) {
      c.tick(); // trop tôt : rien
      clock.t += RETRY_AFTER_MS;
      c.tick();
      await flush();
    }
    expect(cec.calls).toHaveLength(MAX_RETRIES);
    expect(blank.calls).toEqual(["off"]); // déjà réussi, jamais rejoué
  });

  it("un pilote qui lève ne bloque pas les suivants", async () => {
    const cec = new FakeDriver("power.cec");
    cec.setPower = async () => {
      throw new Error("boom");
    };
    const blank = new FakeDriver("power.blank");
    const { clock, c } = setup([cec, blank]);
    clock.t = T0 + 9 * H;
    c.tick();
    await flush();
    expect(blank.calls).toEqual(["off"]);
  });
});

describe("pilotes", () => {
  const bridge = (reply: string | Error): PlatformBridge => ({
    ping: () => {},
    info: () => ({ platform: "android", model: "", app_version: "", os_version: "" }),
    reload: () => {},
    cec: async () => {
      if (reply instanceof Error) throw reply;
      return reply;
    },
  });

  it("power.cec : sans pont indisponible", async () => {
    const d = createCecDriver(() => undefined);
    expect(await d.available()).toBe(false);
    expect(await d.setPower("off")).toMatchObject({ ok: false, code: "power.cec.unavailable" });
  });
  it("power.cec : réponses du pont validées", async () => {
    const ok = createCecDriver(() => bridge('{"ok":true,"state":"on","verified":true}'));
    expect(await ok.available()).toBe(true);
    expect(await ok.setPower("on")).toEqual({ ok: true, state: "on", verified: true });
    const bad = createCecDriver(() => bridge("not json"));
    expect(await bad.setPower("on")).toMatchObject({ ok: false, code: "power.cec.bad_reply" });
    const weird = createCecDriver(() => bridge('{"ok":true,"state":"<script>","verified":1}'));
    expect(await weird.setPower("on")).toEqual({ ok: true, state: "unknown", verified: false });
    const boom = createCecDriver(() => bridge(new Error("x")));
    expect(await boom.setPower("off")).toMatchObject({ ok: false, code: "power.cec.failed" });
  });
  it("power.blank : toujours disponible, jamais vérifié", async () => {
    const calls: boolean[] = [];
    const d = createBlackscreenDriver({ setBlackout: (on) => calls.push(on) });
    expect(await d.available()).toBe(true);
    expect(await d.setPower("off")).toEqual({ ok: true, state: "standby", verified: false });
    expect(await d.setPower("on")).toEqual({ ok: true, state: "on", verified: false });
    expect(calls).toEqual([true, false]);
    expect(await d.getState()).toBe("on");
  });
  afterEach(() => clearPowerDrivers());
  it("registre : un id = un pilote", () => {
    registerPowerDriver(new FakeDriver("power.cec"));
    registerPowerDriver(new FakeDriver("power.cec"));
    expect(getPowerDrivers()).toHaveLength(1);
  });
});
