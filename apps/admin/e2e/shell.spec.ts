import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const PRIMARY: Record<string, { light: string; dark?: string }> = {
  "theme.vtech": { light: "#2a4b5a", dark: "#8bd3e6" },
  "theme.allia": { light: "#0e49be" },
};

const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string };
const home = (info: TestInfo) => `${meta(info).base}/`;

async function primary(page: Page): Promise<string> {
  return page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--vds-primary").trim().toLowerCase());
}

const failures = new WeakMap<Page, string[]>();

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

test("base href, thème actif, logo et icônes sous le préfixe", async ({ page }, info) => {
  const { base, theme } = meta(info);
  await page.goto(home(info));
  await expect(page.getByRole("heading", { level: 1, name: "Tableau de bord" })).toBeVisible();
  expect(await page.locator("base").getAttribute("href")).toBe(`${base}/`);
  expect(await page.evaluate(() => document.documentElement.lang)).toBe("fr");
  expect(await primary(page)).toBe(PRIMARY[theme]!.light);

  const logo = await page.locator(".vds-logo").evaluate((el) => getComputedStyle(el).backgroundImage);
  expect(logo).toContain(`${base}/modules/${theme}/assets/logo`);
  const icon = await page.locator('.vds-icon[data-icon="online"]').first().evaluate((el) => getComputedStyle(el).maskImage);
  expect(icon).toContain(`${base}/modules/${theme}/assets/icons/online.svg`);

  await expect(page.getByText("Serveur joignable")).toBeVisible();
  // Chaque état = icône + libellé.
  for (const label of ["En ligne", "Hors service prévu", "Hors ligne", "Échec d'alimentation", "Forçage en cours", "Appairage"]) {
    await expect(page.locator(".status-badge", { hasText: label }).locator(".vds-icon")).toBeVisible();
  }
});

test("liens du mode lus dans la config, sans branche dans les composants", async ({ page }, info) => {
  await page.goto(home(info));
  const studio = page.getByRole("link", { name: "Retour au Studio" });
  if (meta(info).theme === "theme.allia") {
    await expect(studio).toHaveAttribute("href", "/espace");
  } else {
    await expect(studio).toHaveCount(0);
  }
});

test("lien profond, rechargement et page introuvable", async ({ page }, info) => {
  const { base } = meta(info);
  await page.goto(`${base}/screens`);
  await expect(page.getByRole("heading", { level: 1, name: "Écrans" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "Écrans" })).toBeVisible();
  await page.getByRole("link", { name: "Accueil" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Tableau de bord" })).toBeVisible();
  expect(new URL(page.url()).pathname.replace(/\/$/, "")).toBe(base);
  await page.goto(`${base}/nope`);
  await expect(page.getByRole("heading", { level: 1, name: "Page introuvable" })).toBeVisible();
});

test("hors du préfixe : 404", async ({ request }, info) => {
  test.skip(meta(info).base === "", "à la racine, tout est sous le préfixe");
  const res = await request.get("/");
  expect(res.status()).toBe(404);
});

test("FR/EN : bascule et mémorisation", async ({ page }, info) => {
  await page.goto(home(info));
  await page.getByRole("button", { name: "English" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Dashboard" })).toBeVisible();
  await expect(page.getByText("Scheduled off")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.lang)).toBe("en");
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "Dashboard" })).toBeVisible();
  await page.getByRole("button", { name: "Français" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Tableau de bord" })).toBeVisible();
});

for (const lang of ["fr", "en"] as const) {
  test(`accessibilité AA (axe) en ${lang}`, async ({ page }, info) => {
    await page.goto(home(info));
    if (lang === "en") await page.getByRole("button", { name: "English" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  });
}

test("thème sombre (si le thème le propose)", async ({ page }, info) => {
  const dark = PRIMARY[meta(info).theme]!.dark;
  test.skip(!dark, "pas de mode sombre pour ce thème");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(home(info));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  expect(await primary(page)).toBe(dark);
  for (const lang of ["Français", "English"]) {
    await page.getByRole("button", { name: lang }).click();
    const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    expect(res.violations.map((v) => v.id)).toEqual([]);
  }
});

test("375 px : pas de défilement horizontal, cibles de 44 px", async ({ page }, info) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto(home(info));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  for (const el of await page.locator("a[href]:visible, button:visible").all()) {
    if ((await el.getAttribute("class"))?.includes("skip-link")) continue;
    const box = await el.boundingBox();
    expect(box!.height, await el.innerText()).toBeGreaterThanOrEqual(44);
  }
  await page.getByRole("button", { name: "English" }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
});

test("clavier : lien d'évitement puis toute la navigation", async ({ page }, info) => {
  await page.goto(home(info));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const reached = new Set<string>();
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Tab");
    reached.add(await page.evaluate(() => document.activeElement?.textContent?.trim() ?? ""));
  }
  for (const name of ["Accueil", "Écrans", "English"]) {
    expect([...reached].some((t) => t.includes(name))).toBe(true);
  }
});

test("clavier : le lien d'évitement donne le focus au contenu", async ({ page }, info) => {
  await page.goto(home(info));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Aller au contenu" });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
});
