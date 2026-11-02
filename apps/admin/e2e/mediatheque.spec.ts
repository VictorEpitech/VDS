import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string };
const uniq = (info: TestInfo, label: string) => `${label} ${info.project.name} ${Math.random().toString(36).slice(2, 7)}`;
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
const JPG = Buffer.from("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=", "base64");

test.beforeEach(async ({ page }, info) => {
  await page.context().addCookies([{ name: "vds_session", value: "e2e-admin", url: info.project.use.baseURL! }]);
  page.on("pageerror", (e) => {
    throw e;
  });
});

async function upload(page: Page, name: string) {
  await page.getByLabel("Fichiers à envoyer").setInputFiles({ name, mimeType: "image/png", buffer: PNG });
}

async function openLibrary(page: Page, info: TestInfo) {
  await page.goto(`${meta(info).base}/content/media`);
  await expect(page.getByRole("heading", { level: 1, name: "Médiathèque" })).toBeVisible();
}

const axe = async (page: Page, label: string) => {
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(res.violations.map((v) => `${label} ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
};

test("détail : tags, expiration, usage, remplacement (impact) et bascule tableau mémorisée", async ({ page }, info) => {
  const title = uniq(info, "Affiche");
  const tag = `t${Math.random().toString(36).slice(2, 6)}`;
  await openLibrary(page, info);
  await upload(page, `${title}.png`);
  await page.getByRole("button", { name: title, exact: true }).click();
  const panel = page.getByRole("complementary", { name: title });
  await expect(panel).toBeVisible();
  await expect(panel.locator("details.advanced")).not.toHaveAttribute("open", "");
  await expect(panel.getByText("Ce média n'est utilisé nulle part.")).toBeVisible();

  await panel.getByLabel("Tags", { exact: true }).fill(tag);
  await panel.getByRole("button", { name: "Ajouter le tag" }).click();
  await expect(panel.getByText(tag, { exact: true })).toBeVisible();
  await panel.getByLabel("Date").fill("2099-05-04");
  await panel.getByLabel("Heure").fill("18:30");
  await panel.getByRole("button", { name: "Enregistrer" }).click();
  await expect(panel.getByRole("button", { name: "Retirer l'expiration" })).toBeVisible();
  await expect(panel.getByText(/Expire le/)).toBeVisible();

  await panel.getByRole("button", { name: "Remplacer le fichier" }).click();
  await expect(panel.getByText(/Cette opération touche 0 playlist, 0 écran\./)).toBeVisible();
  await panel.getByLabel("Nouveau fichier").setInputFiles({ name: "nouveau.png", mimeType: "image/png", buffer: JPG });
  await panel.getByRole("button", { name: "Remplacer maintenant" }).click();
  await expect(panel.getByText(/Le fichier est remplacé/)).toBeVisible();

  // Filtre par tag (avancé) puis bascule en tableau, mémorisée après rechargement.
  await page.getByRole("button", { name: "Fermer le détail" }).click();
  await page.getByRole("search").getByText("Avancé").click();
  await page.getByRole("search").getByLabel("Filtrer par tags").fill(tag);
  await page.getByRole("search").getByRole("button", { name: "Ajouter le tag" }).click();
  await expect(page.getByRole("button", { name: title, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Tableau" }).click();
  await expect(page.getByRole("table")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("table")).toBeVisible();
});

test("sélection multiple : expiration groupée, clavier et 375 px", async ({ page }, info) => {
  const a = uniq(info, "Lot A");
  const b = uniq(info, "Lot B");
  await openLibrary(page, info);
  await upload(page, `${a}.png`);
  await upload(page, `${b}.png`);
  await page.getByRole("checkbox", { name: `Sélectionner ${a}` }).check();
  await page.getByRole("checkbox", { name: `Sélectionner ${b}` }).press("Space");
  await expect(page.getByText("2 médias sélectionnés")).toBeVisible();
  await page.getByRole("button", { name: "Définir l'expiration" }).click();
  const bulk = page.getByRole("region", { name: "Actions groupées" });
  await bulk.getByLabel("Date").fill("2099-01-02");
  await bulk.getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByText(/2 médias modifiés/)).toBeVisible();
  await expect(page.getByText(/Expire le/).first()).toBeVisible();

  await page.setViewportSize({ width: 375, height: 800 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Tableau" }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

for (const lang of ["Français", "English"]) {
  test(`accessibilité AA (axe) : médiathèque, détail, playlists, en ${lang}`, async ({ page }, info) => {
    const title = uniq(info, "Axe");
    await openLibrary(page, info);
    await upload(page, `${title}.png`);
    await page.getByRole("button", { name: lang }).click();
    await page.getByRole("checkbox").nth(1).check();
    await axe(page, `${lang} grille`);
    await page.getByRole("button", { name: /Table|Tableau/ }).click();
    await axe(page, `${lang} tableau`);
    await page.getByRole("button", { name: title, exact: true }).click();
    await expect(page.getByRole("complementary")).toBeVisible();
    await axe(page, `${lang} détail`);
    await page.setViewportSize({ width: 375, height: 800 });
    await axe(page, `${lang} 375`);
    await page.goto(`${meta(info).base}/content/playlists`);
    await axe(page, `${lang} playlists`);
  });
}

test("thème sombre V-Tech : médiathèque sans violation de contraste", async ({ page }, info) => {
  test.skip(meta(info).theme !== "theme.vtech", "pas de mode sombre pour ce thème");
  await page.emulateMedia({ colorScheme: "dark" });
  await openLibrary(page, info);
  await upload(page, `${uniq(info, "Sombre")}.png`);
  await page.getByRole("checkbox").nth(1).check();
  await axe(page, "sombre");
});
