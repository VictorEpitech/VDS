import { afterEach, describe, expect, it } from "vitest";
import type { KioskSender } from "../src/kiosk";
import { KioskController, MAX_MAINTENANCE_MIN, RETRY_MS, UNLOCK_RENEW_MS, claimKioskBridge, lockDown } from "../src/kiosk";

const T0 = Date.parse("2026-10-24T10:00:00Z");
const iso = (ms: number): string => new Date(ms).toISOString();
const MIN = 60_000;

interface Fx {
  ctl: KioskController;
  clock: { t: number; m: number };
  calls: [string, number, number][];
  reply: { raw: string };
}

const flush = async (): Promise<void> => {
  for (let i = 0; i < 5; i++) await Promise.resolve();
};

/** Faux pont natif : répond `reply.raw` (qui reflète par défaut le mode demandé). */
function setup(native = true): Fx {
  const clock = { t: T0, m: 0 };
  const calls: [string, number, number][] = [];
  const reply = { raw: "" };
  const send: KioskSender = (mode, remaining, win) => {
    calls.push([mode, remaining, win]);
    return Promise.resolve(reply.raw || `{"ok":true,"enforcement":"lock_task","mode":"${mode}"}`);
  };
  const ctl = new KioskController({ now: () => clock.t, mono: () => clock.m, send: native ? () => send : () => undefined });
  return { ctl, clock, calls, reply };
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
    expect(ctl.locked).toBe(false);
  });

  it("maintenance : déverrouillée jusqu'à l'échéance puis reverrouillage autonome", async () => {
    const { ctl, clock, calls } = setup();
    ctl.apply({ mode: "maintenance", until: iso(T0 + 10 * MIN) }, iso(T0));
    expect(ctl.report().mode).toBe("locked"); // pas encore confirmé par le natif
    await flush();
    expect(ctl.report().mode).toBe("maintenance");
    expect(calls).toEqual([["maintenance", 10 * MIN, (T0 + 10 * MIN) / 1000]]);
    clock.t += 9 * MIN;
    ctl.tick();
    expect(ctl.locked).toBe(false);
    clock.t += 1 * MIN;
    ctl.tick();
    expect(ctl.locked).toBe(true);
    expect(calls.at(-1)).toEqual(["locked", 0, 0]);
    await flush();
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
    expect(calls).toEqual([["unlocked", 0, 0]]);
  });

  it("sans pont natif : enforcement web", () => {
    const { ctl } = setup(false);
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

  it("ok:false du natif : état non confirmé rapporté tel quel, demande renvoyée au plus toutes les 5 s", async () => {
    const { ctl, clock, calls, reply } = setup();
    reply.raw = '{"ok":false,"enforcement":"soft","mode":"maintenance"}';
    ctl.apply(undefined); // lock demandé, natif encore en maintenance (lock task refusé)
    await flush();
    expect(ctl.report()).toEqual({ mode: "maintenance", enforcement: "soft" });
    ctl.tick();
    expect(calls).toHaveLength(1);
    clock.m += RETRY_MS;
    ctl.tick();
    expect(calls).toHaveLength(2);
    await flush();
    reply.raw = '{"ok":true,"enforcement":"lock_task","mode":"locked"}';
    clock.m += RETRY_MS;
    ctl.tick();
    await flush();
    expect(ctl.report()).toEqual({ mode: "locked", enforcement: "lock_task" });
    clock.m += 10 * RETRY_MS;
    ctl.tick();
    expect(calls).toHaveLength(3); // confirmé : plus de renvoi
  });

  it("un envoi rejeté est retenté", async () => {
    const clock = { t: T0, m: 0 };
    let n = 0;
    const ctl = new KioskController({
      now: () => clock.t,
      mono: () => clock.m,
      send: () => () => (++n === 1 ? Promise.reject(new Error("x")) : Promise.resolve('{"ok":true,"enforcement":"lock_task","mode":"unlocked"}')),
    });
    ctl.apply({ mode: "unlocked" });
    await flush();
    clock.m += RETRY_MS;
    ctl.tick();
    await flush();
    expect(n).toBe(2);
    expect(ctl.report().mode).toBe("unlocked");
  });

  it("unlocked est renouvelé (bail natif) tant que le player vit", async () => {
    const { ctl, clock, calls } = setup();
    ctl.apply({ mode: "unlocked" });
    await flush();
    clock.m += UNLOCK_RENEW_MS - 1;
    ctl.tick();
    expect(calls).toHaveLength(1);
    clock.m += 1;
    ctl.tick();
    expect(calls).toHaveLength(2);
  });

  it("horloge reculée sous l'heure serveur du document : maintenance refusée, et reverrouillage au tick", () => {
    const { ctl, clock } = setup();
    clock.t = T0 - 365 * 24 * 60 * MIN;
    ctl.apply({ mode: "maintenance", until: iso(T0 + 10 * MIN) }, iso(T0));
    expect(ctl.locked).toBe(true);
    clock.t = T0;
    ctl.apply({ mode: "maintenance", until: iso(T0 + 10 * MIN) }, iso(T0));
    expect(ctl.locked).toBe(false);
    clock.t = T0 - 365 * 24 * 60 * MIN;
    ctl.tick();
    expect(ctl.locked).toBe(true);
  });

  it("claimKioskBridge : accès pris une fois, retiré de window.VdsNative (un module tiers ne peut plus l'obtenir)", async () => {
    const sent: unknown[][] = [];
    let claims = 0;
    window.VdsNative = {
      ping() {},
      info: () => ({ platform: "android", model: "", app_version: "", os_version: "" }),
      reload() {},
      claimKiosk: () => (++claims === 1 ? (...a) => (sent.push(a), Promise.resolve('{"ok":true,"enforcement":"lock_task","mode":"unlocked"}')) : null),
    };
    claimKioskBridge();
    expect(window.VdsNative.claimKiosk).toBeUndefined();
    const ctl = new KioskController({ now: () => T0 });
    ctl.apply({ mode: "unlocked" });
    await flush();
    expect(sent).toEqual([["unlocked", 0, 0]]);
    claimKioskBridge(); // second appel (ou module tiers) : rien
    expect(claims).toBe(1);
  });
});
