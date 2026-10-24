import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { mockApi, renderRoutes } from "../../test/render";
import { routes } from "../app/routes";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const GROUP = { id: "20000000-0000-4000-8000-000000000001", site_id: SITE.id, name: "Accueil" };
const kioskOf = (k: Record<string, unknown> = {}) => ({ requested: "locked", until: null, applied: "locked", enforcement: "lock_task", ...k });
const SCREEN = { id: "30000000-0000-4000-8000-000000000001", site_id: SITE.id, group_id: null, name: "Hall", orientation: "landscape", archived_at: null, device: null, kiosk: kioskOf() };
const origin = (level: string, name: string | null = null) => ({ level, id: null, name });
const cfg = (value: string, o = origin("default")) => ({ values: { "player.kiosk": { value, origin: o, inherited: "locked", inherited_origin: origin("default"), overridable: true } }, warnings: [] });
const base = (screens: unknown[] = [SCREEN]) => ({
  "GET /sites": [SITE],
  "GET /screens": screens,
  [`GET /sites/${SITE.id}/groups`]: [GROUP],
  [`GET /sites/${SITE.id}/agents`]: [],
  [`GET /config/screen/${SCREEN.id}/core`]: cfg("locked"),
  [`GET /config/group/${GROUP.id}/core`]: cfg("locked", origin("site", "Moulins")),
  [`GET /config/site/${SITE.id}/core`]: { values: {}, warnings: [] },
  "GET /organization": { id: "9", name: "Ville" },
});
const admin = { id: "u1", display_name: "Ada", email: null, role: "admin", provider: "auth.local", locale: null };

describe.each(["", "/espace/affichage"])("kiosque, base « %s »", (b) => {
  it("affiche l'état verrouillé et l'alerte du kiosque doux", async () => {
    mockApi(base([{ ...SCREEN, kiosk: kioskOf({ enforcement: "soft" }) }]), b);
    await renderRoutes(routes, "/screens", b);
    expect(await screen.findByText("Verrouillé")).toBeInTheDocument();
    expect(screen.getByText("Mode appliqué : verrouillage doux")).toBeInTheDocument();
    expect(screen.getByText(/Kiosque doux/)).toBeInTheDocument();
  });

  it("n'affiche aucune alerte en verrouillage natif", async () => {
    mockApi(base(), b);
    await renderRoutes(routes, "/screens", b);
    expect(await screen.findByText("Mode appliqué : verrouillage natif")).toBeInTheDocument();
    expect(screen.queryByText(/Kiosque doux/)).not.toBeInTheDocument();
  });

  it("démarre une maintenance avec durée et motif", async () => {
    const calls = mockApi({ ...base(), [`POST /screens/${SCREEN.id}/kiosk/maintenance`]: { until: "2026-10-24T14:30:00Z" } }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    await user.click(await screen.findByRole("button", { name: "Mode maintenance : Hall" }));
    const duration = screen.getByLabelText("Durée (minutes)");
    await user.clear(duration);
    await user.type(duration, "60");
    await user.type(screen.getByLabelText("Motif (facultatif)"), "Mise à jour");
    await user.click(screen.getByRole("button", { name: "Déverrouiller temporairement" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ duration_min: 60, reason: "Mise à jour" }));
    expect(vi.mocked(fetch).mock.calls.every((c) => String(c[0]).startsWith(`${b}/api/v1/`))).toBe(true);
  });

  it("refuse une durée hors 5-240 min", async () => {
    const calls = mockApi(base(), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    await user.click(await screen.findByRole("button", { name: "Mode maintenance : Hall" }));
    const duration = screen.getByLabelText("Durée (minutes)");
    await user.clear(duration);
    await user.type(duration, "3");
    expect(screen.getByRole("button", { name: "Déverrouiller temporairement" })).toBeDisabled();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("termine une maintenance en cours", async () => {
    const m = { ...SCREEN, kiosk: kioskOf({ requested: "maintenance", until: "2026-10-24T14:30:00Z", applied: "maintenance" }) };
    const calls = mockApi({ ...base([m]), [`DELETE /screens/${SCREEN.id}/kiosk/maintenance`]: null }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    expect(await screen.findByText(/^Maintenance jusqu'à \d{2}:\d{2}$/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Terminer la maintenance : Hall" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path.endsWith("/kiosk/maintenance"))).toBe(true));
  });

  it("masque kiosque et réglage aux non-admins", async () => {
    mockApi({ ...base([{ ...SCREEN, kiosk: undefined }]), "GET /me": { ...admin, role: "editor" } }, b);
    await renderRoutes(routes, "/screens", b);
    await screen.findByRole("heading", { level: 2, name: "Hall" });
    expect(screen.queryByText("Verrouillé")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Mode maintenance/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Kiosque du player")).not.toBeInTheDocument();
  });

  it("demande confirmation avant un déverrouillage permanent", async () => {
    const calls = mockApi({ ...base(), [`PUT /config/screen/${SCREEN.id}/core/player.kiosk`]: null }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    await user.click(await screen.findByText("Avancé"));
    await user.selectOptions(screen.getByLabelText("Kiosque du player"), "unlocked");
    expect(screen.getByText(/laisse l'accès à l'appareil/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Déverrouiller en permanence" }));
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Confirmer le déverrouillage permanent" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ value: "unlocked" }));
  });

  it("enregistre le verrouillage d'un groupe, avec sa provenance, sans confirmation", async () => {
    const calls = mockApi({ ...base(), [`PUT /config/group/${GROUP.id}/core/player.kiosk`]: null, [`GET /config/group/${GROUP.id}/core`]: cfg("unlocked", origin("site", "Moulins")) }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, `/sites/${SITE.id}`, b);
    const form = await screen.findByRole("form", { name: "Réglage du kiosque" });
    await user.click(within(form).getByText("Avancé"));
    expect(within(form).getByText(/Hérité du site Moulins/)).toBeInTheDocument();
    await user.selectOptions(within(form).getByLabelText("Kiosque du player"), "locked");
    await user.click(within(form).getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ value: "locked" }));
  });

  it("n'offre le réglage ni à l'organisation ni au site (un seul formulaire : le groupe)", async () => {
    mockApi(base(), b);
    await renderRoutes(routes, `/sites/${SITE.id}`, b);
    await screen.findByRole("form", { name: "Réglage du kiosque" });
    expect(screen.getAllByRole("form", { name: "Réglage du kiosque" })).toHaveLength(1);
  });

  it("affiche les libellés en anglais", async () => {
    mockApi({ ...base([{ ...SCREEN, kiosk: kioskOf({ enforcement: "web" }) }]) }, b);
    await renderRoutes(routes, "/screens", b, "en");
    expect(await screen.findByText("Locked")).toBeInTheDocument();
    expect(screen.getByText("Applied mode: browser")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Maintenance mode: Hall" })).toBeInTheDocument();
  });
});
