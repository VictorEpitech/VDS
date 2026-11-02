import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Request, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string };
const uniq = (info: TestInfo, label: string) => `${label} ${info.project.name} ${Math.random().toString(36).slice(2, 7)}`;
const failures = new WeakMap<Page, string[]>();
const requests = new WeakMap<Page, Request[]>();

test.beforeEach(async ({ page }, info) => {
  await page.context().addCookies([{ name: "vds_session", value: "e2e-admin", url: info.project.use.baseURL! }]);
  const list: string[] = [];
  const seen: Request[] = [];
  failures.set(page, list);
  requests.set(page, seen);
  page.on("request", (r) => seen.push(r));
  page.on("requestfailed", (r) => list.push(`failed ${r.url()}`));
  page.on("response", (r) => {
    if (r.status() >= 400) list.push(`${r.status()} ${r.url()}`);
  });
  page.on("pageerror", (e) => list.push(`error ${e.message}`));
});

test.afterEach(async ({ page }) => {
  expect(failures.get(page)).toEqual([]);
});

async function seed(page: Page, info: TestInfo) {
  const { base } = meta(info);
  const sites = (await (await page.request.get(`${base}/api/v1/sites`)).json()) as { id: string }[];
  const screenName = uniq(info, "Forcé");
  const playlistName = uniq(info, "Promo");
  const s = await page.request.post(`${base}/api/v1/screens`, { data: { site_id: sites[0]!.id, name: screenName } });
  expect(s.status()).toBe(201);
  const p = await page.request.post(`${base}/api/v1/playlists`, { data: { name: playlistName } });
  expect(p.status()).toBe(201);
  return { screen: (await s.json()) as { id: string }, screenName, playlistName, playlist: (await p.json()) as { id: string } };
}

const card = (page: Page, name: string) => page.getByRole("listitem").filter({ has: page.getByRole("heading", { level: 2, name }) });

test("forcer depuis la carte en 3 interactions, prolonger puis arrêter (toutes les URL sous la base)", async ({ page }, info) => {
  const { base } = meta(info);
  const { screenName, playlistName } = await seed(page, info);
  await page.goto(`${base}/screens`);
  await card(page, screenName).getByRole("button", { name: `Forcer l'affichage sur ${screenName}` }).click();
  const form = page.getByRole("form", { name: "Forcer l'affichage" });
  // Avancé reste replié : le chemin principal n'a que quoi / où / jusqu'à quand.
  await expect(form.locator("details.advanced")).not.toHaveAttribute("open", "");
  await form.getByLabel("Quoi afficher").selectOption({ label: playlistName });
  await expect(form.getByText(/1 écran concerné, jusqu'à/)).toBeVisible();
  await form.getByRole("button", { name: "Forcer l'affichage" }).click();
  await expect(page.getByText(/Forçage en cours sur 1 écran/)).toBeVisible();

  const c = card(page, screenName);
  await expect(c.getByText(/Forcé jusqu'à/)).toBeVisible();
  await c.getByRole("button", { name: /^Prolonger le forçage/ }).click();
  await expect(page.getByText(/est prolongé jusqu'à/)).toBeVisible();
  await c.getByRole("button", { name: /^Arrêter le forçage/ }).click();
  await c.getByRole("button", { name: "Arrêter maintenant" }).click();
  await expect(page.getByText(`Le forçage de ${screenName} est arrêté.`)).toBeVisible();
  await expect(c.getByText(/Forcé jusqu'à/)).toHaveCount(0);

  const api = (requests.get(page) ?? []).filter((r) => r.url().includes("/api/v1/"));
  expect(api.every((r) => new URL(r.url()).pathname.startsWith(`${base}/api/v1/`))).toBe(true);
  expect(api.filter((r) => r.method() === "POST" && new URL(r.url()).pathname === `${base}/api/v1/overrides`)).toHaveLength(1);
});

test("Accueil : bloc des forçages en cours, arrêt avec confirmation", async ({ page }, info) => {
  const { base } = meta(info);
  const { screen, screenName, playlist } = await seed(page, info);
  const r = await page.request.post(`${base}/api/v1/overrides`, { data: { target_type: "screen", target_id: screen.id, playlist_id: playlist.id, duration_minutes: 60 } });
  expect(r.status()).toBe(201);
  await page.goto(`${base}/`);
  const block = page.getByRole("region", { name: "Forçages en cours" });
  const item = block.getByRole("listitem").filter({ hasText: screenName });
  await expect(item.getByText(/Forcé jusqu'à/)).toBeVisible();
  await item.getByRole("button", { name: /^Arrêter le forçage/ }).click();
  await item.getByRole("button", { name: "Arrêter maintenant" }).click();
  await expect(page.getByText(`Le forçage de ${screenName} est arrêté.`)).toBeVisible();
  await expect(block.getByRole("listitem").filter({ hasText: screenName })).toHaveCount(0);
});

for (const lang of ["Français", "English"]) {
  test(`375 px et accessibilité AA du formulaire de forçage (${lang}), Avancé ouvert`, async ({ page }, info) => {
    const { base } = meta(info);
    await page.setViewportSize({ width: 375, height: 800 });
    const { screenName } = await seed(page, info);
    await page.goto(`${base}/screens`);
    await page.getByRole("button", { name: lang }).click();
    await card(page, screenName).getByRole("button", { name: /^(Forcer l'affichage sur|Force display on) / }).click();
    const form = page.getByRole("form", { name: /^(Forcer l'affichage|Force display)$/ });
    await form.locator("summary").click();
    await expect(form.getByLabel(/^(Priorité numérique|Numeric priority)$/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    const small = await form.locator("button, select, input:not([type=radio]):not([type=checkbox]), summary").evaluateAll((els) =>
      els.filter((e) => (e as HTMLElement).offsetParent !== null && (e as HTMLElement).getBoundingClientRect().height < 40).map((e) => e.outerHTML.slice(0, 80)),
    );
    expect(small).toEqual([]);
    const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  });
}

test("thème sombre V-Tech : formulaire et carte forcée sans violation de contraste", async ({ page }, info) => {
  test.skip(meta(info).theme !== "theme.vtech", "pas de mode sombre pour ce thème");
  const { base } = meta(info);
  await page.emulateMedia({ colorScheme: "dark" });
  const { screen, screenName, playlist } = await seed(page, info);
  expect((await page.request.post(`${base}/api/v1/overrides`, { data: { target_type: "screen", target_id: screen.id, playlist_id: playlist.id, duration_minutes: 60 } })).status()).toBe(201);
  await page.goto(`${base}/screens`);
  await card(page, screenName).getByRole("button", { name: `Forcer l'affichage sur ${screenName}` }).click();
  await page.getByRole("form", { name: "Forcer l'affichage" }).locator("summary").click();
  await expect(card(page, screenName).getByText(/Forcé jusqu'à/)).toBeVisible();
  for (const lang of ["Français", "English"]) {
    await page.getByRole("button", { name: lang }).click();
    const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    expect(res.violations.map((v) => `${lang} ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  }
});
