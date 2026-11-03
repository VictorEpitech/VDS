import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string };
const url = (info: TestInfo, p: string) => `${meta(info).base}${p}`;
const AA = ["wcag2a", "wcag2aa", "wcag21aa"];
const failures = new WeakMap<Page, string[]>();
const apiUrls = new WeakMap<Page, string[]>();

test.beforeEach(async ({ page }, info) => {
  await page.context().addCookies([{ name: "vds_session", value: "e2e-admin", url: info.project.use.baseURL! }]);
  const list: string[] = [];
  const urls: string[] = [];
  failures.set(page, list);
  apiUrls.set(page, urls);
  page.on("request", (r) => {
    if (r.url().includes("/planning/")) urls.push(new URL(r.url()).pathname);
  });
  page.on("requestfailed", (r) => list.push(`failed ${r.url()}`));
  page.on("response", (r) => {
    if (r.status() >= 400) list.push(`${r.status()} ${r.url()}`);
  });
  page.on("pageerror", (e) => list.push(`error ${e.message}`));
});

test.afterEach(async ({ page }, info) => {
  if (info.status === "skipped") return;
  expect(failures.get(page)).toEqual([]);
  // Toutes les requêtes du planning passent par la base (VDS_BASE_PATH).
  const urls = apiUrls.get(page) ?? [];
  expect(urls.length).toBeGreaterThan(0);
  expect(urls.every((u) => u.startsWith(`${meta(info).base}/api/v1/planning/`))).toBe(true);
});

const axe = async (page: Page, label: string) => {
  const res = await new AxeBuilder({ page }).withTags(AA).analyze();
  expect(res.violations.map((v) => `${label} ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
};

const noShadow = (page: Page) =>
  page.evaluate(() => Array.from(document.querySelectorAll(".planning *, .inspector, .inspector *")).filter((el) => getComputedStyle(el).boxShadow !== "none").length);

for (const lang of ["fr", "en"] as const) {
  test(`semaine : pistes, clavier complet, inspecteur, AA, aucune ombre (${lang})`, async ({ page }, info) => {
    await page.goto(url(info, "/playback?view=week&date=2026-11-02"));
    if (lang === "en") await page.getByRole("button", { name: "English" }).click();
    const grids = page.getByRole("grid");
    await expect(grids).toHaveCount(7);
    await expect(grids.first().getByRole("rowheader")).toHaveCount(3);
    expect(await noShadow(page)).toBe(0);
    await axe(page, "semaine");

    // Un seul bloc dans l'ordre de tabulation (tabindex itinérant), flèches, Entrée.
    const tabbable = page.locator('.tl-block[tabindex="0"]');
    await expect(tabbable).toHaveCount(1);
    await tabbable.focus();
    const first = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
    await page.keyboard.press("ArrowRight");
    const second = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
    expect(second).not.toBe(first);
    await page.keyboard.press("ArrowDown");
    expect(await page.evaluate(() => document.activeElement?.closest("[role=row]")?.getAttribute("data-kind"))).toBe("lane");
    await page.keyboard.press("PageDown");
    expect(await page.evaluate(() => document.activeElement?.closest("section")?.querySelector("h3")?.textContent)).not.toBe(await page.locator("section.tl-day h3").first().textContent());
    await page.keyboard.press("Home");
    await page.keyboard.press("Enter");
    const panel = page.getByRole("complementary");
    await expect(panel).toBeVisible();
    await expect(panel.getByText(lang === "fr" ? "La cible la plus précise passe avant la priorité" : "The most specific target comes before priority", { exact: false }).first()).toBeVisible();
    await expect(panel.getByText(lang === "fr" ? "Priorité plus haute." : "Higher priority.").or(panel.getByText(lang === "fr" ? "Seule programmation applicable." : "Only applicable schedule.")).first()).toBeVisible();
    expect(page.url()).toContain(`${meta(info).base}/playback`);
    expect(page.url()).toContain("at=");
    expect(await noShadow(page)).toBe(0);
    await axe(page, "inspecteur");

    // Repère d'expiration : lien sous la base.
    const expiry = page.locator("a.tl-expiry").first();
    await expect(expiry).toBeVisible();
    expect(await expiry.getAttribute("href")).toMatch(new RegExp(`^${meta(info).base}/content/playlists/`));
  });
}

test("vue Jour et Liste : navigation clavier, N = maintenant, AA", async ({ page }, info) => {
  await page.goto(url(info, "/playback?view=day&date=2026-11-02"));
  await expect(page.getByRole("grid")).toHaveCount(1);
  await axe(page, "jour");
  await page.getByRole("radio", { name: "Liste" }).click();
  await expect(page.getByRole("radio", { name: "Liste" })).toBeChecked();
  await expect(page.getByRole("table")).toBeVisible();
  await axe(page, "liste");
  await page.getByRole("radio", { name: "Jour" }).click();
  await expect(page.getByRole("grid")).toHaveCount(1);
  await page.locator('.tl-block[tabindex="0"]').focus();
  await page.keyboard.press("n");
  await expect(page.getByRole("complementary")).toBeVisible();
  await page.getByRole("complementary").getByRole("button", { name: "Fermer" }).click();
  await expect(page.getByRole("complementary")).toHaveCount(0);
});

test("thème sombre V-Tech : semaine et inspecteur sans violation de contraste", async ({ page }, info) => {
  test.skip(meta(info).theme !== "theme.vtech", "pas de mode sombre pour ce thème");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(url(info, "/playback?view=week&date=2026-11-02"));
  await expect(page.getByRole("grid")).toHaveCount(7);
  await page.locator('.tl-block[tabindex="0"]').click();
  await expect(page.getByRole("complementary")).toBeVisible();
  await axe(page, "sombre");
});

for (const lang of ["fr", "en"] as const) {
  test(`375 px : Liste par défaut, sans débordement, feuille basse, AA (${lang})`, async ({ page }, info) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto(url(info, "/playback?date=2026-11-02"));
    if (lang === "en") await page.getByRole("button", { name: "English" }).click();
    await expect(page.getByRole("table")).toBeVisible();
    await expect(page.getByRole("grid")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await axe(page, "375 liste");
    await page.getByRole("table").getByRole("button").nth(1).click();
    const panel = page.getByRole("complementary");
    await expect(panel).toBeVisible();
    const box = (await panel.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(375);
    expect(box.y + box.height).toBeLessThanOrEqual(800);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await axe(page, "375 feuille");
    expect(await noShadow(page)).toBe(0);
  });
}

test.describe("fuseau", () => {
  test.describe("navigateur dans un autre fuseau que le site", () => {
    test.use({ timezoneId: "America/New_York" });
    test("la mention du fuseau du site est affichée", async ({ page }, info) => {
      await page.goto(url(info, "/playback?view=list&date=2026-11-02"));
      await expect(page.getByText(/Heures affichées dans le fuseau du site \(Europe\/Paris\)/)).toBeVisible();
    });
  });
  test.describe("navigateur dans le fuseau du site", () => {
    test.use({ timezoneId: "Europe/Paris" });
    test("aucune mention", async ({ page }, info) => {
      await page.goto(url(info, "/playback?view=list&date=2026-11-02"));
      await expect(page.getByRole("table")).toBeVisible();
      await expect(page.getByText(/Heures affichées dans le fuseau/)).toHaveCount(0);
    });
  });
});
