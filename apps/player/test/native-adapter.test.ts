import { afterEach, describe, expect, it, vi } from "vitest";
import type { NativeCmd } from "@vds/sdk-ts";
import { probeHelperAdapter } from "../src/helper-adapter";
import { claimNativeAdapter, decodeJpeg } from "../src/native-adapter";

const JPEG_B64 = btoa(String.fromCharCode(0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4));
const base = (over: Partial<NativeCmd> = {}): NativeCmd => ({
  caps: async () => "{}",
  shot: async () => "{}",
  restart: async () => "{}",
  ...over,
});

function install(cmd: NativeCmd | null): void {
  window.VdsNative = {
    ping() {},
    info: () => ({ platform: "android", model: "", app_version: "", os_version: "" }),
    reload() {},
    claimCmd: () => cmd,
  };
}

afterEach(() => {
  delete window.VdsNative;
});

describe("adaptateur natif (pont claimCmd)", () => {
  it("sans pont : null, jamais de branche par plateforme", () => {
    expect(claimNativeAdapter()).toBeNull();
  });

  it("le jeton n'est remis qu'une fois : claimCmd est retiré", () => {
    install(base());
    expect(claimNativeAdapter()).not.toBeNull();
    expect(window.VdsNative?.claimCmd).toBeUndefined();
    expect(claimNativeAdapter()).toBeNull();
  });

  it("capacités = socle + ce que le natif annonce", async () => {
    install(base({ caps: async () => '{"ok":true,"screenshot":true,"restart_app":true,"restart_device":false}' }));
    const caps = await claimNativeAdapter()!.capabilities();
    expect(caps).toEqual(["cmd.reload", "cmd.identify", "cmd.cache.resync", "cmd.screenshot", "cmd.restart.app"]);
  });

  it("réponse caps invalide ou ok:false : socle seul", async () => {
    for (const raw of ["not json", "[]", '{"ok":false,"screenshot":true}', '{"screenshot":"yes"}']) {
      install(base({ caps: async () => raw }));
      expect(await claimNativeAdapter()!.capabilities()).toEqual(["cmd.reload", "cmd.identify", "cmd.cache.resync"]);
    }
  });

  it("capture : bornes transmises au natif, JPEG validé", async () => {
    const shot = vi.fn(async () => `{"ok":true,"jpeg":"${JPEG_B64}"}`);
    install(base({ shot }));
    const blob = await claimNativeAdapter()!.screenshot({ maxEdge: 99999, quality: 500 });
    expect(shot).toHaveBeenCalledWith(2048, 100);
    expect(blob.type).toBe("image/jpeg");
    expect(blob.size).toBe(8);
  });

  it("capture : refus si pas ok, pas un JPEG, base64 invalide ou trop gros", async () => {
    expect(decodeJpeg(btoa("GIF89a....."))).toBeNull();
    expect(decodeJpeg("@@@@")).toBeNull();
    expect(decodeJpeg("abc")).toBeNull();
    expect(decodeJpeg(42)).toBeNull();
    expect(decodeJpeg("A".repeat(4 * 1024 * 1024))).toBeNull();
    for (const raw of ['{"ok":false,"code":"busy"}', "garbage", `{"ok":false,"jpeg":"${JPEG_B64}"}`]) {
      install(base({ shot: async () => raw }));
      await expect(claimNativeAdapter()!.screenshot({ maxEdge: 1280, quality: 70 })).rejects.toThrow();
    }
  });

  it("restart : échec propagé, succès ne résout jamais", async () => {
    install(base({ restart: async () => '{"ok":false,"code":"unsupported"}' }));
    await expect(claimNativeAdapter()!.restart("device")).rejects.toThrow("unsupported");
    install(base({ restart: async () => '{"ok":true}' }));
    let settled = false;
    void claimNativeAdapter()!
      .restart("app")
      .then(
        () => (settled = true),
        () => (settled = true),
      );
    await new Promise((r) => setTimeout(r, 20));
    expect(settled).toBe(false);
  });
});

describe("adaptateur assistant local (Pi)", () => {
  const ok = (body: unknown): Response => new Response(JSON.stringify(body), { status: 200 });

  it("assistant absent (connexion refusée) : null", async () => {
    expect(await probeHelperAdapter(50, (() => Promise.reject(new TypeError("refused"))) as typeof fetch)).toBeNull();
  });

  it("réponse d'un autre service : null", async () => {
    expect(await probeHelperAdapter(50, (async () => ok({ hello: 1 })) as typeof fetch)).toBeNull();
    expect(await probeHelperAdapter(50, (async () => ok([1])) as typeof fetch)).toBeNull();
  });

  it("capacités : restart app, appareil si autorisé, jamais de capture", async () => {
    const f = (async () => ok({ helper: "vds-pi", restart_app: true, restart_device: true })) as typeof fetch;
    const a = (await probeHelperAdapter(50, f))!;
    expect(a.id).toBe("player.pi");
    const caps = await a.capabilities();
    expect(caps).toContain("cmd.restart.app");
    expect(caps).toContain("cmd.restart.device");
    expect(caps).not.toContain("cmd.screenshot");
    await expect(a.screenshot({ maxEdge: 1, quality: 1 })).rejects.toThrow();
  });

  it("restart : POST JSON (prévalidation CORS), échec HTTP rejeté", async () => {
    const seen: RequestInit[] = [];
    const f = (async (url: string, init?: RequestInit) => {
      if (url.endsWith("/v1/caps")) return ok({ helper: "vds-pi", restart_app: true });
      seen.push(init ?? {});
      return new Response("{}", { status: 429 });
    }) as unknown as typeof fetch;
    const a = (await probeHelperAdapter(50, f))!;
    await expect(a.restart("app")).rejects.toThrow();
    expect(seen[0]?.method).toBe("POST");
    expect((seen[0]?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(seen[0]?.credentials).toBe("omit");
  });
});
