import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PlayerAdapter, PlayerCapability } from "@vds/sdk-ts";
import { resetPlayerAdapter } from "@vds/sdk-ts";
import { CommandRunner, JOURNAL_KEY, RESTART_BURST, RESTART_OBSERVE_MS } from "../src/commands";
import type { CmdResult, IncomingCommand, RunnerDeps } from "../src/commands";
import { kvGet, resetDbForTests } from "../src/db";
import { captureAndUpload } from "../src/screenshots";
import { PlayerSocket } from "../src/ws";
import type { SocketLike } from "../src/ws";
import { createWebAdapter } from "../src/web-adapter";
import { beat } from "../src/heartbeat";
import { Screens } from "../src/screens";

const FUTURE = new Date(Date.now() + 3_600_000).toISOString();
const cmd = (id: string, name: string, args: Record<string, unknown> = {}, expires_at = FUTURE): IncomingCommand => ({
  cmd_id: id,
  name,
  args,
  expires_at,
});

function adapter(caps: PlayerCapability[], over: Partial<PlayerAdapter> = {}): PlayerAdapter {
  return {
    id: "player.test",
    capabilities: async () => caps,
    restart: vi.fn(async () => undefined),
    screenshot: vi.fn(async () => new Blob(["x"], { type: "image/jpeg" })),
    ...over,
  };
}

async function setup(opts: { caps?: PlayerCapability[] | null; boot?: string; clock?: { t: number }; adapterOver?: Partial<PlayerAdapter> } = {}) {
  const sent: CmdResult[] = [];
  const actions = {
    reload: vi.fn(),
    identify: vi.fn<(seconds: number) => Promise<{ status: "ok" }>>(async () => ({ status: "ok" })),
    resync: vi.fn(async () => ({ status: "ok" as const })),
    screenshot: vi.fn(async () => ({ status: "ok" as const })),
  };
  const clock = opts.clock ?? { t: Date.now() };
  const ad = opts.caps === null ? null : adapter(opts.caps ?? ["cmd.reload", "cmd.identify", "cmd.cache.resync", "cmd.restart.app", "cmd.screenshot"], opts.adapterOver);
  const deps: RunnerDeps = { now: () => clock.t, bootId: opts.boot ?? "boot-1", adapter: () => ad, actions, send: (r) => sent.push(r) };
  const r = new CommandRunner(deps);
  await r.init();
  return { r, sent, actions, clock, ad };
}

beforeEach(() => {
  resetDbForTests();
  sessionStorage.clear();
  resetPlayerAdapter();
});
afterEach(() => vi.useRealTimers());

describe("exécution et journal", () => {
  it("reload : journalisé AVANT d'agir, jamais réexécuté si redélivré", async () => {
    const { r, actions } = await setup();
    actions.reload.mockImplementation(() => {
      const raw = sessionStorage.getItem("vds_cmd_journal") ?? "";
      expect(raw).toContain("c1"); // déjà écrit au moment du rechargement
    });
    await r.handle(cmd("c1", "reload"));
    await r.handle(cmd("c1", "reload"));
    await r.handle(cmd("c1", "reload"));
    expect(actions.reload).toHaveBeenCalledTimes(1);
  });

  it("reload : au démarrage suivant (boot_id changé) le résultat est ok ; pas de réexécution", async () => {
    const a = await setup({ boot: "boot-1" });
    await a.r.handle(cmd("c2", "reload"));
    // Nouveau démarrage : même journal, autre boot_id (mémoire de session conservée, comme un rechargement).
    const b = await setup({ boot: "boot-2" });
    expect(b.sent.map((x) => [x.cmd_id, x.status])).toEqual([["c2", "ok"]]);
    expect(b.r.pendingResults()).toHaveLength(1);
    await b.r.handle(cmd("c2", "reload"));
    expect(b.actions.reload).not.toHaveBeenCalled();
    b.r.ack(["c2"]);
    expect(b.r.pendingResults()).toHaveLength(0);
  });

  it("restart non observé après 2 min : failed cmd.restart_not_observed", async () => {
    const { r, clock } = await setup();
    await r.handle(cmd("c3", "restart", { scope: "app" }));
    r.sweep();
    expect(r.pendingResults()).toHaveLength(0);
    clock.t += RESTART_OBSERVE_MS + 1000;
    r.sweep();
    expect(r.pendingResults()[0]).toMatchObject({ cmd_id: "c3", status: "failed", code: "cmd.restart_not_observed" });
  });

  it("filet anti-boucle : au-delà de 3 reload/restart en 10 min, rejet", async () => {
    const { r, actions } = await setup();
    for (let i = 0; i < RESTART_BURST; i++) await r.handle(cmd(`b${i}`, "reload"));
    await r.handle(cmd("b-x", "reload"));
    expect(actions.reload).toHaveBeenCalledTimes(RESTART_BURST);
    expect(r.pendingResults().find((x) => x.cmd_id === "b-x")).toMatchObject({ status: "rejected", code: "cmd.rate_limited" });
  });

  it("restart échoué (l'adaptateur lève) : failed, jamais de silence", async () => {
    const { r } = await setup({ adapterOver: { restart: vi.fn(async () => Promise.reject(new Error("x"))) } });
    await r.handle(cmd("c4", "restart", { scope: "app" }));
    expect(r.pendingResults()[0]).toMatchObject({ status: "failed", code: "cmd.restart_failed" });
  });

  it("expiration avec dérive : l'horloge corrigée décide", async () => {
    const clock = { t: Date.parse("2026-01-01T10:00:00Z") };
    const { r, actions } = await setup({ clock });
    // Horloge locale en retard : corrigée, la commande est échue.
    await r.handle(cmd("c5", "identify", {}, "2026-01-01T09:59:59Z"));
    expect(r.pendingResults()[0]?.status).toBe("expired");
    expect(actions.identify).not.toHaveBeenCalled();
    await r.handle(cmd("c6", "identify", {}, "2026-01-01T10:00:30Z"));
    expect(actions.identify).toHaveBeenCalledWith(10);
  });

  it("identify : secondes bornées 5..30", async () => {
    const { r, actions } = await setup();
    await r.handle(cmd("i1", "identify", { seconds: 1 }));
    await r.handle(cmd("i2", "identify", { seconds: 999 }));
    expect(actions.identify.mock.calls.map((c) => c[0])).toEqual([5, 30]);
  });

  it("inconnue, non annoncée, arguments invalides : rejected", async () => {
    const { r } = await setup({ caps: ["cmd.reload"] });
    await r.handle(cmd("x1", "explode"));
    await r.handle(cmd("x2", "screenshot"));
    await r.handle(cmd("x3", "restart", { scope: "moon" }));
    await r.handle(cmd("x4", "restart", { scope: "device" }));
    expect(r.pendingResults().map((x) => [x.cmd_id, x.status, x.code])).toEqual([
      ["x1", "rejected", "unknown_command"],
      ["x2", "rejected", "unsupported"],
      ["x3", "rejected", "cmd.invalid_args"],
      ["x4", "rejected", "unsupported"],
    ]);
  });

  it("adaptateur absent : capacités minimales (reload seul)", async () => {
    const { r } = await setup({ caps: null });
    expect(r.capabilities()).toEqual(["cmd.reload"]);
    await r.handle(cmd("n1", "identify"));
    expect(r.pendingResults()[0]).toMatchObject({ status: "rejected", code: "unsupported" });
  });

  it("adaptateur web : reload, identify, cache.resync ; ni capture ni redémarrage", async () => {
    expect(await createWebAdapter().capabilities()).toEqual(["cmd.reload", "cmd.identify", "cmd.cache.resync"]);
    await expect(createWebAdapter().screenshot({ maxEdge: 1, quality: 1 })).rejects.toThrow();
  });

  it("une action qui lève : failed sans propagation ; journal borné à 200", async () => {
    const { r, actions } = await setup();
    actions.resync.mockRejectedValueOnce(new Error("boom"));
    await r.handle(cmd("f1", "cache.resync"));
    expect(r.pendingResults()[0]).toMatchObject({ status: "failed", code: "cmd.failed" });
    for (let i = 0; i < 230; i++) await r.handle(cmd(`m${i}`, "cache.resync"));
    const stored = (await kvGet<unknown[]>(JOURNAL_KEY)) ?? [];
    expect(stored.length).toBeLessThanOrEqual(200);
  });

  it("résultats : au plus 20 par heartbeat, retirés à l'accusé", async () => {
    const { r } = await setup();
    for (let i = 0; i < 25; i++) await r.handle(cmd(`p${i}`, "cache.resync"));
    expect(r.pendingResults()).toHaveLength(20);
    r.ack(r.pendingResults().map((x) => x.cmd_id));
    expect(r.pendingResults()).toHaveLength(5);
  });
});

describe("trame WS cmd et heartbeat", () => {
  class Sock implements SocketLike {
    onopen: SocketLike["onopen"] = null;
    onmessage: SocketLike["onmessage"] = null;
    onclose: SocketLike["onclose"] = null;
    onerror: SocketLike["onerror"] = null;
    sent: string[] = [];
    send(d: string): void { this.sent.push(d); }
    close(): void {}
  }
  it("cmd reçue, résultat renvoyé en cmd.result ; trame mal formée ignorée", () => {
    const sock = new Sock();
    const got: IncomingCommand[] = [];
    const s = new PlayerSocket({ server: "https://h/", token: () => "t", onScheduleChanged: () => {}, onRevoked: () => {}, onCommand: (c) => got.push(c), factory: () => sock });
    s.start();
    expect(s.sendFrame("cmd.result", {})).toBe(false); // pas encore prêt
    sock.onmessage?.({ data: JSON.stringify({ type: "welcome", data: {} }) });
    sock.onmessage?.({ data: JSON.stringify({ type: "cmd", data: { cmd_id: "a", name: "reload", expires_at: FUTURE } }) });
    sock.onmessage?.({ data: JSON.stringify({ type: "cmd", data: { name: "reload" } }) });
    expect(got).toHaveLength(1);
    expect(s.sendFrame("cmd.result", { cmd_id: "a", status: "ok" })).toBe(true);
    expect(JSON.parse(sock.sent.at(-1)!)).toMatchObject({ type: "cmd.result", data: { cmd_id: "a" } });
    s.stop();
  });

  it("heartbeat : boot_id, capacités, command_results ; accusés et commandes relayés", async () => {
    const bodies: Record<string, unknown>[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
      bodies.push(JSON.parse(init.body as string) as Record<string, unknown>);
      return new Response(JSON.stringify({ revision: null, server_time: new Date().toISOString(), poll_interval_s: 5, commands: [cmd("h1", "reload")], results_acked: ["r1"] }), { status: 200 });
    }));
    const acked: string[][] = [];
    const cmds: IncomingCommand[][] = [];
    const engine = { takeErrors: () => [], restoreErrors: () => {}, playing: () => null, stale: false } as never;
    const assets = { missing: () => [], lazy: false } as never;
    await beat({
      token: () => "t", engine, assets, getActive: () => null, onRevisionMismatch: () => {}, onCommands: (c) => cmds.push(c),
      commands: { bootId: "B", capabilities: () => ["cmd.reload"], results: () => [{ cmd_id: "r1", status: "ok", code: null, data: {} }], acked: (i) => acked.push(i) },
    });
    vi.unstubAllGlobals();
    expect(bodies[0]).toMatchObject({ boot_id: "B", capabilities: ["cmd.reload"], command_results: [{ cmd_id: "r1", status: "ok" }] });
    expect(acked).toEqual([["r1"]]);
    expect(cmds[0]?.[0]?.cmd_id).toBe("h1");
  });
});

describe("règle 6 : aucune trace publique", () => {
  it("identify n'affiche que le nom ; échecs et captures n'écrivent rien dans le DOM", async () => {
    document.body.innerHTML = '<div id="app"></div>';
    const screens = new Screens(document.getElementById("app")!);
    const before = document.body.innerHTML;
    const remove = screens.showIdentify("Accueil");
    expect(document.querySelector(".identify")?.textContent).toBe("Accueil");
    remove();
    expect(document.body.innerHTML).toBe(before);

    // Capture en échec (adaptateur qui rejette) et réseau absent : aucune écriture DOM.
    const fail = adapter(["cmd.screenshot"], { screenshot: async () => Promise.reject(new Error("no")) });
    expect(await captureAndUpload(fail, "t", "https://h/")).toMatchObject({ status: "failed" });
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("net"))));
    expect(await captureAndUpload(adapter(["cmd.screenshot"]), "t", "https://h/", "cid")).toMatchObject({ status: "failed", code: "screenshot.offline" });
    vi.unstubAllGlobals();
    expect(document.body.innerHTML).toBe(before);
  });

  it("capture : POST image/jpeg avec jeton sur /player/screenshots/{cmd_id}, sans cmd_id pour le périodique", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify({ id: "s1" }), { status: 201 });
    }));
    const a = adapter(["cmd.screenshot"]);
    const r1 = await captureAndUpload(a, "tok", "https://h/espace/affichage/", "cid");
    const r2 = await captureAndUpload(a, "tok", "https://h/espace/affichage/");
    vi.unstubAllGlobals();
    expect(r1).toEqual({ status: "ok", data: { screenshot_id: "s1" } });
    expect(r2.status).toBe("ok");
    expect(calls[0]!.url).toBe("https://h/espace/affichage/api/v1/player/screenshots/cid");
    expect(calls[1]!.url).toBe("https://h/espace/affichage/api/v1/player/screenshots");
    expect((calls[0]!.init.headers as Record<string, string>)["Content-Type"]).toBe("image/jpeg");
    expect(a.screenshot).toHaveBeenCalledWith({ maxEdge: 1280, quality: 70 });
  });
});
