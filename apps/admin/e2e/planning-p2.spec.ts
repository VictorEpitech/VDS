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
  // Toutes les requêtes du planning (simulation comprise) passent par la base (VDS_BASE_PATH).
  const urls = apiUrls.get(page) ?? [];
  expect(urls.length).toBeGreaterThan(0);
  expect(urls.every((u) => u.startsWith(`${meta(info).base}/api/v1/planning/`))).toBe(true);
});

const axe = async (page: Page, label: string) => {
  const res = await new AxeBuilder({ page }).withTags(AA).analyze();
  expect(res.violations.map((v) => `${label} ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
};

const noShadow = (page: Page) =>
  page.evaluate(() => Array.from(document.querySelectorAll(".planning *, .schedule-form, .schedule-form *, .month *")).filter((el) => getComputedStyle(el).boxShadow !== "none").length);

const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

for (const lang of ["fr", "en"] as const) {
  const L = lang === "fr" ? { group: "Groupe", month: "Mois", day: "Jour", create: "Nouvelle programmation", name: "Nom", screen: "Écran", save: "Enregistrer", weekdays: "En semaine 8h–18h" } : { group: "Group", month: "Month", day: "Day", create: "New schedule", name: "Name", screen: "Screen", save: "Save", weekdays: "Weekdays 8am–6pm" };
  const english = async (page: Page) => {
    if (lang === "en") await page.getByRole("button", { name: "English" }).click();
  };

  test(`mois : grille de 42 jours, clavier, clic = vue Jour, AA, aucune ombre (${lang})`, async ({ page }, info) => {
    await page.goto(url(info, "/playback?view=month&date=2026-11-02"));
    await english(page);
    const grid = page.getByRole("grid");
    await expect(grid).toHaveCount(1);
    await expect(grid.getByRole("gridcell")).toHaveCount(42);
    await expect(grid.getByRole("columnheader")).toHaveCount(7);
    expect(await noShadow(page)).toBe(0);
    await axe(page, "mois");

    const tabbable = page.locator('.month-day[tabindex="0"]');
    await expect(tabbable).toHaveCount(1);
    await tabbable.focus();
    const first = await page.evaluate(() => (document.activeElement as HTMLElement).dataset.date);
    await page.keyboard.press("ArrowRight");
    const second = await page.evaluate(() => (document.activeElement as HTMLElement).dataset.date);
    expect(second).not.toBe(first);
    await page.keyboard.press("ArrowDown");
    const third = await page.evaluate(() => (document.activeElement as HTMLElement).dataset.date);
    expect(Date.parse(third!) - Date.parse(second!)).toBe(7 * 86_400_000);

    await page.keyboard.press("Enter");
    await expect(page.getByRole("radio", { name: L.day })).toBeChecked();
    await expect(page.getByRole("grid")).toHaveCount(1);
    expect(page.url()).toContain("view=day");
  });

  test(`groupe : lignes de ressources, clavier, nom d'écran = planning détaillé, AA (${lang})`, async ({ page }, info) => {
    await page.goto(url(info, "/playback?view=day&date=2026-11-02"));
    await english(page);
    await page.getByRole("radio", { name: L.group, exact: true }).click();
    const grid = page.getByRole("grid");
    await expect(grid).toHaveCount(1);
    // pistes des candidats puis une ligne par écran du groupe
    const names = grid.getByRole("button", { name: /Hall d'accueil/ });
    await expect(names).toHaveCount(1);
    expect(await noShadow(page)).toBe(0);
    await axe(page, "ressources");

    await page.locator('.tl-block[tabindex="0"]').focus();
    await page.keyboard.press("ArrowDown");
    expect(await page.evaluate(() => document.activeElement?.closest("[role=row]")?.getAttribute("aria-rowindex"))).toBe("2");
    // deux pistes de candidats, puis la ligne de l'écran
    await page.keyboard.press("ArrowDown");
    expect(await page.evaluate(() => document.activeElement?.closest("[role=row]")?.getAttribute("data-kind"))).toBe("screen");
    // à gauche du premier créneau : le nom de l'écran (colonne -1), puis Entrée ouvre son planning
    await page.keyboard.press("Home");
    await page.keyboard.press("ArrowLeft");
    expect(await page.evaluate(() => (document.activeElement as HTMLElement).dataset.rt)).toMatch(/:-1$/);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("grid").first().getByRole("rowheader")).toHaveCount(3);
  });

  test(`parcours n°3 : assistant, aperçu par simulation, enregistrement (${lang})`, async ({ page }, info) => {
    const playlist = `P2 ${Math.random().toString(36).slice(2, 8)}`;
    const schedule = `Prog ${Math.random().toString(36).slice(2, 8)}`;
    const created = await page.request.post(url(info, "/api/v1/playlists"), { data: { name: playlist } });
    expect(created.status()).toBe(201);
    await page.goto(url(info, "/playback?view=day&date=2026-11-02"));
    await english(page);
    await page.getByRole("button", { name: L.create }).first().click();
    const form = page.getByRole("form", { name: L.create });
    await form.getByLabel(L.name, { exact: true }).fill(schedule);
    await form.getByRole("combobox", { name: L.screen }).selectOption({ label: "Hall d'accueil" });
    await form.getByLabel("Playlist", { exact: true }).selectOption({ label: playlist });
    const region = form.locator("[aria-live=polite]");
    await expect(region).toContainText(new RegExp(lang === "fr" ? "Ajoute « .+ » sur Hall d'accueil" : "Adds “.+” on Hall d'accueil"), { timeout: 5000 });
    expect(await region.getAttribute("aria-live")).toBe("polite");
    await form.getByRole("button", { name: L.weekdays }).click();
    await expect(region).toContainText(new RegExp(lang === "fr" ? "Remplace « .+ » par « .+ » sur Hall d'accueil" : "Replaces “.+” with “.+” on Hall d'accueil"), { timeout: 5000 });
    expect(await noShadow(page)).toBe(0);
    await axe(page, "assistant");

    await form.getByRole("button", { name: L.save }).click();
    await expect(page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: schedule }) })).toContainText("08:00-18:00");
    await expect(page.getByRole("grid")).toHaveCount(1);
  });

  test(`créneau vide : clic ou Entrée ouvre l'assistant pré-rempli (${lang})`, async ({ page }, info) => {
    await page.goto(url(info, "/playback?view=day&date=2026-11-02"));
    await english(page);
    const empty = page.locator('.tl-row[data-kind="effective"] .tl-block[data-state="off"]').first();
    await expect(empty).toBeVisible();
    expect(await page.locator("[draggable=true]").count()).toBe(0);
    await empty.focus();
    await page.keyboard.press("Enter");
    const form = page.getByRole("form", { name: L.create });
    await expect(form).toBeVisible();
    await expect(form.getByRole("combobox", { name: L.screen })).not.toHaveValue("");
    await expect(form.getByLabel(lang === "fr" ? "De" : "From", { exact: true })).toHaveValue("00:00");
    await expect(form.getByLabel(lang === "fr" ? "À" : "To", { exact: true })).toHaveValue("08:00");
    await expect(form.getByLabel(lang === "fr" ? "Nom" : "Name", { exact: true })).toBeFocused();
    await axe(page, "assistant prérempli");
  });
}

test("375 px : mois et assistant sans débordement, AA", async ({ page }, info) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto(url(info, "/playback?view=month&date=2026-11-02"));
  await expect(page.getByRole("grid")).toHaveCount(1);
  expect(await noOverflow(page)).toBe(true);
  await axe(page, "375 mois");
  await page.getByRole("button", { name: "Nouvelle programmation" }).first().click();
  const form = page.getByRole("form", { name: "Nouvelle programmation" });
  await expect(form).toBeVisible();
  await form.getByRole("combobox", { name: "Écran" }).selectOption({ index: 1 });
  expect(await noOverflow(page)).toBe(true);
  const box = (await form.boundingBox())!;
  expect(box.x + box.width).toBeLessThanOrEqual(375);
  await axe(page, "375 assistant");
  expect(await noShadow(page)).toBe(0);
});

test("375 px : lignes de ressources du groupe sans débordement", async ({ page }, info) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto(url(info, "/playback?view=day&date=2026-11-02"));
  await page.getByRole("radio", { name: "Groupe", exact: true }).click();
  await expect(page.getByRole("grid")).toHaveCount(1);
  expect(await noOverflow(page)).toBe(true);
  await axe(page, "375 ressources");
});

test("P2-3 : tableau de bord et fiche écran lisent /planning/at", async ({ page }, info) => {
  await page.goto(url(info, "/"));
  await expect(page.getByRole("heading", { level: 2, name: "Prochains changements" })).toBeVisible();
  await axe(page, "tableau de bord");
  await page.goto(url(info, "/screens"));
  await expect(page.getByText("Prochain changement").first()).toBeVisible();
  const planningCalls = apiUrls.get(page) ?? [];
  expect(planningCalls.some((u) => u.endsWith("/planning/at"))).toBe(true);
});

test("thème sombre V-Tech : mois, ressources et assistant sans violation de contraste", async ({ page }, info) => {
  test.skip(meta(info).theme !== "theme.vtech", "pas de mode sombre pour ce thème");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(url(info, "/playback?view=month&date=2026-11-02"));
  await expect(page.getByRole("grid")).toHaveCount(1);
  await axe(page, "sombre mois");
  await page.getByRole("radio", { name: "Jour" }).click();
  await page.getByRole("radio", { name: "Groupe", exact: true }).click();
  await expect(page.getByRole("grid")).toHaveCount(1);
  await axe(page, "sombre ressources");
  await page.getByRole("button", { name: "Nouvelle programmation" }).first().click();
  await expect(page.getByRole("form", { name: "Nouvelle programmation" })).toBeVisible();
  await axe(page, "sombre assistant");
});
