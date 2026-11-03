import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };

describe.each(["", "/espace/affichage"])("points d'entrée des assistants, base « %s »", (b) => {
  it("Réglages › Sites propose « Connecter un agent »", async () => {
    mockApi({ "GET /sites": [SITE], "GET /screens": [], [`GET /sites/${SITE.id}/agents`]: [] }, b);
    await renderRoutes(routes, "/sites", b);
    expect(await screen.findByRole("link", { name: "Connecter un agent au site Moulins" })).toHaveAttribute("href", `${b}/sites/connect?site=${SITE.id}`);
  });

  it("Contenus propose « Ajouter un contenu »", async () => {
    mockApi({ "GET /media": { items: [], total: 0 }, "GET /media/tags": [] }, b);
    await renderRoutes(routes, "/content/media", b);
    const links = await screen.findAllByRole("link", { name: "Ajouter un contenu" });
    expect(links.some((l) => l.getAttribute("href") === `${b}/content/media/add`)).toBe(true);
  });
});
