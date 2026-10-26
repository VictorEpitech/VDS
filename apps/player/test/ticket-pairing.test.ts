import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const kv = new Map<string, unknown>();
vi.mock("../src/db", () => ({
  kvGet: async (k: string) => kv.get(k),
  kvSet: async (k: string, v: unknown) => void kv.set(k, v),
  kvDel: async (k: string) => void kv.delete(k),
}));

const TICKET = "AbCdEf0123456789_-AbCdEf0123";
const DONE = { device_token: "dev-tok", screen_id: "scr-1", server_time: new Date().toISOString() };
const PAIRING = { pairing_id: "p1", code: "ABC-234", poll_secret: "s", poll_interval_s: 3, expires_at: "x" };

interface Call {
  url: string;
  body: Record<string, unknown>;
}

function setup(base: string, claim: (() => Promise<string>) | undefined, replies: Response[]) {
  const g = globalThis as Record<string, unknown>;
  const calls: Call[] = [];
  g.__PLAYER_VERSION__ = "t";
  g.location = { href: `https://vds.client.fr${base}/player/` };
  g.window = {
    screen: { width: 1920, height: 1080 },
    VdsNative: { ping() {}, reload() {}, info: () => JSON.stringify({ platform: "android", model: "TV" }), claimPairingTicket: claim },
  };
  g.fetch = async (u: string, init: { body?: string }) => {
    calls.push({ url: u, body: init.body ? (JSON.parse(init.body) as Record<string, unknown>) : {} });
    return replies.shift() ?? new Response("{}", { status: 500 });
  };
  const shown: string[] = [];
  const screens = { show: (n: string) => shown.push(n), showPairing: (c: string) => shown.push(`code:${c}`) };
  return { calls, shown, screens };
}

describe("appairage par ticket (assistant Android)", () => {
  beforeEach(() => {
    kv.clear();
    vi.resetModules();
    vi.useFakeTimers({ toFake: ["setTimeout"] });
  });
  afterEach(() => vi.useRealTimers());

  it.each([
    ["", "https://vds.client.fr/api/v1/player/pair"],
    ["/espace/affichage", "https://vds.client.fr/espace/affichage/api/v1/player/pair"],
  ])("base %j : POST player/pair {pairing_ticket}, jeton stocké, aucun code affiché", async (base, url) => {
    const t = setup(base, async () => JSON.stringify({ ticket: TICKET }), [new Response(JSON.stringify(DONE), { status: 200 })]);
    const { pair } = await import("../src/pairing");
    const r = await pair(t.screens as never);
    expect(r).toEqual({ token: "dev-tok", screenId: "scr-1" });
    expect(t.calls).toHaveLength(1);
    expect(t.calls[0]?.url).toBe(url);
    expect(t.calls[0]?.body.pairing_ticket).toBe(TICKET);
    expect(kv.get("token")).toBe("dev-tok");
    expect(t.shown.some((s) => s.startsWith("code:"))).toBe(false);
    // Le ticket n'est jamais persisté.
    expect(JSON.stringify([...kv.entries()])).not.toContain(TICKET);
  });

  it("ticket refusé (410) : repli sur le code manuel, ticket non rejoué", async () => {
    const t = setup("", async () => JSON.stringify({ ticket: TICKET }), [
      new Response("{}", { status: 410 }),
      new Response(JSON.stringify(PAIRING), { status: 201 }),
      new Response(JSON.stringify(DONE), { status: 200 }),
    ]);
    const { pair } = await import("../src/pairing");
    const r = await pair(t.screens as never);
    expect(r.token).toBe("dev-tok");
    expect(t.shown).toContain("code:ABC-234");
    expect(t.calls.filter((c) => "pairing_ticket" in c.body)).toHaveLength(1);
  });

  it.each([
    ["pas de pont", undefined],
    ["pont sans ticket", async () => "{}"],
    ["ticket mal formé", async () => JSON.stringify({ ticket: "x y" })],
    ["pont qui échoue", async () => Promise.reject(new Error("boom"))],
  ])("%s : code manuel direct, aucun appel de ticket", async (_n, claim) => {
    const t = setup("", claim, [new Response(JSON.stringify(PAIRING), { status: 201 }), new Response(JSON.stringify(DONE), { status: 200 })]);
    const { pair } = await import("../src/pairing");
    await pair(t.screens as never);
    expect(t.calls.some((c) => "pairing_ticket" in c.body)).toBe(false);
    expect(t.shown).toContain("code:ABC-234");
  });

  it("réseau coupé : le ticket gardé en mémoire est rejoué", async () => {
    const t = setup("", async () => JSON.stringify({ ticket: TICKET }), []);
    const g = globalThis as Record<string, unknown>;
    let n = 0;
    g.fetch = async (u: string, init: { body: string }) => {
      t.calls.push({ url: u, body: JSON.parse(init.body) as Record<string, unknown> });
      if (++n < 2) throw new TypeError("offline");
      return new Response(JSON.stringify(DONE), { status: 200 });
    };
    const { pair } = await import("../src/pairing");
    const p = pair(t.screens as never);
    await vi.advanceTimersByTimeAsync(6000);
    expect((await p).token).toBe("dev-tok");
    expect(t.calls.map((c) => c.body.pairing_ticket)).toEqual([TICKET, TICKET]);
  });
});
