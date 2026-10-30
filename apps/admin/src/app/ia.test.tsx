import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockApi, renderRoutes } from "../../test/render";
import { trailFor } from "../components/Breadcrumbs";
import { routes } from "./routes";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Siège", slug: "siege", archived_at: null };
const dev = (presence: string) => ({ presence, last_seen_at: null });
const scr = (n: number, device: unknown) => ({ id: `3000000${n}-0000-4000-8000-000000000001`, site_id: SITE.id, group_id: null, name: `E${n}`, orientation: "landscape", archived_at: null, device });

describe.each(["", "/espace/affichage"])("Accueil et fil d'Ariane, base « %s »", (b) => {
  it("compteurs depuis les API existantes, lien « à traiter » vers le filtre", async () => {
    mockApi(
      {
        "GET /sites": [SITE],
        "GET /screens": [scr(1, dev("online")), scr(2, dev("offline")), scr(3, null)],
        "GET /incidents": [],
        "GET /media": [{ id: "m1" }],
        "GET /playlists": [],
        "GET /schedules": [],
      },
      b,
    );
    await renderRoutes(routes, "/", b);
    const card = (await screen.findByRole("heading", { name: "À traiter" })).closest("section")!;
    expect(await within(card).findByText("1")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Voir ce qui est à traiter" })).toHaveAttribute("href", `${b}/screens?filter=issues`);
    expect(screen.getByText("1 en ligne")).toBeInTheDocument();
  });

  it("Accueil sans écran : état vide avec action", async () => {
    mockApi({ "GET /sites": [], "GET /screens": [], "GET /incidents": [], "GET /media": [], "GET /playlists": [], "GET /schedules": [] }, b);
    await renderRoutes(routes, "/", b);
    expect(await screen.findByText(/Aucun écran pour l'instant/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Ajouter un écran" })[0]).toHaveAttribute("href", `${b}/screens/add`);
  });

  it("fil d'Ariane sur Ajouter un écran", async () => {
    mockApi({ "GET /sites": [], "GET /screens": [] }, b);
    await renderRoutes(routes, "/screens/add", b);
    const crumbs = await screen.findByRole("navigation", { name: "Fil d'Ariane" });
    expect(within(crumbs).getByRole("link", { name: "Écrans" })).toHaveAttribute("href", `${b}/screens`);
    expect(within(crumbs).getByText("Ajouter un écran")).toHaveAttribute("aria-current", "page");
  });

  it("filtre « à traiter » : n'affiche que les écrans à traiter", async () => {
    mockApi({ "GET /sites": [SITE], "GET /screens": [scr(1, dev("online")), scr(2, dev("offline"))], "GET /incidents": [], "GET /agents": [] }, b);
    await renderRoutes(routes, "/supervision", b);
    expect(await screen.findByRole("heading", { name: "E2" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "E1" })).toBeNull();
    expect(screen.getByRole("button", { name: "À traiter" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("trailFor", () => {
  it("pas de chemin sur les pages de premier niveau", () => {
    expect(trailFor("/")).toBeNull();
    expect(trailFor("/screens")).toBeNull();
    expect(trailFor("/content/playlists/abc")?.map((c) => c.label)).toEqual(["nav.content", "nav.playlists", "nav.playlist_edit"]);
  });
});
