import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { atOut } from "../../../test/planning-at";
import { errorResponse, mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";
import { clampRect, drag, fromPercent, nudge, toPercent } from "./geometry";
import { TEMPLATE_IDS, templateZones, type Layout } from "./layouts";
import { restack, stackOrder } from "./stack";
import { zoneProblems } from "./LayoutEditor";
import { videoZoneKeys } from "./warnings";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const GROUP = { id: "20000000-0000-4000-8000-000000000001", site_id: SITE.id, name: "Accueil" };
const mkScreen = (n: number, orientation: string) => ({ id: `30000000-0000-4000-8000-${String(n).padStart(12, "0")}`, site_id: SITE.id, group_id: GROUP.id, name: `Écran ${n}`, orientation, archived_at: null, device: { presence: "online", last_seen_at: "2026-10-24T09:00:00Z" }, kiosk: null });
const LAND = mkScreen(1, "landscape");
const PORT = mkScreen(2, "portrait");
const PLAYLIST = { id: "40000000-0000-4000-8000-000000000001", name: "Promo été", description: null, item_count: 2, created_at: "", updated_at: "", expires_at: null, expired: false, tags: [] };
const zone = (key: string, name: string, x: number, w: number, z = 0) => ({ key, name, x, y: 0, w, h: 1, z, playlist_id: null, fit: "contain", default_item_duration_ms: null, active_from: null, active_until: null });
const LAYOUT: Layout = {
  id: "70000000-0000-4000-8000-000000000001", name: "Deux colonnes", orientation: "landscape", background_color: null, background_media_id: null, tags: [], revision: 3,
  created_at: "2026-11-01T08:00:00Z", updated_at: "2026-11-01T08:00:00Z", zones: [zone("left", "Gauche", 0, 0.5), zone("right", "Droite", 0.5, 0.5, 1)],
} as Layout;
const SUMMARY = { id: LAYOUT.id, name: LAYOUT.name, orientation: LAYOUT.orientation, revision: 3, tags: [], updated_at: LAYOUT.updated_at, zone_count: 2, usage_count: 0, zones: LAYOUT.zones };
const ORG = { id: "90000000-0000-4000-8000-000000000001", name: "Collectivité" };
const base = (extra: Record<string, unknown> = {}) => ({
  "GET /sites": [SITE],
  "GET /screens": [LAND, PORT],
  [`GET /sites/${SITE.id}/groups`]: [GROUP],
  "GET /playlists": { items: [PLAYLIST], total: 1 },
  "GET /layouts": { items: [SUMMARY], total: 1 },
  "GET /organization": ORG,
  [`GET /config/organization/${ORG.id}/core`]: { values: { "player.max_exclusive_elements": { value: 1, origin: { level: "default", id: null, name: null } } }, warnings: [] },
  ...extra,
});

describe("géométrie et pile des zones", () => {
  it("déplace d'un pas de 1 % et redimensionne avec Maj, sans sortir de l'écran", () => {
    const r = { x: 0.5, y: 0.5, w: 0.5, h: 0.5 };
    expect(nudge(r, "ArrowLeft", false)).toEqual({ x: 0.49, y: 0.5, w: 0.5, h: 0.5 });
    expect(nudge(r, "ArrowRight", false)).toEqual(r);
    expect(nudge(r, "ArrowLeft", true)).toEqual({ x: 0.5, y: 0.5, w: 0.49, h: 0.5 });
    expect(clampRect({ x: -1, y: 2, w: 3, h: 0 })).toEqual({ x: 0, y: 0.99, w: 1, h: 0.01 });
  });

  it("glisse avec une grille de 1 % (Maj : libre)", () => {
    const r = { x: 0.1, y: 0.1, w: 0.2, h: 0.2 };
    expect(drag(r, "move", 0.1234, 0, false)).toMatchObject({ x: 0.22 });
    expect(drag(r, "move", 0.1234, 0, true)).toMatchObject({ x: 0.2234 });
    expect(drag(r, "resize", 0.05, 0.05, false)).toMatchObject({ w: 0.25, h: 0.25 });
    expect(toPercent(0.1234)).toBe(12.34);
    expect(fromPercent(12.34)).toBe(0.1234);
  });

  it("monte et descend une zone en renumérotant z", () => {
    const zs = [zone("a", "A", 0, 1, 0), zone("b", "B", 0, 1, 1), zone("c", "C", 0, 1, 2)] as Layout["zones"];
    expect(stackOrder(zs).map((z) => z.key)).toEqual(["c", "b", "a"]);
    const up = restack(zs, "a", "up");
    expect(stackOrder(up).map((z) => z.key)).toEqual(["c", "a", "b"]);
    expect(restack(zs, "c", "up")).toEqual(zs);
  });

  it("modèles : chaque modèle a des zones valides et des clés uniques", () => {
    for (const id of TEMPLATE_IDS) {
      const zs = templateZones(id, (k) => k) as Layout["zones"];
      expect(zoneProblems(zs)).toEqual([]);
      expect(new Set(zs.map((z) => z.key)).size).toBe(zs.length);
    }
  });

  it("signale les zones invalides (bornes, clé, fenêtre)", () => {
    const bad = [{ ...zone("ok", "Ok", 0, 0.5) }, { ...zone("Mauvaise", "X", 0, 0.5) }, { ...zone("big", "B", 0.8, 0.5) }, { ...zone("win", "W", 0, 0.1), active_from: "2026-11-02T10:00:00Z", active_until: "2026-11-02T09:00:00Z" }] as Layout["zones"];
    expect(zoneProblems(bad)).toEqual(["Mauvaise", "big", "win"]);
  });

  it("avertissement vidéo : seules les playlists avec une vidéo active comptent", () => {
    const zs = [{ key: "a", playlist_id: "p1" }, { key: "b", playlist_id: "p2" }, { key: "c", playlist_id: null }];
    const detail = (ct: string, enabled = true) => ({ items: [{ content_type: ct, enabled }] }) as never;
    expect(videoZoneKeys(zs, new Map([["p1", detail("content.video")], ["p2", detail("content.image")]]))).toEqual(["a"]);
    expect(videoZoneKeys(zs, new Map([["p1", detail("content.video", false)]]))).toEqual([]);
  });
});

describe.each([
  ["", "fr"],
  ["/espace/affichage", "en"],
] as const)("mises en page, base « %s », %s", (b, locale) => {
  const fr = locale === "fr";
  const L = fr
    ? { create: "Créer une mise en page", name: "Nom", submit: "Créer et ouvrir l'éditeur", save: "Enregistrer", empty: /Aucune mise en page/, zone: /^Zone Gauche/, saved: "Mise en page enregistrée.", reload: "Recharger la dernière version", force: "Forcer l'affichage", zones: "Certaines zones" }
    : { create: "Create a layout", name: "Name", submit: "Create and open the editor", save: "Save", empty: /No layouts yet/, zone: /^Zone Gauche/, saved: "Layout saved.", reload: "Reload the latest version", force: "Force display", zones: "Some zones" };

  it("liste vide : message et action de création", async () => {
    mockApi(base({ "GET /layouts": { items: [], total: 0 } }), b);
    await renderRoutes(routes, "/content/layouts", b, locale);
    expect(await screen.findByText(L.empty)).toBeInTheDocument();
  });

  it("liste : cartes avec orientation, zones et usage ; le réglage a son badge de provenance", async () => {
    mockApi(base(), b);
    await renderRoutes(routes, "/content/layouts", b, locale);
    const link = await screen.findByRole("link", { name: "Deux colonnes" });
    expect(link).toHaveAttribute("href", `${b}/content/layouts/${LAYOUT.id}`);
    expect(screen.getByText(fr ? /Paysage · 2 zones · non utilisée/ : /Landscape · 2 zones · unused/)).toBeInTheDocument();
    // Réglage généré depuis le schéma, replié, avec provenance.
    const user = userEvent.setup();
    await user.click(await screen.findByText(fr ? "Éléments lus en même temps" : "Simultaneous elements", { selector: "summary" }));
    expect(await screen.findByLabelText(fr ? "Éléments lus en même temps" : "Simultaneous elements", { selector: "input" })).toHaveValue(1);
    expect(screen.getByText(fr ? /Valeur par défaut/ : /Default value/)).toBeInTheDocument();
  });

  it("création depuis un modèle : zones préremplies envoyées, puis ouverture de l'éditeur", async () => {
    const calls = mockApi(base({ "GET /layouts": { items: [], total: 0 }, "POST /layouts": { ...LAYOUT, id: "70000000-0000-4000-8000-000000000009" }, "GET /layouts/70000000-0000-4000-8000-000000000009": LAYOUT, [`GET /layouts/${LAYOUT.id}/usage`]: { schedules: [], overrides: [], zone_overrides: [], screens_total: 0, screens_orientation_mismatch: 0 } }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/content/layouts", b, locale);
    await user.click((await screen.findAllByRole("button", { name: L.create }))[0]!);
    const form = within(await screen.findByRole("form", { name: L.create }));
    await user.click(form.getByRole("button", { name: L.submit }));
    expect(await form.findByText(fr ? "Ce champ est obligatoire." : "This field is required.")).toBeInTheDocument();
    await user.type(form.getByLabelText(L.name), "Accueil");
    await user.click(form.getByRole("radio", { name: fr ? "Bandeau bas" : "Bottom banner" }));
    await user.click(form.getByRole("radio", { name: fr ? "Portrait" : "Portrait" }));
    await user.click(form.getByRole("button", { name: L.submit }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/layouts")).toBeDefined());
    const sent = calls.find((c) => c.method === "POST")!.body as { name: string; orientation: string; zones: { key: string; name: string; z: number }[] };
    expect(sent).toMatchObject({ name: "Accueil", orientation: "portrait" });
    expect(sent.zones.map((z) => [z.key, z.name, z.z])).toEqual([["main", fr ? "Principale" : "Main", 0], ["banner", fr ? "Bandeau" : "Banner", 1]]);
  });

  it("éditeur : flèches déplacent, Maj+flèches redimensionnent, enregistrement avec If-Match", async () => {
    const saved = { ...LAYOUT, revision: 4 };
    const calls = mockApi(base({ [`GET /layouts/${LAYOUT.id}`]: LAYOUT, [`GET /layouts/${LAYOUT.id}/usage`]: { schedules: [], overrides: [], zone_overrides: [], screens_total: 2, screens_orientation_mismatch: 0 }, [`PUT /layouts/${LAYOUT.id}`]: saved }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, `/content/layouts/${LAYOUT.id}`, b, locale);
    const zoneEl = await screen.findByRole("button", { name: L.zone });
    zoneEl.focus();
    await user.keyboard("{ArrowRight}{ArrowRight}");
    await user.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(screen.getByRole("button", { name: L.zone })).toHaveAccessibleName(fr ? /position 2 % 0 %, taille 49 % sur 100 %/ : /position 2% 0%, size 49% by 100%/);
    await user.click(screen.getByRole("button", { name: L.save }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")).toBeDefined());
    const put = calls.find((c) => c.method === "PUT")!.body as { zones: { key: string; y: number; w: number }[] };
    expect(put.zones[0]).toMatchObject({ key: "left", x: 0.02, w: 0.49 });
    const init = vi.mocked(fetch).mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "PUT")![1] as RequestInit;
    expect((init.headers as Record<string, string>)["If-Match"]).toBe("3");
  });

  it("éditeur : un conflit de révision propose de recharger", async () => {
    mockApi(base({ [`GET /layouts/${LAYOUT.id}`]: LAYOUT, [`GET /layouts/${LAYOUT.id}/usage`]: { schedules: [], overrides: [], zone_overrides: [], screens_total: 0, screens_orientation_mismatch: 0 }, [`PUT /layouts/${LAYOUT.id}`]: () => errorResponse(412, "layout.revision_conflict") }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, `/content/layouts/${LAYOUT.id}`, b, locale);
    (await screen.findByRole("button", { name: L.zone })).focus();
    await user.keyboard("{ArrowRight}");
    await user.click(screen.getByRole("button", { name: L.save }));
    expect(await screen.findByRole("button", { name: L.reload })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(fr ? /modifiée ailleurs/ : /changed elsewhere/);
  });

  it("éditeur : champs numériques en %, Avancé replié, clé en doublon refusée avant l'envoi", async () => {
    const calls = mockApi(base({ [`GET /layouts/${LAYOUT.id}`]: LAYOUT, [`GET /layouts/${LAYOUT.id}/usage`]: { schedules: [], overrides: [], zone_overrides: [], screens_total: 0, screens_orientation_mismatch: 0 }, [`PUT /layouts/${LAYOUT.id}`]: LAYOUT }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, `/content/layouts/${LAYOUT.id}`, b, locale);
    await screen.findByRole("button", { name: L.zone });
    const props = within(screen.getByRole("region", { name: fr ? /Propriétés/ : /Properties/ }));
    expect(props.getByText(fr ? "Avancé" : "Advanced").closest("details")!.open).toBe(false);
    expect(props.getByLabelText(fr ? "Largeur" : "Width")).toHaveValue(50);
    await user.click(props.getByText(fr ? "Avancé" : "Advanced"));
    const key = props.getByLabelText(fr ? "Identifiant" : "Identifier");
    await user.clear(key);
    await user.type(key, "right");
    expect(await props.findByText(fr ? /déjà pris/ : /already uses/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: L.save }));
    expect(await screen.findByRole("alert")).toHaveTextContent(fr ? /invalide/ : /invalid/);
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("éditeur : deux zones avec vidéo au-delà de la limite affichent l'avertissement", async () => {
    const layout = { ...LAYOUT, zones: [{ ...zone("left", "Gauche", 0, 0.5), playlist_id: PLAYLIST.id }, { ...zone("right", "Droite", 0.5, 0.5, 1), playlist_id: PLAYLIST.id }] };
    mockApi(base({ [`GET /layouts/${LAYOUT.id}`]: layout, [`GET /layouts/${LAYOUT.id}/usage`]: { schedules: [], overrides: [], zone_overrides: [], screens_total: 0, screens_orientation_mismatch: 0 }, [`GET /playlists/${PLAYLIST.id}`]: { ...PLAYLIST, items: [{ id: "i", content_type: "content.video", enabled: true, duration_ms: null, media_ids: [], position: 0, props: {} }] } }), b);
    await renderRoutes(routes, `/content/layouts/${LAYOUT.id}`, b, locale);
    expect(await screen.findByText(fr ? /2 zones contiennent des vidéos.*n'en lit que 1/ : /2 zones contain videos.*plays only 1/)).toBeInTheDocument();
  });

  it("programmation : choisir une mise en page avertit sur l'orientation sans bloquer, et envoie layout_id", async () => {
    const calls = mockApi(base({ "GET /schedules": [], "POST /schedules": {} , "GET /planning/at": atOut([]) }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/playback", b, locale);
    await user.click(await screen.findByRole("button", { name: fr ? "Nouvelle programmation" : "New schedule" }));
    const form = within(await screen.findByRole("form", { name: fr ? "Nouvelle programmation" : "New schedule" }));
    await user.type(form.getByLabelText(fr ? "Nom" : "Name"), "Mur");
    await user.click(form.getByRole("radio", { name: fr ? "Mise en page" : "Layout" }));
    await form.findByRole("option", { name: "Deux colonnes" });
    await user.selectOptions(form.getByLabelText(fr ? "Mise en page" : "Layout", { selector: "select" }), LAYOUT.id);
    await user.click(form.getByRole("radio", { name: fr ? "Groupe" : "Group" }));
    await form.findByRole("option", { name: "Accueil" });
    await user.selectOptions(form.getByLabelText(fr ? "Groupe" : "Group", { selector: "select" }), GROUP.id);
    expect(await form.findByText(fr ? /1 écran ciblé a une orientation différente/ : /1 targeted screen has an orientation different/)).toBeInTheDocument();
    await user.click(form.getByRole("button", { name: L.save }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/schedules")).toBeDefined());
    const sent = calls.find((c) => c.method === "POST" && c.path === "/schedules")!.body as Record<string, unknown>;
    expect(sent).toMatchObject({ layout_id: LAYOUT.id, target_id: GROUP.id });
    expect(sent).not.toHaveProperty("playlist_id");
  });

  it("forçage de zone : zones proposées, couverture, avertissement et envoi de zone_ids sans mise en page", async () => {
    const ends = new Date(Date.now() + 3_600_000).toISOString();
    const preview = (zoneCoverage: unknown[]) => ({ screens_total: 1, screens_offline: 0, screens_in_scheduled_off: 0, ends_at: ends, superseded_override_ids: [], screens_orientation_mismatch: 0, zone_candidates: [{ key: "banner", name: "Bandeau", layouts: 2 }, { key: "side", name: "Côté", layouts: 1 }], zone_coverage: zoneCoverage });
    const calls = mockApi(
      base({
        "GET /incidents": [], "GET /overrides": [], "GET /media": { items: [], total: 0 }, "GET /planning/at": atOut([]), [`GET /config/screen/${LAND.id}/core`]: { values: {}, warnings: [] },
        "POST /overrides/preview": (_p: string, init: RequestInit | undefined) => {
          const body = JSON.parse(String(init?.body)) as { zone_ids?: string[] };
          return preview(body.zone_ids ? body.zone_ids.map((z) => ({ zone_id: z, screens_total: 1, screens_missing: z === "side" ? 1 : 0 })) : []);
        },
        "POST /overrides": { id: "x", name: "F", kind: "playlist", ephemeral: false, target_type: "screen", target_id: LAND.id, target_name: "Écran 1", starts_at: ends, ends_at: ends, status: "active", priority: 0, respect_scheduled_off: false, note: null, created_at: ends, ended_at: null, end_reason: null, created_by: null, created_by_module: null, playlist_id: PLAYLIST.id, zone_ids: ["banner"], warnings: [], screens_missing: 0 },
      }),
      b,
    );
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b, locale);
    await user.click(await screen.findByRole("button", { name: `${L.force.replace("Forcer l'affichage", "Forcer l'affichage sur")}`.replace("Force display", "Force display on") + " Écran 1" }));
    const form = within(await screen.findByRole("form", { name: L.force }));
    await user.selectOptions(form.getByLabelText(fr ? "Quoi afficher" : "What to show"), `p:${PLAYLIST.id}`);
    await user.click(form.getByRole("radio", { name: L.zones }));
    // Mise en page et média ne sont plus proposés : playlist seulement.
    expect(form.queryByRole("group", { name: fr ? "Mises en page" : "Layouts" })).not.toBeInTheDocument();
    await user.click(await form.findByRole("checkbox", { name: /Bandeau/ }));
    expect(await form.findByText(fr ? /s'applique à 1 écran sur 1/ : /applies to 1 screen out of 1/)).toBeInTheDocument();
    await user.click(form.getByRole("checkbox", { name: /Côté/ }));
    expect(await form.findByText(fr ? /la zone n'existe pas : le forçage y sera ignoré/ : /the zone does not exist: the override is ignored there/)).toBeInTheDocument();
    await user.click(form.getByRole("checkbox", { name: /Côté/ }));
    await user.click(form.getByRole("button", { name: L.force }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/overrides")).toBeDefined());
    const sent = calls.find((c) => c.method === "POST" && c.path === "/overrides")!.body as Record<string, unknown>;
    expect(sent).toMatchObject({ zone_ids: ["banner"], playlist_id: PLAYLIST.id, kind: "playlist" });
    expect(sent).not.toHaveProperty("layout_id");
  });

  it("forçage de zone : choisir zéro zone est refusé avant l'envoi", async () => {
    const ends = new Date(Date.now() + 3_600_000).toISOString();
    const calls = mockApi(base({ "GET /incidents": [], "GET /overrides": [], "GET /media": { items: [], total: 0 }, "GET /planning/at": atOut([]), [`GET /config/screen/${LAND.id}/core`]: { values: {}, warnings: [] }, "POST /overrides/preview": { screens_total: 1, screens_offline: 0, screens_in_scheduled_off: 0, ends_at: ends, superseded_override_ids: [], zone_candidates: [], zone_coverage: [] } }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b, locale);
    await user.click(await screen.findByRole("button", { name: (fr ? "Forcer l'affichage sur" : "Force display on") + " Écran 1" }));
    const form = within(await screen.findByRole("form", { name: L.force }));
    await user.selectOptions(form.getByLabelText(fr ? "Quoi afficher" : "What to show"), `p:${PLAYLIST.id}`);
    await user.click(form.getByRole("radio", { name: L.zones }));
    await user.click(form.getByRole("button", { name: L.force }));
    expect(await form.findByText(fr ? "Choisissez au moins une zone." : "Choose at least one zone.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/overrides")).toBe(false);
  });
});

describe("petit écran et pastille de zone", () => {
  it("375 px : le canevas est en lecture seule, les champs numériques restent éditables", async () => {
    const mq = (matches: boolean) => ({ matches, media: "", addEventListener: () => undefined, removeEventListener: () => undefined }) as unknown as MediaQueryList;
    vi.stubGlobal("matchMedia", () => mq(true));
    mockApi(base({ [`GET /layouts/${LAYOUT.id}`]: LAYOUT, [`GET /layouts/${LAYOUT.id}/usage`]: { schedules: [], overrides: [], zone_overrides: [], screens_total: 0, screens_orientation_mismatch: 0 } }));
    const user = userEvent.setup();
    await renderRoutes(routes, `/content/layouts/${LAYOUT.id}`, "");
    const canvas = await screen.findByRole("group", { name: "Canevas de la mise en page" });
    expect(canvas).toHaveAttribute("data-readonly", "true");
    expect(screen.getByText(/le canevas est en lecture seule/)).toBeInTheDocument();
    (await screen.findByRole("button", { name: /^Zone Gauche/ })).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("button", { name: /^Zone Gauche, position 0 %/ })).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Gauche"));
    await user.type(screen.getByLabelText("Gauche"), "10");
    expect(screen.getByRole("button", { name: /^Zone Gauche, position 10 %/ })).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("forçage de zone actif : pastille « Zone forcée » avec clé en mono et mention « non appliqué »", async () => {
    const ends = new Date(Date.now() + 90 * 60_000).toISOString();
    const ov = { id: "50000000-0000-4000-8000-000000000001", name: "Bandeau urgent", kind: "playlist", playlist_id: PLAYLIST.id, layout_id: null, zone_ids: ["banner"], ephemeral: false, target_type: "group", target_id: GROUP.id, target_name: GROUP.name, starts_at: new Date(Date.now() - 600_000).toISOString(), ends_at: ends, ended_at: null, end_reason: null, status: "active", priority: 0, respect_scheduled_off: false, note: null, created_by: null, created_by_module: null, created_at: ends, warnings: ["override_zone_missing"], screens_missing: 1 };
    mockApi(base({ "GET /overrides": [ov], "GET /incidents": [], "GET /planning/at": atOut([]), "GET /schedules": [] }));
    await renderRoutes(routes, "/playback", "");
    const badge = await screen.findByText(/Zone forcée :/);
    expect(badge.closest(".status-badge")).toHaveAttribute("data-state", "overridden");
    expect(within(badge.closest(".status-badge") as HTMLElement).getByText("banner")).toHaveClass("mono");
    expect(screen.getByText("non appliqué sur 1 écran")).toBeInTheDocument();
  });
});
