import { getPlatformBridge, type ScheduleDocument } from "@vds/sdk-ts";
import { NetworkError, request } from "./api";
import type { AssetStore } from "./assets";
import { storageEstimate } from "./assets";
import { driftFrom, now, setDrift } from "./clock";
import { kvSet } from "./db";
import type { Engine } from "./engine";
import { KV } from "./shared";
import { allAssets, neededSlots, slotAssets } from "./timeline";

export interface RemoteCommand {
  type?: string;
  [key: string]: unknown;
}

export interface HeartbeatDeps {
  token: () => string;
  engine: Engine;
  assets: AssetStore;
  getActive: () => ScheduleDocument | null;
  onRevisionMismatch: () => void;
  onCommands: (cmds: RemoteCommand[]) => void;
}

export const DEFAULT_INTERVAL_S = 60;
export const INSECURE_CONTEXT = "player.insecure_context";

export const INSECURE_UPSTREAM = "player.insecure_upstream";

/** Le pont natif (Android, option LAN) signale un amont en HTTP : remonté à l'admin, jamais affiché. */
export function insecureUpstream(): boolean {
  try {
    const raw: unknown = getPlatformBridge().info();
    const info = (typeof raw === "string" ? JSON.parse(raw) : raw) as { insecure_upstream?: boolean } | null;
    return info?.insecure_upstream === true;
  } catch {
    return false;
  }
}

export function insecureContext(): boolean {
  return typeof window !== "undefined" && window.isSecureContext === false;
}

/** Un battement : remonte l'état, retourne l'intervalle suivant (s). Le jeton révoqué lève `DeviceRevokedError`. */
export async function beat(deps: HeartbeatDeps): Promise<number> {
  const doc = deps.getActive();
  const keys = doc ? (deps.assets.lazy ? slotAssets(doc, neededSlots(doc, now())) : allAssets(doc)) : [];
  const missing = deps.assets.missing(keys);
  const est = await storageEstimate();
  const errors = deps.engine.takeErrors();
  // Sans contexte sécurisé : ni Service Worker ni cache, rien à jouer hors ligne. Signalé à l'admin, jamais au public.
  const flags = [
    ...(insecureContext() ? [INSECURE_CONTEXT] : []),
    ...(insecureUpstream() ? [INSECURE_UPSTREAM] : []),
  ];
  const sentErrors = flags.length
    ? [...errors.slice(-(50 - flags.length)), ...flags.map((code) => ({ at: new Date().toISOString(), code }))]
    : errors;
  const body = {
    revision: doc?.revision ?? null,
    playing: deps.engine.playing(),
    cache: { bytes: est?.bytes ?? 0, quota: est?.quota ?? 0, missing: missing.slice(0, 500) },
    app_version: __PLAYER_VERSION__,
    uptime_s: Math.floor(performance.now() / 1000),
    stale: deps.engine.stale,
    errors: sentErrors,
  };
  const sent = Date.now();
  try {
    const res = await request("api/v1/player/heartbeat", { method: "POST", token: deps.token(), body });
    if (!res.ok) {
      deps.engine.restoreErrors(errors);
      return DEFAULT_INTERVAL_S;
    }
    const ack = (await res.json()) as {
      revision: string | null;
      server_time: string;
      poll_interval_s: number;
      commands: RemoteCommand[];
    };
    const d = driftFrom(ack.server_time, sent, Date.now());
    if (d !== null) {
      setDrift(d);
      void kvSet(KV.drift, d).catch(() => {});
    }
    if (ack.revision !== (doc?.revision ?? null) || missing.length > 0) deps.onRevisionMismatch();
    if (ack.commands?.length) deps.onCommands(ack.commands);
    return ack.poll_interval_s > 0 ? ack.poll_interval_s : DEFAULT_INTERVAL_S;
  } catch (e) {
    deps.engine.restoreErrors(errors);
    if (e instanceof NetworkError) return DEFAULT_INTERVAL_S;
    throw e;
  }
}
