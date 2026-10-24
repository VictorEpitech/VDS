import { getPlatformBridge } from "@vds/sdk-ts";
import { serverUrl } from "./api";
import { setDrift } from "./clock";
import { kvGet } from "./db";
import { setLocale } from "./i18n";
import { claimKioskBridge, lockDown } from "./kiosk";
import { pair } from "./pairing";
import { startRuntime } from "./runtime";
import type { Runtime } from "./runtime";
import { Screens } from "./screens";
import { KV } from "./shared";
import { startWatchdog } from "./watchdog";
import "./style.css";

async function registerServiceWorker(): Promise<void> {
  if (!("serviceWorker" in navigator)) return;
  try {
    await navigator.serviceWorker.register("sw.js");
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) {
      await new Promise<void>((resolve) => {
        const t = setTimeout(resolve, 5000);
        navigator.serviceWorker.addEventListener(
          "controllerchange",
          () => {
            clearTimeout(t);
            resolve();
          },
          { once: true },
        );
      });
    }
  } catch {
    // Contexte non sécurisé ou SW indisponible : pas de lecture hors ligne, le player reste neutre.
  }
}

function loadTheme(cssUrl: string | undefined): void {
  document.documentElement.dataset.theme = "dark";
  if (!cssUrl) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = cssUrl;
  document.head.append(link);
}

async function main(): Promise<void> {
  claimKioskBridge(); // avant tout content-type : seul ce contrôleur parle au kiosque natif
  const cfg = window.__VDS__;
  const locale = setLocale(cfg?.defaultLocale ?? navigator.language.slice(0, 2));
  loadTheme(cfg?.theme?.cssUrl);
  lockDown();
  const root = document.getElementById("app");
  if (!root) return;
  const screens = new Screens(root);
  const server = serverUrl();
  let runtime: Runtime | null = null;
  startWatchdog({ engine: () => runtime?.engine ?? null });
  await registerServiceWorker();
  const drift = await safe(() => kvGet<number>(KV.drift));
  if (typeof drift === "number") setDrift(drift);
  try {
    void Promise.resolve(navigator.storage?.persist?.()).catch(() => {});
  } catch {
    // API absente ou refusée : sans effet.
  }

  let failures = 0;
  for (;;) {
    try {
      let token = await safe(() => kvGet<string>(KV.token));
      if (!token) token = (await pair(screens)).token;
      runtime = await startRuntime({ screens, server, locale, token });
      failures = 0;
      await runtime.revoked; // révocation : état effacé, retour à l'appairage
      runtime = null;
    } catch (e) {
      // Jamais de spinner infini : journal interne, retour à l'écran neutre puis nouvelle tentative.
      runtime = null;
      failures++;
      internalLog("player.start_failed", e);
      screens.show("loading");
      await sleep(Math.min(30_000, 1_000 * 2 ** Math.min(failures, 5)));
    }
  }
}

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/** Lecture tolérante : une base illisible équivaut à « rien en cache ». */
async function safe<T>(fn: () => Promise<T>): Promise<T | undefined> {
  try {
    return await fn();
  } catch (e) {
    internalLog("player.storage_failed", e);
    return undefined;
  }
}

/** Journal interne (console, jamais à l'écran public). */
function internalLog(code: string, e: unknown): void {
  try {
    console.warn(code, e instanceof Error ? e.message : String(e));
  } catch {
    // console absente : ignorer.
  }
}

window.addEventListener("unhandledrejection", (ev) => internalLog("player.unhandled", ev.reason));
window.addEventListener("error", (ev) => internalLog("player.error", ev.error ?? ev.message));

getPlatformBridge().ping();
main().catch((e) => internalLog("player.fatal", e));
