import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string };
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

const T = {
  fr: { files: "Choisissez vos fichiers", input: "Fichiers à envoyer", send: "Envoyer 3 fichiers", refused: "Ce type de fichier n'est pas accepté.", next: "Continuer", add: "Ajouter à la playlist", done: "Import terminé", remove: "Retirer", gen: "Générer la commande", ran: "J'ai lancé la commande", connected: /Agent connecté il y a/, skip: "Passer cette étape", agentDone: "Agent connecté", copy: "Copier" },
  en: { files: "Choose your files", input: "Files to upload", send: "Upload 3 files", refused: "This file type is not accepted.", next: "Continue", add: "Add to playlist", done: "Import complete", remove: "Remove", gen: "Generate the command", ran: "I ran the command", connected: /Agent connected/, skip: "Skip this step", agentDone: "Agent connected", copy: "Copy" },
} as const;

test.beforeEach(async ({ page }, info) => {
  await page.context().addCookies([{ name: "vds_session", value: "e2e-admin", url: info.project.use.baseURL! }]);
  page.on("pageerror", (e) => {
    throw e;
  });
});

const axe = async (page: Page, label: string) => {
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(res.violations.map((v) => `${label} ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
};

for (const lang of ["fr", "en"] as const) {
  test(`import de 3 fichiers (1 refusé) puis ajout à une playlist (${lang}, 375 px, clavier)`, async ({ page }, info) => {
    const t = T[lang];
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto(`${meta(info).base}/content/media/add`);
    if (lang === "en") await page.getByRole("button", { name: "English" }).click();
    await expect(page.getByRole("heading", { name: t.files })).toBeVisible();
    await axe(page, "étape 1");
    const tag = `${info.project.name}-${lang}`;
    await page.getByLabel(t.input).setInputFiles([
      { name: `a-${tag}.png`, mimeType: "image/png", buffer: PNG },
      { name: `b-${tag}.png`, mimeType: "image/png", buffer: PNG },
      { name: `note-${tag}.txt`, mimeType: "text/plain", buffer: Buffer.from("hello") },
    ]);
    await page.getByRole("button", { name: t.send }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText(t.refused)).toBeVisible();
    await expect(page.getByRole("button", { name: new RegExp(`${t.remove}.*note-`) })).toBeVisible();
    await axe(page, "étape 2");
    // Le fichier refusé est retiré au clavier, puis on continue.
    await page.getByRole("button", { name: new RegExp(`${t.remove}.*note-`) }).focus();
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: t.next }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: t.add })).toBeVisible();
    await axe(page, "étape 3");
    await page.getByRole("button", { name: t.add }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: t.done })).toBeVisible();
    await axe(page, "étape 4");
  });

  test(`assistant « Connecter un agent » avec agent simulé (${lang}, 375 px)`, async ({ page }, info) => {
    const t = T[lang];
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto(`${meta(info).base}/sites`);
    if (lang === "en") await page.getByRole("button", { name: "English" }).click();
    await page.getByRole("link", { name: /^(Connecter un agent au site|Connect an agent to site)/ }).first().click();
    await page.getByRole("button", { name: t.gen }).click();
    const command = page.getByTestId("enroll-command");
    await expect(command).toContainText("VDS_SERVER_URL=https://vds.example");
    await expect(command).toContainText(meta(info).base);
    await expect(page.getByRole("button", { name: t.copy })).toBeVisible();
    await axe(page, "commande");
    await page.getByRole("button", { name: t.ran }).click();
    await expect(page.getByText(t.connected)).toBeVisible({ timeout: 15_000 });
    await axe(page, "connexion");
    await page.getByRole("button", { name: t.next }).click();
    await page.getByRole("button", { name: t.skip }).click();
    await expect(page.getByText(t.agentDone, { exact: true }).first()).toBeVisible();
    await axe(page, "résumé");
  });
}
