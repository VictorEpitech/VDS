import { expect, test, type Page, type Request, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string };
const failures = new WeakMap<Page, string[]>();
const requests = new WeakMap<Page, Request[]>();

test.beforeEach(async ({ page }, info) => {
  await page.context().addCookies([{ name: "vds_session", value: "e2e-admin", url: info.project.use.baseURL! }]);
  const list: string[] = [];
  const seen: Request[] = [];
  failures.set(page, list);
  requests.set(page, seen);
  page.on("request", (r) => seen.push(r));
  page.on("requestfailed", (r) => list.push(`failed ${r.url()}`));
  page.on("response", (r) => {
    if (r.status() >= 400) list.push(`${r.status()} ${r.url()}`);
  });
  page.on("pageerror", (e) => list.push(`error ${e.message}`));
});

test.afterEach(async ({ page }, info) => {
  expect(failures.get(page)).toEqual([]);
  // Garde-fou : l'admin ne parle qu'au serveur (aucun appareil du LAN, aucun tiers).
  const origin = new URL(info.project.use.baseURL!).origin;
  const foreign = (requests.get(page) ?? []).map((r) => r.url()).filter((u) => !u.startsWith("data:") && !u.startsWith("blob:") && new URL(u).origin !== origin);
  expect(foreign).toEqual([]);
});

const sent = (page: Page, method: string, re: RegExp) => (requests.get(page) ?? []).filter((r) => r.method() === method && re.test(new URL(r.url()).pathname));

test("allumer un écran en ligne depuis sa carte (envoi immédiat, sans délai)", async ({ page }, info) => {
  const { base } = meta(info);
  await page.goto(`${base}/screens`);
  const card = page.getByRole("listitem").filter({ has: page.getByRole("heading", { level: 2, name: "Hall d'accueil" }) });
  await expect(card.getByText("Accueil", { exact: false }).first()).toBeVisible();
  const on = card.getByRole("button", { name: "Allumer Hall d'accueil" });
  await expect(on).toBeEnabled();
  await on.click();
  await expect(card.getByText("Hall d'accueil est allumé.")).toBeVisible({ timeout: 5000 });
  expect(sent(page, "POST", /\/api\/v1\/sites\/[^/]+\/agents\/[^/]+\/commands$/)).toHaveLength(1);
  // Le base path est respecté par toutes les requêtes d'API.
  expect((requests.get(page) ?? []).filter((r) => r.url().includes("/api/v1/")).every((r) => new URL(r.url()).pathname.startsWith(`${base}/api/v1/`))).toBe(true);
});

test("action en lot puis annulation : rien n'est envoyé", async ({ page }, info) => {
  await page.goto(`${meta(info).base}/screens`);
  await page.getByRole("checkbox", { name: "Sélectionner Hall d'accueil" }).check();
  await page.getByRole("checkbox", { name: "Sélectionner Totem" }).check();
  const bar = page.getByRole("region", { name: "Actions sur la sélection" });
  await expect(bar.getByText("2 écrans sélectionnés")).toBeVisible();
  await bar.getByRole("button", { name: "Allumer", exact: true }).click();

  await expect(page.getByText("En attente d'envoi : Allumer 2 écrans. Vous pouvez encore annuler.")).toBeVisible();
  await expect(bar).toHaveCount(0);
  await page.getByRole("button", { name: "Annuler l'action" }).click();
  await expect(page.getByText(/En attente d'envoi/)).toHaveCount(0);
  // Au-delà du délai de 8 s, toujours aucune commande envoyée.
  await page.waitForTimeout(8_700);
  expect(sent(page, "POST", /\/commands$/)).toHaveLength(0);
  expect(sent(page, "DELETE", /\/screens\//)).toHaveLength(0);
});

test("suppression en lot : confirmation explicite, puis suppression des écrans choisis", async ({ page }, info) => {
  const { base } = meta(info);
  const tag = `Jetable ${info.project.name} ${info.workerIndex} ${Date.now()}`;
  const sites = (await (await page.request.get(`${base}/api/v1/sites`)).json()) as { id: string }[];
  for (const n of ["A", "B"]) {
    const r = await page.request.post(`${base}/api/v1/screens`, { data: { site_id: sites[0]!.id, name: `${tag} ${n}` } });
    expect(r.status()).toBe(201);
  }
  await page.goto(`${base}/screens`);
  await page.getByRole("checkbox", { name: `Sélectionner ${tag} A` }).check();
  await page.getByRole("checkbox", { name: `Sélectionner ${tag} B` }).check();
  const bar = page.getByRole("region", { name: "Actions sur la sélection" });
  await bar.getByRole("button", { name: "Supprimer", exact: true }).click();
  const dialog = bar.getByRole("alertdialog");
  await expect(dialog.getByText(/irréversible/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Supprimer définitivement 2 écrans" })).toBeFocused();
  // Sans confirmation, rien n'est parti.
  expect(sent(page, "DELETE", /\/screens\//)).toHaveLength(0);
  await dialog.getByRole("button", { name: "Supprimer définitivement 2 écrans" }).click();

  await expect(page.getByText("Terminé : Supprimer 2 écrans.")).toBeVisible({ timeout: 8000 });
  expect(sent(page, "DELETE", /\/api\/v1\/screens\/[^/]+$/)).toHaveLength(2);
  await expect(page.getByRole("heading", { level: 2, name: `${tag} A` })).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 2, name: "Hall d'accueil" })).toBeVisible();
});
