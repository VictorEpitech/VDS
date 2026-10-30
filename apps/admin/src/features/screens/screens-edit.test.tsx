import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { errorResponse, mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const GROUP = { id: "20000000-0000-4000-8000-000000000001", site_id: SITE.id, name: "Accueil" };
const SCREEN = { id: "30000000-0000-4000-8000-000000000001", site_id: SITE.id, group_id: null, name: "Hall", orientation: "landscape", archived_at: null, device: null, kiosk: null };
const base = (extra: Record<string, unknown> = {}) => ({
  "GET /sites": [SITE],
  "GET /screens": [SCREEN],
  [`GET /sites/${SITE.id}/groups`]: [GROUP],
  [`GET /config/screen/${SCREEN.id}/core`]: { values: {}, warnings: [] },
  ...extra,
});

describe.each(["", "/espace/affichage"])("modifier / supprimer un écran, base « %s »", (b) => {
  it("modifie le nom, l'orientation et le groupe via PATCH", async () => {
    const calls = mockApi(base({ [`PATCH /screens/${SCREEN.id}`]: { ...SCREEN, name: "Accueil TV" } }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    await user.click(await screen.findByRole("button", { name: "Modifier l'écran Hall" }));
    const name = await screen.findByLabelText("Nom de l'écran");
    await user.clear(name);
    await user.type(name, "Accueil TV");
    await user.selectOptions(screen.getByLabelText("Orientation"), "portrait");
    await screen.findByRole("option", { name: "Accueil" });
    await user.selectOptions(screen.getByLabelText("Groupe (facultatif)"), GROUP.id);
    await user.click(within(screen.getByRole("form", { name: "Modifier l'écran Hall" })).getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "Accueil TV", orientation: "portrait", group_id: GROUP.id, archived: false }));
    expect(await screen.findByText("L'écran Accueil TV est enregistré.")).toBeInTheDocument();
  });

  it("exige le nom retapé avant de supprimer, puis retire l'écran de la liste", async () => {
    let gone = false;
    const calls = mockApi(
      base({
        "GET /screens": () => (gone ? [] : [SCREEN]),
        [`DELETE /screens/${SCREEN.id}`]: () => {
          gone = true;
          return null;
        },
      }),
      b,
    );
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    await user.click(await screen.findByRole("button", { name: "Supprimer l'écran Hall" }));
    expect(screen.getByText(/planning/)).toBeInTheDocument();
    const del = screen.getByRole("button", { name: "Supprimer définitivement" });
    expect(del).toBeDisabled();
    await user.type(screen.getByLabelText("Pour confirmer, retapez le nom : Hall"), "Hall");
    await user.click(del);
    expect(await screen.findByText("L'écran Hall est supprimé.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE" && c.path === `/screens/${SCREEN.id}`)).toBe(true);
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Hall" })).not.toBeInTheDocument());
  });

  it("propose d'archiver à la place et affiche une erreur actionnable", async () => {
    mockApi(base({ [`DELETE /screens/${SCREEN.id}`]: errorResponse(409, "errors.conflict") }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    await user.click(await screen.findByRole("button", { name: "Supprimer l'écran Hall" }));
    await user.type(screen.getByLabelText("Pour confirmer, retapez le nom : Hall"), "Hall");
    await user.click(screen.getByRole("button", { name: "Supprimer définitivement" }));
    expect(await screen.findByRole("button", { name: "Réessayer" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Archiver à la place" }));
    expect(await screen.findByLabelText("Archiver cet écran (il disparaît de la liste)")).toBeChecked();
  });
});
