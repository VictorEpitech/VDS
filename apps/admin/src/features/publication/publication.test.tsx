import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { errorResponse, mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";
import { viewOf } from "./publication";

const ID = "50000000-0000-4000-8000-000000000001";
const PL = { id: ID, name: "Accueil boucle", description: null, item_count: 0, created_at: "", updated_at: "", expires_at: null, expired: false, tags: [], items: [], owner: null, media_ids: [] };
const pub = (over: Record<string, unknown> = {}) => ({ entity_type: "playlist", entity_id: ID, state: "draft", working_rev: 3, published_rev: 2, submitted_by: null, submitted_at: null, published_by: null, published_at: null, review_comment: null, blocked_by: [], name: "Accueil boucle", ...over });
const editor = { id: "u2", display_name: "Marie", email: null, role: "editor", provider: "auth.local", locale: null, role_source: "vds", perimeter: { unrestricted: true, can_share: true, nodes: [] } };

describe("viewOf", () => {
  it("distingue jamais publié et modifications non publiées", () => {
    expect(viewOf({ state: "draft", published_rev: null })).toBe("never");
    expect(viewOf({ state: "draft", published_rev: 1 })).toBe("changed");
    expect(viewOf({ state: "in_review", published_rev: 1 })).toBe("in_review");
    expect(viewOf({ state: "published", published_rev: 1 })).toBe("published");
  });
});

describe.each(["", "/espace/affichage"])("publication (base %s)", (base) => {
  it("éditeur : badge, soumission, pas de lien d'administration", async () => {
    const calls = mockApi({ "GET /me": editor, [`GET /playlists/${ID}`]: PL, [`GET /publications/playlist/${ID}`]: pub(), [`POST /publications/playlist/${ID}/submit`]: pub({ state: "in_review" }) }, base);
    await renderRoutes(routes, `/content/playlists/${ID}`, base);
    expect(await screen.findByText("Modifications non publiées")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Soumettre pour validation" }));
    await userEvent.type(screen.getByLabelText(/Commentaire/), "Prêt");
    await userEvent.click(screen.getByRole("button", { name: "Soumettre" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/submit"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/submit"))?.body).toEqual({ comment: "Prêt" });
  });

  it("en revue : retirer disponible, pas de validation pour un éditeur", async () => {
    mockApi({ "GET /me": editor, [`GET /playlists/${ID}`]: PL, [`GET /publications/playlist/${ID}`]: pub({ state: "in_review" }) }, base);
    await renderRoutes(routes, `/content/playlists/${ID}`, base);
    expect(await screen.findByRole("button", { name: "Retirer de la validation" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Valider/ })).toBeNull();
    expect(screen.queryByRole("link", { name: "Ouvrir la file à valider" })).toBeNull();
  });

  it("motif de refus affiché sur un brouillon", async () => {
    mockApi({ "GET /me": editor, [`GET /playlists/${ID}`]: PL, [`GET /publications/playlist/${ID}`]: pub({ review_comment: "Image floue" }) }, base);
    await renderRoutes(routes, `/content/playlists/${ID}`, base);
    expect(await screen.findByText("Image floue")).toBeInTheDocument();
  });

  it("admin : file à valider, approuve avec la révision vue, recharge sur stale", async () => {
    let n = 0;
    const calls = mockApi(
      {
        "GET /publications": [pub({ state: "in_review" })],
        [`POST /publications/playlist/${ID}/approve`]: () => (n++ === 0 ? errorResponse(409, "publication.stale") : pub({ state: "published" })),
      },
      base,
    );
    await renderRoutes(routes, "/playback/review", base);
    expect(await screen.findByText("Accueil boucle")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Valider Accueil boucle" }));
    expect(await screen.findByText(/Le contenu a changé depuis votre ouverture/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Recharger" })).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/approve"))?.body).toEqual({ expected_rev: 3, include_dependencies: false });
  });

  it("admin : refus exige un motif", async () => {
    const calls = mockApi({ "GET /publications": [pub({ state: "in_review" })], [`POST /publications/playlist/${ID}/reject`]: pub() }, base);
    await renderRoutes(routes, "/playback/review", base);
    await userEvent.click(await screen.findByRole("button", { name: "Refuser Accueil boucle" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirmer le refus" }));
    expect(await screen.findByText("Ce champ est obligatoire.")).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/reject"))).toBe(false);
    await userEvent.type(screen.getByLabelText(/Motif du refus/), "Trop long");
    await userEvent.click(screen.getByRole("button", { name: "Confirmer le refus" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/reject"))?.body).toEqual({ comment: "Trop long" }));
  });

  it("dépendances : case proposée si bloqué, envoyée à l'approbation", async () => {
    const calls = mockApi({ "GET /publications": [pub({ state: "in_review", entity_type: "schedule", blocked_by: [{ entity_type: "playlist", entity_id: "x" }] })], [`POST /publications/schedule/${ID}/approve`]: pub({ state: "published" }) }, base);
    await renderRoutes(routes, "/playback/review", base);
    await userEvent.click(await screen.findByLabelText("Valider aussi les éléments dont celui-ci dépend"));
    await userEvent.click(screen.getByRole("button", { name: /^Valider / }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/approve"))?.body).toEqual({ expected_rev: 3, include_dependencies: true }));
  });

  it("éditeur sur la file : accès refusé affiché, aucune requête de liste", async () => {
    const calls = mockApi({ "GET /me": editor }, base);
    await renderRoutes(routes, "/playback/review", base);
    expect(await screen.findByText("Seul un administrateur peut valider les publications.")).toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/publications"))).toBe(false);
  });
});

describe("messages d'erreur", () => {
  it.each(["stale", "invalid_transition", "dependency_unpublished", "not_published", "delete_requires_admin", "media_in_use_requires_admin"])("%s traduit en FR et EN", async (code) => {
    for (const lang of ["fr", "en"]) {
      const { initI18n } = await import("../../lib/i18n");
      const i18n = await initI18n("fr", lang);
      const v = i18n.t(`errors.publication.${code}`);
      expect(v).not.toBe(`errors.publication.${code}`);
      expect(v).not.toBe(i18n.t("errors.unknown"));
    }
  });
});
