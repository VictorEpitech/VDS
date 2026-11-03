import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";
import type { Api } from "../../lib/api";
import { ActionQueue, DELAY_MS, type Target } from "../actions/queue";
import { availableActions, pollDelay, relativeAge, type PlayerCommand } from "./commands";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const ALL = ["cmd.reload", "cmd.identify", "cmd.screenshot", "cmd.restart.app", "cmd.restart.device", "cmd.cache.resync"];
const SHOT = { id: "50000000-0000-4000-8000-000000000001", screen_id: "", trigger: "on_demand", captured_at: new Date(Date.now() - 180_000).toISOString(), width: 640, height: 360, bytes: 1000, image_url: "/api/v1/screenshots/50000000-0000-4000-8000-000000000001/image" };
const dev = { presence: "online", last_seen_at: "2026-10-24T09:00:00Z", platform: "android" };
const mk = (n: number, extra: Record<string, unknown> = {}) => ({ id: `30000000-0000-4000-8000-${String(n).padStart(12, "0")}`, site_id: SITE.id, group_id: null, name: `Écran ${n}`, orientation: "landscape", archived_at: null, device: dev, kiosk: null, capabilities: ALL, last_screenshot: null, ...extra });
const pc = (id: string, status: PlayerCommand["status"], extra: Partial<PlayerCommand> = {}): PlayerCommand => ({ id, screen_id: null, name: "reload", args: {}, status, code: null, requested_by: "Ada", created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 600_000).toISOString(), done_at: null, screenshot_id: null, ...extra });

afterEach(() => vi.useRealTimers());

describe("règles d'affichage", () => {
  it("masque restart et cache.resync à l'éditeur, et ce que l'appareil ne sait pas faire", () => {
    const s = mk(1);
    expect(availableActions(s as never, false).map((a) => a.id)).toEqual(["reload", "identify", "screenshot"]);
    expect(availableActions(s as never, true).map((a) => a.id)).toContain("restart.device");
    expect(availableActions(mk(2, { capabilities: ["cmd.reload"] }) as never, true).map((a) => a.id)).toEqual(["reload"]);
    expect(availableActions(mk(3, { device: null }) as never, true)).toEqual([]);
  });
  it("sondage 1 s pendant 20 s puis 5 s", () => {
    expect(pollDelay(0)).toBe(1000);
    expect(pollDelay(19_999)).toBe(1000);
    expect(pollDelay(20_000)).toBe(5000);
  });
  it("âge relatif dans la langue active", () => {
    const now = Date.now();
    expect(relativeAge(new Date(now - 180_000).toISOString(), "fr", now)).toBe("il y a 3 minutes");
    expect(relativeAge(new Date(now - 180_000).toISOString(), "en", now)).toBe("3 minutes ago");
  });
});

describe.each(["", "/espace/affichage"])("fiche écran, base « %s »", (b) => {
  const base = (screens: unknown[], extra: Record<string, unknown> = {}) => ({ "GET /sites": [SITE], "GET /screens": screens, [`GET /sites/${SITE.id}/groups`]: [], "GET /fleet/playback": [], ...extra });

  it("affiche la dernière capture (alt daté, chemin sous la base) et les actions", async () => {
    const s = mk(1);
    s.last_screenshot = { ...SHOT, screen_id: s.id } as never;
    mockApi(base([s], { [`GET /screens/${s.id}/commands`]: [], [`GET /screens/${s.id}/screenshots`]: [s.last_screenshot] }), b);
    await renderRoutes(routes, "/screens", b);
    const img = await screen.findByRole("img", { name: /Capture de Écran 1, il y a 3 minutes/ });
    expect(img.getAttribute("src")).toBe(`${b}${SHOT.image_url}`);
    expect(screen.getByRole("button", { name: "Recharger : Écran 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Identifier : Écran 1" })).toBeInTheDocument();
  });

  it("envoie reload avec une clé d'idempotence et annonce l'état", async () => {
    const s = mk(1);
    const calls = mockApi(base([s], { [`GET /screens/${s.id}/commands`]: [], [`POST /screens/${s.id}/commands`]: pc("c1", "queued") }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    await user.click(await screen.findByRole("button", { name: "Recharger : Écran 1" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ name: "reload", args: {}, request_key: expect.stringMatching(/^[0-9a-f-]{36}$/) }));
    expect(calls.find((c) => c.method === "POST")?.path).toBe(`/screens/${s.id}/commands`);
  });

  it("redémarrer demande une confirmation explicite avant tout envoi", async () => {
    const s = mk(1);
    const calls = mockApi(base([s], { [`GET /screens/${s.id}/commands`]: [], [`POST /screens/${s.id}/commands`]: pc("c1", "queued", { name: "restart", args: { scope: "app" } }) }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    await user.click(await screen.findByRole("button", { name: "Redémarrer l'application" }));
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await user.click(screen.getByRole("button", { name: /Confirmer le redémarrage de l'application de Écran 1/ }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ name: "restart", args: { scope: "app" } }));
  });

  it("l'éditeur ne voit ni redémarrage ni resynchronisation", async () => {
    const s = mk(1);
    mockApi(base([s], { "GET /me": { id: "u2", display_name: "Marie", email: null, role: "editor", provider: "auth.local", locale: null, role_source: "vds", perimeter: { unrestricted: false, can_share: false, nodes: [{ type: "site", id: SITE.id, name: SITE.name }] } }, [`GET /screens/${s.id}/commands`]: [] }), b);
    await renderRoutes(routes, "/screens", b);
    expect(await screen.findByRole("button", { name: "Recharger : Écran 1" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Redémarrer l'application" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Autres commandes" })).not.toBeInTheDocument();
  });

  it("range la resynchronisation du cache dans le volet Avancé", async () => {
    const s = mk(1);
    mockApi(base([s], { [`GET /screens/${s.id}/commands`]: [] }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    expect(screen.queryByRole("button", { name: "Resynchroniser le cache : Écran 1" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Autres commandes" }));
    expect(screen.getByRole("button", { name: "Resynchroniser le cache : Écran 1" })).toBeInTheDocument();
  });

  it("libelle l'aperçu théorique quand l'appareil ne sait pas capturer, sans bouton de capture", async () => {
    const s = mk(1, { capabilities: ["cmd.reload", "cmd.identify"], device: { ...dev, platform: "web" } });
    mockApi(base([s], { "GET /fleet/playback": [{ screen_id: s.id, source: "schedule", playlist_name: "Accueil", schedule_name: "Semaine", until: null, next_change_at: null }], [`GET /screens/${s.id}/commands`]: [] }), b);
    await renderRoutes(routes, "/screens", b);
    expect((await screen.findAllByText("Aperçu de la programmation, pas une capture réelle")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("img", { name: /Capture de/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Actualiser la capture/ })).not.toBeInTheDocument();
  });

  it("montre statut (icône + libellé), cause traduite et annulation d'une commande en attente", async () => {
    const s = mk(1);
    const calls = mockApi(
      base([s], {
        [`GET /screens/${s.id}/commands`]: [pc("c1", "queued", { name: "identify" }), pc("c2", "failed", { code: "cmd.failed" }), pc("c3", "expired")],
        [`POST /screens/${s.id}/commands/c1/cancel`]: pc("c1", "cancelled", { name: "identify" }),
      }),
      b,
    );
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    const list = await screen.findByRole("list", { name: "Commandes récentes" });
    expect(within(list).getByText("En attente")).toBeInTheDocument();
    expect(within(list).getByText("Échec")).toBeInTheDocument();
    expect(within(list).getByText(/l'appareil n'a pas pu l'exécuter/)).toBeInTheDocument();
    expect(within(list).getByText("Expirée")).toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: "Annuler la commande : Identifier" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/c1/cancel"))).toBe(true));
  });

  it("tableau de bord des captures : filtre et actualisation", async () => {
    const a = mk(1, { last_screenshot: { ...SHOT, screen_id: "x" } });
    const w = mk(2, { capabilities: ["cmd.reload"], device: { ...dev, platform: "web" } });
    mockApi(base([a, w], { [`GET /screens/${a.id}/commands`]: [], [`POST /screens/${a.id}/commands`]: pc("c1", "queued", { name: "screenshot" }) }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/captures", b);
    expect(await screen.findByRole("heading", { level: 1, name: "Captures d'écran" })).toBeInTheDocument();
    expect(await screen.findAllByRole("heading", { level: 2 })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: /Actualiser la capture/ })).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "À traiter" }));
    expect(screen.queryAllByRole("heading", { level: 2 })).toHaveLength(0);
  });
});

describe("EN", () => {
  it("libellés anglais de la fiche", async () => {
    const s = mk(1);
    mockApi({ "GET /sites": [SITE], "GET /screens": [s], [`GET /sites/${SITE.id}/groups`]: [], "GET /fleet/playback": [], [`GET /screens/${s.id}/commands`]: [] });
    await renderRoutes(routes, "/screens", "", "en");
    expect(await screen.findByRole("button", { name: "Reload: Écran 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restart the app" })).toBeInTheDocument();
  });
});

describe("actions groupées sur les commandes player", () => {
  const T = (n: number): Target => ({ screenId: `s${n}`, name: `Écran ${n}`, siteId: SITE.id });
  function fake() {
    const posts: { path: string; body: Record<string, unknown> }[] = [];
    const gets: string[] = [];
    const store = new Map<string, PlayerCommand>();
    const api = {
      get: vi.fn((path: string) => {
        gets.push(path);
        const ids = new URL(`http://x${path}`).searchParams.get("ids")?.split(",") ?? [];
        return Promise.resolve(ids.map((id) => store.get(id)).filter(Boolean).map((c) => ({ ...c!, status: "ok" })));
      }),
      post: vi.fn((path: string, body: Record<string, unknown>) => {
        posts.push({ path, body });
        const c = pc(`c${posts.length}`, "queued", { name: body.name as PlayerCommand["name"] });
        store.set(c.id, c);
        return Promise.resolve(c);
      }),
      patch: vi.fn(),
      delete: vi.fn(),
    };
    return { api: api as unknown as Api, posts, gets };
  }

  it("reload part tout de suite avec un batch_id commun, un seul sondage groupé", async () => {
    vi.useFakeTimers();
    const { api, posts, gets } = fake();
    const q = new ActionQueue(api);
    q.enqueue("reload", [T(1), T(2), T(3)], { skipped: 2 });
    await vi.advanceTimersByTimeAsync(10);
    expect(posts).toHaveLength(3);
    expect(new Set(posts.map((p) => p.body.batch_id)).size).toBe(1);
    expect(new Set(posts.map((p) => p.body.request_key)).size).toBe(3);
    await vi.advanceTimersByTimeAsync(1100);
    expect(gets).toHaveLength(1);
    expect(gets[0]).toContain("/player-commands?ids=c1,c2,c3");
    expect(q.getSnapshot()[0]?.phase).toBe("done");
  });

  it("restart attend 8 s : annuler n'envoie rien", async () => {
    vi.useFakeTimers();
    const { api, posts } = fake();
    const q = new ActionQueue(api);
    const id = q.enqueue("restart", [T(1)]);
    await vi.advanceTimersByTimeAsync(DELAY_MS - 1000);
    expect(q.cancel(id)).toBe(true);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(posts).toHaveLength(0);
  });

  it("restart envoie scope app après le délai", async () => {
    vi.useFakeTimers();
    const { api, posts } = fake();
    const q = new ActionQueue(api);
    q.enqueue("restart", [T(1)]);
    await vi.advanceTimersByTimeAsync(DELAY_MS + 10);
    expect(posts[0]?.body).toMatchObject({ name: "restart", args: { scope: "app" } });
  });
});
