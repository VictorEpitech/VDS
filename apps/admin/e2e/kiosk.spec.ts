import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string };
const failures = new WeakMap<Page, string[]>();
// L'état de maintenance vit dans le serveur simulé : un test à la fois par projet.
test.describe.configure({ mode: "serial" });

const AA = ["wcag2a", "wcag2aa", "wcag21aa"];

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

test.afterEach(async ({ page }) => {
  expect(failures.get(page)).toEqual([]);
});

for (const path of ["/screens", "/supervision"] as const) {
  for (const lang of ["fr", "en"] as const) {
    const t = lang === "fr" ? { locked: "Verrouillé", soft: /Kiosque doux/, start: "Mode maintenance", end: "Terminer", until: /^Maintenance jusqu'à/ } : { locked: "Locked", soft: /Soft kiosk/, start: "Maintenance mode", end: "End", until: /^Maintenance until/ };
    test(`${path} : kiosque, maintenance, AA et 375 px en ${lang}`, async ({ page }, info) => {
      await page.goto(`${meta(info).base}${path}`);
      if (lang === "en") await page.getByRole("button", { name: "English" }).click();
      const card = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: "Hall d'accueil" }) });
      await expect(card.locator(".status-badge", { hasText: t.locked })).toBeVisible();
      await expect(card.getByText(t.soft)).toBeVisible();

      await card.getByRole("button", { name: new RegExp(`^${t.start}`) }).click();
      await card.getByRole("button", { name: lang === "fr" ? "Déverrouiller temporairement" : "Unlock temporarily" }).click();
      await expect(card.locator(".status-badge", { hasText: t.until })).toBeVisible();
      await expect(card.getByRole("button", { name: new RegExp(`^${t.start}`) })).toHaveCount(0);

      const res = await new AxeBuilder({ page }).withTags(AA).analyze();
      expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
      await page.setViewportSize({ width: 375, height: 800 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
      const end = card.getByRole("button", { name: new RegExp(`^${lang === "fr" ? "Terminer la maintenance" : "End maintenance"}`) });
      expect((await end.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      await end.click();
      await expect(card.locator(".status-badge", { hasText: t.locked })).toBeVisible();
    });
  }
}

test("thème sombre : kiosque sans violation (si le thème le propose)", async ({ page }, info) => {
  test.skip(meta(info).theme !== "theme.vtech", "pas de mode sombre pour ce thème");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(`${meta(info).base}/screens`);
  await expect(page.locator(".status-badge", { hasText: "Verrouillé" }).first()).toBeVisible();
  const res = await new AxeBuilder({ page }).withTags(AA).analyze();
  expect(res.violations.map((v) => v.id)).toEqual([]);
});

test("réglage du kiosque d'un écran : confirmation avant déverrouillage permanent", async ({ page }, info) => {
  await page.goto(`${meta(info).base}/screens`);
  const card = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: "Hall d'accueil" }) });
  await card.getByText("Avancé").click();
  await card.getByLabel("Kiosque du player").selectOption("unlocked");
  await card.getByRole("button", { name: "Déverrouiller en permanence" }).click();
  await card.getByRole("button", { name: "Confirmer le déverrouillage permanent" }).click();
  await expect(card.getByText("Réglages enregistrés.")).toBeVisible();
  await expect(card.getByText("Défini ici")).toBeVisible();
  await card.getByRole("button", { name: /Revenir à l'héritage/ }).click();
  await expect(card.getByText("Valeur par défaut")).toBeVisible();
});
