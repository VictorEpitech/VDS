import type { PlayerAdapter, PlayerCapability } from "@vds/sdk-ts";

/** Assistant local de l'image Pi (systemd), joignable en boucle seulement. Voir packaging/pi. */
export const HELPER_ORIGIN = "http://127.0.0.1:47801";
const CORE: PlayerCapability[] = ["cmd.reload", "cmd.identify", "cmd.cache.resync"];

/**
 * Adaptateur pour un assistant local (Chromium kiosque sur Pi) : redémarrage de l'application, et de
 * l'appareil si l'assistant l'autorise, via systemd. Pas de capture en V1. Retourne `null` s'il n'y a pas
 * d'assistant (navigateur ordinaire) : le sondage échoue vite (connexion refusée) et sans aucun effet visible.
 */
export async function probeHelperAdapter(timeoutMs = 800, f: typeof fetch = fetch): Promise<PlayerAdapter | null> {
  let caps: Record<string, unknown>;
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await f(`${HELPER_ORIGIN}/v1/caps`, { cache: "no-store", credentials: "omit", signal: ctl.signal });
      if (!res.ok) return null;
      const v: unknown = await res.json();
      if (v === null || typeof v !== "object" || Array.isArray(v)) return null;
      caps = v as Record<string, unknown>;
    } finally {
      clearTimeout(timer);
    }
  } catch {
    return null;
  }
  if (caps.helper !== "vds-pi") return null;
  return {
    id: "player.pi",
    capabilities: async () => {
      const out = [...CORE];
      if (caps.restart_app === true) out.push("cmd.restart.app");
      if (caps.restart_device === true) out.push("cmd.restart.device");
      return out;
    },
    restart: async (scope) => {
      if (scope !== "app" && scope !== "device") throw new Error("unsupported");
      const res = await f(`${HELPER_ORIGIN}/v1/restart/${scope}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" }, // force la prévalidation CORS (origine vérifiée par l'assistant)
        body: "{}",
        cache: "no-store",
        credentials: "omit",
      });
      if (!res.ok) throw new Error("failed");
      await new Promise<never>(() => {});
    },
    screenshot: () => Promise.reject(new Error("unsupported")),
  };
}
