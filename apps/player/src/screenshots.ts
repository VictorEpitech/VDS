import type { PlayerAdapter } from "@vds/sdk-ts";
import { NetworkError, resolveServer } from "./api";
import type { Outcome } from "./commands";

export const SHOT_MAX_EDGE = 1280;
/** Qualité JPEG 1..100 transmise à l'adaptateur. */
export const SHOT_QUALITY = 70;
export const SHOT_MAX_BYTES = 2 * 1024 * 1024;

/**
 * Capture via l'adaptateur puis envoi au serveur (`POST player/screenshots[/{cmd_id}]`).
 * Aucun effet visible : ni indicateur, ni message d'échec (règle 6).
 */
export async function captureAndUpload(
  adapter: PlayerAdapter | null,
  token: string,
  server: string,
  cmdId?: string,
): Promise<Outcome> {
  if (!adapter) return { status: "failed", code: "screenshot.unavailable" };
  let blob: Blob;
  try {
    blob = await adapter.screenshot({ maxEdge: SHOT_MAX_EDGE, quality: SHOT_QUALITY });
  } catch {
    return { status: "failed", code: "screenshot.unavailable" };
  }
  if (blob.size === 0 || blob.size > SHOT_MAX_BYTES) return { status: "failed", code: "screenshot.too_large" };
  try {
    const path = cmdId ? `api/v1/player/screenshots/${encodeURIComponent(cmdId)}` : "api/v1/player/screenshots";
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 30_000);
    let res: Response;
    try {
      res = await fetch(resolveServer(path, server), {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "image/jpeg", Accept: "application/json" },
        body: blob,
        cache: "no-store",
        credentials: "omit",
        signal: ctl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
    if (!res.ok) return { status: "failed", code: `screenshot.upload_${res.status}` };
    let id: unknown;
    try {
      id = ((await res.json()) as { id?: unknown }).id;
    } catch {
      /* corps absent */
    }
    return { status: "ok", data: typeof id === "string" ? { screenshot_id: id } : {} };
  } catch (e) {
    return { status: "failed", code: e instanceof NetworkError || e instanceof Error ? "screenshot.offline" : "cmd.failed" };
  }
}
