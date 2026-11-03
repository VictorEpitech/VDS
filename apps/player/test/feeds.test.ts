import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetDbForTests, feedGet, feedKeys } from "../src/db";
import { FEED_NET_TIMEOUT_MS, FEED_POLL_MS, FeedService } from "../src/feeds";
import { allFeeds } from "../src/timeline";

const ID = "a".repeat(32);
const ID2 = "b".repeat(32);
const g = globalThis as Record<string, unknown>;

const okRes = (fetchedAt: string, data: unknown, etag = '"e1"'): Response =>
  new Response(JSON.stringify({ feed_id: ID, fetched_at: fetchedAt, data }), { status: 200, headers: { ETag: etag } });

let savedDb: unknown;
beforeEach(() => {
  savedDb = g.indexedDB;
  g.indexedDB = undefined; // repli mémoire : même contrat, sans persistance
  resetDbForTests();
});
afterEach(() => {
  g.indexedDB = savedDb;
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("FeedService.get", () => {
  it("réseau OK : snapshot renvoyé et cache mis à jour", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => okRes("2026-11-03T10:00:00Z", { t: 1 })));
    const s = new FeedService({ token: () => "tok" });
    expect(await s.get(ID)).toEqual({ feedId: ID, fetchedAt: "2026-11-03T10:00:00Z", data: { t: 1 } });
    expect((await feedGet(ID))?.etag).toBe('"e1"');
    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]!;
    expect(String(call[0])).toMatch(new RegExp(`api/v1/player/feeds/${ID}$`));
    expect((call[1] as RequestInit).headers).toMatchObject({ Authorization: "Bearer tok" });
  });

  it("réseau coupé : dernier état connu ; jamais reçu : null", async () => {
    const f = vi.fn().mockResolvedValueOnce(okRes("2026-11-03T10:00:00Z", { t: 1 })).mockRejectedValue(new TypeError("net"));
    vi.stubGlobal("fetch", f);
    const s = new FeedService({ token: () => "tok" });
    await s.get(ID);
    expect((await s.get(ID))?.data).toEqual({ t: 1 });
    expect(await s.get(ID2)).toBeNull();
  });

  it("304 et 204 : état connu conservé, requête conditionnelle", async () => {
    const f = vi
      .fn()
      .mockResolvedValueOnce(okRes("2026-11-03T10:00:00Z", { t: 1 }))
      .mockResolvedValueOnce(new Response(null, { status: 304 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", f);
    const s = new FeedService({ token: () => "tok" });
    await s.get(ID);
    expect((await s.get(ID))?.data).toEqual({ t: 1 });
    expect((f.mock.calls[1]![1] as RequestInit).headers).toMatchObject({ "If-None-Match": '"e1"' });
    expect((await s.get(ID))?.data).toEqual({ t: 1 });
    expect(await s.get(ID2)).toBeNull();
  });

  it("serveur muet : repli sur le cache après 3 s", async () => {
    vi.useFakeTimers();
    const f = vi.fn().mockResolvedValueOnce(okRes("2026-11-03T10:00:00Z", { t: 1 })).mockImplementation(
      (_u: string, init: RequestInit) =>
        new Promise((_, rej) => init.signal!.addEventListener("abort", () => rej(new DOMException("abort", "AbortError")))),
    );
    vi.stubGlobal("fetch", f);
    const s = new FeedService({ token: () => "tok" });
    await s.get(ID);
    const p = s.get(ID);
    await vi.advanceTimersByTimeAsync(FEED_NET_TIMEOUT_MS + 1);
    expect((await p)?.data).toEqual({ t: 1 });
  });

  it("identifiant non conforme : jamais de requête", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    expect(await new FeedService({ token: () => "t" }).get("../etc/passwd")).toBeNull();
    expect(f).not.toHaveBeenCalled();
  });

  it("requêtes simultanées partagées", async () => {
    const f = vi.fn(async () => okRes("2026-11-03T10:00:00Z", 1));
    vi.stubGlobal("fetch", f);
    const s = new FeedService({ token: () => "t" });
    await Promise.all([s.get(ID), s.get(ID), s.get(ID)]);
    expect(f).toHaveBeenCalledTimes(1);
  });
});

describe("FeedService.preload", () => {
  it("précharge, purge les flux non référencés, sonde toutes les 300 s puis s'arrête", async () => {
    vi.useFakeTimers();
    const f = vi.fn(async (u: string) => okRes("2026-11-03T10:00:00Z", { u: String(u).slice(-4) }));
    vi.stubGlobal("fetch", f);
    const s = new FeedService({ token: () => "t" });
    await s.get(ID2);
    await s.preload([ID]);
    expect(await feedKeys()).toEqual([ID]);
    const n = f.mock.calls.length;
    await vi.advanceTimersByTimeAsync(FEED_POLL_MS - 1);
    expect(f.mock.calls.length).toBe(n);
    await vi.advanceTimersByTimeAsync(2);
    expect(f.mock.calls.length).toBe(n + 1);
    s.stop();
    await vi.advanceTimersByTimeAsync(FEED_POLL_MS * 3);
    expect(f.mock.calls.length).toBe(n + 1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("allFeeds : flux de toutes les playlists, sans doublon", () => {
    const doc = { playlists: { a: { items: [{ feeds: [ID] }, { feeds: [ID, ID2] }, {}] } } } as never;
    expect(allFeeds(doc).sort()).toEqual([ID, ID2]);
  });
});
