import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string };
const AA = ["wcag2a", "wcag2aa", "wcag21aa"];
const failures = new WeakMap<Page, string[]>();

async function as(page: Page, info: TestInfo, session: string) {
  await page.context().addCookies([{ name: "vds_session", value: session, url: info.project.use.baseURL! }]);
}

test.beforeEach(async ({ page }) => {
  const list: string[] = [];
  failures.set(page, list);
  page.on("requestfailed", (r) => list.push(`failed ${r.url()}`));
  page.on("response", (r) => {
    if (r.status() >= 400) list.push(`${r.status()} ${r.url()}`);
  });
  page.on("pageerror", (e) => list.push(`error ${e.message}`));
});

test.afterEach(async ({ page }) => {
  expect(failures.get(page)).toEqual([]);
});

for (const lang of ["fr", "en"] as const) {
  test(`utilisateurs : liste puis fiche, AA et 375 px en ${lang}`, async ({ page }, info) => {
    const { base } = meta(info);
    await as(page, info, "e2e-admin");
    await page.goto(`${base}/users`);
    if (lang === "en") await page.getByRole("button", { name: "English" }).click();
    await expect(page.getByRole("heading", { level: 1, name: lang === "fr" ? "Utilisateurs" : "Users" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Marie Dupont", exact: true })).toBeVisible();
    await expect(page.getByText(lang === "fr" ? "Chargement en cours" : "Loading")).toHaveCount(0);
    const list = await new AxeBuilder({ page }).withTags(AA).analyze();
    expect(list.violations.map((v) => v.id)).toEqual([]);
    await page.setViewportSize({ width: 375, height: 800 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);

    await page.getByRole("link", { name: "Marie Dupont", exact: true }).first().click();
    await expect(page.getByRole("heading", { level: 1, name: "Marie Dupont" })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "Moulins" })).toBeVisible();
    const detail = await new AxeBuilder({ page }).withTags(AA).analyze();
    expect(detail.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  });
}

test("périmètre au clavier : flèches, espace, groupes couverts, enregistrement et conflit 412", async ({ page }, info) => {
  const { base } = meta(info);
  const requests: string[] = [];
  page.on("request", (r) => r.url().includes("/api/v1/") && requests.push(new URL(r.url()).pathname));
  await as(page, info, "e2e-admin");
  await page.goto(`${base}/users`);
  await page.getByRole("link", { name: "Paul Martin", exact: true }).first().click();
  // Rôle géré par le fournisseur : lecture seule.
  await expect(page.getByLabel("Rôle")).toBeDisabled();
  await expect(page.getByText("Rôle géré par le service de connexion")).toBeVisible();

  const org = page.getByRole("checkbox", { name: "Toute l'organisation" });
  await org.focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("checkbox", { name: "Moulins" })).toBeFocused();
  await page.keyboard.press("Space");
  await expect(page.getByRole("checkbox", { name: "Accueil" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Accueil" })).toBeDisabled();
  await expect(page.getByText("Périmètre choisi : 1 site")).toBeVisible();

  // Conflit : une autre session modifie le périmètre avant l'enregistrement.
  const id = page.url().split("/").pop();
  const other = await page.request.put(`${base}/api/v1/users/${id}/scopes`, { data: { scopes: [] }, headers: { "If-Match": "v1" } });
  expect(other.status()).toBe(200);
  const form = page.getByRole("form", { name: "Périmètre" });
  await form.getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByText(/modifié par quelqu'un d'autre/)).toBeVisible();
  failures.get(page)!.length = 0; // 412 attendu
  await page.getByRole("button", { name: "Recharger" }).click();
  await expect(page.getByRole("checkbox", { name: "Moulins" })).not.toBeChecked();
  await page.getByRole("checkbox", { name: "Moulins" }).check();
  await form.getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByText("Périmètre enregistré.")).toBeVisible();
  expect(requests.every((p) => base === "" || p.startsWith(`${base}/`))).toBe(true);
});

test("éditeur restreint : menu réduit, sélecteur de propriétaire filtré, badge", async ({ page }, info) => {
  const { base } = meta(info);
  await as(page, info, "e2e-editor");
  await page.goto(`${base}/content/playlists`);
  await expect(page.getByRole("heading", { level: 1, name: "Playlists" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Plus" })).toHaveCount(0);
  const owner = page.getByLabel("Propriétaire");
  await expect(owner.getByRole("option", { name: "Partagé" })).toHaveCount(0);
  await expect(owner.getByRole("option", { name: "Moulins", exact: true })).toHaveCount(1);
  await expect(owner.getByRole("option", { name: "Moulins / Accueil", exact: true })).toHaveCount(1);
  const name = `Menu ${info.project.name} ${Math.random().toString(36).slice(2, 7)}`;
  await page.getByLabel("Nom de la playlist").fill(name);
  await owner.selectOption({ label: "Moulins / Accueil" });
  await page.getByRole("button", { name: "Créer la playlist" }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  await expect(page.locator(".owner-badge", { hasText: "Moulins / Accueil" })).toBeVisible();
  const res = await new AxeBuilder({ page }).withTags(AA).analyze();
  expect(res.violations.map((v) => v.id)).toEqual([]);
  // La page Utilisateurs est réservée aux administrateurs.
  await page.goto(`${base}/users`);
  await expect(page.getByRole("heading", { level: 1, name: "Page introuvable" })).toBeVisible();
});

test("administrateur : option Partagé et badge Partagé", async ({ page }, info) => {
  const { base } = meta(info);
  await as(page, info, "e2e-admin");
  await page.goto(`${base}/content/playlists`);
  const owner = page.getByLabel("Propriétaire");
  await expect(owner.getByRole("option", { name: "Partagé" })).toHaveCount(1);
  const name = `Partagée ${info.project.name} ${Math.random().toString(36).slice(2, 7)}`;
  await page.getByLabel("Nom de la playlist").fill(name);
  await page.getByRole("button", { name: "Créer la playlist" }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  await expect(page.locator(".owner-badge", { hasText: "Partagé" })).toBeVisible();
});

test("éditeur sans périmètre : message d'attente, aucune erreur", async ({ page }, info) => {
  const { base } = meta(info);
  await as(page, info, "e2e-editor-empty");
  await page.goto(`${base}/`);
  await expect(page.getByText(/Aucun périmètre : un administrateur doit vous en attribuer un/)).toBeVisible();
});

test("thème sombre : utilisateurs sans violation (si le thème le propose)", async ({ page }, info) => {
  test.skip(meta(info).theme !== "theme.vtech", "pas de mode sombre pour ce thème");
  await as(page, info, "e2e-admin");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(`${meta(info).base}/users/u-2`);
  await expect(page.getByRole("checkbox", { name: "Moulins" })).toBeVisible();
  const res = await new AxeBuilder({ page }).withTags(AA).analyze();
  expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
});
