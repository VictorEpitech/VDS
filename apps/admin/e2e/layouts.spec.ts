import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string };
const uniq = (info: TestInfo, label: string) => `${label} ${info.project.name} ${Math.random().toString(36).slice(2, 7)}`;
const failures = new WeakMap<Page, string[]>();

test.beforeEach(async ({ page }, info) => {
  await page.context().addCookies([{ name: "vds_session", value: "e2e-admin", url: info.project.use.baseURL! }]);
  const list: string[] = [];
  failures.set(page, list);
  page.on("requestfailed", (r) => list.push(`failed ${r.url()}`));
  // Les 4xx attendus (conflits provoqués par un test) sont filtrés au cas par cas ; tout le reste est une erreur.
  page.on("response", (r) => {
    if (r.status() >= 400) list.push(`${r.status()} ${r.url()}`);
  });
  page.on("pageerror", (e) => list.push(`error ${e.message}`));
});

test.afterEach(async ({ page }) => {
  expect(failures.get(page)).toEqual([]);
});

const zone = (key: string, name: string, x: number, y: number, w: number, h: number, z = 0) => ({ key, name, x, y, w, h, z, fit: "contain" });

async function seedLayout(page: Page, info: TestInfo, orientation = "landscape") {
  const { base } = meta(info);
  const name = uniq(info, "Bandeau");
  const r = await page.request.post(`${base}/api/v1/layouts`, { data: { name, orientation, tags: [], zones: [zone("main", "Principale", 0, 0, 1, 0.85), zone("banner", "Bandeau", 0, 0.85, 1, 0.15, 1)] } });
  expect(r.status()).toBe(201);
  return { name, layout: (await r.json()) as { id: string } };
}

test("créer depuis un modèle, éditer au clavier, enregistrer (URL sous la base)", async ({ page }, info) => {
  const { base } = meta(info);
  const requests: string[] = [];
  page.on("request", (r) => r.url().includes("/api/v1/") && requests.push(new URL(r.url()).pathname));
  await page.goto(`${base}/content/layouts`);
  await expect(page.getByRole("heading", { level: 1, name: "Mises en page" })).toBeVisible();
  await page.getByRole("button", { name: "Créer une mise en page" }).first().click();
  const form = page.getByRole("form", { name: "Créer une mise en page" });
  const name = uniq(info, "Colonnes");
  await form.getByLabel("Nom").fill(name);
  await form.getByRole("radio", { name: "2 colonnes" }).check();
  await form.getByRole("button", { name: "Créer et ouvrir l'éditeur" }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();

  const left = page.getByRole("button", { name: /^Zone Gauche/ });
  await left.focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Shift+ArrowLeft");
  await expect(left).toHaveAccessibleName(/position 1 % 0 %, taille 49 % sur 100 %/);
  await page.getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByText("Mise en page enregistrée.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: /^Zone Gauche, position 1 % 0 %/ })).toBeVisible();
  expect(requests.every((p) => p.startsWith(`${base}/api/v1/`))).toBe(true);
  expect(requests.some((p) => p.endsWith("/layouts"))).toBe(true);
});

test("un enregistrement périmé propose de recharger (If-Match)", async ({ page }, info) => {
  const { base } = meta(info);
  const { name, layout } = await seedLayout(page, info);
  await page.goto(`${base}/content/layouts/${layout.id}`);
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  // Une autre session modifie la mise en page entre-temps.
  const other = await page.request.put(`${base}/api/v1/layouts/${layout.id}`, { headers: { "If-Match": "1" }, data: { name, orientation: "landscape", tags: [], zones: [zone("main", "Principale", 0, 0, 1, 1)] } });
  expect(other.status()).toBe(200);
  await page.getByRole("button", { name: /^Zone Principale/ }).focus();
  await page.keyboard.press("Shift+ArrowLeft");
  await page.getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByRole("button", { name: "Recharger la dernière version" })).toBeVisible();
  failures.set(page, (failures.get(page) ?? []).filter((f) => !f.startsWith("412 ")));
  await page.getByRole("button", { name: "Recharger la dernière version" }).click();
  await expect(page.getByRole("button", { name: /^Zone Principale, position 0 % 0 %, taille 100 % sur 100 %/ })).toBeVisible();
});

test("forçage de zone : choisir la zone, créer, voir la pastille, arrêter", async ({ page }, info) => {
  const { base } = meta(info);
  await seedLayout(page, info);
  const sites = (await (await page.request.get(`${base}/api/v1/sites`)).json()) as { id: string }[];
  const screenName = uniq(info, "Zone");
  const playlistName = uniq(info, "Annonce");
  expect((await page.request.post(`${base}/api/v1/screens`, { data: { site_id: sites[0]!.id, name: screenName } })).status()).toBe(201);
  expect((await page.request.post(`${base}/api/v1/playlists`, { data: { name: playlistName } })).status()).toBe(201);
  await page.goto(`${base}/screens`);
  const card = page.getByRole("listitem").filter({ has: page.getByRole("heading", { level: 2, name: screenName }) });
  await card.getByRole("button", { name: `Forcer l'affichage sur ${screenName}` }).click();
  const form = page.getByRole("form", { name: "Forcer l'affichage" });
  await form.getByLabel("Quoi afficher").selectOption({ label: playlistName });
  await form.getByRole("radio", { name: "Certaines zones" }).check();
  await form.getByRole("checkbox", { name: /Bandeau/ }).check();
  await expect(form.getByText(/s'applique à 1 écran sur 1/)).toBeVisible();
  await form.getByRole("button", { name: "Forcer l'affichage" }).click();
  await expect(card.getByText(/Zone forcée/)).toBeVisible();
  await expect(card.getByText("banner")).toBeVisible();
  await card.getByRole("button", { name: /^Arrêter le forçage/ }).click();
  await card.getByRole("button", { name: "Arrêter maintenant" }).click();
  await expect(card.getByText(/Zone forcée/)).toHaveCount(0);
});

for (const [lang, width] of [["Français", 1280], ["English", 1280], ["Français", 375], ["English", 375]] as const) {
  test(`éditeur et liste : accessibilité AA, ${lang}, ${width} px`, async ({ page }, info) => {
    const { base } = meta(info);
    await page.setViewportSize({ width, height: 800 });
    const { layout } = await seedLayout(page, info);
    await page.goto(`${base}/content/layouts`);
    await page.getByRole("button", { name: lang }).click();
    await expect(page.getByRole("link", { name: /Bandeau/ }).first()).toBeVisible();
    const axe = () => new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    expect((await axe()).violations.map((v) => `list ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
    await page.goto(`${base}/content/layouts/${layout.id}`);
    await page.getByRole("button", { name: lang }).click();
    await expect(page.getByRole("button", { name: /^(Zone|Zone) Bandeau/ })).toBeVisible();
    expect((await axe()).violations.map((v) => `editor ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    if (width === 375) await expect(page.getByRole("group", { name: /^(Canevas de la mise en page|Layout canvas)$/ })).toHaveAttribute("data-readonly", "true");
  });
}

test("thème sombre V-Tech : éditeur sans violation de contraste", async ({ page }, info) => {
  test.skip(meta(info).theme !== "theme.vtech", "pas de mode sombre pour ce thème");
  const { base } = meta(info);
  await page.emulateMedia({ colorScheme: "dark" });
  const { layout } = await seedLayout(page, info);
  await page.goto(`${base}/content/layouts/${layout.id}`);
  await expect(page.getByRole("button", { name: /^Zone Bandeau/ })).toBeVisible();
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
});
