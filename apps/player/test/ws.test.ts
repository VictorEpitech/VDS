import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BACKOFF_MAX_MS, BACKOFF_MIN_MS, PlayerSocket, SUBPROTOCOL, wsUrl } from "../src/ws";
import type { SocketLike } from "../src/ws";

class FakeSocket implements SocketLike {
  onopen: SocketLike["onopen"] = null;
  onmessage: SocketLike["onmessage"] = null;
  onclose: SocketLike["onclose"] = null;
  onerror: SocketLike["onerror"] = null;
  sent: string[] = [];
  closed = false;
  constructor(
    readonly url: string,
    readonly protocols: string[],
  ) {}
  send(d: string): void {
    this.sent.push(d);
  }
  close(): void {
    this.closed = true;
  }
  recv(frame: unknown): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

describe("wsUrl : origine courante + préfixe", () => {
  it.each([
    ["http://127.0.0.1:47800/", "ws://127.0.0.1:47800/ws/player"],
    ["https://vds.client.fr/", "wss://vds.client.fr/ws/player"],
    ["https://allia-innovation.fr/espace/affichage/", "wss://allia-innovation.fr/espace/affichage/ws/player"],
    ["http://127.0.0.1:47800/espace/affichage/", "ws://127.0.0.1:47800/espace/affichage/ws/player"],
  ])("%s", (server, expected) => expect(wsUrl(server)).toBe(expected));
});

describe("PlayerSocket", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function setup() {
    const socks: FakeSocket[] = [];
    const changed = vi.fn();
    const revoked = vi.fn();
    const s = new PlayerSocket({
      server: "https://h/base/",
      token: () => "vds_dev_x",
      onScheduleChanged: changed,
      onRevoked: revoked,
      factory: (u, p) => {
        const f = new FakeSocket(u, p);
        socks.push(f);
        return f;
      },
      random: () => 0.5, // jitter x1
    });
    return { socks, changed, revoked, s };
  }

  it("auth dans le premier message, sous-protocole, poussée de schedule.changed", () => {
    const { socks, changed, s } = setup();
    s.start();
    const ws = socks[0]!;
    expect(ws.url).toBe("wss://h/base/ws/player");
    expect(ws.protocols).toEqual([SUBPROTOCOL]);
    ws.onopen?.({});
    expect(JSON.parse(ws.sent[0]!)).toMatchObject({ type: "auth", data: { token: "vds_dev_x" } });
    expect(ws.url).not.toContain("vds_dev_x"); // jamais en query string
    ws.recv({ type: "welcome", data: { revision: "r1" } });
    expect(s.connected).toBe(true);
    ws.recv({ type: "schedule.changed", data: { revision: "r2" } });
    ws.recv({ type: "cmd", data: {} }); // ignorée
    ws.onmessage?.({ data: "pas du json" });
    expect(changed.mock.calls).toEqual([["r1"], ["r2"]]);
  });

  it("coupure : reconnexion avec backoff exponentiel plafonné, remis à zéro après welcome", () => {
    const { socks, s } = setup();
    s.start();
    for (let i = 0, wait = BACKOFF_MIN_MS; i < 8; i++, wait = Math.min(wait * 2, BACKOFF_MAX_MS)) {
      socks.at(-1)!.onclose?.({ code: 1006 });
      expect(s.connected).toBe(false);
      vi.advanceTimersByTime(wait - 1);
      expect(socks).toHaveLength(i + 1);
      vi.advanceTimersByTime(1);
      expect(socks).toHaveLength(i + 2);
    }
    socks.at(-1)!.recv({ type: "welcome", data: { revision: "r" } });
    socks.at(-1)!.onclose?.({ code: 1006 });
    vi.advanceTimersByTime(BACKOFF_MIN_MS);
    expect(socks).toHaveLength(10);
  });

  it("4410 : révocation, plus de reconnexion", () => {
    const { socks, revoked, s } = setup();
    s.start();
    socks[0]!.onclose?.({ code: 4410 });
    expect(revoked).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(BACKOFF_MAX_MS * 2);
    expect(socks).toHaveLength(1);
  });

  it("4426 : serveur trop ancien, sondage HTTP seul (pas de boucle de reconnexion)", () => {
    const { socks, s } = setup();
    s.start();
    socks[0]!.onclose?.({ code: 4426 });
    vi.advanceTimersByTime(BACKOFF_MAX_MS * 2);
    expect(socks).toHaveLength(1);
  });

  it("stop : ferme, détache, plus de reconnexion ; WebSocket qui lève = nouvelle tentative", () => {
    const { socks, s } = setup();
    s.start();
    const ws = socks[0]!;
    s.stop();
    expect(ws.closed && ws.onclose === null).toBe(true);
    vi.advanceTimersByTime(BACKOFF_MAX_MS * 2);
    expect(socks).toHaveLength(1);
    let n = 0;
    const s2 = new PlayerSocket({
      server: "https://h/",
      token: () => "t",
      onScheduleChanged: () => {},
      onRevoked: () => {},
      factory: () => {
        n++;
        throw new Error("CSP");
      },
      random: () => 0.5,
    });
    s2.start();
    vi.advanceTimersByTime(BACKOFF_MIN_MS);
    expect(n).toBe(2);
    s2.stop();
  });
});
