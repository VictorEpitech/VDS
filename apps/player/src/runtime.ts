import { getPlatformBridge, registerPowerDriver } from "@vds/sdk-ts";
import type { Locale, ScheduleDocument } from "@vds/sdk-ts";
import { AssetCache } from "./assets";
import { DeviceRevokedError } from "./api";
import { setDrift } from "./clock";
import { ContentLoader } from "./content-loader";
import { clearAll, kvGet, scheduleGet } from "./db";
import { now } from "./clock";
import { Engine } from "./engine";
import { DEFAULT_INTERVAL_S, beat } from "./heartbeat";
import type { RemoteCommand } from "./heartbeat";
import type { Screens } from "./screens";
import { KV } from "./shared";
import { createBlackscreenDriver } from "./power/blackscreen";
import { createCecDriver } from "./power/cec";
import { PowerController } from "./power/controller";
import { Syncer } from "./sync";
import { PlayerSocket } from "./ws";
import { reloadPage } from "./watchdog";

export interface RuntimeDeps {
  screens: Screens;
  server: string;
  locale: Locale;
  token: string;
}

export interface Runtime {
  engine: Engine;
  /** Résolu lorsque le jeton est révoqué : l'état local a alors été effacé. */
  revoked: Promise<void>;
  stop(): void;
}

/** Joue la programmation, la synchronise et remonte l'état. Aucune dépendance à un serveur joignable pour jouer. */
export async function startRuntime(d: RuntimeDeps): Promise<Runtime> {
  const life = new AbortController();
  const assets = new AssetCache(d.server);
  await assets.init();
  const drift = await kvGet<number>(KV.drift);
  if (typeof drift === "number") setDrift(drift);

  let active: ScheduleDocument | null = (await scheduleGet()) ?? null;
  // Plan d'alimentation : exécuté par le player seul (règle 5), à chaque tick, serveur ou non.
  let power: PowerController | null = null;
  const engine = new Engine({
    onTick: () => power?.tick(),
    screens: d.screens,
    loader: new ContentLoader(d.server),
    assets,
    server: d.server,
    locale: d.locale,
    isOnline: () => navigator.onLine,
  });

  registerPowerDriver(createCecDriver());
  registerPowerDriver(createBlackscreenDriver({ setBlackout: (on) => engine.setSuspended(on) }));
  power = new PowerController({ now, getDoc: () => active, report: (code) => engine.report(code) });

  let resolveRevoked: () => void = () => {};
  const revoked = new Promise<void>((r) => (resolveRevoked = r));
  let revokedOnce = false;
  const onRevoked = async (): Promise<void> => {
    if (revokedOnce) return;
    revokedOnce = true;
    life.abort();
    socket.stop();
    engine.stop();
    // Jeton, programmation, journal, révision et cache média : retour à l'appairage.
    await clearAll().catch(() => {});
    await assets.clear().catch(() => {});
    setDrift(0);
    d.screens.show("loading");
    resolveRevoked();
  };
  const guard = async (fn: () => Promise<unknown>): Promise<void> => {
    try {
      await fn();
    } catch (e) {
      if (e instanceof DeviceRevokedError) await onRevoked();
    }
  };

  const syncer = new Syncer({
    token: () => d.token,
    assets,
    server: d.server,
    getActive: () => active,
    activate: (doc) => {
      active = doc;
      engine.setDoc(doc);
    },
    report: () => {},
  });
  const sync = (): Promise<void> => guard(() => syncer.trigger());

  // Canal WS facultatif : réactivité seulement, le sondage HTTP ci-dessous reste la voie nominale.
  const socket = new PlayerSocket({
    server: d.server,
    token: () => d.token,
    onScheduleChanged: (rev) => {
      if (rev !== active?.revision) void sync();
    },
    onRevoked: () => void onRevoked(),
  });
  socket.start();

  const maintenance = { on: false };
  const onCommands = (cmds: RemoteCommand[]): void => {
    for (const c of cmds) {
      if (c.type === "reload") reloadPage();
      else if (c.type === "maintenance" && typeof c.code === "string") {
        maintenance.on = true;
        d.screens.showMaintenance({
          code: c.code,
          revision: active?.revision ?? "",
          online: navigator.onLine,
          uptime_s: Math.floor(performance.now() / 1000),
          cache_bytes: 0,
        });
      } else if (c.type === "maintenance_end" && maintenance.on) {
        maintenance.on = false;
        d.screens.show("none");
        engine.tick();
      }
    }
  };

  engine.setDoc(active);
  engine.start();
  if (!active) d.screens.show("loading");

  const onlineHandler = (): void => void sync();
  window.addEventListener("online", onlineHandler);
  life.signal.addEventListener("abort", () => window.removeEventListener("online", onlineHandler));

  void sync();
  void (async () => {
    while (!life.signal.aborted) {
      let next = DEFAULT_INTERVAL_S;
      await guard(async () => {
        next = await beat({
          token: () => d.token,
          engine,
          assets,
          getActive: () => active,
          onRevisionMismatch: () => void sync(),
          onCommands,
        });
      });
      await new Promise<void>((r) => {
        const t = setTimeout(r, next * 1000);
        life.signal.addEventListener("abort", () => (clearTimeout(t), r()), { once: true });
      });
    }
  })();

  getPlatformBridge().ping();
  return {
    engine,
    revoked,
    stop: () => {
      life.abort();
      socket.stop();
      engine.stop();
    },
  };
}
