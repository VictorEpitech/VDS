import { getPlatformBridge } from "@vds/sdk-ts";
import { serverUrl } from "./api";
import { setDrift } from "./clock";
import { kvGet } from "./db";
import { setLocale } from "./i18n";
import { lockDown } from "./kiosk";
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
  const drift = await kvGet<number>(KV.drift);
  if (typeof drift === "number") setDrift(drift);
  void navigator.storage?.persist?.().catch(() => {});

  for (;;) {
    let token = await kvGet<string>(KV.token);
    if (!token) token = (await pair(screens)).token;
    runtime = await startRuntime({ screens, server, locale, token });
    await runtime.revoked; // révocation : état effacé, retour à l'appairage
    runtime = null;
  }
}

getPlatformBridge().ping();
void main();
