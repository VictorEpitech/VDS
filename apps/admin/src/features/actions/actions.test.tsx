import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { atOut } from "../../../test/planning-at";
import { errorResponse, mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";
import { ApiError, type Api } from "../../lib/api";
import type { Command } from "../../lib/queries";
import { ActionQueue, DELAY_MS, MAX_PARALLEL, type Target } from "./queue";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const AGENT = { id: "70000000-0000-4000-8000-000000000001", site_id: SITE.id, name: "Salle technique", token_prefix: "vds_agt_AbCd", status: "online", version: "1.0.0", last_seen_at: "2026-10-24T09:00:00Z", last_heartbeat: null, created_at: "2026-10-01T08:00:00Z", rotated_at: null, revoked_at: null };
const mkScreen = (n: number, device: unknown = { presence: "online", last_seen_at: "2026-10-24T09:00:00Z" }) => ({ id: `30000000-0000-4000-8000-${String(n).padStart(12, "0")}`, site_id: SITE.id, group_id: null, name: `Écran ${n}`, orientation: "landscape", archived_at: null, device, kiosk: null });
const cmd = (id: string, status: string, extra: Partial<Command> = {}): Command => ({ id, seq: 1, name: "power.set", screen_id: null, args: {}, status, code: null, result: null, created_at: "", sent_at: null, done_at: null, expires_at: "", ...extra }) as Command;
const target = (n: number): Target => ({ screenId: `s${n}`, name: `Écran ${n}`, siteId: SITE.id, agentId: AGENT.id });

afterEach(() => vi.useRealTimers());

function fakeApi() {
  const posts: { path: string; body: { args: { request_id: string } } }[] = [];
  const gets: string[] = [];
  let seq = 0;
  const statuses = new Map<string, Command>();
  const api = {
    get: vi.fn((path: string) => {
      gets.push(path);
      return Promise.resolve([...statuses.values()]);
    }),
    post: vi.fn((path: string, body: { args: { request_id: string }; screen_id: string }) => {
      posts.push({ path, body });
      const c = cmd(`c${++seq}`, "pending", { screen_id: body.screen_id });
      statuses.set(c.id, c);
      return Promise.resolve(c);
    }),
    patch: vi.fn(() => Promise.resolve({})),
    delete: vi.fn(() => Promise.resolve()),
  };
  return { api: api as unknown as Api & typeof api, posts, gets, statuses };
}

describe("file d'actions", () => {
  it("annuler pendant l'attente n'envoie rien", async () => {
    vi.useFakeTimers();
    const { api } = fakeApi();
    const q = new ActionQueue(api);
    const id = q.enqueue("on", [target(1), target(2)]);
    expect(q.hasUnfinished()).toBe(true);
    await vi.advanceTimersByTimeAsync(DELAY_MS - 1000);
    expect(q.cancel(id)).toBe(true);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(api.post).not.toHaveBeenCalled();
    expect(q.getSnapshot()).toHaveLength(0);
    expect(q.hasUnfinished()).toBe(false);
  });

  it("n'envoie rien avant 8 s, puis rapporte l'échec partiel et relance seulement les échecs avec de nouveaux request_id", async () => {
    vi.useFakeTimers();
    const f = fakeApi();
    const first = f.api.post.getMockImplementation()!;
    f.api.post.mockImplementation((path: string, body: { args: { request_id: string }; screen_id: string }) => (body.screen_id === "s2" ? Promise.reject(new ApiError(502, "errors.agent_unreachable")) : first(path, body)));
    const q = new ActionQueue(f.api);
    const id = q.enqueue("on", [target(1), target(2), target(3)]);
    await vi.advanceTimersByTimeAsync(DELAY_MS - 1);
    expect(f.api.post).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(f.api.post).toHaveBeenCalledTimes(3);
    for (const [cid, c] of f.statuses) f.statuses.set(cid, { ...c, status: "ok" });
    await vi.advanceTimersByTimeAsync(2_000);
    const done = q.getSnapshot()[0]!;
    expect(done.phase).toBe("done");
    expect(done.results.s1?.status).toBe("ok");
    expect(done.results.s2?.status).toBe("failed");
    expect(done.results.s3?.status).toBe("ok");
    const firstIds = f.posts.map((p) => p.body.args.request_id);
    f.api.post.mockImplementation(first);
    const retry = q.retryFailed(id);
    expect(retry).not.toBeNull();
    await vi.advanceTimersByTimeAsync(0);
    expect(f.posts).toHaveLength(3);
    expect(f.posts[2]!.body).toMatchObject({ screen_id: "s2" });
    expect(firstIds).not.toContain(f.posts[2]!.body.args.request_id);
  });

  it("Réessayer réutilise le request_id après une erreur réseau, en génère un nouveau après une erreur HTTP", async () => {
    vi.useFakeTimers();
    const f = fakeApi();
    const seen: string[] = [];
    let n = 0;
    f.api.post.mockImplementation((_p: string, body: { args: { request_id: string }; screen_id: string }) => {
      seen.push(body.args.request_id);
      n++;
      return n === 1 ? Promise.reject(new TypeError("Failed to fetch")) : Promise.reject(new ApiError(502, "errors.agent_unreachable"));
    });
    const q = new ActionQueue(f.api);
    const id = q.enqueue("on", [target(1)], { delayMs: 0 });
    await vi.advanceTimersByTimeAsync(0);
    const r1 = q.retryFailed(id)!;
    await vi.advanceTimersByTimeAsync(0);
    expect(seen).toHaveLength(2);
    expect(seen[1]).toBe(seen[0]);
    const r2 = q.retryFailed(r1)!;
    await vi.advanceTimersByTimeAsync(0);
    expect(r2).not.toBeNull();
    expect(seen).toHaveLength(3);
    expect(seen[2]).not.toBe(seen[1]);
  });

  it("apk_update : envoi immédiat sans délai d'annulation, commande apk.update avec request_id seul, sondage jusqu'à 300 s", async () => {
    vi.useFakeTimers();
    const f = fakeApi();
    const q = new ActionQueue(f.api);
    const id = q.enqueue("apk_update", [target(1)]);
    expect(q.getSnapshot()[0]!.phase).toBe("running");
    expect(q.cancel(id)).toBe(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(f.posts[0]!.body).toMatchObject({ name: "apk.update", screen_id: "s1", args: { request_id: expect.any(String) } });
    expect(Object.keys(f.posts[0]!.body.args)).toEqual(["request_id"]);
    await vi.advanceTimersByTimeAsync(200_000);
    expect(q.getSnapshot()[0]!.phase).toBe("running");
    for (const [cid, c] of f.statuses) f.statuses.set(cid, { ...c, status: "ok" });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(q.getSnapshot()[0]!.phase).toBe("done");
  });

  it("apk_update : un 409 est un échec par écran et Réessayer ne renvoie que les échecs", async () => {
    vi.useFakeTimers();
    const f = fakeApi();
    const first = f.api.post.getMockImplementation()!;
    f.api.post.mockImplementation((path: string, body: { args: { request_id: string }; screen_id: string }) => (body.screen_id === "s2" ? Promise.reject(new ApiError(409, "errors.update_running")) : first(path, body)));
    const q = new ActionQueue(f.api);
    const id = q.enqueue("apk_update", [target(1), target(2)]);
    await vi.advanceTimersByTimeAsync(0);
    for (const [cid, c] of f.statuses) f.statuses.set(cid, { ...c, status: "ok" });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(q.getSnapshot()[0]!.results.s2?.status).toBe("failed");
    f.api.post.mockImplementation(first);
    expect(q.retryFailed(id)).not.toBeNull();
    expect(q.getSnapshot()[0]!.phase).toBe("running");
    await vi.advanceTimersByTimeAsync(0);
    expect(f.posts).toHaveLength(2);
    expect(f.posts[1]!.body).toMatchObject({ name: "apk.update", screen_id: "s2" });
  });

  it("dispose() résout les sondages en attente en « expired »", async () => {
    vi.useFakeTimers();
    const f = fakeApi();
    const q = new ActionQueue(f.api);
    q.enqueue("on", [target(1)], { delayMs: 0 });
    await vi.advanceTimersByTimeAsync(100);
    expect(q.hasUnfinished()).toBe(true);
    q.dispose();
    await vi.advanceTimersByTimeAsync(0);
    const b = q.getSnapshot()[0]!;
    expect(b.phase).toBe("done");
    const o = b.results.s1;
    expect(o?.status === "failed" && o.command?.status).toBe("expired");
  });

  it("un seul sondage par agent, quel que soit le nombre d'écrans", async () => {
    vi.useFakeTimers();
    const f = fakeApi();
    const q = new ActionQueue(f.api);
    q.enqueue("off", [target(1), target(2), target(3), target(4)], { delayMs: 0 });
    await vi.advanceTimersByTimeAsync(0);
    expect(f.posts).toHaveLength(4);
    await vi.advanceTimersByTimeAsync(700);
    expect(f.gets).toHaveLength(1);
    for (const [cid, c] of f.statuses) f.statuses.set(cid, { ...c, status: "ok" });
    await vi.advanceTimersByTimeAsync(700);
    expect(f.gets).toHaveLength(2);
    expect(q.getSnapshot()[0]!.phase).toBe("done");
  });

  it("au plus 4 requêtes en parallèle", async () => {
    vi.useFakeTimers();
    const f = fakeApi();
    let inflight = 0;
    let peak = 0;
    f.api.patch.mockImplementation(() => {
      inflight++;
      peak = Math.max(peak, inflight);
      return new Promise((r) => setTimeout(() => { inflight--; r({}); }, 50));
    });
    const q = new ActionQueue(f.api);
    q.enqueue("group", Array.from({ length: 10 }, (_, i) => target(i)), { groupId: "g1", delayMs: 0 });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(f.api.patch).toHaveBeenCalledTimes(10);
    expect(peak).toBe(MAX_PARALLEL);
    expect(q.getSnapshot()[0]!.phase).toBe("done");
  });
});

describe.each(["", "/espace/affichage"])("liste des écrans : actions, base « %s »", (b) => {
  const power = (id: string) => ({ [`GET /power/screens/${id}`]: { screen_id: id, timezone: "Europe/Paris", plan_id: null, plan_id_origin: { level: "default", id: null, name: null }, calendars: [], calendars_origin: { level: "default", id: null, name: null }, slots: [] } });
  const incident = (id: string) => ({ id: "i1", kind: "power.failed", severity: "critical", subject_type: "screen", subject_id: id, subject_name: "Écran 1", site_id: SITE.id, site_name: "Moulins", details: {}, opened_at: "2026-10-24T08:00:00Z", resolved_at: null, acknowledged_at: null, acknowledged_by: null });
  const base = (screens: unknown[], extra: Record<string, unknown> = {}) => ({
    "GET /sites": [SITE],
    "GET /screens": screens,
    "GET /incidents": [],
    "GET /planning/at": atOut(screens.map((s) => ({ screen_id: (s as { id: string }).id, source: "none", schedule_id: null, schedule_name: null, playlist_id: null, playlist_name: null, until: null, next_change_at: null }))),
    [`GET /sites/${SITE.id}/groups`]: [],
    [`GET /sites/${SITE.id}/agents`]: [AGENT],
    ...extra,
  });

  it("Allumer est actif pour un écran en ligne, sous « tous » comme sous « à traiter »", async () => {
    const s = mkScreen(1);
    mockApi(base([s], { ...power(s.id), "GET /incidents": [incident(s.id)], [`GET /config/screen/${s.id}/core`]: { values: {}, warnings: [] } }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    expect(await screen.findByRole("button", { name: "Allumer Écran 1" })).toBeEnabled();
    expect(await screen.findByText("Aucune programmation")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /À traiter/ }));
    expect(await screen.findByRole("button", { name: "Allumer Écran 1" })).toBeEnabled();
    expect(screen.getAllByRole("heading", { name: "Écran 1" })).toHaveLength(1);
  });

  it("plafonne la sélection à 200 écrans", async () => {
    const list = Array.from({ length: 201 }, (_, i) => mkScreen(i + 1));
    mockApi(base(list), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    await user.click(await screen.findByRole("checkbox", { name: /Tout sélectionner \(201/ }));
    expect(await screen.findByText("200 écrans sélectionnés")).toBeInTheDocument();
    expect(screen.getAllByText("Sélection limitée à 200 écrans.", { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getByRole("checkbox", { name: "Sélectionner Écran 201" })).toBeDisabled();
  }, 30_000);

  it("annuler l'action en attente n'envoie aucune commande ; les écrans sans agent en ligne sont comptés", async () => {
    const a = mkScreen(1);
    const c = { ...mkScreen(2), site_id: "10000000-0000-4000-8000-0000000000ff" };
    const calls = mockApi(base([a, c], { "GET /sites": [SITE, { ...SITE, id: c.site_id, name: "Autre" }], [`GET /sites/${c.site_id}/groups`]: [], [`GET /sites/${c.site_id}/agents`]: [] }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    await user.click(await screen.findByRole("checkbox", { name: /Tout sélectionner/ }));
    const bar = await screen.findByRole("region", { name: "Actions sur la sélection" });
    expect(within(bar).getByText(/1 écran ignoré pour Allumer et Éteindre/)).toBeInTheDocument();
    await user.click(within(bar).getByRole("button", { name: "Allumer" }));
    expect(await screen.findByText(/En attente d'envoi : Allumer 1 écran/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Annuler l'action" }));
    await waitFor(() => expect(screen.queryByText(/En attente d'envoi/)).not.toBeInTheDocument());
    expect(calls.some((x) => x.method === "POST")).toBe(false);
  });

  it("supprimer en masse demande une confirmation puis envoie un DELETE par écran", async () => {
    const list = [mkScreen(1), mkScreen(2)];
    const calls = mockApi(base(list, { [`DELETE /screens/${list[0]!.id}`]: null, [`DELETE /screens/${list[1]!.id}`]: errorResponse(409, "errors.conflict") }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    await user.click(await screen.findByRole("checkbox", { name: /Tout sélectionner/ }));
    const bar = await screen.findByRole("region", { name: "Actions sur la sélection" });
    await user.click(within(bar).getByRole("button", { name: "Supprimer" }));
    expect(calls.some((x) => x.method === "DELETE")).toBe(false);
    await user.click(await screen.findByRole("button", { name: "Supprimer définitivement 2 écrans" }));
    expect(await screen.findByText(/1 échec/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Réessayer l'échec" })).toBeInTheDocument();
    expect(calls.filter((x) => x.method === "DELETE")).toHaveLength(2);
  });
});

describe.each(["", "/espace/affichage"])("liste des écrans : mise à jour de l'application, base « %s »", (b) => {
  const apkRow = (s: { id: string }, extra: Record<string, unknown>) => ({ screen_id: s.id, installed_version_code: 10, installed_version_name: "1.0.10", state: "outdated", updatable: true, reason: null, ...extra });
  const fleet = (screens: unknown[]) => ({ available: { version_code: 12, version_name: "1.0.12", source: "image" }, screens });
  const setup = (rows: Record<string, unknown>[], list: ReturnType<typeof mkScreen>[], extra: Record<string, unknown> = {}) =>
    mockApi(
      {
        "GET /sites": [SITE],
        "GET /screens": list,
        "GET /incidents": [],
        "GET /planning/at": atOut([]),
        "GET /fleet/apk": fleet(rows),
        [`GET /sites/${SITE.id}/groups`]: [],
        [`GET /sites/${SITE.id}/agents`]: [AGENT],
        ...Object.fromEntries(list.map((s) => [`GET /power/screens/${s.id}`, { screen_id: s.id, timezone: "Europe/Paris", plan_id: null, plan_id_origin: { level: "default", id: null, name: null }, calendars: [], calendars_origin: { level: "default", id: null, name: null }, slots: [] }])),
        ...extra,
      },
      b,
    );

  it("affiche la version installée et la pastille, demande confirmation, puis envoie apk.update sans délai", async () => {
    const [a, c] = [mkScreen(1), mkScreen(2)];
    const calls = setup([apkRow(a, {}), apkRow(c, { state: "up_to_date", installed_version_code: 12, installed_version_name: "1.0.12", updatable: false, reason: "up_to_date" })], [a, c], {
      [`POST /sites/${SITE.id}/agents/${AGENT.id}/commands`]: cmd("c1", "ok", { name: "apk.update" }),
    });
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    expect(await screen.findByText("1.0.10 (10)")).toBeInTheDocument();
    expect(screen.getAllByText("Mise à jour disponible")).toHaveLength(1);
    await user.click(await screen.findByRole("button", { name: "Mettre à jour le player de Écran 1" }));
    expect(calls.some((x) => x.method === "POST")).toBe(false);
    expect(screen.getByText(/30 à 90 secondes/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Mettre à jour 1 écran" }));
    expect(await screen.findByText("Terminé : Mettre à jour le player de 1 écran.")).toBeInTheDocument();
    const post = calls.find((x) => x.method === "POST");
    expect(post?.body).toMatchObject({ name: "apk.update", screen_id: a.id, args: { request_id: expect.any(String) } });
    // L'écran déjà à jour n'a pas de bouton.
    expect(screen.queryByRole("button", { name: "Mettre à jour le player de Écran 2" })).not.toBeInTheDocument();
  });

  it("la sélection exclut les écrans non éligibles en donnant la raison, et tout un site se met à jour depuis le panneau", async () => {
    const [a, c] = [mkScreen(1), mkScreen(2)];
    setup([apkRow(a, {}), apkRow(c, { updatable: false, reason: "no_adb_host" })], [a, c]);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens", b);
    await user.click(await screen.findByRole("checkbox", { name: /Tout sélectionner/ }));
    const bar = await screen.findByRole("region", { name: "Actions sur la sélection" });
    await user.click(within(bar).getByRole("button", { name: "Mettre à jour le player" }));
    const dialog = within(bar).getByRole("alertdialog");
    expect(within(dialog).getByText(/Écran 2 : adresse ADB de l'écran non renseignée/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Mettre à jour 1 écran" })).toBeInTheDocument();
    await user.click(within(bar).getByRole("button", { name: "Annuler" }));
    await user.click(within(bar).getByRole("button", { name: "Tout désélectionner" }));
    await user.click(screen.getByRole("button", { name: "Mettre à jour les écrans du site Moulins" }));
    expect(screen.getByText(/Écran 2 : adresse ADB/)).toBeInTheDocument();
  });

  it("sans application disponible : aide affichée et boutons désactivés", async () => {
    const a = mkScreen(1);
    mockApi(
      {
        "GET /sites": [SITE], "GET /screens": [a], "GET /incidents": [], "GET /planning/at": atOut([]),
        "GET /fleet/apk": { available: null, screens: [apkRow(a, { updatable: false, reason: "apk_unavailable" })] },
        [`GET /sites/${SITE.id}/groups`]: [], [`GET /sites/${SITE.id}/agents`]: [AGENT],
        [`GET /power/screens/${a.id}`]: { screen_id: a.id, timezone: "Europe/Paris", plan_id: null, plan_id_origin: { level: "default", id: null, name: null }, calendars: [], calendars_origin: { level: "default", id: null, name: null }, slots: [] },
      },
      b,
    );
    await renderRoutes(routes, "/screens", b);
    expect(await screen.findByText(/VDS_APK_DIR/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Mettre à jour le player de Écran 1" })).toBeDisabled();
  });
});
