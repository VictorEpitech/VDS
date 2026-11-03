/**
 * Simule les routes de flotte du serveur pour les e2e : onboarding, synthèse, programmation active,
 * canal d'agent local, inscriptions d'agent et suppression d'écran. Aucune logique propre à un mode.
 */
import { randomUUID } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";

interface World {
  sites: { id: string }[];
  screens: { id: string; site_id: string; name: string; group_id?: string | null }[];
}

interface OverrideRow {
  id: string;
  name: string;
  playlist_id: string | null;
  layout_id?: string | null;
  zone_ids?: string[] | null;
  target_type: string;
  target_id: string;
  starts_at: string;
  ends_at: string;
  ended_at: string | null;
}

const readJson = (req: IncomingMessage): Promise<Record<string, unknown>> =>
  new Promise((resolve) => {
    let raw = "";
    req.on("data", (c: Buffer) => (raw += c.toString("utf8")));
    req.on("end", () => {
      try {
        resolve(JSON.parse(raw || "{}") as Record<string, unknown>);
      } catch {
        resolve({});
      }
    });
  });

export function createFleetMock(world: World, zoneCandidates: () => { key: string; name: string; layouts: number }[] = () => []) {
  const now = () => new Date().toISOString();
  // Forçages simulés (mêmes formes que /api/v1/overrides) : en mémoire, le temps réel fait foi.
  const overrides: OverrideRow[] = [];
  const active = (o: OverrideRow) => !o.ended_at && new Date(o.ends_at).getTime() > Date.now();
  const touched = (o: Pick<OverrideRow, "target_type" | "target_id">) =>
    world.screens.filter((s) => (o.target_type === "screen" ? s.id === o.target_id : o.target_type === "group" ? s.group_id === o.target_id : o.target_type === "site" ? s.site_id === o.target_id : true));
  const targetName = (o: Pick<OverrideRow, "target_type" | "target_id">) => touched(o)[0]?.name ?? o.target_id;
  const out = (o: OverrideRow) => ({
    id: o.id, name: o.name, kind: "playlist", playlist_id: o.playlist_id, layout_id: o.layout_id ?? null, zone_ids: o.zone_ids ?? null, warnings: [] as string[], screens_missing: 0, ephemeral: false, target_type: o.target_type, target_id: o.target_id,
    target_name: o.target_type === "screen" ? targetName(o) : o.target_type === "group" ? "Accueil" : "Site", starts_at: o.starts_at, ends_at: o.ends_at, ended_at: o.ended_at,
    end_reason: o.ended_at ? "stopped" : null, status: o.ended_at || new Date(o.ends_at).getTime() <= Date.now() ? "ended" : new Date(o.starts_at).getTime() > Date.now() ? "scheduled" : "active",
    priority: 0, respect_scheduled_off: false, note: null, created_by: null, created_by_module: null, created_at: o.starts_at,
  });
  const endOf = (b: Record<string, unknown>): string | null => {
    if (typeof b.ends_at === "string") return new Date(b.ends_at).toISOString();
    if (typeof b.duration_minutes === "number") return new Date(Date.now() + b.duration_minutes * 60_000).toISOString();
    if (b.until === "end_of_day") return new Date(new Date().setHours(24, 0, 0, 0)).toISOString();
    return null;
  };
  const handle = async function handle(req: IncomingMessage, res: ServerResponse, route: string): Promise<boolean> {
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
    if (route === "/overrides" && method === "GET") return send(200, overrides.filter(active).map(out)), true;
    if (route === "/overrides/preview" && method === "POST") {
      const b = await readJson(req);
      const ends = endOf(b);
      if (!ends) return send(422, { code: "override.end_required", params: {} }), true;
      if (new Date(ends).getTime() <= Date.now()) return send(422, { code: "override.end_in_past", params: {} }), true;
      const list = touched({ target_type: String(b.target_type), target_id: String(b.target_id) });
      return send(200, { screens_total: list.length, screens_offline: 0, screens_in_scheduled_off: 0, ends_at: ends, zone_candidates: zoneCandidates(), zone_coverage: Array.isArray(b.zone_ids) ? (b.zone_ids as string[]).map((z) => ({ zone_id: z, screens_total: list.length, screens_missing: zoneCandidates().some((c) => c.key === z) ? 0 : list.length })) : [], superseded_override_ids: overrides.filter((o) => active(o) && touched(o).some((s) => list.includes(s))).map((o) => o.id) }), true;
    }
    if (route === "/overrides" && method === "POST") {
      const b = await readJson(req);
      const ends = endOf(b);
      if (!ends) return send(422, { code: "override.end_required", params: {} }), true;
      const row: OverrideRow = { id: randomUUID(), name: typeof b.name === "string" && b.name ? b.name : "Forçage", playlist_id: typeof b.playlist_id === "string" ? b.playlist_id : null, layout_id: typeof b.layout_id === "string" ? b.layout_id : null, zone_ids: Array.isArray(b.zone_ids) ? (b.zone_ids as string[]) : null, target_type: String(b.target_type), target_id: String(b.target_id), starts_at: now(), ends_at: ends, ended_at: null };
      overrides.push(row);
      const missing = row.zone_ids?.some((z) => !zoneCandidates().some((c) => c.key === z)) ? touched(row).length : 0;
      return send(201, { ...out(row), warnings: missing ? ["override_zone_missing"] : [], screens_missing: missing }), true;
    }
    const ov = /^\/overrides\/([^/]+)(\/stop)?$/.exec(route);
    if (ov) {
      const row = overrides.find((o) => o.id === ov[1]);
      if (!row) return send(404, { code: "override.target_not_found", params: {} }), true;
      if (ov[2] && method === "POST") {
        row.ended_at ??= now();
        return send(200, out(row)), true;
      }
      if (!ov[2] && method === "PATCH") {
        const b = await readJson(req);
        if (typeof b.ends_at === "string") row.ends_at = new Date(b.ends_at).toISOString();
        return send(200, out(row)), true;
      }
    }
    if (route === "/fleet/playback" && method === "GET") {
      const until = new Date(Date.now() + 2 * 3_600_000).toISOString();
      return send(
        200,
        world.screens.map((s, i) => {
          const forced = overrides.filter(active).filter((o) => touched(o).includes(s)).at(-1);
          if (forced) return { screen_id: s.id, source: "override", override_id: forced.id, override_name: forced.name, schedule_id: null, schedule_name: null, playlist_id: forced.playlist_id, playlist_name: forced.name, until: forced.ends_at, next_change_at: forced.ends_at };
          return i === 0
            ? { screen_id: s.id, source: "schedule", schedule_id: "00000000-0000-4000-8000-000000000001", schedule_name: "Semaine", playlist_id: "00000000-0000-4000-8000-000000000002", playlist_name: "Accueil", until, next_change_at: until }
            : { screen_id: s.id, source: "none", schedule_id: null, schedule_name: null, playlist_id: null, playlist_name: null, until: null, next_change_at: null };
        }),
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
  /** Forçage actif sur un écran (le plus récent), pour que `/planning/at` du mock de planning en tienne compte. */
  const forcedOn = (screenId: string): OverrideRow | undefined => overrides.filter(active).filter((o) => touched(o).some((sc) => sc.id === screenId)).at(-1);
  return Object.assign(handle, { forcedOn });
}
