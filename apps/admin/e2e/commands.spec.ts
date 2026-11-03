import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string };
const AA = ["wcag2a", "wcag2aa", "wcag21aa"];
const failures = new WeakMap<Page, string[]>();
const posts = new WeakMap<Page, string[]>();

async function as(page: Page, info: TestInfo, session: string) {
  await page.context().addCookies([{ name: "vds_session", value: session, url: info.project.use.baseURL! }]);
}

test.beforeEach(async ({ page }) => {
  const list: string[] = [];
  const sent: string[] = [];
  failures.set(page, list);
  posts.set(page, sent);
  page.on("requestfailed", (r) => list.push(`failed ${r.url()}`));
  page.on("response", (r) => {
    if (r.status() >= 400) list.push(`${r.status()} ${r.url()}`);
  });
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/commands")) sent.push(new URL(r.url()).pathname);
  });
  page.on("pageerror", (e) => list.push(`error ${e.message}`));
});

test.afterEach(async ({ page }) => {
  expect(failures.get(page)).toEqual([]);
});

const hall = (page: Page) => page.getByRole("listitem").filter({ has: page.getByRole("heading", { level: 2, name: "Hall d'accueil" }) });

for (const lang of ["fr", "en"] as const) {
  const t =
    lang === "fr"
      ? { shot: "Actualiser la capture : Hall d'accueil", restart: "Redémarrer l'application", confirm: /Confirmer le redémarrage/, alt: /^Capture de Hall d'accueil, /, done: "Réussie" }
      : { shot: "Refresh capture: Hall d'accueil", restart: "Restart the app", confirm: /Confirm restarting/, alt: /^Capture of Hall d'accueil, /, done: "Done" };
  test(`capture à la demande, redémarrage confirmé, AA et 375 px en ${lang}`, async ({ page }, info) => {
    const { base } = meta(info);
    await as(page, info, "e2e-admin");
    await page.goto(`${base}/screens`);
    if (lang === "en") await page.getByRole("button", { name: "English" }).click();
    const card = hall(page);
    await card.getByRole("button", { name: t.shot }).click();
    // Capture visible en moins de 15 s, servie sous la base.
    const img = card.getByRole("img", { name: t.alt }).first();
    await expect(img).toBeVisible({ timeout: 15_000 });
    expect(await img.getAttribute("src")).toMatch(new RegExp(`^${base}/api/v1/screenshots/[^/]+/image$`));
    await expect(card.locator('[data-command-status="ok"]').first()).toContainText(t.done);

    // Redémarrage : rien n'est envoyé avant la confirmation.
    const before = posts.get(page)!.length;
    await card.getByRole("button", { name: t.restart }).click();
    expect(posts.get(page)!.length).toBe(before);
    await card.getByRole("button", { name: t.confirm }).click();
    await expect.poll(() => posts.get(page)!.length).toBe(before + 1);

    const res = await new AxeBuilder({ page }).withTags(AA).analyze();
    expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
    await page.setViewportSize({ width: 375, height: 800 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    expect((await card.getByRole("button", { name: t.shot }).boundingBox())!.height).toBeGreaterThanOrEqual(44);
  });

  test(`tableau de bord des captures, AA et 375 px en ${lang}`, async ({ page }, info) => {
    const { base } = meta(info);
    await as(page, info, "e2e-admin");
    await page.goto(`${base}/screens/captures`);
    if (lang === "en") await page.getByRole("button", { name: "English" }).click();
    await expect(page.getByRole("heading", { level: 1, name: lang === "fr" ? "Captures d'écran" : "Screen captures" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Hall d'accueil" })).toBeVisible();
    const res = await new AxeBuilder({ page }).withTags(AA).analyze();
    expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
    await page.setViewportSize({ width: 375, height: 800 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  });
}

test("éditeur : recharger et identifier oui, redémarrer non (aucune requête interdite)", async ({ page }, info) => {
  await as(page, info, "e2e-editor");
  await page.goto(`${meta(info).base}/screens`);
  const card = hall(page);
  await expect(card.getByRole("button", { name: "Recharger : Hall d'accueil" })).toBeVisible();
  await expect(card.getByRole("button", { name: "Redémarrer l'application" })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "Autres commandes" })).toHaveCount(0);
});

test("action groupée : recharger deux écrans, l'écran sans appareil est ignoré et compté", async ({ page }, info) => {
  await as(page, info, "e2e-admin");
  await page.goto(`${meta(info).base}/screens`);
  await page.getByRole("checkbox", { name: "Sélectionner Hall d'accueil" }).check();
  await page.getByRole("checkbox", { name: "Sélectionner Totem" }).check();
  const bar = page.getByRole("region", { name: "Actions sur la sélection" });
  await bar.getByRole("button", { name: "Recharger" }).click();
  await expect(page.getByText(/1 écran ignoré : non pris en charge/)).toBeVisible();
  await expect.poll(() => posts.get(page)!.length).toBe(1);
});

test("thème sombre : commandes et captures sans violation (si le thème le propose)", async ({ page }, info) => {
  test.skip(meta(info).theme !== "theme.vtech", "pas de mode sombre pour ce thème");
  await as(page, info, "e2e-admin");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(`${meta(info).base}/screens`);
  await expect(hall(page).getByRole("button", { name: "Recharger : Hall d'accueil" })).toBeVisible();
  const res = await new AxeBuilder({ page }).withTags(AA).analyze();
  expect(res.violations.map((v) => v.id)).toEqual([]);
});
