import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string };
const url = (info: TestInfo, p: string) => `${meta(info).base}${p}`;
const uniq = (info: TestInfo, label: string) => `${label} ${info.project.name} ${Math.random().toString(36).slice(2, 7)}`;

async function as(page: Page, info: TestInfo, session: "e2e-admin" | "e2e-editor") {
  await page.context().clearCookies();
  await page.context().addCookies([{ name: "vds_session", value: session, url: info.project.use.baseURL! }]);
}

test("éditeur soumet, admin refuse, éditeur resoumet, admin valide", async ({ page }, info) => {
  const name = uniq(info, "Pub");
  await as(page, info, "e2e-editor");
  await page.goto(url(info, "/playlists"));
  await page.getByLabel("Nom de la playlist").fill(name);
  await page.getByRole("button", { name: "Créer la playlist" }).click();
  await expect(page.getByText("Brouillon, jamais publié")).toBeVisible();
  await page.getByRole("button", { name: "Soumettre pour validation" }).click();
  await page.getByRole("button", { name: "Soumettre", exact: true }).click();
  await expect(page.getByText("En revue", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retirer de la validation" })).toBeVisible();
  const editUrl = page.url();

  await as(page, info, "e2e-admin");
  await page.goto(url(info, "/playback/review"));
  await expect(page.getByRole("heading", { name: "Publications à valider", level: 1 })).toBeVisible();
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(axe.violations).toEqual([]);
  await page.getByRole("button", { name: `Refuser ${name}` }).click();
  await page.getByRole("button", { name: "Confirmer le refus" }).click();
  await expect(page.getByText("Ce champ est obligatoire.")).toBeVisible();
  await page.getByLabel(/Motif du refus/).fill("Titre à revoir");
  await page.getByRole("button", { name: "Confirmer le refus" }).click();
  await expect(page.getByText(name)).toHaveCount(0);

  await as(page, info, "e2e-editor");
  await page.goto(editUrl);
  await expect(page.getByText("Titre à revoir")).toBeVisible();
  await page.getByRole("button", { name: "Soumettre pour validation" }).click();
  await page.getByRole("button", { name: "Soumettre", exact: true }).click();
  await expect(page.getByText("En revue", { exact: true })).toBeVisible();

  await as(page, info, "e2e-admin");
  await page.goto(url(info, "/playback/review"));
  await page.getByRole("button", { name: `Valider ${name}` }).click();
  await expect(page.getByText(name)).toHaveCount(0);
  await page.goto(editUrl);
  await expect(page.getByText("Publié", { exact: true })).toBeVisible();
});

test("file vide et 375 px sans défilement horizontal", async ({ page }, info) => {
  await as(page, info, "e2e-admin");
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto(url(info, "/playback/review"));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("interface en anglais", async ({ page }, info) => {
  await as(page, info, "e2e-admin");
  await page.addInitScript(() => localStorage.setItem("vds.locale", "en"));
  await page.goto(url(info, "/playback/review"));
  await expect(page.getByRole("heading", { name: "Publications to review", level: 1 })).toBeVisible();
});
