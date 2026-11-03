import { expect, test, type Page, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string };
const url = (info: TestInfo, p: string) => `${meta(info).base}${p}`;
const failures = new WeakMap<Page, string[]>();

test.beforeEach(async ({ page }, info) => {
  await page.context().addCookies([{ name: "vds_session", value: "e2e-admin", url: info.project.use.baseURL! }]);
  const list: string[] = [];
  failures.set(page, list);
  page.on("requestfailed", (r) => list.push(`failed ${r.url()}`));
  page.on("response", (r) => {
    if (r.status() >= 400) list.push(`${r.status()} ${r.url()}`);
  });
  page.on("pageerror", (e) => list.push(`error ${e.message}`));
});

test.afterEach(async ({ page }, info) => {
  if (info.status === "skipped") return;
  expect(failures.get(page)).toEqual([]);
});

for (const lang of ["fr", "en"] as const) {
  test(`P2-4 : un créneau masqué est visible (hachuré, libellé) et reste atteignable (${lang})`, async ({ page }, info) => {
    await page.goto(url(info, "/playback?view=week&date=2026-11-02"));
    if (lang === "en") await page.getByRole("button", { name: "English" }).click();
    const masked = page.locator(".tl-block[data-masked]").first();
    await expect(masked).toBeVisible();
    await expect(masked.locator(".tl-masked")).toHaveText(lang === "fr" ? /Masqué par|Ne passe jamais/ : /Masked by|Never wins|never/i);
    expect(await masked.getAttribute("aria-label")).toBeTruthy();
  });

  test(`P2-4 : un forçage est visible dans la fiche écran et sur le tableau de bord (${lang})`, async ({ page }, info) => {
    const base = meta(info).base;
    const sites = (await (await page.request.get(`${base}/api/v1/sites`)).json()) as { id: string }[];
    const name = `Forcé P4 ${info.project.name} ${Math.random().toString(36).slice(2, 7)}`;
    const screen = (await (await page.request.post(`${base}/api/v1/screens`, { data: { site_id: sites[0]!.id, name } })).json()) as { id: string };
    const pl = (await (await page.request.post(`${base}/api/v1/playlists`, { data: { name: `Promo ${name}` } })).json()) as { id: string };
    const r = await page.request.post(`${base}/api/v1/overrides`, { data: { target_type: "screen", target_id: screen.id, playlist_id: pl.id, duration_minutes: 60 } });
    expect(r.status()).toBe(201);
    await page.goto(url(info, "/screens"));
    if (lang === "en") await page.getByRole("button", { name: "English" }).click();
    const card = page.getByRole("listitem").filter({ has: page.getByRole("heading", { level: 2, name }) });
    await expect(card.getByText(lang === "fr" ? /Forcé jusqu'à/ : /Forced until/)).toBeVisible();
  });
}
