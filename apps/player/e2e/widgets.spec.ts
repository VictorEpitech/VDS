import { expect, test } from "@playwright/test";
import type { Page, TestInfo } from "@playwright/test";

/**
 * Widgets de données (météo, RSS) contre le serveur simulé, sous `/` ET `/espace/affichage` :
 * paysage, portrait, bandeau, liste, défilement, dernier état connu hors ligne, saut au-delà de `max_age_s`,
 * aucune information technique au public, `stop()` sans fuite sur 200 cycles.
 */
const meta = (info: TestInfo): { base: string; port: number } => info.project.metadata as { base: string; port: number };
const origin = (info: TestInfo): string => `http://127.0.0.1:${meta(info).port}`;
const playerUrl = (info: TestInfo): string => `${meta(info).base}/player/`;

async function ctl(info: TestInfo, path: string, body?: unknown): Promise<Record<string, unknown>> {
  const res = await fetch(`${origin(info)}/__test/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return (await res.json()) as Record<string, unknown>;
}

async function pair(page: Page, info: TestInfo): Promise<void> {
  await page.goto(playerUrl(info));
  const code = page.getByTestId("pairing-code");
  await expect(code).toHaveText(/^[A-Z0-9]{3}-[A-Z0-9]{3}$/);
  await ctl(info, "claim", { code: await code.innerText() });
}

const WID = "a".repeat(32);
const RID = "b".repeat(32);
const weatherData = {
  now: { temp_c: 14.3, symbol: "partlycloudy_day" },
  days: [
    { date: "2026-11-03", min_c: 8, max_c: 15, symbol: "rain" },
    { date: "2026-11-04", min_c: 6, max_c: 12, symbol: "cloudy" },
    { date: "2026-11-05", min_c: 5, max_c: 11, symbol: "clearsky_day" },
  ],
  attribution: "MET Norway",
};
const rssData = {
  title: "Journal",
  items: [
    { title: "Premier titre <b>gras</b>", summary: "Résumé <img src=x onerror=window.__xss=1> un", published: "2026-11-03T08:00:00+00:00" },
    { title: "Deuxième titre", summary: "Résumé deux", published: "2026-11-02T08:00:00+00:00" },
    { title: "Troisième titre", summary: "Résumé trois", published: null },
  ],
};
const nowIso = (): string => new Date().toISOString();
const weatherItem = (props: Record<string, unknown> = {}, ms = 60_000) => ({
  id: "w1", kind: "weather", feed: WID, ms, props: { label: "Paris", days: 3, units: "metric", max_age_s: 21600, ...props },
});
const rssItem = (props: Record<string, unknown> = {}, ms: number | null = 60_000) => ({
  id: "r1", kind: "rss", feed: RID, ms, props: { display: "list", max_items: 3, item_duration_s: 4, show_date: true, max_age_s: 86400, ...props },
});
const single = (item: Record<string, unknown>, zone: Record<string, unknown> = {}) => ({
  layout: { zones: [{ id: "main", x: 0, y: 0, w: 1, h: 1, playlist: "a", ...zone }], playlists: { a: [item] }, background: { color: "#102030" } },
});

// eslint-disable-next-line no-empty-pattern -- Playwright impose la déstructuration
test.beforeEach(async ({}, info) => {
  await ctl(info, "reset", {});
});

const weather = (page: Page) => page.locator("vds-content-weather:visible");
const rss = (page: Page) => page.locator("vds-content-rss:visible");

for (const [name, viewport, zone, layout] of [
  ["paysage", { width: 1280, height: 720 }, {}, "landscape"],
  ["portrait", { width: 720, height: 1280 }, {}, "portrait"],
  ["bandeau", { width: 1280, height: 720 }, { y: 0.88, h: 0.12 }, "banner"],
] as const) {
  test(`météo ${name} : données, mention MET Norway, rien qui déborde`, async ({ page }, info) => {
    await page.setViewportSize(viewport);
    await ctl(info, "set", { ...single(weatherItem(), zone), feeds: { [WID]: { fetched_at: nowIso(), data: weatherData } } });
    await pair(page, info);
    await expect(weather(page)).toBeVisible({ timeout: 20_000 });
    await expect(weather(page)).toHaveAttribute("data-layout", layout);
    const txt = await weather(page).innerText();
    expect(txt).toContain("14°");
    expect(txt).toContain("Paris");
    expect(txt).toContain("Données MET Norway");
    await expect.poll(() => weather(page).locator("img").first().evaluate((i: HTMLImageElement) => i.naturalWidth)).toBeGreaterThan(0);
    const fits = await weather(page).evaluate((el) => {
      const box = el.getBoundingClientRect();
      return Array.from(el.querySelectorAll("*")).every((n) => {
        const r = n.getBoundingClientRect();
        return r.left >= box.left - 1 && r.right <= box.right + 1 && r.top >= box.top - 1 && r.bottom <= box.bottom + 1;
      });
    });
    expect(fits).toBe(true);
    await page.screenshot({ path: `test-results/weather-${name}.png` });
  });
}

test("météo : unités impériales", async ({ page }, info) => {
  await ctl(info, "set", { ...single(weatherItem({ units: "imperial", days: 1 })), feeds: { [WID]: { fetched_at: nowIso(), data: weatherData } } });
  await pair(page, info);
  await expect(weather(page)).toContainText("58°", { timeout: 20_000 }); // 14,3 °C
});

test("météo hors ligne : dernier état connu après coupure et rechargement (IndexedDB)", async ({ page, context }, info) => {
  await ctl(info, "set", { ...single(weatherItem()), feeds: { [WID]: { fetched_at: nowIso(), data: weatherData } } });
  await pair(page, info);
  await expect(weather(page)).toContainText("14°", { timeout: 20_000 });
  expect((await ctl(info, "state")).feedHits as number).toBeGreaterThan(0);
  await ctl(info, "set", { down: true });
  await context.setOffline(true);
  await page.reload();
  await expect(weather(page)).toContainText("14°", { timeout: 20_000 });
  await expect(weather(page)).toContainText("Données MET Norway");
  await context.setOffline(false);
  await ctl(info, "set", { down: false });
});

test("flux préchargé à la synchro : en cache sans avoir été joué", async ({ page }, info) => {
  // Le widget n'est pas le premier de la liste : son flux doit être en cache avant son tour.
  await ctl(info, "set", {
    layout: { zones: [{ id: "main", x: 0, y: 0, w: 1, h: 1, playlist: "a" }], playlists: { a: [{ id: "i1", media: "red", ms: 60_000 }, weatherItem()] } },
    feeds: { [WID]: { fetched_at: nowIso(), data: weatherData } },
  });
  await pair(page, info);
  await expect(page.locator("vds-content-image:visible")).toBeVisible({ timeout: 20_000 });
  await expect
    .poll(
      () =>
        page.evaluate(
          () =>
            new Promise<number>((res) => {
              const r = indexedDB.open("vds-player");
              r.onsuccess = () => {
                const q = r.result.transaction("feeds").objectStore("feeds").count();
                q.onsuccess = () => res(q.result);
              };
            }),
        ),
      { timeout: 15_000 },
    )
    .toBe(1);
});

test("météo au-delà de max_age_s : élément sauté, rien d'affiché au public (heure simulée)", async ({ page }, info) => {
  const t0 = "2026-11-03T10:00:00Z";
  await ctl(info, "set", {
    clock: t0,
    ...single(weatherItem({ max_age_s: 600 }, 3000)),
    feeds: { [WID]: { fetched_at: "2026-11-03T09:59:00Z", data: weatherData } },
  });
  await page.clock.install({ time: new Date(t0) });
  await pair(page, info);
  await expect(weather(page)).toContainText("14°", { timeout: 20_000 });
  await ctl(info, "set", { down: true }); // l'horloge simulée de la page ne doit pas être recalée par le serveur
  await page.clock.fastForward("12:00"); // 10:12 : donnée vieille de 13 minutes > 600 s
  await expect(weather(page)).toHaveCount(0, { timeout: 20_000 });
  await page.clock.runFor(8000);
  await expect(weather(page)).toHaveCount(0);
  expect((await page.locator("#app").innerText()).trim()).toBe("");
  await ctl(info, "set", { down: false });
});

test("flux jamais reçu : élément sauté, écran sans texte", async ({ page }, info) => {
  await ctl(info, "set", { ...single(weatherItem()), feeds: {} });
  await pair(page, info);
  await expect.poll(async () => (await ctl(info, "state")).feedHits as number, { timeout: 20_000 }).toBeGreaterThan(0);
  await page.waitForTimeout(1500);
  await expect(weather(page)).toHaveCount(0);
  expect((await page.locator("#app").innerText()).trim()).toBe("");
});

test("RSS liste : titres, dates, texte brut, aucun balisage interprété", async ({ page }, info) => {
  await ctl(info, "set", { ...single(rssItem()), feeds: { [RID]: { fetched_at: nowIso(), data: rssData } } });
  await pair(page, info);
  await expect(rss(page)).toBeVisible({ timeout: 20_000 });
  const txt = await rss(page).innerText();
  expect(txt).toContain("Premier titre <b>gras</b>"); // le balisage reste du texte
  expect(await rss(page).locator("b, img").count()).toBe(0);
  expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined();
  expect(txt).toMatch(/2026/);
});

test("RSS liste, plus d'entrées que la zone n'en tient : pagination des entrées", async ({ page }, info) => {
  const many = { items: Array.from({ length: 10 }, (_, i) => ({ title: `Titre numéro ${i + 1}`, summary: "Un résumé", published: null })) };
  await ctl(info, "set", { ...single(rssItem({ item_duration_s: 2, max_items: 10 })), feeds: { [RID]: { fetched_at: nowIso(), data: many } } });
  await pair(page, info);
  await expect(rss(page)).toBeVisible({ timeout: 20_000 });
  const first = await rss(page).innerText();
  await expect.poll(() => rss(page).innerText(), { timeout: 15_000 }).not.toBe(first);
});

test("RSS défilement : les titres défilent en continu", async ({ page }, info) => {
  await ctl(info, "set", { ...single(rssItem({ display: "ticker" }, null), { y: 0.88, h: 0.12 }), feeds: { [RID]: { fetched_at: nowIso(), data: rssData } } });
  await pair(page, info);
  await expect(rss(page)).toBeVisible({ timeout: 20_000 });
  expect(await rss(page).innerText()).toContain("Deuxième titre");
  const left = (): Promise<number> => rss(page).evaluate((el) => (el.firstElementChild!.firstElementChild as HTMLElement).getBoundingClientRect().left);
  const t0 = await left();
  await page.waitForTimeout(800);
  expect(await left()).toBeLessThan(t0);
});

test("RSS hors ligne : dernier état connu puis saut au-delà de max_age_s", async ({ page, context }, info) => {
  const t0 = "2026-11-03T10:00:00Z";
  await ctl(info, "set", { clock: t0, ...single(rssItem({ max_age_s: 3600 }, 3000)), feeds: { [RID]: { fetched_at: "2026-11-03T09:50:00Z", data: rssData } } });
  await page.clock.install({ time: new Date(t0) });
  await pair(page, info);
  await expect(rss(page)).toContainText("Deuxième titre", { timeout: 20_000 });
  await ctl(info, "set", { down: true });
  await context.setOffline(true);
  await page.clock.fastForward("20:00"); // dernier état connu (30 min) : toujours affiché
  await expect(rss(page)).toContainText("Deuxième titre", { timeout: 20_000 });
  await page.clock.fastForward("45:00"); // 65 min depuis la récupération > 3600 s
  await expect(rss(page)).toHaveCount(0, { timeout: 20_000 });
  expect((await page.locator("#app").innerText()).trim()).toBe("");
  await context.setOffline(false);
  await ctl(info, "set", { down: false });
});

test("stop() sans fuite : 200 cycles météo et RSS (nœuds DOM, tas JS)", async ({ page }, info) => {
  await page.goto(playerUrl(info)); // contexte same-origin pour importer les modules
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("HeapProfiler.enable");
  await cdp.send("Performance.enable");
  const heap = async (): Promise<number> => {
    await cdp.send("HeapProfiler.collectGarbage");
    const m = (await cdp.send("Performance.getMetrics")).metrics.find((x) => x.name === "JSHeapUsedSize");
    return m!.value;
  };
  const run = (): Promise<void> =>
    page.evaluate(
      async ({ base, wid, rid, w, r }) => {
        await import(/* @vite-ignore */ `${base}/modules/content.weather/assets/player.js`);
        await import(/* @vite-ignore */ `${base}/modules/content.rss/assets/player.js`);
        const feeds: Record<string, unknown> = { [wid]: w, [rid]: r };
        const ctx = {
          locale: "fr", orientation: "landscape", zone: { width: 640, height: 360 }, resolveUrl: (x: string) => x,
          now: () => Date.now(),
          feed: async (id: string) => ({ feedId: id, fetchedAt: new Date().toISOString(), data: feeds[id] }),
        };
        const host = document.createElement("div");
        host.style.cssText = "position:fixed;left:0;top:0;width:640px;height:360px";
        document.body.append(host);
        type El = HTMLElement & { configure(p: unknown, c: unknown): void; prepare(): Promise<void>; start(): void; stop(): void };
        for (let i = 0; i < 200; i++) {
          for (const [tag, props] of [
            ["vds-content-weather", { label: "Paris", days: 3, feed_id: wid }],
            ["vds-content-rss", { display: i % 2 ? "ticker" : "list", max_items: 3, item_duration_s: 2, feed_id: rid }],
          ] as const) {
            const el = document.createElement(tag) as El;
            el.configure(props, ctx);
            host.append(el);
            await el.prepare();
            el.start();
            el.stop();
            el.remove();
          }
        }
        host.remove();
      },
      { base: `${meta(info).base}`, wid: WID, rid: RID, w: weatherData, r: rssData },
    );
  await run(); // échauffement (compilation, caches)
  const before = await heap();
  const nodesBefore = await page.evaluate(() => document.getElementsByTagName("*").length - (document.getElementById("app")?.getElementsByTagName("*").length ?? 0));
  await run();
  const after = await heap();
  expect(await page.evaluate(() => document.getElementsByTagName("*").length - (document.getElementById("app")?.getElementsByTagName("*").length ?? 0))).toBe(nodesBefore);
  expect(after - before).toBeLessThan(3 * 1024 * 1024);
});
