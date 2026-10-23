import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const meta = (info: TestInfo) => info.project.metadata as { base: string; theme: string };
const url = (info: TestInfo, p: string) => `${meta(info).base}${p}`;
/** Nom unique par test et par projet : les tests d'un même projet partagent le serveur simulé. */
const uniq = (info: TestInfo, label: string) => `${label} ${info.project.name} ${Math.random().toString(36).slice(2, 7)}`;
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
const failures = new WeakMap<Page, string[]>();

test.beforeEach(async ({ page }, info) => {
  await page.context().addCookies([{ name: "vds_session", value: "e2e-admin", url: info.project.use.baseURL! }]);
  const list: string[] = [];
  failures.set(page, list);
  page.on("requestfailed", (r) => list.push(`failed ${r.url()}`));
  // Les 4xx attendus (409, 404) sont déclenchés volontairement : seules les ressources de l'app comptent.
  page.on("response", (r) => {
    if (r.status() >= 400 && !r.url().includes("/api/v1/")) list.push(`${r.status()} ${r.url()}`);
  });
  page.on("pageerror", (e) => list.push(`error ${e.message}`));
});

test.afterEach(async ({ page }) => {
  expect(failures.get(page)).toEqual([]);
});

async function upload(page: Page, name: string) {
  await page.getByLabel("Fichiers à envoyer").setInputFiles({ name, mimeType: "image/png", buffer: PNG });
}

async function createMedia(page: Page, info: TestInfo, title: string) {
  await page.goto(url(info, "/media"));
  await upload(page, `${title}.png`);
  const row = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: title }) });
  await expect(row.getByText("Prêt")).toBeVisible();
  return row;
}

async function createPlaylist(page: Page, info: TestInfo, name: string) {
  await page.goto(url(info, "/playlists"));
  await page.getByLabel("Nom de la playlist").fill(name);
  await page.getByRole("button", { name: "Créer la playlist" }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
}

test("parcours éditeur : média, playlist, programmation, appairage et révocation", async ({ page }, info) => {
  const title = uniq(info, "Affiche");
  const playlist = uniq(info, "Boucle");
  const schedule = uniq(info, "Semaine");

  // Médiathèque : envoi, traitement, statut prêt.
  await page.goto(url(info, "/media"));
  await expect(page.getByRole("heading", { level: 1, name: "Médiathèque" })).toBeVisible();
  await upload(page, `${title}.png`);
  await expect(page.getByText(`${title}.png`)).toBeVisible();
  await expect(page.getByText("Envoyé")).toBeVisible();
  const row = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: title }) });
  await expect(row.getByText("Prêt")).toBeVisible();

  // Playlist : élément image généré depuis le schéma, avancé replié.
  await createPlaylist(page, info, playlist);
  await page.getByRole("button", { name: "Ajouter un élément" }).click();
  const form = page.getByRole("form", { name: "Ajouter un élément" });
  await form.getByLabel(/^Image/).selectOption({ label: title });
  await form.getByLabel("Durée d'affichage (secondes)").fill("8");
  await form.getByRole("button", { name: "Ajouter" }).click();
  await expect(page.getByRole("heading", { level: 3, name: `Image - ${title}` })).toBeVisible();
  await expect(page.getByText("8 secondes")).toBeVisible();

  // Le média utilisé ne peut plus être supprimé (409).
  await page.goto(url(info, "/media"));
  const used = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: title }) });
  await used.getByRole("button", { name: "Supprimer", exact: true }).click();
  await used.getByRole("button", { name: `Supprimer ${title}` }).click();
  const alert = page.getByRole("alert").filter({ hasText: "ne peut pas être supprimé" });
  await expect(alert).toBeVisible();
  await expect(alert.getByText(playlist)).toBeVisible();

  // Programmation : cible écran, playlist, créneau en semaine.
  await page.goto(url(info, "/schedules"));
  await page.getByRole("button", { name: "Nouvelle programmation" }).click();
  await page.getByLabel("Nom", { exact: true }).fill(schedule);
  await page.getByRole("combobox", { name: "Écran" }).selectOption({ label: "Hall d'accueil" });
  await expect(page.getByText("1 écran touché")).toBeVisible();
  await page.getByLabel("Playlist", { exact: true }).selectOption({ label: playlist });
  await page.getByRole("button", { name: "Ajouter un créneau" }).click();
  await page.getByRole("button", { name: "samedi" }).click();
  await page.getByRole("button", { name: "Enregistrer" }).click();
  const item = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: schedule }) });
  await expect(item).toContainText("Hall d'accueil");
  await expect(item).toContainText(playlist);
  await expect(item).toContainText("08:00-18:00");

  // Appairage : code affiché sur l'écran + choix de l'écran.
  await page.goto(url(info, "/screens"));
  const card = (name: string) => page.getByRole("listitem").filter({ has: page.getByRole("heading", { level: 2, name }) });
  await expect(card("Hall d'accueil").getByText("Appareil en ligne")).toBeVisible();
  await expect(card("Hall d'accueil").getByText("android Tab A9")).toBeVisible();
  await expect(card("Totem").getByText("Aucun appareil appairé.")).toBeVisible();
  await expect(card("Totem").getByRole("button", { name: "Révoquer l'appareil" })).toHaveCount(0);
  await page.getByRole("link", { name: "Appairer un écran" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Appairer un écran" })).toBeVisible();
  await page.getByLabel("Code affiché sur l'écran").fill("ab3 k7p");
  await page.getByLabel("Écran à associer").selectOption({ label: "Totem" });
  await page.getByRole("button", { name: "Appairer" }).click();
  await expect(page.getByText(/Appairage validé pour l'écran Totem/)).toBeVisible();
  // Le même code ne sert qu'une fois.
  await page.getByLabel("Code affiché sur l'écran").fill("AB3K7P");
  await page.getByRole("button", { name: "Appairer" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Code inconnu" })).toBeVisible();

  // Révocation de l'appareil de l'écran.
  await page.getByRole("link", { name: "Retour aux écrans" }).click();
  const totem = card("Totem");
  await expect(totem.getByText("Appairé, jamais connecté")).toBeVisible();
  await totem.getByRole("button", { name: "Révoquer l'appareil" }).click();
  await totem.getByRole("button", { name: "Révoquer l'appareil de Totem" }).click();
  await expect(page.getByText(/appareil de l'écran Totem est révoqué/)).toBeVisible();
  await expect(totem.getByText("Appareil révoqué")).toBeVisible();
  await expect(totem.getByRole("button", { name: "Révoquer l'appareil" })).toHaveCount(0);
});

test("écrans à 375 px : état d'appareil lisible, sans débordement horizontal (EN)", async ({ page }, info) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto(url(info, "/screens"));
  await page.getByRole("button", { name: "English" }).click();
  await expect(page.getByText("Last seen").first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("playlist : réordonner au clavier garde le focus sur l'élément", async ({ page }, info) => {
  const name = uniq(info, "Ordre");
  await createPlaylist(page, info, name);
  for (const address of ["https://un.example.test", "https://deux.example.test"]) {
    await page.getByRole("button", { name: "Ajouter un élément" }).click();
    const form = page.getByRole("form", { name: "Ajouter un élément" });
    await form.getByLabel("Type de contenu").selectOption({ label: "Page web" });
    await expect(form.getByText("Avancé")).toBeVisible();
    await form.getByLabel(/^Adresse/).fill(address);
    await form.getByRole("button", { name: "Ajouter" }).click();
    await expect(page.getByRole("heading", { level: 3, name: new RegExp(address) })).toBeVisible();
  }
  const titles = () => page.getByRole("heading", { level: 3 }).allInnerTexts();
  await expect(titles()).resolves.toEqual(["Page web - https://un.example.test", "Page web - https://deux.example.test"]);
  const down = page.getByRole("button", { name: "Descendre Page web - https://un.example.test" });
  await down.focus();
  await page.keyboard.press("Enter");
  await expect.poll(titles).toEqual(["Page web - https://deux.example.test", "Page web - https://un.example.test"]);
  await expect(page.getByRole("button", { name: "Monter Page web - https://un.example.test" })).toBeFocused();
});

test("médiathèque : un fichier non accepté est refusé avec un message traduit", async ({ page }, info) => {
  await page.goto(url(info, "/media"));
  await page.getByLabel("Fichiers à envoyer").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("x") });
  await expect(page.getByText("Ce type de fichier n'est pas accepté.")).toBeVisible();
});

test("médiathèque : un média en échec affiche icône, libellé et cause", async ({ page }, info) => {
  const title = uniq(info, "broken");
  await page.goto(url(info, "/media"));
  await upload(page, `${title}.png`);
  const row = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: title }) });
  await expect(row.locator(".status-badge", { hasText: "Échec" }).locator(".vds-icon")).toBeVisible();
  await expect(row.getByText("Le contenu du fichier ne correspond pas à son type.")).toBeVisible();
});

test("anglais : médiathèque, programmation et appairage", async ({ page }, info) => {
  await page.goto(url(info, "/media"));
  await page.getByRole("button", { name: "English" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Media library" })).toBeVisible();
  await page.goto(url(info, "/schedules"));
  await expect(page.getByRole("heading", { level: 1, name: "Schedule" })).toBeVisible();
  await page.getByRole("button", { name: "New schedule" }).click();
  await expect(page.getByRole("radio", { name: "Screen" })).toBeChecked();
  await page.getByRole("button", { name: "Add a slot" }).click();
  await expect(page.getByRole("button", { name: "Saturday" })).toBeVisible();
  await page.goto(url(info, "/pairing"));
  await expect(page.getByLabel("Code shown on the screen")).toBeVisible();
});

const PAGES = ["/screens", "/pairing", "/media", "/playlists", "/schedules"] as const;

for (const lang of ["fr", "en"] as const) {
  test(`accessibilité AA (axe) des écrans du lot 2 en ${lang}`, async ({ page }, info) => {
    for (const p of PAGES) {
      await page.goto(url(info, p));
      if (lang === "en") await page.getByRole("button", { name: "English" }).click();
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      if (p === "/schedules") {
        await page.getByRole("button", { name: lang === "fr" ? "Nouvelle programmation" : "New schedule" }).click();
        await page.getByRole("button", { name: lang === "fr" ? "Ajouter un créneau" : "Add a slot" }).click();
        await page.getByText(lang === "fr" ? "Avancé" : "Advanced").click();
      }
      if (p === "/screens") await expect(page.getByRole("heading", { level: 2, name: "Hall d'accueil" })).toBeVisible();
      const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      expect(res.violations.map((v) => `${p} ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
    }
  });
}

test("accessibilité AA de l'éditeur de playlist et de son formulaire", async ({ page }, info) => {
  await createPlaylist(page, info, uniq(info, "Axe"));
  await page.getByRole("button", { name: "Ajouter un élément" }).click();
  await page.getByLabel("Type de contenu").selectOption({ label: "Page web" });
  await page.getByText("Avancé").click();
  await expect(page.getByLabel(/Rechargement/)).toBeVisible();
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
});

test("thème sombre V-Tech : écrans du lot 2 sans violation de contraste", async ({ page }, info) => {
  test.skip(meta(info).theme !== "theme.vtech", "pas de mode sombre pour ce thème");
  await page.emulateMedia({ colorScheme: "dark" });
  for (const p of PAGES) {
    await page.goto(url(info, p));
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    for (const lang of ["Français", "English"]) {
      await page.getByRole("button", { name: lang }).click();
      const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      expect(res.violations.map((v) => `${p} ${v.id}`)).toEqual([]);
    }
  }
});

test("375 px : pas de défilement horizontal, cibles de 44 px, en FR et EN", async ({ page }, info) => {
  await page.setViewportSize({ width: 375, height: 800 });
  const title = uniq(info, "Mobile");
  await createMedia(page, info, title);
  for (const lang of ["Français", "English"]) {
    for (const p of PAGES) {
      await page.goto(url(info, p));
      await page.getByRole("button", { name: lang }).click();
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      if (p === "/schedules") {
        await page.locator(".page-head button").click();
        await page.locator(".rules .actions button").first().click();
      }
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${p} ${lang}`).toBeLessThanOrEqual(0);
      for (const el of await page.locator("a[href]:visible, button:visible, select:visible, input:visible:not([type=checkbox]):not([type=radio]):not([type=file])").all()) {
        if ((await el.getAttribute("class"))?.includes("skip-link")) continue;
        const box = await el.boundingBox();
        expect(box!.height, `${p} ${lang} ${await el.evaluate((n) => n.outerHTML.slice(0, 80))}`).toBeGreaterThanOrEqual(43.5);
      }
    }
  }
});

test("liens et appels sous la base : aucune requête hors préfixe", async ({ page }, info) => {
  const outside: string[] = [];
  const base = meta(info).base;
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (!p.startsWith(`${base}/`) && p !== base) outside.push(p);
  });
  for (const p of PAGES) await page.goto(url(info, p));
  expect(outside).toEqual([]);
});
