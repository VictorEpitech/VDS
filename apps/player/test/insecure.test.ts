import { describe, expect, it } from "vitest";
import { INSECURE_CONTEXT, INSECURE_UPSTREAM, beat, insecureContext } from "../src/heartbeat";

describe("contexte non sécurisé", () => {
  it("signale player.insecure_context dans errors[] du heartbeat", async () => {
    const g = globalThis as Record<string, unknown>;
    const saved = { window: g.window, fetch: g.fetch, v: g.__PLAYER_VERSION__ };
    const sent: { errors: { code: string }[] }[] = [];
    g.window = { isSecureContext: false, location: { href: "http://x/player/" } };
    g.__PLAYER_VERSION__ = "t";
    g.fetch = async (_u: unknown, init: { body: string }) => {
      sent.push(JSON.parse(init.body));
      return new Response("{}", { status: 500 });
    };
    try {
      expect(insecureContext()).toBe(true);
      await beat({
        token: () => "t",
        engine: { takeErrors: () => [], restoreErrors: () => {}, playing: () => null, stale: false },
        assets: { missing: () => [] },
        getActive: () => null,
        onRevisionMismatch: () => {},
        onCommands: () => {},
      } as never).catch(() => {});
      expect(sent[0]?.errors.map((e) => e.code)).toContain(INSECURE_CONTEXT);
    } finally {
      g.window = saved.window;
      g.fetch = saved.fetch;
      g.__PLAYER_VERSION__ = saved.v;
    }
  });

  it("signale player.insecure_upstream quand le pont natif l'indique", async () => {
    const g = globalThis as Record<string, unknown>;
    const saved = { window: g.window, fetch: g.fetch, v: g.__PLAYER_VERSION__ };
    const sent: { errors: { code: string }[] }[] = [];
    g.window = {
      isSecureContext: true,
      location: { href: "http://127.0.0.1/player/" },
      VdsNative: { ping() {}, reload() {}, info: () => JSON.stringify({ platform: "android", insecure_upstream: true }) },
    };
    g.__PLAYER_VERSION__ = "t";
    g.fetch = async (_u: unknown, init: { body: string }) => {
      sent.push(JSON.parse(init.body));
      return new Response("{}", { status: 500 });
    };
    try {
      await beat({
        token: () => "t",
        engine: { takeErrors: () => [], restoreErrors: () => {}, playing: () => null, stale: false },
        assets: { missing: () => [] },
        getActive: () => null,
        onRevisionMismatch: () => {},
        onCommands: () => {},
      } as never).catch(() => {});
      expect(sent[0]?.errors.map((e) => e.code)).toEqual([INSECURE_UPSTREAM]);
    } finally {
      g.window = saved.window;
      g.fetch = saved.fetch;
      g.__PLAYER_VERSION__ = saved.v;
    }
  });

  it.each([
    [{ platform: "android", app_version_code: 42 }, 42],
    [{ platform: "android" }, undefined],
    [{ platform: "android", app_version_code: "x" }, undefined],
  ])("heartbeat app_version_code %j -> %s", async (info, expected) => {
    const g = globalThis as Record<string, unknown>;
    const saved = { window: g.window, fetch: g.fetch, v: g.__PLAYER_VERSION__ };
    const sent: Record<string, unknown>[] = [];
    g.window = {
      isSecureContext: true,
      location: { href: "http://127.0.0.1/player/" },
      VdsNative: { ping() {}, reload() {}, info: () => JSON.stringify(info) },
    };
    g.__PLAYER_VERSION__ = "t";
    g.fetch = async (_u: unknown, init: { body: string }) => {
      sent.push(JSON.parse(init.body));
      return new Response("{}", { status: 500 });
    };
    try {
      await beat({
        token: () => "t",
        engine: { takeErrors: () => [], restoreErrors: () => {}, playing: () => null, stale: false },
        assets: { missing: () => [] },
        getActive: () => null,
        onRevisionMismatch: () => {},
        onCommands: () => {},
      } as never).catch(() => {});
      expect(sent[0]?.app_version_code).toBe(expected);
      expect("app_version_code" in (sent[0] ?? {})).toBe(expected !== undefined);
    } finally {
      g.window = saved.window;
      g.fetch = saved.fetch;
      g.__PLAYER_VERSION__ = saved.v;
    }
  });
});
