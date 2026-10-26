import { getPlatformBridge } from "@vds/sdk-ts";
import { NetworkError, request } from "./api";
import { mark } from "./alive";
import { driftFrom, setDrift } from "./clock";
import { kvDel, kvGet, kvSet } from "./db";
import { KV } from "./shared";
import type { Screens } from "./screens";

interface StoredPairing {
  pairing_id: string;
  code: string;
  poll_secret: string;
  poll_interval_s: number;
}

export interface Paired {
  token: string;
  screenId: string;
}

const RETRY_MS = 5_000;
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

function safeJson(s: string): Record<string, string> {
  try {
    return JSON.parse(s) as Record<string, string>;
  } catch {
    return {};
  }
}

function deviceInfo(): Record<string, unknown> {
  // Le pont Android ne renvoie que des chaînes : `info()` y est un JSON.
  const raw: unknown = getPlatformBridge().info();
  const info = (typeof raw === "string" ? safeJson(raw) : raw) as ReturnType<ReturnType<typeof getPlatformBridge>["info"]>;
  return {
    platform: info.platform || "web",
    model: info.model || undefined,
    app_version: info.app_version || __PLAYER_VERSION__,
    user_agent: navigator.userAgent.slice(0, 400),
    screen_w: window.screen?.width || undefined,
    screen_h: window.screen?.height || undefined,
  };
}

async function start(): Promise<StoredPairing | null> {
  try {
    const res = await request("api/v1/player/pairings", { method: "POST", body: { device: deviceInfo() } });
    if (res.status !== 201) return null;
    const b = (await res.json()) as StoredPairing & { expires_at: string };
    const stored: StoredPairing = {
      pairing_id: b.pairing_id,
      code: b.code,
      poll_secret: b.poll_secret,
      poll_interval_s: b.poll_interval_s || 3,
    };
    await kvSet(KV.pairing, stored);
    return stored;
  } catch (e) {
    if (e instanceof NetworkError) return null;
    throw e;
  }
}

const TICKET_TRIES = 4;
// Le ticket vit en mémoire seulement (jamais en IndexedDB) : le natif ne le remet qu'une fois.
let ticket: string | null | undefined;

async function claimTicket(): Promise<string | null> {
  if (ticket !== undefined) return ticket;
  ticket = null;
  try {
    const raw = await getPlatformBridge().claimPairingTicket?.();
    const t = (safeJson(raw ?? "{}") as { ticket?: unknown }).ticket;
    if (typeof t === "string" && /^[A-Za-z0-9_-]{16,64}$/.test(t)) ticket = t;
  } catch {
    /* pas de pont ou pont muet : repli code */
  }
  return ticket;
}

async function finish(
  body: { device_token: string; screen_id: string; server_time: string },
  sent: number,
): Promise<Paired> {
  const d = driftFrom(body.server_time, sent, Date.now());
  if (d !== null) {
    setDrift(d);
    await kvSet(KV.drift, d);
  }
  await kvSet(KV.screenId, body.screen_id);
  await kvSet(KV.token, body.device_token);
  await kvDel(KV.pairing);
  return { token: body.device_token, screenId: body.screen_id };
}

/** Appairage automatique par ticket (assistant Android) ; null = repli sur le code manuel. */
async function pairWithTicket(screens: Screens): Promise<Paired | null> {
  const t = await claimTicket();
  if (!t) return null;
  screens.show("loading");
  for (let i = 0; i < TICKET_TRIES; i++) {
    mark();
    const sent = Date.now();
    try {
      const res = await request("api/v1/player/pair", { method: "POST", body: { pairing_ticket: t, device: deviceInfo() } });
      if (res.status === 200) {
        ticket = null;
        return await finish((await res.json()) as { device_token: string; screen_id: string; server_time: string }, sent);
      }
      if (res.status !== 429 && res.status < 500) break; // refusé (expiré, déjà utilisé) : inutile d'insister
    } catch (e) {
      if (!(e instanceof NetworkError)) throw e;
    }
    await sleep(RETRY_MS);
  }
  ticket = null;
  return null;
}

/** Affiche le code et attend l'association par un administrateur ; retourne le jeton d'appareil. */
export async function pair(screens: Screens): Promise<Paired> {
  const auto = await pairWithTicket(screens);
  if (auto) return auto;
  for (;;) {
    mark();
    let st = (await kvGet<StoredPairing>(KV.pairing)) ?? null;
    st ??= await start();
    if (!st) {
      screens.show("loading");
      await sleep(RETRY_MS);
      continue;
    }
    screens.showPairing(st.code);
    for (;;) {
      mark();
      const sent = Date.now();
      let res: Response;
      try {
        res = await request(`api/v1/player/pairings/${st.pairing_id}`, { token: st.poll_secret });
      } catch (e) {
        if (e instanceof NetworkError) {
          await sleep(RETRY_MS);
          continue;
        }
        throw e;
      }
      if (res.status === 200) {
        return finish((await res.json()) as { device_token: string; screen_id: string; server_time: string }, sent);
      }
      if (res.status === 202) {
        await sleep(st.poll_interval_s * 1000);
        continue;
      }
      if (res.status === 429) {
        await sleep(RETRY_MS * 2);
        continue;
      }
      // 410 (expiré ou consommé), 404 : nouveau code.
      await kvDel(KV.pairing);
      break;
    }
  }
}
