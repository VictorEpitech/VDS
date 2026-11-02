/**
 * Simule les routes de flotte du serveur pour les e2e : onboarding, synthèse, programmation active,
 * canal d'agent local, inscriptions d'agent et suppression d'écran. Aucune logique propre à un mode.
 */
import type { IncomingMessage, ServerResponse } from "node:http";

interface World {
  sites: { id: string }[];
  screens: { id: string; site_id: string; name: string }[];
}

export function createFleetMock(world: World) {
  const now = () => new Date().toISOString();
  return async function handle(req: IncomingMessage, res: ServerResponse, route: string): Promise<boolean> {
    const method = req.method ?? "GET";
    const send = (status: number, payload: unknown) => {
      res.statusCode = status;
      res.setHeader("Content-Type", "application/json");
      res.setHeader("Cache-Control", "private, no-store");
      res.end(JSON.stringify(payload));
    };
    if (route === "/onboarding" && method === "GET") {
      return send(200, { card: { hidden: true, hidden_at: now() }, complete: true, next_step: null, progress: { completed: 7, total: 7 }, steps: [] }), true;
    }
    if (route === "/fleet/summary" && method === "GET") {
      return send(200, {
        agents: { offline: 0, online: 1, total: 1 },
        generated_at: now(),
        incidents: { by_kind: {}, by_severity: {}, open: 0, unacknowledged: 0 },
        screens: { by_status: { online: world.screens.length }, total: world.screens.length },
        site_id: null,
        to_handle: { by_reason: {}, screens: 0 },
      }), true;
    }
    if (route === "/fleet/playback" && method === "GET") {
      const until = new Date(Date.now() + 2 * 3_600_000).toISOString();
      return send(
        200,
        world.screens.map((s, i) =>
          i === 0
            ? { screen_id: s.id, source: "schedule", schedule_id: "00000000-0000-4000-8000-000000000001", schedule_name: "Semaine", playlist_id: "00000000-0000-4000-8000-000000000002", playlist_name: "Accueil", until, next_change_at: until }
            : { screen_id: s.id, source: "none", schedule_id: null, schedule_name: null, playlist_id: null, playlist_name: null, until: null, next_change_at: null },
        ),
      ), true;
    }
    if (route === "/fleet/apk" && method === "GET") {
      return send(200, {
        available: { version_code: 12, version_name: "1.0.12", source: "image" },
        screens: world.screens.map((s, i) => ({
          screen_id: s.id,
          installed_version_code: i < 2 ? 10 : null,
          installed_version_name: i < 2 ? "1.0.10" : null,
          state: i < 2 ? "outdated" : "not_android",
          updatable: i < 2,
          reason: i < 2 ? null : "not_android",
        })),
      }), true;
    }
    if (route === "/agent/local" && method === "GET") return send(200, { available: false, detected: null, site_id: null }), true;
    if (/^\/sites\/[^/]+\/agent\/enrollments$/.test(route) && method === "GET") return send(200, []), true;
    const del = /^\/screens\/([^/]+)$/.exec(route);
    if (del && method === "DELETE") {
      const idx = world.screens.findIndex((s) => s.id === del[1]);
      if (idx < 0) return send(404, { code: "errors.screen_not_found", params: {} }), true;
      world.screens.splice(idx, 1);
      res.statusCode = 204;
      return res.end(), true;
    }
    return false;
  };
}
