import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string };
const failures = new WeakMap<Page, string[]>();

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

const PAGES = [
  { path: "/sites", fr: "Sites", en: "Sites" },
  { path: "/power", fr: "Planning d'alimentation", en: "Power schedule" },
  { path: "/supervision", fr: "Supervision", en: "Monitoring" },
  { path: "/notifications", fr: "Notifications par e-mail", en: "Email notifications" },
] as const;

const AA = ["wcag2a", "wcag2aa", "wcag21aa"];

for (const p of PAGES) {
  for (const lang of ["fr", "en"] as const) {
    test(`${p.path} : accessibilité AA et 375 px en ${lang}`, async ({ page }, info) => {
      const { base } = meta(info);
      await page.goto(`${base}${p.path}`);
      if (lang === "en") await page.getByRole("button", { name: "English" }).click();
      await expect(page.getByRole("heading", { level: 1, name: p[lang] })).toBeVisible();
      await expect(page.getByText(/Chargement en cours|Loading/)).toHaveCount(0);
      const res = await new AxeBuilder({ page }).withTags(AA).analyze();
      expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
      await page.setViewportSize({ width: 375, height: 800 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
      for (const el of await page.locator("main a[href]:visible, main button:visible").all()) {
        const box = await el.boundingBox();
        expect(box!.height, await el.innerText()).toBeGreaterThanOrEqual(44);
      }
    });
  }
}

test("thème sombre : pages du lot 3 sans violation (si le thème le propose)", async ({ page }, info) => {
  test.skip(meta(info).theme !== "theme.vtech", "pas de mode sombre pour ce thème");
  await page.emulateMedia({ colorScheme: "dark" });
  for (const p of PAGES) {
    await page.goto(`${meta(info).base}${p.path}`);
    await expect(page.getByRole("heading", { level: 1, name: p.fr })).toBeVisible();
    await expect(page.getByText("Chargement en cours")).toHaveCount(0);
    const res = await new AxeBuilder({ page }).withTags(AA).analyze();
    expect(res.violations.map((v) => `${p.path} ${v.id}`)).toEqual([]);
  }
});

test("agent : le jeton renouvelé n'est affiché qu'une fois", async ({ page }, info) => {
  const { base } = meta(info);
  await page.goto(`${base}/sites`);
  await page.getByRole("link", { name: "Gérer le site Moulins" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Agent du site" })).toBeVisible();
  await page.getByRole("button", { name: "Renouveler le jeton" }).click();
  await page.getByRole("button", { name: "Renouveler le jeton de Serveur technique" }).click();
  const token = page.getByTestId("agent-token");
  await expect(token).toContainText("vds_agt_");
  await expect(page.getByRole("button", { name: "Copier le jeton" })).toBeVisible();
  await page.getByRole("button", { name: "J'ai copié le jeton" }).click();
  await expect(token).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { level: 2, name: "Agent du site" })).toBeVisible();
  await expect(token).toHaveCount(0);
});

test("planning : plan, rattachement hérité et retour à l'héritage au clavier", async ({ page }, info) => {
  await page.goto(`${meta(info).base}/power`);
  await page.getByRole("button", { name: "Nouveau plan" }).click();
  await page.getByLabel("Nom", { exact: true }).fill("Semaine bureau");
  await page.getByRole("button", { name: "Ajouter un créneau" }).click();
  await page.getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByRole("heading", { level: 3, name: "Semaine bureau" })).toBeVisible();

  const attachment = page.getByRole("region", { name: "Rattachement" });
  await attachment.getByLabel("Niveau").selectOption("organization");
  await attachment.getByLabel("Plan d'alimentation").selectOption({ label: "Semaine bureau" });
  await expect(attachment.getByText("Rattachement enregistré.")).toBeVisible();
  await attachment.getByLabel("Niveau").selectOption("site");
  await attachment.getByLabel("Site", { exact: true }).selectOption({ label: "Moulins" });
  await expect(attachment.getByText("Hérité de l'organisation").first()).toBeVisible();
  await expect(attachment.getByRole("button", { name: /Revenir à l'héritage/ })).toHaveCount(0);

  await attachment.getByLabel("Plan d'alimentation").selectOption({ label: "Aucun plan" });
  await expect(attachment.getByText("Défini ici").first()).toBeVisible();
  const revert = attachment.getByRole("button", { name: "Revenir à l'héritage : Plan d'alimentation" });
  await revert.focus();
  await page.keyboard.press("Enter");
  await expect(attachment.getByText("Hérité de l'organisation").first()).toBeVisible();
});

test("supervision : acquittement d'un incident et ordre d'allumage", async ({ page }, info) => {
  await page.goto(`${meta(info).base}/supervision`);
  await expect(page.getByRole("heading", { level: 3, name: "Agent hors ligne" })).toBeVisible();
  await page.getByRole("button", { name: "Acquitter : Agent hors ligne" }).click();
  await expect(page.locator(".status-badge", { hasText: "Acquitté" })).toBeVisible();

  const card = page.getByRole("listitem").filter({ has: page.getByRole("heading", { level: 3, name: "Hall d'accueil" }) });
  await expect(card.locator('.status-badge:not([data-state^="kiosk-"])')).toBeVisible();
  await card.getByRole("button", { name: "Allumer Hall d'accueil" }).click();
  await expect(card.getByText("Hall d'accueil est allumé.")).toBeVisible();
  await card.getByRole("button", { name: /Détecter le mode de pilotage/ }).click();
  await expect(card.getByText("power.wol")).toBeVisible();
});

test("pilotes : détection, proposition préremplie, enregistrement, cascade au clavier, 375 px", async ({ page }, info) => {
  const { base } = meta(info);
  await page.goto(`${base}/supervision`);
  const card = page.getByRole("listitem").filter({ has: page.getByRole("heading", { level: 3, name: "Hall d'accueil" }) });
  await card.getByRole("button", { name: /Détecter le mode de pilotage/ }).click();
  await card.getByRole("button", { name: "Appliquer la proposition du pilote power.wol" }).click();

  await expect(page.getByRole("heading", { level: 2, name: "Pilotes de l'écran" })).toBeFocused();
  const drivers = page.getByRole("region", { name: "Pilotes de l'écran" });
  const wol = drivers.getByRole("region", { name: /Wake-on-LAN/ });
  await expect(wol.getByLabel("Adresse MAC", { exact: true })).toHaveValue("aa:bb:cc:dd:ee:ff");
  await expect(drivers.getByText(/Proposition de la détection/)).toBeVisible();
  const res = await new AxeBuilder({ page }).withTags(AA).analyze();
  expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  await page.setViewportSize({ width: 375, height: 800 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  await page.setViewportSize({ width: 1280, height: 800 });

  await wol.getByRole("button", { name: "Enregistrer" }).click();
  await expect(wol.getByText("Réglages enregistrés.")).toBeVisible();
  await expect(wol.getByText("Défini ici").first()).toBeVisible();

  const down = drivers.getByRole("button", { name: "Descendre power.wol" });
  await down.focus();
  await page.keyboard.press("Enter");
  await drivers.getByRole("button", { name: "Enregistrer" }).last().click();
  await expect(drivers.getByText("Ordre enregistré.")).toBeVisible();
  await expect(drivers.getByRole("region", { name: /ADB/i })).toBeVisible();
  await drivers.getByRole("button", { name: /^Ajouter power\.\S+ à la cascade$/ }).first().click();
  await drivers.getByRole("button", { name: "Revenir à l'héritage : Ordre d'essai des pilotes" }).click();
  await expect(drivers.getByRole("button", { name: "Revenir à l'héritage : Ordre d'essai des pilotes" })).toHaveCount(0);

  await page.reload();
  await drivers.getByLabel("Écran", { exact: true }).selectOption({ label: "Hall d'accueil" });
  await expect(wol.getByLabel("Adresse MAC", { exact: true })).toHaveValue("aa:bb:cc:dd:ee:ff");
});

test("notifications : formulaire généré, connexion SMTP verrouillée, enregistrement", async ({ page }, info) => {
  await page.goto(`${meta(info).base}/notifications`);
  const admins = page.getByRole("checkbox", { name: "Prévenir les administrateurs" });
  await expect(admins).toBeChecked();
  await page.getByRole("textbox", { name: "Destinataires supplémentaires" }).fill("equipe@example.org");
  await page.getByText("Avancé").click();
  await expect(page.getByLabel("Serveur SMTP")).toBeDisabled();
  await expect(page.getByText("Variable VDS_SMTP_HOST")).toBeVisible();
  await admins.uncheck();
  await page.getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByText("Réglages enregistrés.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Prévenir les administrateurs" })).not.toBeChecked();
  await expect(page.getByRole("textbox", { name: "Destinataires supplémentaires" })).toHaveValue("equipe@example.org");
  await page.getByText("Avancé").click();
  await page.getByRole("button", { name: /Revenir à l'héritage : Prévenir les administrateurs/ }).click();
  await expect(page.getByRole("checkbox", { name: "Prévenir les administrateurs" })).toBeChecked();
});

test("CSP : l'écran se charge sous le préfixe sans erreur de console", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  await page.goto(`${meta(info).base}/supervision`);
  await expect(page.getByRole("heading", { level: 1, name: "Supervision" })).toBeVisible();
  expect(errors).toEqual([]);
});
