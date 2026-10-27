import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string };
const failures = new WeakMap<Page, string[]>();
const AA = ["wcag2a", "wcag2aa", "wcag21aa"];

const T = {
  fr: { name: "Nom de l'écran", next: "Continuer", ip: "Adresse IP de la TV", start: "Lancer l'installation", prompt: "Acceptez l'invite sur la TV et cochez « Toujours autoriser ».", done: "L'écran est en ligne : l'installation est validée.", soft: /Kiosque doux : les touches Accueil/, google: "Compte Google", tls: /n'accepte que le débogage sans fil chiffré/, retry: "Réessayer à cette étape", failed: "Échec" },
  en: { name: "Screen name", next: "Continue", ip: "TV IP address", start: "Start installation", prompt: "Accept the prompt on the TV and tick \"Always allow\".", done: "The screen is online: the installation is validated.", soft: /Soft kiosk: the TV's Home/, google: "Google account", tls: /only accepts encrypted wireless debugging/, retry: "Retry from this step", failed: "Failed" },
} as const;

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

async function expectAccessible(page: Page) {
  const res = await new AxeBuilder({ page }).withTags(AA).analyze();
  expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
}

async function toAddress(page: Page, lang: "fr" | "en", name: string) {
  const t = T[lang];
  await page.getByLabel(t.name).fill(name);
  await page.getByLabel(/^Site$/).selectOption({ index: 1 });
  await page.getByRole("button", { name: t.next }).click();
  await expect(page.getByRole("heading", { name: t.google })).toBeVisible();
  await expectAccessible(page);
  await page.getByRole("button", { name: t.next }).click();
}

for (const lang of ["fr", "en"] as const) {
  test(`assistant TV Android : invite RSA puis validation « En ligne » (${lang}, 375 px)`, async ({ page }, info) => {
    const t = T[lang];
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto(`${meta(info).base}/screens/android`);
    if (lang === "en") await page.getByRole("button", { name: "English" }).click();
    await toAddress(page, lang, `TV salon ${lang}`);
    await page.getByLabel(t.ip).fill("192.168.1.50");
    await expectAccessible(page);
    await page.getByRole("button", { name: t.start }).click();
    await expect(page.getByRole("status").filter({ hasText: t.prompt }).first()).toBeVisible();
    await expectAccessible(page);
    await expect(page.getByText(t.done)).toBeVisible({ timeout: 15_000 });
    await expectAccessible(page);
  });
}

test("assistant TV Android : tls_required lisible, puis reprise réussie", async ({ page }, info) => {
  await page.goto(`${meta(info).base}/screens/android`);
  await toAddress(page, "fr", "TV bureau");
  await page.getByLabel(T.fr.ip).fill("192.168.1.51");
  await page.getByRole("button", { name: T.fr.start }).click();
  await expect(page.getByRole("alert").filter({ hasText: T.fr.tls })).toBeVisible();
  await expectAccessible(page);
  await page.getByRole("button", { name: T.fr.retry }).click();
  await expect(page.getByText(T.fr.done)).toBeVisible({ timeout: 15_000 });
});

test("assistant TV Android : kiosque doux sans Device Owner, adresse publique refusée", async ({ page }, info) => {
  await page.goto(`${meta(info).base}/screens/android`);
  await toAddress(page, "fr", "TV accueil");
  await page.getByLabel(T.fr.ip).fill("8.8.8.8");
  await page.getByRole("button", { name: T.fr.start }).click();
  await expect(page.getByText("Utilisez l'adresse IP locale de la TV (réseau privé).")).toBeVisible();
  // Le 422 est le comportement attendu : on le retire du relevé d'erreurs réseau.
  failures.get(page)!.splice(0);
  await page.getByLabel(T.fr.ip).fill("192.168.1.52");
  await page.getByText("Avancé").click();
  await page.getByRole("button", { name: T.fr.start }).click();
  await expect(page.getByText(T.fr.soft)).toBeVisible({ timeout: 15_000 });
  await expectAccessible(page);
});

test("thème sombre : assistant sans violation (si le thème le propose)", async ({ page }, info) => {
  test.skip(meta(info).theme !== "theme.vtech", "pas de mode sombre pour ce thème");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(`${meta(info).base}/screens/android`);
  await toAddress(page, "fr", "TV sombre");
  await page.getByLabel(T.fr.ip).fill("192.168.1.52");
  await page.getByRole("button", { name: T.fr.start }).click();
  await expect(page.getByText(T.fr.done)).toBeVisible({ timeout: 15_000 });
  await expectAccessible(page);
});

test("l'assistant est atteignable au clavier depuis la liste des écrans", async ({ page }, info) => {
  await page.goto(`${meta(info).base}/screens`);
  await page.getByRole("link", { name: "Ajouter une TV Android" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Ajouter une TV Android" })).toBeVisible();
});
