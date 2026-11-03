import { expect, test } from "@playwright/test";
import type { Page, TestInfo } from "@playwright/test";

/** Mises en page multi-zones (document 1.9) contre le serveur simulé, sous `/` ET `/espace/affichage`. */
const meta = (info: TestInfo): { base: string; port: number } => info.project.metadata as { base: string; port: number };
const origin = (info: TestInfo): string => `http://127.0.0.1:${meta(info).port}`;
const playerUrl = (info: TestInfo): string => `${meta(info).base}/player/`;

async function ctl(info: TestInfo, path: string, body?: unknown): Promise<void> {
  await fetch(`${origin(info)}/__test/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

const zoneImg = (page: Page, zone: string) => page.locator(`[data-zone="${zone}"] vds-content-image:visible img`);
const srcOf = async (page: Page, zone: string): Promise<string | null> =>
  zoneImg(page, zone).first().getAttribute("src", { timeout: 1500 }).catch(() => null);

async function pair(page: Page, info: TestInfo): Promise<void> {
  await page.goto(playerUrl(info));
  const code = page.getByTestId("pairing-code");
  await expect(code).toHaveText(/^[A-Z0-9]{3}-[A-Z0-9]{3}$/);
  await ctl(info, "claim", { code: await code.innerText() });
}

const zone = (id: string, playlist: string, extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  id, x: 0, y: 0, w: 1, h: 1, playlist, ...extra,
});
const img = (id: string, media: string, ms = 60_000): Record<string, unknown> => ({ id, media, ms });

// eslint-disable-next-line no-empty-pattern -- Playwright impose la déstructuration
test.beforeEach(async ({}, info) => {
  await ctl(info, "reset", {});
});

for (const [name, viewport] of [
  ["paysage", { width: 1280, height: 720 }],
  ["portrait", { width: 720, height: 1280 }],
] as const) {
  test(`3 zones dont un calque, hors ligne, ${name} : géométrie, z et aucune information technique`, async ({ page, context }, info) => {
    await page.setViewportSize(viewport);
    await ctl(info, "set", {
      layout: {
        zones: [
          zone("main", "a", { z: 0, fit: "cover" }),
          zone("logo", "c", { x: 0.7, y: 0.05, w: 0.25, h: 0.2, z: 2 }),
          zone("bandeau", "b", { x: 0, y: 0.9, w: 1, h: 0.1, z: 1 }),
        ],
        playlists: { a: [img("a1", "red")], b: [img("b1", "blue")], c: [img("c1", "blue")] },
        background: { color: "#102030" },
      },
    });
    await pair(page, info);
    await expect(zoneImg(page, "bandeau")).toBeVisible({ timeout: 20_000 });
    await expect(zoneImg(page, "main")).toBeVisible();
    await expect(zoneImg(page, "logo")).toBeVisible();
    await ctl(info, "set", { down: true });
    await context.setOffline(true);
    await page.reload();
    await expect(zoneImg(page, "bandeau")).toBeVisible({ timeout: 20_000 });
    const box = await page.locator('[data-zone="bandeau"]').boundingBox();
    expect(box!.y).toBeCloseTo(viewport.height * 0.9, 0);
    expect(box!.height).toBeCloseTo(viewport.height * 0.1, 0);
    expect(box!.width).toBeCloseTo(viewport.width, 0);
    const z = await page.evaluate(() =>
      ["main", "bandeau", "logo"].map((id) => Number(getComputedStyle(document.querySelector(`[data-zone="${id}"]`)!).zIndex)),
    );
    expect(z).toEqual([0, 1, 2]);
    expect((await page.locator("#app").innerText()).trim()).toBe("");
    await context.setOffline(false);
    await ctl(info, "set", { down: false });
  });
}

test("forçage de zone hors ligne : début et fin sans redémarrer les autres zones", async ({ page, context }, info) => {
  const now = Date.now();
  await ctl(info, "set", {
    layout: {
      zones: [
        zone("main", "a"),
        zone("bandeau", "b", {
          y: 0.9, h: 0.1, z: 1,
          overrides: [{ override_id: "ov1", playlist: "f", from: new Date(now + 9000).toISOString(), until: new Date(now + 17_000).toISOString() }],
        }),
      ],
      playlists: { a: [img("a1", "red")], b: [img("b1", "red")], f: [img("f1", "blue")] },
    },
  });
  await pair(page, info);
  await expect(zoneImg(page, "bandeau")).toBeVisible({ timeout: 20_000 });
  await ctl(info, "set", { down: true });
  await context.setOffline(true);
  await page.evaluate(() => {
    document.querySelector('[data-zone="main"] vds-content-image:not([hidden]) img')!.setAttribute("data-mark", "kept");
  });
  expect(await srcOf(page, "bandeau")).toMatch(/media\/red\//);
  await expect.poll(() => srcOf(page, "bandeau"), { timeout: 20_000 }).toMatch(/media\/blue\//);
  await expect(page.locator('[data-zone="main"] img[data-mark="kept"]')).toHaveCount(1);
  await expect.poll(() => srcOf(page, "bandeau"), { timeout: 20_000 }).toMatch(/media\/red\//);
  await expect(page.locator('[data-zone="main"] img[data-mark="kept"]')).toHaveCount(1);
  await context.setOffline(false);
  await ctl(info, "set", { down: false });
});

test("fenêtre de zone : apparition à from, retrait à until, hors ligne", async ({ page, context }, info) => {
  const now = Date.now();
  await ctl(info, "set", {
    layout: {
      zones: [
        zone("main", "a"),
        zone("pop", "b", { w: 0.3, h: 0.3, z: 1, from: new Date(now + 8000).toISOString(), until: new Date(now + 15_000).toISOString() }),
      ],
      playlists: { a: [img("a1", "red")], b: [img("b1", "blue")] },
    },
  });
  await pair(page, info);
  await expect(zoneImg(page, "main")).toBeVisible({ timeout: 20_000 });
  await ctl(info, "set", { down: true });
  await context.setOffline(true);
  await expect(zoneImg(page, "pop")).toBeVisible({ timeout: 20_000 });
  await expect(zoneImg(page, "pop")).toHaveCount(0, { timeout: 20_000 });
  await expect(zoneImg(page, "main")).toBeVisible();
  await context.setOffline(false);
  await ctl(info, "set", { down: false });
});

test("une seule vidéo à la fois entre deux zones (max_exclusive_elements = 1)", async ({ page }, info) => {
  await ctl(info, "set", {
    layout: {
      zones: [zone("main", "a"), zone("side", "b", { w: 0.4, h: 0.4, z: 1 })],
      playlists: { a: [{ id: "va", media: "clip", ms: null }], b: [{ id: "vb", media: "clip", ms: null }] },
    },
  });
  await pair(page, info);
  await expect(page.locator("vds-content-video video").first()).toBeVisible({ timeout: 20_000 });
  let max = 0;
  const end = Date.now() + 6000;
  while (Date.now() < end) {
    max = Math.max(max, await page.evaluate(() => [...document.querySelectorAll("video")].filter((v) => v.getAttribute("src")).length));
    await page.waitForTimeout(100);
  }
  expect(max).toBe(1);
  expect((await page.locator("#app").innerText()).trim()).toBe("");
});

test("document 1.8 (zone main seule, aucun champ 1.9) : lecture normale", async ({ page }, info) => {
  await pair(page, info);
  await expect(page.locator("vds-content-image:visible img")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('[data-zone="main"]')).toHaveCount(1);
});
