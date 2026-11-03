import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { atOut } from "../../../test/planning-at";
import { errorResponse, mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";
import { NextChanges } from "./NextChanges";
import { ROW_PX } from "./ResourceTimeline";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const GROUP = { id: "20000000-0000-4000-8000-000000000001", site_id: SITE.id, name: "Accueil" };
const SCREEN = { id: "30000000-0000-4000-8000-000000000001", site_id: SITE.id, group_id: GROUP.id, name: "Hall", orientation: "landscape", archived_at: null, device: null };
const PL_A = { id: "50000000-0000-4000-8000-00000000000a", name: "Boucle accueil", description: null, item_count: 1, created_at: "2026-10-01T08:00:00Z", updated_at: "2026-10-01T08:00:00Z" };
const PL_B = { ...PL_A, id: "50000000-0000-4000-8000-00000000000b", name: "Boucle promo" };
const SRC = { kind: "schedule", id: "60000000-0000-4000-8000-00000000000a", name: "Accueil", target_type: "site", target_id: SITE.id, target_name: "Moulins", inherited: true, priority: 0, priority_level: "normal" };
const NONE = { kind: "none", id: null, name: null, target_type: null, target_id: null, target_name: null, inherited: false, priority: null, priority_level: null };
const CONTENT_A = { kind: "playlist", id: PL_A.id, name: PL_A.name };
const CONTENT_B = { kind: "playlist", id: PL_B.id, name: PL_B.name };

/** Heure de Paris (UTC+1 en novembre), `day` = jour de novembre 2026. */
const at = (day: number, h: number, m = 0) => new Date(Date.UTC(2026, 10, day, h - 1, m)).toISOString();
const seg = (day: number, s: number, e: number, play: boolean, content = CONTENT_A) => ({
  start: at(day, s),
  end: at(day, e),
  state: play ? "play" : "off",
  content: play ? content : null,
  source: play ? SRC : NONE,
  reason: play ? "only_candidate" : "no_candidate",
  losers: [],
});
const DAY = [seg(2, 0, 8, false), seg(2, 8, 12, true), seg(2, 12, 18, true, CONTENT_B), seg(2, 18, 24, false)];
const lane = { source: SRC, content: CONTENT_A, occurrences: [[at(2, 8), at(2, 12)]], effective_ratio: 1 };

const mkScreen = (i: number) => ({ id: `30000000-0000-4000-8000-${String(i + 100).padStart(12, "0")}`, site_id: SITE.id, group_id: GROUP.id, name: `Écran ${i + 1}`, orientation: "landscape", archived_at: null, device: null });
const timeline = (screens: { id: string; name: string }[], next: string | null, extra: Record<string, unknown> = {}, segments: unknown[] = DAY) => ({
  format: 1,
  target_type: "group",
  target_id: GROUP.id,
  window: [at(2, 0), at(3, 0)],
  timezone: "Europe/Paris",
  mixed_timezones: false,
  lanes: [lane],
  screens: screens.map((s) => ({ screen_id: s.id, screen_name: s.name, timezone: "Europe/Paris", segments, markers: [], conflicts: [] })),
  next_cursor: next,
  computed_at: at(2, 9),
  ...extra,
});

const base = (screens: unknown[], extra: Record<string, unknown> = {}) => ({
  "GET /sites": [SITE],
  [`GET /sites/${SITE.id}/groups`]: [GROUP],
  "GET /screens": screens,
  "GET /schedules": [],
  "GET /overrides": [],
  "GET /incidents": [],
  "GET /playlists": { items: [PL_A, PL_B], total: 2 },
  "GET /planning/at": { at: at(2, 9), next_cursor: null, screens: [] },
  ...extra,
});

describe.each(["", "/espace/affichage"])("ResourceTimeline, base « %s »", (b) => {
  it("100 écrans : lignes fenêtrées, défilement, clavier (↓, Ctrl+Fin), fuseaux mélangés signalés", async () => {
    const all = Array.from({ length: 100 }, (_, i) => mkScreen(i));
    const calls = mockApi(base(all, { "GET /planning/timeline": timeline(all, null, { mixed_timezones: true }) }), b);
    await renderRoutes(routes, `/playback?target=group:${GROUP.id}&view=day&date=2026-11-02`, b);
    const grid = await screen.findByRole("grid");
    expect(grid.getAttribute("aria-rowcount")).toBe("101");
    const rendered = within(grid).getAllByRole("row");
    expect(rendered.length).toBeGreaterThan(5);
    expect(rendered.length).toBeLessThan(40);
    expect(within(grid).queryByText("Écran 90")).toBeNull();
    // fuseaux mélangés : mention dans la barre d'outils et fuseau de chaque ligne
    expect(screen.getByText(/n'ont pas tous le même fuseau/)).toBeInTheDocument();
    expect(within(grid).getAllByText("Europe/Paris").length).toBeGreaterThan(0);
    // explain=false et limite de page pour la liste de ressources
    const path = calls.find((c) => c.path.startsWith("/planning/timeline"))!.path;
    expect(path).toContain("explain=false");
    expect(path).toContain("limit=100");

    const viewport = grid.closest(".rt-viewport") as HTMLElement;
    viewport.scrollTop = ROW_PX * 85;
    fireEvent.scroll(viewport);
    expect(await within(grid).findByText("Écran 86")).toBeInTheDocument();
    expect(within(grid).queryByText("Écran 2")).toBeNull();

    // un seul bloc dans l'ordre de tabulation ; ↓ passe à la ligne suivante, Ctrl+Fin à la dernière (même hors fenêtre)
    expect(grid.querySelectorAll('.tl-block[tabindex="0"]')).toHaveLength(1);
    const first = grid.querySelector<HTMLElement>('.tl-block[tabindex="0"]')!;
    first.focus();
    await userEvent.keyboard("{ArrowDown}");
    expect((document.activeElement as HTMLElement).dataset.key).toMatch(/^r1:c/);
    await userEvent.keyboard("{Control>}{End}{/Control}");
    await waitFor(() => expect((document.activeElement as HTMLElement).dataset.key).toMatch(/^r100:c/));
    expect(within(grid).getByText("Écran 100")).toBeInTheDocument();
  });

  it("pagination par curseur : 100 écrans par page, page suivante demandée avec le curseur", async () => {
    const all = Array.from({ length: 130 }, (_, i) => mkScreen(i));
    const calls = mockApi(
      base(all, { "GET /planning/timeline": (path: string) => (path.includes("cursor=100") ? timeline(all.slice(100), null) : timeline(all.slice(0, 100), "100")) }),
      b,
    );
    await renderRoutes(routes, `/playback?target=group:${GROUP.id}&view=day&date=2026-11-02`, b);
    await screen.findByRole("grid");
    expect(await screen.findByText("Page 1 : 100 écrans")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Écrans suivants" }));
    expect(await screen.findByText("Page 2 : 30 écrans")).toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/planning/timeline") && c.path.includes("cursor=100"))).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Écrans précédents" }));
    expect(await screen.findByText("Page 1 : 100 écrans")).toBeInTheDocument();
  });

  it("le nom d'un écran ouvre son planning détaillé", async () => {
    const all = [mkScreen(0), mkScreen(1)];
    mockApi(base([...all, SCREEN], { "GET /planning/timeline": (path: string) => (path.includes("target_type=screen") ? timeline([all[0]!], null, { target_type: "screen" }) : timeline(all, null)) }), b);
    await renderRoutes(routes, `/playback?target=group:${GROUP.id}&view=day&date=2026-11-02`, b);
    await userEvent.click(await screen.findByRole("button", { name: "Voir le planning de l'écran Écran 1" }));
    const grid = await screen.findByRole("grid", { name: /Planning du/ });
    expect(within(grid).getAllByRole("rowheader")).toHaveLength(2);
  });
});

describe.each(["", "/espace/affichage"])("MonthGrid, base « %s »", (b) => {
  const NOV3 = [seg(3, 8, 9, true), seg(3, 9, 10, true, CONTENT_B), seg(3, 10, 11, true), seg(3, 11, 12, true, CONTENT_B), seg(3, 12, 13, true)];
  const screenTl = () => ({
    ...timeline([SCREEN], null, { target_type: "screen", target_id: SCREEN.id }),
    screens: [
      {
        screen_id: SCREEN.id,
        screen_name: "Hall",
        timezone: "Europe/Paris",
        segments: [...DAY, ...NOV3],
        markers: [{ at: at(4, 16), kind: "playlist_expires", ref_id: PL_A.id, name: PL_A.name, impact: "gap" }],
        conflicts: [{ kind: "content_expires", severity: "warning", source: SRC, other: null, first_at: at(4, 16), occurrences: 1 }],
      },
    ],
  });

  it("six semaines, 3 segments + « +N », badges conflit et expiration ; clic = vue Jour ; clavier", async () => {
    const calls = mockApi(base([SCREEN], { "GET /planning/timeline": screenTl() }), b);
    await renderRoutes(routes, `/playback?target=screen:${SCREEN.id}&view=month&date=2026-11-02`, b);
    const grid = await screen.findByRole("grid", { name: "Calendrier de novembre 2026" });
    expect(within(grid).getAllByRole("gridcell")).toHaveLength(42);
    expect(within(grid).getAllByRole("columnheader")).toHaveLength(7);
    // semaine du lundi en français : la grille démarre le 26 octobre et couvre 42 jours
    const tl = calls.find((c) => c.path.startsWith("/planning/timeline"))!.path;
    expect(tl).toContain("from=2026-10-26&to=2026-12-06");
    const nov3 = within(grid).getByRole("button", { name: /mardi 3 novembre/ });
    expect(nov3.getAttribute("aria-label")).toMatch(/5 créneaux.*\+2/);
    expect(nov3.querySelectorAll(".month-seg")).toHaveLength(3);
    expect(within(nov3).getByText("+2")).toBeInTheDocument();
    const nov4 = within(grid).getByRole("button", { name: /mercredi 4 novembre/ });
    expect(nov4.getAttribute("aria-label")).toMatch(/1 point à vérifier.*1 expiration/);
    expect(nov4.querySelector('[data-icon="warning"]')).not.toBeNull();
    expect(nov4.querySelector('[data-icon="expiry"]')).not.toBeNull();

    // clavier : un seul tabindex 0, → = jour suivant, ↓ = semaine suivante
    expect(grid.querySelectorAll('button[tabindex="0"]')).toHaveLength(1);
    const nov2 = within(grid).getByRole("button", { name: /lundi 2 novembre/ });
    nov2.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(nov3);
    await userEvent.keyboard("{ArrowDown}");
    expect((document.activeElement as HTMLElement).dataset.date).toBe("2026-11-10");
    await userEvent.click(nov2);
    expect(await screen.findByRole("grid", { name: /Planning du lundi 2 novembre/ })).toBeInTheDocument();
  });
});

describe.each(["", "/espace/affichage"])("assistant de programmation, base « %s »", (b) => {
  const SIM = () => ({
    after: timeline([SCREEN], null, { target_type: "screen", target_id: SCREEN.id }),
    changes: [{ screen_id: SCREEN.id, start: at(2, 8), end: at(2, 12), before: DAY[1], after: { ...DAY[2], start: at(2, 8), end: at(2, 12), source: { ...SRC, kind: "draft" } } }],
    new_conflicts: [],
    resolved_conflicts: [],
    screens_touched: 1,
  });
  const routesWith = (extra: Record<string, unknown> = {}) => ({
    ...base([SCREEN], { "GET /planning/timeline": timeline([SCREEN], null, { target_type: "screen", target_id: SCREEN.id }) }),
    "POST /planning/simulate": SIM(),
    "POST /schedules": {},
    ...extra,
  });

  it.each([
    ["fr", "Remplace « Boucle accueil » par « Boucle promo » sur Hall, lundi 2 novembre, de", "Tous les jours 8h–18h", "Nouvelle programmation", "Nom", "Écran", "Enregistrer"],
    ["en", "Replaces “Boucle accueil” with “Boucle promo” on Hall, Monday, November 2, from", "Every day 8am–6pm", "New schedule", "Name", "Screen", "Save"],
  ] as const)("aperçu par simulation (anti-rebond, aria-live), phrases issues de changes, modèles (%s)", async (locale, phrase, template, create, nameLabel, screenLabel, save) => {
    const calls = mockApi(routesWith(), b);
    await renderRoutes(routes, `/playback?target=screen:${SCREEN.id}&view=list&date=2026-11-02`, b, locale);
    await userEvent.click(await screen.findByRole("button", { name: create }));
    const form = within(await screen.findByRole("form", { name: create }));
    expect(form.getByText(locale === "fr" ? "Choisissez une cible et une playlist pour voir l'aperçu." : "Pick a target and a playlist to see the preview.")).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/planning/simulate")).toHaveLength(0);

    await userEvent.type(form.getByLabelText(nameLabel), "Promo");
    await form.findByRole("option", { name: "Hall" });
    await userEvent.selectOptions(form.getByLabelText(screenLabel, { selector: "select" }), SCREEN.id);
    await userEvent.selectOptions(form.getByLabelText("Playlist"), PL_B.id);
    // un seul appel malgré les saisies : anti-rebond
    expect(await form.findByText(new RegExp(phrase), {}, { timeout: 3000 })).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/planning/simulate")).toHaveLength(1);
    const sim = calls.find((c) => c.path === "/planning/simulate")!;
    expect(sim.body).toMatchObject({ draft: { name: "Promo", target_type: "screen", target_id: SCREEN.id, playlist_id: PL_B.id, rules: [] }, target_type: "screen", target_id: SCREEN.id });
    expect(Object.keys(sim.body as object)).toEqual(expect.arrayContaining(["from", "to"]));
    // la région d'annonce est polie
    expect(form.getByText(new RegExp(phrase)).closest("[aria-live]")?.getAttribute("aria-live")).toBe("polite");

    // modèle de créneaux : préréglage du formulaire, nouvelle simulation
    await userEvent.click(form.getByRole("button", { name: template }));
    expect(form.getByRole("button", { name: template }).getAttribute("aria-pressed")).toBe("true");
    await waitFor(() => expect(calls.filter((c) => c.path === "/planning/simulate")).toHaveLength(2), { timeout: 3000 });
    expect((calls.filter((c) => c.path === "/planning/simulate")[1]!.body as { draft: { rules: unknown } }).draft.rules).toEqual([{ weekdays: 127, start: "08:00", end: "18:00" }]);

    await userEvent.click(form.getByRole("button", { name: save }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/schedules")).toBe(true));
    // la simulation n'écrit rien : un seul POST /schedules
    expect(calls.filter((c) => c.method === "POST" && c.path === "/schedules")).toHaveLength(1);
  });

  it("erreur de validation du serveur affichée dans l'aperçu", async () => {
    mockApi(routesWith({ "POST /planning/simulate": errorResponse(422, "errors.planning.window_too_large", { max_days: 14 }) }), b);
    await renderRoutes(routes, `/playback?target=screen:${SCREEN.id}&view=list&date=2026-11-02`, b);
    await userEvent.click(await screen.findByRole("button", { name: "Nouvelle programmation" }));
    const form = within(await screen.findByRole("form", { name: "Nouvelle programmation" }));
    await form.findByRole("option", { name: "Hall" });
    await userEvent.selectOptions(form.getByLabelText("Écran", { selector: "select" }), SCREEN.id);
    await userEvent.selectOptions(form.getByLabelText("Playlist"), PL_B.id);
    expect(await form.findByRole("alert", {}, { timeout: 3000 })).toBeInTheDocument();
  });

  it("un clic sur un créneau vide ouvre l'assistant pré-rempli (cible, jour, heures du site)", async () => {
    mockApi(routesWith(), b);
    await renderRoutes(routes, `/playback?target=screen:${SCREEN.id}&view=day&date=2026-11-02`, b);
    const empty = await screen.findByRole("button", { name: /Rien à diffuser.*de 00:00–08:00.*Activer pour programmer ce créneau/ });
    await userEvent.click(empty);
    const form = within(await screen.findByRole("form", { name: "Nouvelle programmation" }));
    expect(form.getByText(/Cible, jour et heures sont repris du créneau choisi/)).toBeInTheDocument();
    expect((form.getByLabelText("Écran", { selector: "select" }) as HTMLSelectElement).value).toBe(SCREEN.id);
    expect((form.getByLabelText("De") as HTMLInputElement).value).toBe("00:00");
    expect((form.getByLabelText("À") as HTMLInputElement).value).toBe("08:00");
    expect(form.getByRole("button", { name: "lundi" }).getAttribute("aria-pressed")).toBe("true");
    expect(form.getByRole("button", { name: "mardi" }).getAttribute("aria-pressed")).toBe("false");
    // pas de glisser-déposer
    expect(document.querySelectorAll(".planning [draggable=true]")).toHaveLength(0);
  });
});

describe.each(["", "/espace/affichage"])("P2-3 : fiche écran et tableau de bord via /planning/at, base « %s »", (b) => {
  it("le tableau de bord liste les prochains changements triés ; liens sous la base", async () => {
    const s2 = mkScreen(1);
    const out = atOut(
      [
        { screen_id: SCREEN.id, source: "schedule", schedule_id: SRC.id, schedule_name: "Accueil", playlist_id: PL_A.id, playlist_name: PL_A.name, next_change_at: new Date(Date.now() + 7_200_000).toISOString() },
        { screen_id: s2.id, source: "schedule", schedule_id: SRC.id, schedule_name: "Accueil", playlist_id: PL_B.id, playlist_name: PL_B.name, next_change_at: new Date(Date.now() + 3_600_000).toISOString() },
      ],
      (id) => (id === SCREEN.id ? "Hall" : "Écran 2"),
    );
    const calls = mockApi({ "GET /sites": [SITE], "GET /planning/at": out }, b);
    await renderRoutes([{ path: "*", element: <NextChanges /> }], "/", b);
    const links = await screen.findAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual(["Écran 2", "Hall"]);
    expect(links[0]!.getAttribute("href")).toContain(`/playback?target=screen:${s2.id}`);
    expect(calls.some((c) => c.path.startsWith("/planning/at?target_type=site&"))).toBe(true);
    expect(calls.some((c) => c.path.startsWith("/fleet/playback"))).toBe(false);
  });

  it("la fiche écran affiche programmation active et prochain changement issus de /planning/at", async () => {
    const out = atOut([{ screen_id: SCREEN.id, source: "schedule", schedule_id: SRC.id, schedule_name: "Semaine", playlist_id: PL_A.id, playlist_name: PL_A.name, next_change_at: new Date(Date.now() + 3_600_000).toISOString() }], () => "Hall");
    const calls = mockApi({ ...base([SCREEN]), "GET /planning/at": out }, b);
    await renderRoutes(routes, "/screens", b);
    const card = (await screen.findByRole("heading", { level: 2, name: "Hall" })).closest("li")!;
    expect(await within(card).findByText("Semaine · Boucle accueil")).toBeInTheDocument();
    expect(within(card).getByText("Prochain changement")).toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/fleet/playback"))).toBe(false);
  });
});
