import { afterEach, describe, expect, it } from "vitest";
import { KioskController, MAX_MAINTENANCE_MIN, lockDown } from "../src/kiosk";

const T0 = Date.parse("2026-10-24T10:00:00Z");
const iso = (ms: number): string => new Date(ms).toISOString();
const MIN = 60_000;

function setup(): { ctl: KioskController; clock: { t: number }; calls: [string, number][] } {
  const clock = { t: T0 };
  const calls: [string, number][] = [];
  window.VdsNative = {
    ping() {},
    info: () => ({ platform: "android", model: "", app_version: "", os_version: "" }),
    reload() {},
    kiosk: (mode, remaining) => {
      calls.push([mode, remaining]);
      return Promise.resolve('{"ok":true,"enforcement":"lock_task"}');
    },
  };
  return { ctl: new KioskController({ now: () => clock.t }), clock, calls };
}

afterEach(() => {
  delete window.VdsNative;
});

describe("kiosque : le player applique, ne décide pas", () => {
  it("verrouillé par défaut (champ absent, vieux serveur) et sur valeur inconnue", () => {
    const { ctl } = setup();
    ctl.apply(undefined);
    expect(ctl.report().mode).toBe("locked");
    ctl.apply({ mode: "bogus" as never });
    expect(ctl.locked).toBe(true);
  });

  it("unlocked persistant : reste déverrouillé sans échéance", () => {
    const { ctl, clock } = setup();
    ctl.apply({ mode: "unlocked" });
    clock.t += 30 * 24 * 60 * MIN;
    ctl.tick();
    expect(ctl.report().mode).toBe("unlocked");
  });

  it("maintenance : déverrouillée jusqu'à l'échéance puis reverrouillage autonome", async () => {
    const { ctl, clock, calls } = setup();
    ctl.apply({ mode: "maintenance", until: iso(T0 + 10 * MIN) }, iso(T0));
    expect(ctl.report().mode).toBe("maintenance");
    expect(calls).toEqual([["maintenance", 10 * MIN]]);
    clock.t += 9 * MIN;
    ctl.tick();
    expect(ctl.locked).toBe(false);
    clock.t += 1 * MIN;
    ctl.tick();
    expect(ctl.locked).toBe(true);
    expect(calls.at(-1)).toEqual(["locked", 0]);
    await Promise.resolve();
    await Promise.resolve();
    expect(ctl.report().enforcement).toBe("lock_task");
  });

  it("maintenance déjà expirée au chargement (redémarrage hors ligne) = verrouillé", () => {
    const { ctl, clock } = setup();
    clock.t = T0 + 20 * MIN;
    ctl.apply({ mode: "maintenance", until: iso(T0 + 10 * MIN) }, iso(T0));
    expect(ctl.locked).toBe(true);
  });

  it("échéance invalide ou absente = verrouillé ; échéance bornée à 240 min de l'heure serveur", () => {
    const { ctl, clock } = setup();
    ctl.apply({ mode: "maintenance", until: "pas une date" }, iso(T0));
    expect(ctl.locked).toBe(true);
    ctl.apply({ mode: "maintenance" }, iso(T0));
    expect(ctl.locked).toBe(true);
    ctl.apply({ mode: "maintenance", until: iso(T0 + 5000 * MIN) }, iso(T0));
    expect(ctl.locked).toBe(false);
    clock.t = T0 + MAX_MAINTENANCE_MIN * MIN;
    ctl.tick();
    expect(ctl.locked).toBe(true);
  });

  it("n'envoie pas deux fois le même état au natif", () => {
    const { ctl, calls } = setup();
    ctl.apply({ mode: "unlocked" });
    ctl.apply({ mode: "unlocked" });
    ctl.tick();
    expect(calls).toEqual([["unlocked", 0]]);
  });

  it("sans pont natif : enforcement web", () => {
    const ctl = new KioskController({ now: () => T0 });
    ctl.apply({ mode: "unlocked" });
    expect(ctl.report()).toEqual({ mode: "unlocked", enforcement: "web" });
  });

  it("les entrées de la page sont bloquées seulement en mode verrouillé", () => {
    const { ctl } = setup();
    lockDown();
    ctl.apply(undefined);
    const locked = new KeyboardEvent("keydown", { key: "F5", cancelable: true });
    window.dispatchEvent(locked);
    expect(locked.defaultPrevented).toBe(true);
    ctl.apply({ mode: "unlocked" });
    const free = new KeyboardEvent("keydown", { key: "F5", cancelable: true });
    window.dispatchEvent(free);
    expect(free.defaultPrevented).toBe(false);
    ctl.apply(undefined);
  });
});
