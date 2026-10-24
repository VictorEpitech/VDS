import { expect, test } from "@playwright/test";
import type { Page, TestInfo } from "@playwright/test";

/** Parcours du player contre le serveur simulé, sous `/` ET `/espace/affichage` (un projet par préfixe). */
const meta = (info: TestInfo): { base: string; port: number } => info.project.metadata as { base: string; port: number };
const origin = (info: TestInfo): string => `http://127.0.0.1:${meta(info).port}`;

interface MockState {
  down: boolean;
  heartbeats: { revision: string | null; cache: { missing: string[] } }[];
  mediaHits: Record<string, number>;
  hits: number;
  rejected: number;
  ws: { upgrades: number; open: number; auths: { type: string; token: string | null; url: string }[] };
}

async function ctl(info: TestInfo, path: string, body?: unknown): Promise<MockState> {
  const res = await fetch(`${origin(info)}/__test/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return (await res.json()) as MockState;
}

const playerUrl = (info: TestInfo): string => `${meta(info).base}/player/`;
const img = (page: Page) => page.locator("vds-content-image:visible img");

async function pairAndPlay(page: Page, info: TestInfo): Promise<void> {
  await page.goto(playerUrl(info));
  const code = page.getByTestId("pairing-code");
  await expect(code).toHaveText(/^[A-Z0-9]{3}-[A-Z0-9]{3}$/);
  await ctl(info, "claim", { code: await code.innerText() });
  await expect(img(page)).toBeVisible({ timeout: 20_000 });
}

/** Médias distincts vus à l'écran (red / blue) pendant `ms`. */
async function seenMedia(page: Page, ms: number): Promise<Set<string>> {
  const seen = new Set<string>();
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const src = await img(page).first().getAttribute("src", { timeout: 2000 }).catch(() => null);
    const m = src && /media\/([a-z]+)\/original/.exec(src);
    if (m) seen.add(m[1]!);
    await page.waitForTimeout(250);
  }
  return seen;
}

// eslint-disable-next-line no-empty-pattern -- Playwright impose la déstructuration
test.beforeEach(async ({}, info) => {
  await ctl(info, "reset", {});
});

test("appairage puis lecture, sans information technique pour le public", async ({ page }, info) => {
  await page.goto(playerUrl(info));
  await expect(page.getByTestId("pairing-code")).toBeVisible();
  const code = await page.getByTestId("pairing-code").innerText();
  await ctl(info, "claim", { code });
  await expect(img(page)).toBeVisible({ timeout: 20_000 });
  // Lecture : l'écran ne montre que le contenu.
  await expect(page.locator(".system")).toBeHidden();
  expect((await page.locator("#app").innerText()).trim()).toBe("");
  // Premier heartbeat remonté avec la révision active et l'état du cache.
  await expect
    .poll(async () => (await ctl(info, "state")).heartbeats.some((h: { revision: string | null }) => h.revision), { timeout: 15_000 })
    .toBeTruthy();
  const hb = (await ctl(info, "state")).heartbeats.find((h: { revision: string | null }) => h.revision);
  expect(hb?.revision).toMatch(/^sha256:/);
  expect(hb?.cache.missing).toEqual([]);
  // Le SW contrôle la page et le code d'appairage est en mono (donnée technique).
  expect(await page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
});

test("les images alternent, puis la lecture continue hors ligne, même après rechargement", async ({ page, context }, info) => {
  await pairAndPlay(page, info);
  expect([...(await seenMedia(page, 6000))].sort()).toEqual(["blue", "red"]);
  // Pas d'accumulation d'éléments : courant + suivant préparé au plus.
  expect(await page.locator(".zone > *").count()).toBeLessThanOrEqual(2);
  const before = (await ctl(info, "state")).mediaHits;

  // Coupure réseau : le serveur ne répond plus et le navigateur est hors ligne.
  await ctl(info, "set", { down: true });
  await context.setOffline(true);
  expect([...(await seenMedia(page, 6000))].sort()).toEqual(["blue", "red"]);

  // Redémarrage du player (coupure de courant) hors ligne : application, config et programmation viennent du cache.
  await page.reload();
  await expect(img(page)).toBeVisible({ timeout: 20_000 });
  expect([...(await seenMedia(page, 6000))].sort()).toEqual(["blue", "red"]);
  // Aucun média n'a été relu sur le réseau pendant ce temps.
  expect((await ctl(info, "state").catch(() => ({ mediaHits: before }))).mediaHits ?? before).toEqual(before);
  await context.setOffline(false);
  await ctl(info, "set", { down: false });
});

test("le Service Worker sert les Range depuis le cache et saute un média absent", async ({ page }, info) => {
  await pairAndPlay(page, info);
  const url = `${meta(info).base}/media/red/original`;
  const r = await page.evaluate(async (u) => {
    const res = await fetch(u, { headers: { Range: "bytes=0-9" } });
    const full = await fetch(u);
    return { status: res.status, range: res.headers.get("content-range"), len: (await res.arrayBuffer()).byteLength, fullStatus: full.status };
  }, url);
  expect(r.status).toBe(206);
  expect(r.range).toMatch(/^bytes 0-9\/\d+$/);
  expect(r.len).toBe(10);
  expect(r.fullStatus).toBe(200);
  // Absent du cache : 404 immédiat sans solliciter le serveur.
  const hits = (await ctl(info, "state")).hits;
  const missing = await page.evaluate(async (u) => (await fetch(u)).status, `${meta(info).base}/media/green/original`);
  expect(missing).toBe(404);
  expect((await ctl(info, "state")).hits).toBe(hits);
});

test("une vidéo est lue depuis le cache (Range) et la lecture enchaîne", async ({ page }, info) => {
  await ctl(info, "set", { items: [{ id: "it_clip", media: "clip", ms: null }, { id: "it_red", media: "red", ms: 1500 }] });
  await page.goto(playerUrl(info));
  const code = page.getByTestId("pairing-code");
  await expect(code).toBeVisible();
  await ctl(info, "claim", { code: await code.innerText() });
  const video = page.locator("vds-content-video video");
  await expect(video).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 10_000 }).toBeGreaterThan(0);
  // Fin naturelle de la vidéo (durée inconnue) puis image suivante.
  await expect(img(page)).toBeVisible({ timeout: 20_000 });
});

test("l'extinction et le rallumage programmés s'appliquent sans serveur", async ({ page }, info) => {
  const t0 = "2026-10-24T09:59:40Z";
  await ctl(info, "set", { clock: t0, slots: [[0, 10, "play"], [10, 10.5, "off"], [10.5, 48, "play"]] });
  await page.clock.install({ time: new Date(t0) });
  await pairAndPlay(page, info);
  await ctl(info, "set", { down: true }); // plus aucun serveur
  await page.clock.fastForward("00:40"); // 10:00:20 : plage éteinte
  await expect(page.locator(".system-black")).toBeVisible({ timeout: 10_000 });
  await expect(img(page)).toHaveCount(0);
  await page.clock.fastForward("30:00"); // 10:30:20 : rallumage
  await expect(img(page)).toBeVisible({ timeout: 15_000 });
  await ctl(info, "set", { down: false });
});

test("401 device_revoked : jeton, programmation et cache effacés, retour à l'appairage", async ({ page }, info) => {
  await pairAndPlay(page, info);
  await ctl(info, "set", { revoked: true });
  await expect(page.getByTestId("pairing-code")).toBeVisible({ timeout: 20_000 });
  await expect(img(page)).toHaveCount(0);
  const state = await page.evaluate(async () => {
    const db: IDBDatabase = await new Promise((res, rej) => {
      const r = indexedDB.open("vds-player");
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
    const get = (store: string, key: string) =>
      new Promise((res) => {
        const q = db.transaction(store).objectStore(store).get(key);
        q.onsuccess = () => res(q.result ?? null);
      });
    const token = await get("kv", "token");
    const schedule = await get("schedule", "active");
    const media = await caches.open("vds-media-v1");
    return { token, schedule, cached: (await media.keys()).length };
  });
  expect(state).toEqual({ token: null, schedule: null, cached: 0 });
});

test("le player redémarre sur le code d'appairage conservé tant qu'il n'est pas réclamé", async ({ page }, info) => {
  await page.goto(playerUrl(info));
  const code = await page.getByTestId("pairing-code").innerText();
  await page.reload();
  await expect(page.getByTestId("pairing-code")).toHaveText(code);
});

test("jamais deux <video> chargées en même temps (un seul décodeur matériel sur TCL)", async ({ page }, info) => {
  await ctl(info, "set", { items: [{ id: "it_a", media: "clip", ms: null }, { id: "it_b", media: "clip", ms: null }] });
  await page.goto(playerUrl(info));
  const code = page.getByTestId("pairing-code");
  await expect(code).toBeVisible();
  await ctl(info, "claim", { code: await code.innerText() });
  await expect(page.locator("vds-content-video video").first()).toBeVisible({ timeout: 20_000 });
  let max = 0;
  const end = Date.now() + 8000;
  while (Date.now() < end) {
    max = Math.max(max, await page.evaluate(() => [...document.querySelectorAll("video")].filter((v) => v.getAttribute("src")).length));
    await page.waitForTimeout(100);
  }
  expect(max).toBe(1);
});

test("WS : schedule.changed poussé met à jour la lecture sans attendre le sondage ; le jeton n'est pas dans l'URL", async ({ page }, info) => {
  await ctl(info, "set", { pollS: 300, items: [{ id: "it_red", media: "red", ms: 60_000 }] });
  await pairAndPlay(page, info);
  await expect.poll(async () => (await ctl(info, "state")).ws.open, { timeout: 10_000 }).toBe(1);
  const st = await ctl(info, "state");
  expect(st.ws.auths[0]!.type).toBe("auth");
  expect(st.ws.auths[0]!.token).toMatch(/^vds_dev_/);
  expect(st.ws.auths[0]!.url).not.toContain("vds_dev_");
  expect(st.ws.auths[0]!.url).toBe(`${meta(info).base}/ws/player`);
  expect(await img(page).getAttribute("src")).toContain("/red/");
  await ctl(info, "set", { items: [{ id: "it_blue", media: "blue", ms: 60_000 }] });
  await ctl(info, "ws", { action: "push" });
  await expect(img(page)).toHaveAttribute("src", /\/blue\//, { timeout: 10_000 });
});

test("WS bloqué (proxy) : repli sur le sondage HTTP, la lecture suit", async ({ page }, info) => {
  await ctl(info, "set", { wsBlocked: true, pollS: 2, items: [{ id: "it_red", media: "red", ms: 60_000 }] });
  await pairAndPlay(page, info);
  expect((await ctl(info, "state")).ws.open).toBe(0);
  await ctl(info, "set", { items: [{ id: "it_blue", media: "blue", ms: 60_000 }] });
  await expect(img(page)).toHaveAttribute("src", /\/blue\//, { timeout: 20_000 });
});

test("WS coupé : le player continue de jouer et se reconnecte", async ({ page }, info) => {
  await ctl(info, "set", { pollS: 300, items: [{ id: "it_red", media: "red", ms: 60_000 }] });
  await pairAndPlay(page, info);
  await expect.poll(async () => (await ctl(info, "state")).ws.open, { timeout: 10_000 }).toBe(1);
  await ctl(info, "ws", { action: "drop" });
  await expect(img(page)).toBeVisible();
  await expect.poll(async () => (await ctl(info, "state")).ws.open, { timeout: 20_000 }).toBe(1);
});

test("WS : 4410 efface l'état et ramène à l'appairage", async ({ page }, info) => {
  await ctl(info, "set", { pollS: 300 });
  await pairAndPlay(page, info);
  await expect.poll(async () => (await ctl(info, "state")).ws.open, { timeout: 10_000 }).toBe(1);
  await ctl(info, "ws", { action: "close", code: 4410 });
  await expect(page.getByTestId("pairing-code")).toBeVisible({ timeout: 20_000 });
});

test("plan d'alimentation : écran noir à l'heure et rallumage, sans serveur", async ({ page }, info) => {
  const t0 = "2026-10-24T07:59:40Z";
  // Lecture toute la journée ; le plan éteint de 08:00 à 18:00 (jour férié simulé).
  await ctl(info, "set", { clock: t0, power: [[0, 8, "on"], [8, 18, "off"], [18, 48, "on"]] });
  await page.clock.install({ time: new Date(t0) });
  await pairAndPlay(page, info);
  await ctl(info, "set", { down: true });
  await page.clock.fastForward("00:40"); // 08:00:20
  await expect(page.locator(".system-black")).toBeVisible({ timeout: 10_000 });
  await expect(img(page)).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".system-black")).toBeVisible({ timeout: 15_000 }); // reste éteint après rechargement
  await page.clock.fastForward("10:00:00"); // 18:00:20
  await expect(img(page)).toBeVisible({ timeout: 15_000 });
  await ctl(info, "set", { down: false });
});

test("IndexedDB absent : appairage et lecture quand même (repli mémoire)", async ({ page }, info) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, "indexedDB", { value: undefined, configurable: true });
  });
  await pairAndPlay(page, info);
});

test("IndexedDB muet : pas de spinner infini", async ({ page }, info) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, "indexedDB", { value: { open: () => ({}) }, configurable: true });
  });
  await pairAndPlay(page, info);
});

// --- Kiosque piloté depuis l'admin (K2) : le player applique la valeur servie, jamais d'information au public ---

/** Une touche est-elle neutralisée par la page (kiosque verrouillé) ? */
const keyBlocked = (page: Page): Promise<boolean | null> =>
  page
    .evaluate(() => {
      const ev = new KeyboardEvent("keydown", { key: "F5", cancelable: true, bubbles: true });
      window.dispatchEvent(ev);
      return ev.defaultPrevented;
    })
    .catch(() => null); // le watchdog peut recharger la page après un saut d'horloge : on réessaie

const lastKiosk = async (info: TestInfo): Promise<unknown> => {
  const hb = (await ctl(info, "state")).heartbeats as { kiosk?: unknown }[];
  return hb.at(-1)?.kiosk;
};

test("kiosque verrouillé par défaut (champ absent) : rien n'est affiché, le heartbeat le dit", async ({ page }, info) => {
  await pairAndPlay(page, info);
  expect(await keyBlocked(page)).toBe(true);
  await expect.poll(() => lastKiosk(info), { timeout: 15_000 }).toEqual({ mode: "locked", enforcement: "web" });
  const text = (await page.locator("body").innerText()).toLowerCase();
  expect(text).not.toMatch(/kiosk|maintenance|d[ée]verrouill|unlock/);
});

test("maintenance temporaire : déverrouillée, puis reverrouillée seule à l'échéance, serveur coupé", async ({ page }, info) => {
  const t0 = "2026-10-24T09:00:00Z";
  await ctl(info, "set", { clock: t0, kiosk: { mode: "maintenance", until: "2026-10-24T09:10:00+00:00" } });
  await page.clock.install({ time: new Date(t0) });
  await pairAndPlay(page, info);
  await expect.poll(() => keyBlocked(page)).toBe(false);
  await expect.poll(() => lastKiosk(info), { timeout: 15_000 }).toEqual({ mode: "maintenance", enforcement: "web" });
  expect(await page.locator(".system:visible").count()).toBe(0); // aucun bandeau ni compte à rebours
  await ctl(info, "set", { down: true });
  await page.clock.fastForward("11:00"); // 09:11 : maintenance expirée, aucun serveur
  await expect.poll(() => keyBlocked(page), { timeout: 10_000 }).toBe(true);
  await expect(img(page)).toBeVisible(); // la lecture continue
  await ctl(info, "set", { down: false });
});

test("maintenance expirée pendant que l'écran est hors ligne et éteint : verrouillé au redémarrage", async ({ page, context }, info) => {
  const t0 = "2026-10-24T09:00:00Z";
  await ctl(info, "set", { clock: t0, kiosk: { mode: "maintenance", until: "2026-10-24T09:10:00+00:00" } });
  await page.clock.install({ time: new Date(t0) });
  await pairAndPlay(page, info);
  await expect.poll(() => keyBlocked(page)).toBe(false);
  await ctl(info, "set", { down: true });
  await context.setOffline(true);
  await page.clock.fastForward("30:00"); // 09:30 ; le watchdog recharge la page (boucle figée par le saut)
  await page.reload().catch(() => {});
  await expect(img(page)).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => keyBlocked(page)).toBe(true);
  await context.setOffline(false);
  await ctl(info, "set", { down: false });
});

test("unlocked persisté : reste déverrouillé après redémarrage hors ligne ; le serveur reverrouille ensuite", async ({ page, context }, info) => {
  await ctl(info, "set", { kiosk: { mode: "unlocked" } });
  await pairAndPlay(page, info);
  await expect.poll(() => keyBlocked(page)).toBe(false);
  await ctl(info, "set", { down: true });
  await context.setOffline(true);
  await page.reload();
  await expect(img(page)).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => keyBlocked(page)).toBe(false);
  await context.setOffline(false);
  await ctl(info, "set", { down: false, kiosk: { mode: "locked" } });
  await expect.poll(() => keyBlocked(page), { timeout: 20_000 }).toBe(true);
});

test("l'ancienne commande maintenance {code} est ignorée : aucun écran de diagnostic", async ({ page }, info) => {
  await pairAndPlay(page, info);
  await page.route("**/api/v1/player/heartbeat", async (route) => {
    const res = await route.fetch();
    const body = (await res.json()) as Record<string, unknown>;
    await route.fulfill({ response: res, json: { ...body, commands: [{ type: "maintenance", code: "123456" }] } });
  });
  await page.waitForTimeout(5000);
  await expect(page.getByText("123456")).toHaveCount(0);
  await expect(img(page)).toBeVisible();
});

/** Faux pont Android : `claimKiosk` remis une seule fois, réponses pilotées par le test. */
async function installFakeNative(page: Page, reply: "echo" | "stuck-maintenance"): Promise<void> {
  await page.addInitScript((mode) => {
    const w = window as unknown as { __kioskCalls: unknown[][]; VdsNative: unknown };
    w.__kioskCalls = [];
    let claimed = false;
    w.VdsNative = {
      ping() {},
      reload() {},
      info: () => ({ platform: "android", model: "t", app_version: "0", os_version: "0" }),
      claimKiosk() {
        if (claimed) return null;
        claimed = true;
        return (m: string, r: number, u: number) => {
          w.__kioskCalls.push([m, r, u]);
          return Promise.resolve(
            mode === "echo"
              ? JSON.stringify({ ok: true, enforcement: "lock_task", mode: m })
              : JSON.stringify({ ok: false, enforcement: "soft", mode: "maintenance" }),
          );
        };
      },
    };
  }, reply);
}

test("pont natif : claimKiosk retiré de la page, heartbeat = mode appliqué par le natif", async ({ page }, info) => {
  await ctl(info, "set", { kiosk: { mode: "unlocked" } });
  await installFakeNative(page, "echo");
  await pairAndPlay(page, info);
  await expect.poll(() => lastKiosk(info), { timeout: 15_000 }).toEqual({ mode: "unlocked", enforcement: "lock_task" });
  expect(await page.evaluate(() => typeof (window as unknown as { VdsNative: { claimKiosk?: unknown } }).VdsNative.claimKiosk)).toBe(
    "undefined",
  );
  await ctl(info, "set", { kiosk: { mode: "locked" } });
});

test("pont natif : verrouillage non confirmé (ok:false) = rapporté tel quel et renvoyé", async ({ page }, info) => {
  await installFakeNative(page, "stuck-maintenance");
  await pairAndPlay(page, info); // le serveur sert locked ; le natif reste en maintenance
  await expect.poll(() => lastKiosk(info), { timeout: 15_000 }).toEqual({ mode: "maintenance", enforcement: "soft" });
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { __kioskCalls: unknown[] }).__kioskCalls.length), { timeout: 15_000 })
    .toBeGreaterThanOrEqual(2);
});
