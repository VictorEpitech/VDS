import { getPlatformBridge } from "@vds/sdk-ts";
import { age } from "./alive";
import { now } from "./clock";
import type { Engine } from "./engine";

export const WATCHDOG_INTERVAL_MS = 10_000;
/** Sans signe de vie de la boucle principale pendant ce délai, la page se recharge. */
export const STALL_LIMIT_MS = 45_000;
const DAILY_RELOAD_AFTER_MS = 20 * 3_600_000;
const DAILY_RELOAD_HOUR = 4;

export function reloadPage(): void {
  if (window.VdsNative) getPlatformBridge().reload();
  else location.reload();
}

export interface WatchdogOptions {
  engine: () => Engine | null;
  reload?: () => void;
}

/**
 * (1) `VdsNative.ping()` toutes les 10 s tant que la boucle principale vit : le pont natif recharge
 * la WebView s'il ne reçoit plus rien. (2) rechargement de la page si la boucle est figée.
 * (3) rechargement quotidien dans un créneau `off`, sinon à 04:00 locale.
 */
export function startWatchdog(opts: WatchdogOptions): () => void {
  const reload = opts.reload ?? reloadPage;
  const bridge = getPlatformBridge();
  const timer = setInterval(() => {
    if (age() > STALL_LIMIT_MS) {
      reload();
      return;
    }
    bridge.ping();
    const engine = opts.engine();
    if (performance.now() > DAILY_RELOAD_AFTER_MS) {
      const hour = new Date(now()).getHours();
      if (engine?.currentKind === "off" || hour === DAILY_RELOAD_HOUR) reload();
    }
  }, WATCHDOG_INTERVAL_MS);
  return () => clearInterval(timer);
}
