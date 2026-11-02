import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { errorResponse, mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";
import { expiryKind, fromParts, readView, storeView, toParts } from "../../lib/media-view";
import { listQuery } from "../../lib/queries";
import { isValidTag, normalizeTag } from "./TagEditor";

const M = (n: number, extra: Record<string, unknown> = {}) => ({
  id: `40000000-0000-4000-8000-00000000000${n}`,
  title: `Média ${n}`,
  kind: "image",
  mime: "image/png",
  bytes: 2048,
  status: "ready",
  error: null,
  original_filename: `m${n}.png`,
  width: 800,
  height: 600,
  duration_ms: null,
  page_count: null,
  sha256: "ab".repeat(32),
  created_at: "2026-10-01T08:00:00Z",
  updated_at: "2026-10-01T08:00:00Z",
  expires_at: null,
  expired: false,
  revision: 1,
  tags: [],
  thumb: null,
  usage_count: 1,
  ...extra,
});
const USAGE = { playlists: [{ id: "p1", name: "Accueil", expires_at: null, expired: false }, { id: "p2", name: "Hall", expires_at: null, expired: false }], schedules: [], overrides: [], screens_now: 3 };

beforeEach(() => window.localStorage.clear());

describe("aides de médiathèque", () => {
  it("mode d'affichage mémorisé, tolérant à un stockage indisponible", () => {
    expect(readView()).toBe("grid");
    storeView("table");
    expect(readView()).toBe("table");
    const spy = Object.getOwnPropertyDescriptor(window, "localStorage")!;
    Object.defineProperty(window, "localStorage", { get: () => { throw new Error("denied"); }, configurable: true });
    expect(readView()).toBe("grid");
    expect(() => storeView("table")).not.toThrow();
    Object.defineProperty(window, "localStorage", spy);
  });

  it("date + heure locales aller-retour, états d'expiration, tags, requête", () => {
    const iso = fromParts("2026-12-31", "18:30")!;
    expect(toParts(iso)).toEqual({ date: "2026-12-31", time: "18:30" });
    expect(fromParts("", "10:00")).toBeNull();
    const now = Date.parse("2026-11-01T00:00:00Z");
    expect(expiryKind({ expired: true, expires_at: "2026-10-01T00:00:00Z" }, now)).toBe("expired");
    expect(expiryKind({ expired: false, expires_at: "2026-11-03T00:00:00Z" }, now)).toBe("soon");
    expect(expiryKind({ expired: false, expires_at: "2027-01-01T00:00:00Z" }, now)).toBe("later");
    expect(expiryKind({ expired: false, expires_at: null }, now)).toBe("none");
    expect(normalizeTag("  Été ")).toBe("été");
    expect(isValidTag("-x")).toBe(false);
    expect(listQuery({ q: " a ", tags: ["x", "y"], unused: true, state: "all", offset: 24, limit: 24 })).toBe("q=a&state=all&tags=x&tags=y&unused=true&limit=24&offset=24");
  });
});

describe.each(["", "/espace/affichage"])("médiathèque sous la base « %s »", (b) => {
  const media = { items: [M(1, { expires_at: "2099-01-01T10:00:00Z" }), M(2, { expired: true, expires_at: "2020-01-01T10:00:00Z", usage_count: 0, thumb: "media/x/thumb" })], total: 2 };

  it("grille : badges avec icône et libellé, aperçu sous la base, bascule tableau mémorisée", async () => {
    mockApi({ "GET /media": media, "GET /playlists": { items: [], total: 0 } }, b);
    await renderRoutes(routes, "/content/media", b);
    expect(await screen.findByText(/Expire le/)).toBeInTheDocument();
    expect(screen.getByText("Expiré")).toBeInTheDocument();
    expect(screen.getByText("Inutilisé")).toBeInTheDocument();
    expect(document.querySelector("img")!.getAttribute("src")).toBe(`${b}/media/x/thumb`);
    await userEvent.click(screen.getByRole("button", { name: "Tableau" }));
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(window.localStorage.getItem("vds.media.view")).toBe("table");
  });

  it("filtres envoyés à l'API et retour à la première page", async () => {
    const calls = mockApi({ "GET /media": media, "GET /playlists": { items: [], total: 0 } }, b);
    await renderRoutes(routes, "/content/media", b);
    await screen.findByText("Média 1");
    await userEvent.selectOptions(screen.getByLabelText("État"), "expired");
    await waitFor(() => expect(calls.some((c) => c.path.includes("state=expired"))).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText("Type"), "video");
    await waitFor(() => expect(calls.some((c) => c.path.includes("kind=video") && c.path.includes("state=expired"))).toBe(true));
  });

  it("sélection multiple : expiration groupée et échecs listés", async () => {
    const calls = mockApi(
      { "GET /media": media, "GET /playlists": { items: [], total: 0 }, "POST /media/bulk": { done: [media.items[0]!.id], failed: [{ id: media.items[1]!.id, code: "media.in_use" }] } },
      b,
    );
    await renderRoutes(routes, "/content/media", b);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Sélectionner la page" }));
    expect(screen.getByText("2 médias sélectionnés")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Supprimer la sélection" }));
    await userEvent.click(screen.getByRole("button", { name: "Supprimer 2 médias" }));
    const post = calls.find((c) => c.method === "POST" && c.path === "/media/bulk")!;
    expect(post.body).toMatchObject({ action: "delete", ids: media.items.map((m) => m.id) });
    expect(await screen.findByText(/1 média modifié, 1 échec/)).toBeInTheDocument();
    expect(screen.getByText(/Média 2 : Ce média est utilisé/)).toBeInTheDocument();
  });

  it("détail : usage, impact du remplacement « N playlists, M écrans », expiration et erreur de type", async () => {
    const m = media.items[0]!;
    const calls = mockApi(
      {
        "GET /media": media,
        "GET /playlists": { items: [], total: 0 },
        [`GET /media/${m.id}`]: { ...m, variants: [], used_by: [] },
        [`GET /media/${m.id}/usage`]: USAGE,
        [`PATCH /media/${m.id}`]: m,
        [`POST /media/${m.id}/file`]: () => errorResponse(422, "media.replace_kind_mismatch"),
        "GET /media/tags": [],
      },
      b,
    );
    await renderRoutes(routes, "/content/media", b);
    await userEvent.click(await screen.findByRole("button", { name: "Média 1" }));
    const panel = await screen.findByRole("complementary", { name: "Média 1" });
    expect(await within(panel).findByRole("link", { name: "Accueil" })).toHaveAttribute("href", `${b}/content/playlists/p1`);
    expect(within(panel).getByText("image/png")).toHaveClass("mono");
    await userEvent.click(within(panel).getByRole("button", { name: "Remplacer le fichier" }));
    expect(await within(panel).findByText("Cette opération touche 2 playlists, 3 écrans.")).toBeInTheDocument();
    await userEvent.click(within(panel).getByRole("button", { name: "Retirer l'expiration" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ expires_at: null }));
    // Avancé reste replié.
    expect(panel.querySelector("details.advanced")).not.toHaveAttribute("open");
  });
});

describe("éditeur de playlist", () => {
  it("élément dont le média est expiré : barré avec badge « Expiré » ; options repliées", async () => {
    const m = M(1, { expired: true, expires_at: "2020-01-01T00:00:00Z" });
    const pl = {
      id: "p1", name: "Boucle", description: null, item_count: 1, created_at: "", updated_at: "", expires_at: null, expired: false, tags: [],
      items: [{ id: "i1", content_type: "content.image", duration_ms: null, enabled: true, position: 0, props: { media: m.id }, media_ids: [m.id] }],
    };
    mockApi({ "GET /playlists/p1": pl, "GET /media": { items: [m], total: 1 }, "GET /playlists/p1/usage": { schedules: [], overrides: [] } });
    await renderRoutes(routes, "/content/playlists/p1", "");
    const title = await screen.findByRole("heading", { level: 3, name: /Média 1/ });
    expect(title).toHaveClass("item-expired");
    expect(within(title.closest("li")!).getByText("Expiré")).toBeInTheDocument();
    expect(await screen.findByText("Cette playlist n'est ni programmée ni forcée.")).toBeInTheDocument();
    expect(document.querySelector("details.advanced")).not.toHaveAttribute("open");
  });
});
