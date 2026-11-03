import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";
import { fold, matches } from "./registry";

const me = (role: string) => ({ id: "u1", display_name: "Ada", email: null, role, provider: "auth.local", locale: null, role_source: "vds", perimeter: { unrestricted: true, can_share: true, nodes: [] } });
const SCREEN = { id: "30000000-0000-4000-8000-000000000001", site_id: "s1", group_id: null, name: "Accueil hall", orientation: "landscape", archived_at: null, device: null, kiosk: null };
const data = {
  "GET /screens": [SCREEN],
  "GET /sites": [{ id: "s1", name: "Moulins", slug: "moulins", archived_at: null }],
  "GET /media": { items: [], total: 0 },
  "GET /playlists": { items: [{ id: "p1", name: "Promo été" }], total: 1 },
  "GET /layouts": { items: [], total: 0 },
  "GET /schedules": [],
};
const CTRL_K = "{Control>}k{/Control}";

describe("recherche insensible aux accents", () => {
  it("« ecran » trouve « Écran »", () => {
    expect(fold("Écran Accueil")).toBe("ecran accueil");
    expect(matches("ecran", "Ouvrir l'écran Accueil")).toBe(true);
    expect(matches("hall ecran", "Ouvrir l'écran Accueil hall")).toBe(true);
    expect(matches("xyz", "Ouvrir l'écran")).toBe(false);
  });
});

describe.each(["", "/espace/affichage"])("palette Ctrl+K, base « %s »", (b) => {
  it("s'ouvre au clavier, trouve un écran sans accent, Entrée l'exécute, Échap ferme et rend le focus", async () => {
    mockApi(data, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/content/media", b);
    const opener = await screen.findByRole("button", { name: "Rechercher" });
    opener.focus();
    await user.keyboard(CTRL_K);
    const box = await screen.findByRole("combobox", { name: "Rechercher une page, un élément ou une action" });
    expect(box).toHaveFocus();
    await user.type(box, "ecran accueil");
    expect(await screen.findByRole("option", { name: /Ouvrir l'écran Accueil hall/ })).toBeInTheDocument();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.keyboard(CTRL_K);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("Entrée sur une création navigue sous la base", async () => {
    mockApi(data, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/", b);
    await screen.findByRole("button", { name: "Rechercher" });
    await user.keyboard(CTRL_K);
    await user.type(await screen.findByRole("combobox"), "ajouter un contenu");
    await user.keyboard("{ArrowDown}{ArrowUp}{Enter}");
    expect(await screen.findByRole("heading", { level: 1, name: "Ajouter un contenu" })).toBeInTheDocument();
  });

  it("un éditeur ne voit pas les actions réservées à l'administrateur", async () => {
    const asEditor = { ...data, "GET /me": me("editor") };
    mockApi(asEditor, b);
    const user = userEvent.setup();
    const { unmount } = await renderRoutes(routes, "/", b);
    await screen.findByRole("button", { name: "Rechercher" });
    await user.keyboard(CTRL_K);
    await user.type(await screen.findByRole("combobox"), "ajouter");
    expect(await screen.findByRole("option", { name: /Ajouter un contenu/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Ajouter un écran/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Connecter un agent/ })).not.toBeInTheDocument();
    unmount();
    mockApi({ ...data, "GET /me": me("admin") }, b);
    await renderRoutes(routes, "/", b);
    await screen.findByRole("button", { name: "Rechercher" });
    await user.keyboard(CTRL_K);
    await user.type(await screen.findByRole("combobox"), "ajouter");
    expect(await screen.findByRole("option", { name: /Ajouter un écran/ })).toBeInTheDocument();
  });
});
