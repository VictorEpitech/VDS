import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string };
const AA = ["wcag2a", "wcag2aa", "wcag21aa"];

const T = {
  fr: { helpBtn: "Aide : Écrans", panel: "Écrans", search: "Rechercher", dialog: "Palette de commandes", combo: "Rechercher une page, un élément ou une action", page: "Aide", glossary: "Glossaire", neutral: /Un clic sur un écran ouvre/, formal: /Vous voyez son état/, more: "Ouvrir l'aide", query: "aide" },
  en: { helpBtn: "Help: Screens", panel: "Screens", search: "Search", dialog: "Command palette", combo: "Search for a page, an item or an action", page: "Help", glossary: "Glossary", neutral: /Selecting a screen opens/, formal: /You see its state/, more: "Open help", query: "open help" },
} as const;

test.beforeEach(async ({ page }, info) => {
  await page.context().addCookies([{ name: "vds_session", value: "e2e-admin", url: info.project.use.baseURL! }]);
  page.on("pageerror", (e) => {
    throw e;
  });
});

const axe = async (page: Page, label: string) => {
  const res = await new AxeBuilder({ page }).withTags(AA).analyze();
  expect(res.violations.map((v) => `${label} ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
};
const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);

for (const lang of ["fr", "en"] as const) {
  for (const width of [1280, 375]) {
    test(`panneau « ? » et ton du thème (${lang}, ${width} px)`, async ({ page }, info) => {
      const t = T[lang];
      await page.setViewportSize({ width, height: 800 });
      await page.goto(`${meta(info).base}/screens`);
      if (lang === "en") await page.getByRole("button", { name: "English" }).click();
      const button = page.getByRole("button", { name: t.helpBtn });
      await button.focus();
      await page.keyboard.press("Enter");
      await expect(button).toHaveAttribute("aria-expanded", "true");
      await expect(page.getByRole("complementary", { name: t.panel }).getByRole("heading")).toBeFocused();
      // Le ton vient du module de thème actif (jeton de sa feuille), jamais du mode.
      await expect(page.getByText(meta(info).theme === "theme.allia" ? t.formal : t.neutral)).toBeVisible();
      await axe(page, "panneau");
      await noOverflow(page);
      await page.keyboard.press("Escape");
      await expect(button).toHaveAttribute("aria-expanded", "false");
      await expect(button).toBeFocused();
    });

    test(`palette Ctrl+K et page Aide (${lang}, ${width} px)`, async ({ page }, info) => {
      const t = T[lang];
      await page.setViewportSize({ width, height: 800 });
      await page.goto(`${meta(info).base}/`);
      if (lang === "en") await page.getByRole("button", { name: "English" }).click();
      await expect(page.getByRole("button", { name: t.search })).toBeVisible();
      if (width === 375) await page.getByRole("button", { name: t.search }).click();
      else await page.keyboard.press("Control+k");
      await expect(page.getByRole("dialog", { name: t.dialog })).toBeVisible();
      await expect(page.getByRole("combobox", { name: t.combo })).toBeFocused();
      await page.keyboard.type(t.query);
      await axe(page, "palette");
      await noOverflow(page);
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(new RegExp(`${meta(info).base}/help$`));
      await expect(page.getByRole("heading", { level: 1, name: t.page })).toBeVisible();
      await expect(page.getByRole("heading", { level: 2, name: t.glossary })).toBeVisible();
      await axe(page, "page Aide");
      await noOverflow(page);
    });
  }
}

test("Plus › Aide : lien et route sous la base", async ({ page }, info) => {
  await page.goto(`${meta(info).base}/more`);
  await page.getByRole("link", { name: "Ouvrir l'aide" }).click();
  await expect(page).toHaveURL(new RegExp(`${meta(info).base}/help$`));
});

test("thème sombre V-Tech : panneau d'aide et palette sans violation AA", async ({ page }, info) => {
  test.skip(meta(info).theme !== "theme.vtech", "le thème sombre est celui de V-Tech");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(`${meta(info).base}/screens`);
  await page.getByRole("button", { name: "Aide : Écrans" }).click();
  await axe(page, "panneau sombre");
  await page.keyboard.press("Escape");
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog", { name: "Palette de commandes" })).toBeVisible();
  await axe(page, "palette sombre");
});
