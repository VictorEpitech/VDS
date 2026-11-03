import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { atOut } from "../../../test/planning-at";
import { errorResponse, mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";
import { displayState } from "../supervision/status";
import { minutesLeft, wholeGroup } from "./overrides";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const GROUP = { id: "20000000-0000-4000-8000-000000000001", site_id: SITE.id, name: "Accueil" };
const PLAYLIST = { id: "40000000-0000-4000-8000-000000000001", name: "Promo été", description: null, item_count: 2, created_at: "2026-10-01T08:00:00Z", updated_at: "2026-10-01T08:00:00Z" };
const online = { presence: "online", last_seen_at: "2026-10-24T09:00:00Z" };
const offline = { presence: "offline", last_seen_at: "2026-10-24T07:00:00Z" };
const mkScreen = (n: number, device: unknown = online) => ({ id: `30000000-0000-4000-8000-${String(n).padStart(12, "0")}`, site_id: SITE.id, group_id: GROUP.id, name: `Écran ${n}`, orientation: "landscape", archived_at: null, device, kiosk: null });
const S1 = mkScreen(1);
const S2 = mkScreen(2, offline);
const ENDS = new Date(Date.now() + 90 * 60_000).toISOString();
const OVERRIDE = {
  id: "50000000-0000-4000-8000-000000000001", name: "Promo été", kind: "playlist", playlist_id: PLAYLIST.id, ephemeral: false,
  target_type: "group", target_id: GROUP.id, target_name: GROUP.name, starts_at: new Date(Date.now() - 600_000).toISOString(), ends_at: ENDS,
  ended_at: null, end_reason: null, status: "active", priority: 0, respect_scheduled_off: false, note: null, created_by: null, created_by_module: null, created_at: "2026-10-24T09:00:00Z",
};
const playbackFor = (s: { id: string }, forced: boolean) => ({ screen_id: s.id, source: forced ? "override" : "none", override_id: forced ? OVERRIDE.id : null, schedule_id: null, schedule_name: null, playlist_id: forced ? PLAYLIST.id : null, playlist_name: forced ? PLAYLIST.name : null, until: forced ? ENDS : null, next_change_at: null });
const PREVIEW = { screens_total: 2, screens_offline: 1, screens_in_scheduled_off: 1, ends_at: ENDS, superseded_override_ids: [] };

const base = (extra: Record<string, unknown> = {}) => ({
  "GET /sites": [SITE],
  "GET /screens": [S1, S2],
  [`GET /sites/${SITE.id}/groups`]: [GROUP],
  "GET /playlists": { items: [PLAYLIST], total: 1 },
  "GET /media": { items: [], total: 0 },
  "GET /incidents": [],
  "GET /planning/at": atOut([playbackFor(S1, false), playbackFor(S2, false)]),
  "GET /overrides": [],
  "POST /overrides/preview": PREVIEW,
  [`GET /config/group/${GROUP.id}/core`]: { values: {}, warnings: [] },
  ...extra,
});

describe.each([
  ["", "fr"],
  ["/espace/affichage", "en"],
] as const)("forçage, base « %s », %s", (b, locale) => {
  const L = locale === "fr" ? { force: "Forcer l'affichage", what: "Quoi afficher", adv: "Avancé", until: /Forcé jusqu'à/, stop: "Arrêter", extend: "Prolonger (+1 h)", prio: "Priorité numérique", off: "Forcer l'extinction (aucun contenu)", offlineWarn: /1 écran hors ligne l'affichera/ } : { force: "Force display", what: "What to show", adv: "Advanced", until: /Forced until/, stop: "Stop", extend: "Extend (+1 h)", prio: "Numeric priority", off: "Force power-off (no content)", offlineWarn: /1 offline screen will show it/ };

  it("forcer une playlist depuis la carte : 3 champs, Avancé replié, aperçu, envoi sous la bonne base", async () => {
    const calls = mockApi(base({ "POST /overrides": OVERRIDE }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b, locale);
    await user.click(await screen.findByRole("button", { name: `${locale === "fr" ? "Forcer l'affichage sur" : "Force display on"} Écran 1` }));
    const form = await screen.findByRole("form", { name: L.force });
    // Simple par défaut : Avancé replié, priorité numérique et extinction hors du chemin principal.
    const details = within(form).getByText(L.adv).closest("details")!;
    expect(details.open).toBe(false);
    await user.selectOptions(within(form).getByLabelText(L.what), `p:${PLAYLIST.id}`);
    expect(await within(form).findByText(L.offlineWarn)).toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: L.force }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/overrides")).toBeDefined());
    const sent = calls.find((c) => c.method === "POST" && c.path === "/overrides")!.body as Record<string, unknown>;
    expect(sent).toMatchObject({ target_type: "screen", target_id: S1.id, kind: "playlist", playlist_id: PLAYLIST.id, duration_minutes: 60 });
    expect(typeof sent.request_id).toBe("string");
    expect(sent).not.toHaveProperty("respect_scheduled_off");
  });

  it("fin de journée : envoie until, sans durée", async () => {
    const calls = mockApi(base({ "POST /overrides": OVERRIDE }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b, locale);
    await user.click(await screen.findByRole("button", { name: `${locale === "fr" ? "Forcer l'affichage sur" : "Force display on"} Écran 1` }));
    const form = await screen.findByRole("form", { name: L.force });
    await user.selectOptions(within(form).getByLabelText(L.what), `p:${PLAYLIST.id}`);
    await user.click(within(form).getByRole("radio", { name: locale === "fr" ? "Fin de journée" : "End of day" }));
    await user.click(within(form).getByRole("button", { name: L.force }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/overrides")).toBe(true));
    const sent = calls.find((c) => c.method === "POST" && c.path === "/overrides")!.body as Record<string, unknown>;
    expect(sent.until).toBe("end_of_day");
    expect(sent).not.toHaveProperty("duration_minutes");
  });

  it("champ obligatoire : rien n'est envoyé sans contenu", async () => {
    const calls = mockApi(base(), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b, locale);
    await user.click(await screen.findByRole("button", { name: `${locale === "fr" ? "Forcer l'affichage sur" : "Force display on"} Écran 1` }));
    const form = await screen.findByRole("form", { name: L.force });
    await user.click(within(form).getByRole("button", { name: L.force }));
    expect(within(form).getByLabelText(L.what)).toHaveAttribute("aria-invalid", "true");
    expect(calls.some((c) => c.method === "POST" && c.path === "/overrides")).toBe(false);
  });

  it("Avancé : priorité numérique pour l'éditeur, extinction forcée réservée à l'administrateur", async () => {
    mockApi(base({ "GET /me": { id: "u2", display_name: "Eli", email: null, role: "editor", provider: "auth.local", locale: null } }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b, locale);
    await user.click(await screen.findByRole("button", { name: `${locale === "fr" ? "Forcer l'affichage sur" : "Force display on"} Écran 1` }));
    const form = await screen.findByRole("form", { name: L.force });
    await user.click(within(form).getByText(L.adv));
    expect(within(form).getByLabelText(L.prio)).toBeInTheDocument();
    expect(within(form).queryByLabelText(L.off)).not.toBeInTheDocument();
  });

  it("carte forcée : fin visible, prolonger (+1 h) et arrêter avec l'avertissement hors ligne", async () => {
    const calls = mockApi(
      base({
        "GET /planning/at": atOut([playbackFor(S1, true), playbackFor(S2, true)]),
        "GET /overrides": [OVERRIDE],
        [`PATCH /overrides/${OVERRIDE.id}`]: OVERRIDE,
        [`POST /overrides/${OVERRIDE.id}/stop`]: { ...OVERRIDE, status: "ended" },
      }),
      b,
    );
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b, locale);
    const card = (await screen.findByRole("heading", { level: 2, name: "Écran 1" })).closest("li")!;
    expect(await within(card).findByText(L.until)).toBeInTheDocument();
    expect(within(card).getByText(locale === "fr" ? "Forçage en cours" : "Overridden")).toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: new RegExp(L.extend.slice(0, 5)) }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")).toBeDefined());
    expect((calls.find((c) => c.method === "PATCH")!.body as { ends_at: string }).ends_at).toBe(new Date(new Date(ENDS).getTime() + 3_600_000).toISOString());

    await user.click(within(card).getByRole("button", { name: new RegExp(`^${locale === "fr" ? "Arrêter le forçage" : "Stop the override"}`) }));
    expect(within(card).getByText(locale === "fr" ? /1 écran hors ligne l'affichera encore/ : /1 offline screen will keep showing it/)).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/stop"))).toBe(false);
    await user.click(within(card).getByRole("button", { name: locale === "fr" ? "Arrêter maintenant" : "Stop now" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === `/overrides/${OVERRIDE.id}/stop`)).toBe(true));
  });

  it("Accueil : bloc des forçages en cours avec la fin et les actions", async () => {
    mockApi(base({ "GET /overrides": [OVERRIDE], "GET /fleet/summary": { agents: { offline: 0, online: 1, total: 1 }, generated_at: "2026-10-24T09:00:00Z", incidents: { by_kind: {}, by_severity: {}, open: 0, unacknowledged: 0 }, screens: { by_status: { online: 1 }, total: 2 }, site_id: null, to_handle: { by_reason: {}, screens: 0 } } }), b);
    await renderRoutes(routes, "/", b, locale);
    const block = await screen.findByRole("region", { name: locale === "fr" ? "Forçages en cours" : "Running overrides" });
    expect(await within(block).findByText(L.until)).toBeInTheDocument();
    expect(within(block).getByRole("button", { name: new RegExp(`^${locale === "fr" ? "Arrêter le forçage" : "Stop the override"}`) })).toBeInTheDocument();
  });

  it("éditeur : forçage d'extinction ou d'organisation sans Prolonger/Arrêter", async () => {
    mockApi(
      base({
        "GET /me": { id: "u2", display_name: "Eli", email: null, role: "editor", provider: "auth.local", locale: null },
        "GET /planning/at": atOut([playbackFor(S1, true), playbackFor(S2, true)]),
        "GET /overrides": [{ ...OVERRIDE, kind: "off", playlist_id: null }],
      }),
      b,
    );
    await renderRoutes(routes, "/screens", b, locale);
    const card = (await screen.findByRole("heading", { level: 2, name: "Écran 1" })).closest("li")!;
    expect(await within(card).findByText(locale === "fr" ? "Géré par un administrateur" : "Managed by an administrator")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: new RegExp(`^${locale === "fr" ? "Arrêter le forçage" : "Stop the override"}`) })).toBeNull();
  });

  it.each(["override.too_many_active", "override.start_too_far", "override.forbidden_kind", "override.content_requires_admin"])("code %s traduit, jamais « erreur inconnue »", async (code) => {
    mockApi(base({ "POST /overrides/preview": errorResponse(code === "override.forbidden_kind" || code === "override.content_requires_admin" ? 403 : code === "override.too_many_active" ? 409 : 422, code) }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b, locale);
    await user.click(await screen.findByRole("button", { name: `${locale === "fr" ? "Forcer l'affichage sur" : "Force display on"} Écran 1` }));
    const form = await screen.findByRole("form", { name: L.force });
    await user.selectOptions(within(form).getByLabelText(L.what), `p:${PLAYLIST.id}`);
    const alert = await within(form).findByRole("alert");
    expect(alert).not.toHaveTextContent(locale === "fr" ? "Une erreur est survenue" : /something went wrong|error occurred/i);
    expect(alert.textContent).not.toMatch(/errors\./);
    if (code === "override.content_requires_admin") expect(alert).toHaveTextContent(locale === "fr" ? "réservé aux administrateurs" : "reserved for administrators");
  });

  it("erreur serveur de l'aperçu : message traduit, envoi bloqué", async () => {
    mockApi(base({ "POST /overrides/preview": errorResponse(422, "override.duration_too_long") }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b, locale);
    await user.click(await screen.findByRole("button", { name: `${locale === "fr" ? "Forcer l'affichage sur" : "Force display on"} Écran 1` }));
    const form = await screen.findByRole("form", { name: L.force });
    await user.selectOptions(within(form).getByLabelText(L.what), `p:${PLAYLIST.id}`);
    expect(await within(form).findByRole("alert")).toHaveTextContent(locale === "fr" ? "trop long" : "too long");
    expect(within(form).getByRole("button", { name: L.force })).toBeDisabled();
  });
});

describe("aides du forçage", () => {
  it("minutesLeft arrondit au-dessus et ne passe jamais sous zéro", () => {
    const now = new Date("2026-10-24T10:00:00Z");
    expect(minutesLeft("2026-10-24T10:00:30Z", now)).toBe(1);
    expect(minutesLeft("2026-10-24T09:00:00Z", now)).toBe(0);
  });
  it("wholeGroup : seulement si la sélection couvre tout le groupe", () => {
    const g = [{ id: GROUP.id, site_id: SITE.id, name: "A" }];
    expect(wholeGroup([S1 as never, S2 as never], [S1, S2] as never, g)?.id).toBe(GROUP.id);
    expect(wholeGroup([S1 as never], [S1, S2] as never, g)).toBeNull();
  });
  it("displayState : forçage seulement pour un écran en ligne (hors ligne reste une alerte)", () => {
    const now = new Date();
    const pb = playbackFor(S1, true) as never;
    expect(displayState(S1 as never, undefined, [], now, pb)).toBe("overridden");
    expect(displayState(S2 as never, undefined, [], now, pb)).toBe("offline");
    expect(displayState(S1 as never, undefined, [], now)).toBe("online");
  });
});
