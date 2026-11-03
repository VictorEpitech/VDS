import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { errorResponse, mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";
import { ownerKey, ownerOptions, type ScopeTree } from "./scope";

const ORG = { id: "90000000-0000-4000-8000-000000000001", name: "Collectivité" };
const SA = "10000000-0000-4000-8000-00000000000a";
const SB = "10000000-0000-4000-8000-00000000000b";
const GA1 = "20000000-0000-4000-8000-0000000000a1";
const GA2 = "20000000-0000-4000-8000-0000000000a2";
const TREE: ScopeTree = {
  can_share: true,
  sites: [
    { id: SA, name: "Moulins", writable: true, groups: [{ id: GA1, name: "Accueil", writable: true }, { id: GA2, name: "Cantine", writable: true }] },
    { id: SB, name: "Vichy", writable: true, groups: [] },
  ],
};
const ME = (over: Record<string, unknown> = {}) => ({ id: "u-admin", display_name: "Ada", email: null, role: "admin", provider: "auth.local", locale: null, role_source: "vds", perimeter: { unrestricted: true, can_share: true, nodes: [] }, ...over });
const user = (over: Record<string, unknown> = {}) => ({ id: "u-ed", display_name: "Marie", email: "marie@vds.test", role: "editor", role_source: "vds", is_active: true, last_seen_at: null, scopes: [], scopes_version: "v1", ...over });

const BASES = ["", "/espace/affichage"];

describe.each(BASES)("utilisateurs et périmètre (base %s)", (basePath) => {
  it("liste les utilisateurs et résume le périmètre", async () => {
    mockApi(
      {
        "GET /me": ME(),
        "GET /scope-tree": TREE,
        "GET /users": { items: [user({ scopes: [{ type: "site", id: SA, name: "Moulins" }, { type: "group", id: GA1, name: "Accueil" }] }), user({ id: "u-ad", display_name: "Boss", role: "admin" })], total: 2 },
      },
      basePath,
    );
    await renderRoutes(routes, "/users", basePath);
    expect(await screen.findByRole("heading", { level: 1, name: "Utilisateurs" })).toBeInTheDocument();
    expect(await screen.findByText(/1 site, 1 groupe/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Marie" })).toHaveAttribute("href", `${basePath}/users/u-ed`);
  });

  it("enregistre le périmètre avec If-Match, cocher un site couvre ses groupes", async () => {
    let saved = false;
    const calls = mockApi(
      {
        "GET /me": ME(),
        "GET /scope-tree": TREE,
        "GET /organization": ORG,
        "GET /users/u-ed": () => (saved ? user({ scopes: [{ type: "site", id: SA, name: "Moulins" }], scopes_version: "v2" }) : user()),
        "GET /users": { items: [], total: 0 },
        "PUT /users/u-ed/scopes": () => {
          saved = true;
          return user({ scopes: [{ type: "site", id: SA, name: "Moulins" }], scopes_version: "v2" });
        },
      },
      basePath,
    );
    const ev = userEvent.setup();
    await renderRoutes(routes, "/users/u-ed", basePath);
    expect(await screen.findByText(/Aucun périmètre : un administrateur doit attribuer/)).toBeInTheDocument();
    await ev.click(await screen.findByRole("checkbox", { name: "Moulins" }));
    const accueil = screen.getByRole("checkbox", { name: "Accueil" });
    expect(accueil).toBeChecked();
    expect(accueil).toBeDisabled();
    await ev.click(within(screen.getByRole("form", { name: "Périmètre" })).getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ scopes: [{ type: "site", id: SA }] });
    expect(calls.find((c) => c.method === "PUT")?.headers?.["If-Match"]).toBe("v1");
    expect(await screen.findByText("Périmètre enregistré.")).toBeInTheDocument();
  });

  it("conflit 412 : message et bouton Recharger qui reprend la version du serveur", async () => {
    let fetched = 0;
    mockApi(
      {
        "GET /me": ME(),
        "GET /scope-tree": TREE,
        "GET /organization": ORG,
        "GET /users/u-ed": () => (++fetched === 1 ? user() : user({ scopes: [{ type: "site", id: SB, name: "Vichy" }], scopes_version: "v9" })),
        "PUT /users/u-ed/scopes": errorResponse(412, "errors.precondition_failed"),
      },
      basePath,
    );
    const ev = userEvent.setup();
    await renderRoutes(routes, "/users/u-ed", basePath);
    await ev.click(await screen.findByRole("checkbox", { name: "Moulins" }));
    await ev.click(within(screen.getByRole("form", { name: "Périmètre" })).getByRole("button", { name: "Enregistrer" }));
    expect(await screen.findByText(/modifié par quelqu'un d'autre/)).toBeInTheDocument();
    await ev.click(screen.getByRole("button", { name: "Recharger" }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Vichy" })).toBeChecked());
    expect(screen.getByRole("checkbox", { name: "Moulins" })).not.toBeChecked();
  });

  it("rôle en lecture seule quand il est géré par le fournisseur", async () => {
    mockApi({ "GET /me": ME(), "GET /scope-tree": TREE, "GET /organization": ORG, "GET /users": { items: [user({ role_source: "provider" })], total: 1 }, "GET /users/u-ed": user({ role_source: "provider" }) }, basePath);
    await renderRoutes(routes, "/users/u-ed", basePath);
    const role = await screen.findByLabelText("Rôle");
    expect(role).toBeDisabled();
    expect(screen.getByText(/Rôle géré par le service de connexion/)).toBeInTheDocument();
  });

  it("garde dernier admin et auto-rétrogradation", async () => {
    mockApi(
      { "GET /me": ME({ id: "u-ad" }), "GET /scope-tree": TREE, "GET /organization": ORG, "GET /users": { items: [user({ id: "u-ad", role: "admin" })], total: 1 }, "GET /users/u-ad": user({ id: "u-ad", role: "admin" }) },
      basePath,
    );
    await renderRoutes(routes, "/users/u-ad", basePath);
    const role = await screen.findByLabelText("Rôle");
    expect(role).toBeDisabled();
    expect(screen.getByLabelText("Compte actif")).toBeDisabled();
    expect(screen.getByText(/ne pouvez pas modifier votre propre rôle/)).toBeInTheDocument();
  });

  it("traduit les refus du serveur (dernier admin, 403 scope)", async () => {
    mockApi({ "GET /me": ME(), "GET /scope-tree": TREE, "GET /organization": ORG, "GET /users": { items: [user(), user({ id: "x", role: "admin" }), user({ id: "y", role: "admin" })], total: 3 }, "GET /users/u-ed": user(), "PATCH /users/u-ed": errorResponse(409, "user.last_admin") }, basePath);
    const ev = userEvent.setup();
    await renderRoutes(routes, "/users/u-ed", basePath);
    await ev.selectOptions(await screen.findByLabelText("Rôle"), "admin");
    await ev.click(screen.getAllByRole("button", { name: "Enregistrer" })[0]!);
    expect(await screen.findByText("Il doit rester au moins un administrateur actif.")).toBeInTheDocument();
  });

  it("page Utilisateurs refusée à un éditeur", async () => {
    mockApi({ "GET /me": ME({ role: "editor", perimeter: { unrestricted: false, can_share: false, nodes: [{ type: "site", id: SA, name: "Moulins" }] } }), "GET /scope-tree": { ...TREE, can_share: false } }, basePath);
    await renderRoutes(routes, "/users", basePath);
    expect(await screen.findByRole("heading", { level: 1, name: "Page introuvable" })).toBeInTheDocument();
  });
});

describe("propriétaire des contenus", () => {
  it("sans choix multiple, pas de sélecteur ; « Partagé » seulement si can_share", () => {
    const only: ScopeTree = { can_share: false, sites: [{ id: SA, name: "Moulins", writable: true, groups: [] }] };
    expect(ownerOptions(only, "Partagé")).toHaveLength(1);
    const two = ownerOptions({ ...TREE, can_share: false }, "Partagé");
    expect(two.map((o) => o.label)).toEqual(["Moulins", "Moulins / Accueil", "Moulins / Cantine", "Vichy"]);
    expect(ownerOptions(TREE, "Partagé")[0]).toMatchObject({ key: "shared", owner: null });
    expect(ownerKey(null)).toBe("shared");
    expect(ownerKey({ type: "organization", id: ORG.id })).toBe("shared");
  });

  it("création de playlist : sélecteur, propriétaire envoyé, badge affiché", async () => {
    const calls = mockApi(
      {
        "GET /me": ME(),
        "GET /scope-tree": TREE,
        "GET /playlists": { items: [{ id: "p1", name: "Menu", item_count: 0, created_at: "", updated_at: "", expires_at: null, expired: false, tags: [], owner: { type: "site", id: SA } }], total: 1 },
        "POST /playlists": { id: "p2", name: "Nouvelle", items: [], tags: [], expired: false, expires_at: null },
        "GET /playlists/p2": { id: "p2", name: "Nouvelle", items: [], tags: [], expired: false, expires_at: null },
      },
      "/espace/affichage",
    );
    const ev = userEvent.setup();
    await renderRoutes(routes, "/content/playlists", "/espace/affichage");
    expect(await screen.findByText("Moulins", { selector: ".owner-badge span span:last-child, .owner-badge > span:last-child" })).toBeInTheDocument();
    await ev.selectOptions(await screen.findByLabelText("Propriétaire"), "Moulins / Cantine");
    await ev.type(screen.getByLabelText("Nom de la playlist"), "Nouvelle");
    await ev.click(screen.getByRole("button", { name: "Créer la playlist" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "Nouvelle", owner: { type: "group", id: GA2 } }));
  });

  it("éditeur restreint : pas d'option Partagé, suppression masquée sur un contenu partagé, usages cachés comptés", async () => {
    const restricted = ME({ role: "editor", perimeter: { unrestricted: false, can_share: false, nodes: [{ type: "site", id: SA, name: "Moulins" }] } });
    mockApi(
      {
        "GET /me": restricted,
        "GET /scope-tree": { can_share: false, sites: [TREE.sites[0]!] },
        "GET /playlists": { items: [{ id: "p1", name: "Partagée", item_count: 0, created_at: "", updated_at: "", expires_at: null, expired: false, tags: [], owner: null }, { id: "p2", name: "Locale", item_count: 0, created_at: "", updated_at: "", expires_at: null, expired: false, tags: [], owner: { type: "site", id: SA } }], total: 2 },
      },
      "",
    );
    await renderRoutes(routes, "/content/playlists", "");
    await screen.findByText("Locale");
    const rows = screen.getAllByRole("listitem").filter((li) => li.classList.contains("row"));
    expect(within(rows[0]!).queryByRole("button", { name: "Supprimer" })).toBeNull();
    expect(within(rows[1]!).getByRole("button", { name: "Supprimer" })).toBeInTheDocument();
    // Plus d'un choix (site + 2 groupes) mais jamais « Partagé ».
    const select = screen.getByLabelText("Propriétaire");
    expect(within(select).queryByRole("option", { name: "Partagé" })).toBeNull();
    // Menu réduit : pas de « Plus ».
    expect(screen.queryByRole("link", { name: /^Plus$/ })).toBeNull();
  });

  it("éditeur sans périmètre : message dédié", async () => {
    mockApi({ "GET /me": ME({ role: "editor", perimeter: { unrestricted: false, can_share: false, nodes: [] } }), "GET /scope-tree": { can_share: false, sites: [] } }, "");
    await renderRoutes(routes, "/", "");
    expect(await screen.findByText(/Aucun périmètre : un administrateur doit vous en attribuer un/)).toBeInTheDocument();
  });

  it("hidden_count d'un média : nombre sans nom, en FR et en EN", async () => {
    const media = { id: "m1", title: "Affiche", kind: "image", status: "ready", mime: "image/png", bytes: 10, usage_count: 2, tags: [], expired: false, expires_at: null, created_at: "2026-11-01T08:00:00Z", owner: null, revision: 1 };
    const usage = { playlists: [], schedules: [], overrides: [], screens_now: 0, hidden_count: 3 };
    for (const [locale, text] of [["fr", "3 autres usages hors de votre périmètre"], ["en", "3 other uses outside your scope"]] as const) {
      mockApi({ "GET /me": ME(), "GET /scope-tree": TREE, "GET /media": { items: [media], total: 1 }, "GET /media/m1": media, "GET /media/m1/usage": usage }, "");
      const ev = userEvent.setup();
      const view = await renderRoutes(routes, "/content/media", "", locale);
      await ev.click(await screen.findByRole("button", { name: "Affiche" }));
      expect(await screen.findByText(text)).toBeInTheDocument();
      view.unmount();
    }
  });
});
