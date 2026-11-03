import { getPlatformBridge, type ScheduleDocument } from "@vds/sdk-ts";
import { NetworkError, request } from "./api";
import type { AssetStore } from "./assets";
import { storageEstimate } from "./assets";
import { driftFrom, now, setDrift } from "./clock";
import { kvSet } from "./db";
import type { Engine } from "./engine";
import type { KioskReport } from "./kiosk";
import type { CmdResult, IncomingCommand } from "./commands";
import { KV } from "./shared";
import { allAssets, neededSlots, slotAssets } from "./timeline";

export type { IncomingCommand as RemoteCommand } from "./commands";

export interface HeartbeatDeps {
  token: () => string;
  engine: Engine;
  assets: AssetStore;
  getActive: () => ScheduleDocument | null;
  onRevisionMismatch: () => void;
  onCommands: (cmds: IncomingCommand[]) => void;
  /** 1.11 : `boot_id`, capacités et résultats non acquittés ; accusés `results_acked` rendus à l'appelant. */
  commands?: {
    bootId: string;
    capabilities: () => string[];
    results: () => CmdResult[];
    acked: (ids: string[]) => void;
  };
  kiosk?: () => KioskReport;
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

/** Code de version de l'APK (pont Android) ; absent sur une APK ancienne ou hors Android. Entier borné, sinon omis. */
export function appVersionCode(): number | undefined {
  try {
    const raw: unknown = getPlatformBridge().info();
    const info = (typeof raw === "string" ? JSON.parse(raw) : raw) as { app_version_code?: unknown } | null;
    const v = info?.app_version_code;
    return typeof v === "number" && Number.isInteger(v) && v >= 1 && v <= 2_100_000_000 ? v : undefined;
  } catch {
    return undefined;
  }
}

export function insecureContext(): boolean {
  return typeof window !== "undefined" && window.isSecureContext === false;
}

/** Un battement : remonte l'état, retourne l'intervalle suivant (s). Le jeton révoqué lève `DeviceRevokedError`. */
export async function beat(deps: HeartbeatDeps): Promise<number> {
  const doc = deps.getActive();
  const keys = doc ? (deps.assets.lazy ? slotAssets(doc, neededSlots(doc, now())) : allAssets(doc)) : [];
  const missing = deps.assets.missing(keys, doc?.assets);
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
    ...(appVersionCode() !== undefined ? { app_version_code: appVersionCode() } : {}),
    uptime_s: Math.floor(performance.now() / 1000),
    stale: deps.engine.stale,
    errors: sentErrors,
    ...(deps.kiosk ? { kiosk: deps.kiosk() } : {}),
    ...(deps.commands
      ? {
          boot_id: deps.commands.bootId,
          capabilities: deps.commands.capabilities(),
          command_results: deps.commands.results(),
        }
      : {}),
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
      commands: IncomingCommand[];
      results_acked?: string[];
    };
    const d = driftFrom(ack.server_time, sent, Date.now());
    if (d !== null) {
      setDrift(d);
      void kvSet(KV.drift, d).catch(() => {});
    }
    if (ack.revision !== (doc?.revision ?? null) || missing.length > 0) deps.onRevisionMismatch();
    if (ack.results_acked?.length) deps.commands?.acked(ack.results_acked);
    if (ack.commands?.length) deps.onCommands(ack.commands);
    return ack.poll_interval_s > 0 ? ack.poll_interval_s : DEFAULT_INTERVAL_S;
  } catch (e) {
    deps.engine.restoreErrors(errors);
    if (e instanceof NetworkError) return DEFAULT_INTERVAL_S;
    throw e;
  }
}
