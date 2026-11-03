import type { NativeCmd, PlayerAdapter, PlayerCapability } from "@vds/sdk-ts";

const CORE: PlayerCapability[] = ["cmd.reload", "cmd.identify", "cmd.cache.resync"];
/** Plafond identique à celui du serveur (2 Mo). */
export const NATIVE_SHOT_MAX_BYTES = 2 * 1024 * 1024;
const B64 = /^[A-Za-z0-9+/]+={0,2}$/;

function parse(raw: string): Record<string, unknown> {
  try {
    const v: unknown = JSON.parse(raw);
    return v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** JPEG base64 validé strictement (alphabet, taille, octets magiques) ; sinon `null`. */
export function decodeJpeg(b64: unknown): Blob | null {
  if (typeof b64 !== "string" || b64.length === 0 || b64.length > Math.ceil((NATIVE_SHOT_MAX_BYTES * 4) / 3) + 4) return null;
  if (b64.length % 4 !== 0 || !B64.test(b64)) return null;
  let bin: string;
  try {
    bin = atob(b64);
  } catch {
    return null;
  }
  if (bin.length < 4 || bin.length > NATIVE_SHOT_MAX_BYTES || bin.charCodeAt(0) !== 0xff || bin.charCodeAt(1) !== 0xd8) return null;
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: "image/jpeg" });
}

/**
 * Adaptateur construit sur le pont natif `VdsNative.claimCmd()` (Android). Aucune connaissance de la plateforme
 * ici : le natif annonce ce qu'il sait faire (`caps`), le player n'expose que cela. À appeler une seule fois,
 * au démarrage, avant tout content-type (le jeton n'est remis qu'une fois, puis `claimCmd` est retiré).
 */
export function claimNativeAdapter(): PlayerAdapter | null {
  const native = window.VdsNative;
  if (!native?.claimCmd) return null;
  const cmd: NativeCmd | null = native.claimCmd();
  delete native.claimCmd;
  if (!cmd) return null;
  let caps: Promise<Record<string, unknown>> | null = null;
  const probe = (): Promise<Record<string, unknown>> => (caps ??= cmd.caps().then(parse, () => ({})));
  return {
    id: "player.android",
    capabilities: async () => {
      const c = await probe();
      const out = [...CORE];
      if (c.ok === true && c.screenshot === true) out.push("cmd.screenshot");
      if (c.ok === true && c.restart_app === true) out.push("cmd.restart.app");
      if (c.ok === true && c.restart_device === true) out.push("cmd.restart.device");
      return out;
    },
    restart: async (scope) => {
      if (scope !== "app" && scope !== "device") throw new Error("unsupported");
      const r = parse(await cmd.restart(scope));
      if (r.ok !== true) throw new Error(typeof r.code === "string" ? r.code : "failed");
      await new Promise<never>(() => {}); // le redémarrage a lieu : ne résout jamais
    },
    screenshot: async ({ maxEdge, quality }) => {
      const e = Math.min(2048, Math.max(64, Math.floor(maxEdge)));
      const q = Math.min(100, Math.max(1, Math.floor(quality)));
      const r = parse(await cmd.shot(e, q));
      const blob = r.ok === true ? decodeJpeg(r.jpeg) : null;
      if (!blob) throw new Error("unavailable");
      return blob;
    },
  };
}
