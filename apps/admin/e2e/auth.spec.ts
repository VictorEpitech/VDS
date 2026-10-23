import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const PASSWORD = "correct-horse-battery";
const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string; auth: string };
const local = (info: TestInfo) => meta(info).auth === "auth.local";
const path = (info: TestInfo, p: string) => `${meta(info).base}${p}`;
const apiPath = (info: TestInfo, p: string) => `**${meta(info).base}/api/v1${p}`;
const here = (page: Page, info: TestInfo) => new URL(page.url()).pathname.slice(meta(info).base.length) || "/";

async function signIn(page: Page, email: string, password = PASSWORD) {
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByLabel("Mot de passe").fill(password);
  await page.getByRole("button", { name: "Se connecter" }).click();
}

async function uninitialized(page: Page, info: TestInfo) {
  await page.route(apiPath(info, "/m/auth.local/status"), (r) => r.fulfill({ json: { initialized: false } }));
}

async function fillBootstrap(page: Page) {
  await page.getByLabel("Adresse e-mail").fill("root@vds.test");
  await page.getByLabel("Nom affiché").fill("Root");
  await page.getByLabel("Mot de passe").fill("a-long-enough-secret");
  await page.getByLabel("Jeton d'amorçage").fill("bootstrap-token");
}

test.describe("auth.local", () => {
  // eslint-disable-next-line no-empty-pattern
  test.beforeEach(({}, info) => {
    test.skip(!local(info), "formulaires fournis par auth.local uniquement");
  });

  test("non connecté : connexion avec return_to sous le préfixe, puis retour à la page demandée et déconnexion", async ({ page }, info) => {
    await page.goto(path(info, "/screens"));
    await expect(page.getByRole("heading", { level: 1, name: "Connexion" })).toBeVisible();
    expect(here(page, info)).toBe("/login");
    expect(new URL(page.url()).searchParams.get("return_to")).toBe(path(info, "/screens"));
    await signIn(page, "ada@vds.test");
    await expect(page.getByRole("heading", { level: 1, name: "Écrans" })).toBeVisible();
    expect(here(page, info)).toBe("/screens");
    await expect(page.locator(".app-user")).toHaveText("Ada");
    await page.getByRole("button", { name: "Se déconnecter" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Connexion" })).toBeVisible();
    await page.goto(path(info, "/"));
    await expect(page.getByRole("heading", { level: 1, name: "Connexion" })).toBeVisible();
  });

  test("return_to externe ou hors préfixe ignoré", async ({ page }, info) => {
    await page.goto(path(info, "/login?return_to=https%3A%2F%2Fevil.test%2F"));
    await signIn(page, "ada@vds.test");
    await expect(page.getByRole("heading", { level: 1, name: "Tableau de bord" })).toBeVisible();
    expect(new URL(page.url()).origin).toBe(new URL(info.project.use.baseURL!).origin);
  });

  test("erreurs : identifiants (FR), trop de tentatives (EN) avec icône et libellé", async ({ page }, info) => {
    await page.goto(path(info, "/login"));
    await signIn(page, "ada@vds.test", "mauvais");
    const alert = page.getByRole("alert").filter({ hasText: "Identifiants incorrects." });
    await expect(alert).toBeVisible();
    await expect(alert.locator(".vds-icon")).toBeVisible();
    await page.getByRole("button", { name: "English" }).click();
    await page.getByLabel("Email address").fill("locked@vds.test");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Too many sign-in attempts. Try again later.")).toBeVisible();
  });

  test("403 changement de mot de passe requis => écran dédié", async ({ page }, info) => {
    await page.goto(path(info, "/login"));
    await signIn(page, "temp@vds.test");
    await expect(page.getByRole("heading", { level: 1, name: "Changer le mot de passe" })).toBeVisible();
    expect(here(page, info)).toBe("/change-password");
    await page.getByLabel("Mot de passe actuel").fill(PASSWORD);
    await page.getByLabel("Nouveau mot de passe").fill("another-long-password");
    await page.getByRole("button", { name: "Changer le mot de passe" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Tableau de bord" })).toBeVisible();
  });

  test("amorçage : déjà initialisé => redirection vers la connexion", async ({ page }, info) => {
    await page.goto(path(info, "/bootstrap"));
    await expect(page.getByRole("heading", { level: 1, name: "Connexion" })).toBeVisible();
    expect(here(page, info)).toBe("/login");
  });

  test("amorçage : formulaire, 409 => connexion, succès => tableau de bord", async ({ page }, info) => {
    await uninitialized(page, info);
    await page.route(apiPath(info, "/m/auth.local/bootstrap"), (r) => r.fulfill({ status: 409, json: { code: "auth_local.already_initialized", params: {} } }));
    await page.goto(path(info, "/bootstrap"));
    await expect(page.getByRole("heading", { level: 1, name: "Premier démarrage" })).toBeVisible();
    await fillBootstrap(page);
    await page.getByRole("button", { name: "Créer le compte" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Connexion" })).toBeVisible();
    expect(here(page, info)).toBe("/login");

    await page.unroute(apiPath(info, "/m/auth.local/bootstrap"));
    await page.unroute(apiPath(info, "/m/auth.local/status"));
    await uninitialized(page, info);
    await page.route(apiPath(info, "/m/auth.local/bootstrap"), (r) => {
      expect(r.request().postDataJSON()).toEqual({
        email: "root@vds.test",
        display_name: "Root",
        password: "a-long-enough-secret",
        bootstrap_token: "bootstrap-token",
      });
      return r.fulfill({
        status: 201,
        json: { redirect_to: path(info, "/") },
        headers: { "Set-Cookie": `vds_session=e2e-admin; Path=${meta(info).base || "/"}` },
      });
    });
    await page.goto(path(info, "/bootstrap"));
    await fillBootstrap(page);
    await page.getByRole("button", { name: "Créer le compte" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Tableau de bord" })).toBeVisible();
  });

  test("clavier : parcours des champs et envoi par Entrée", async ({ page }, info) => {
    await page.goto(path(info, "/login"));
    await page.getByRole("heading", { level: 1 }).waitFor();
    await page.getByLabel("Adresse e-mail").focus();
    await page.keyboard.type("ada@vds.test");
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("Mot de passe")).toBeFocused();
    await page.keyboard.type(PASSWORD);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { level: 1, name: "Tableau de bord" })).toBeVisible();
  });

  for (const lang of ["fr", "en"] as const) {
    test(`accessibilité AA (axe) : connexion, amorçage, erreurs, en ${lang}`, async ({ page }, info) => {
      await uninitialized(page, info);
      const axe = async () => {
        const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
        expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
      };
      await page.goto(path(info, "/login"));
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      if (lang === "en") await page.getByRole("button", { name: "English" }).click();
      await axe();
      await page.locator("form button[type=submit]").click(); // erreurs de champs
      await expect(page.locator(".field-error").first()).toBeVisible();
      await axe();
      await page.goto(path(info, "/bootstrap"));
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await axe();
    });
  }

  test("thème sombre V-Tech : connexion lisible (axe)", async ({ page }, info) => {
    test.skip(meta(info).theme !== "theme.vtech", "mode sombre V-Tech uniquement");
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto(path(info, "/login"));
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.locator("form button[type=submit]").click();
    for (const lang of ["Français", "English"]) {
      await page.getByRole("button", { name: lang }).click();
      const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      expect(res.violations.map((v) => v.id)).toEqual([]);
    }
  });

  test("375 px : pas de défilement horizontal, cibles de 44 px, FR et EN", async ({ page }, info) => {
    await uninitialized(page, info);
    await page.setViewportSize({ width: 375, height: 800 });
    for (const route of ["/login", "/bootstrap"]) {
      await page.goto(path(info, route));
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      for (const lang of ["Français", "English"]) {
        await page.getByRole("button", { name: lang }).click();
        expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
        for (const el of await page.locator("input:visible, button:visible, main a[href]:visible").all()) {
          const box = await el.boundingBox();
          expect(box!.height, `${route} ${lang}`).toBeGreaterThanOrEqual(44);
        }
      }
    }
  });
});

test("mode hébergé : pas d'écran de connexion local, la session vient du Studio", async ({ page }, info) => {
  test.skip(local(info), "mode hébergé uniquement");
  await page.goto(path(info, "/login"));
  await expect(page.getByRole("heading", { level: 1, name: "Tableau de bord" })).toBeVisible();
  await expect(page.locator(".app-user")).toHaveText("Camille");
  await expect(page.getByRole("link", { name: "Se déconnecter" })).toHaveAttribute("href", "/espace/deconnexion");
});
