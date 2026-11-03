/**
 * Simule /api/v1/layouts pour le dev et les e2e (le vrai serveur est dans apps/server) : liste, création,
 * remplacement conditionnel (If-Match), duplication, suppression, usage. Aucune logique propre à un mode.
 */
import { randomUUID } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";

type Json = Record<string, unknown>;
interface Zone extends Json {
  key: string;
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
  z: number;
}
interface LayoutRow {
  id: string;
  name: string;
  orientation: "landscape" | "portrait";
  background_color: string | null;
  background_media_id: string | null;
  tags: string[];
  zones: Zone[];
  revision: number;
  created_at: string;
  updated_at: string;
}
interface World {
  screens: { id: string; site_id: string; group_id?: string | null; orientation?: string }[];
  schedules: Json[];
}

const readJson = (req: IncomingMessage): Promise<Json> =>
  new Promise((resolve) => {
    let raw = "";
    req.on("data", (c: Buffer) => (raw += c.toString("utf8")));
    req.on("end", () => {
      try {
        resolve(JSON.parse(raw || "{}") as Json);
      } catch {
        resolve({});
      }
    });
  });

const KEY_RE = /^[a-z0-9_-]{1,32}$/;
const MAX_ZONES = 12;

export function createLayoutsMock(world: World) {
  const rows: LayoutRow[] = [];
  const now = () => new Date().toISOString();
  const usage = (l: LayoutRow) => world.schedules.filter((s) => s.layout_id === l.id);
  const full = (l: LayoutRow) => ({ ...l, warnings: [] });
  const summary = (l: LayoutRow) => ({ id: l.id, name: l.name, orientation: l.orientation, revision: l.revision, tags: l.tags, updated_at: l.updated_at, zone_count: l.zones.length, usage_count: usage(l).length, zones: l.zones });

  /** Zones rencontrées dans les mises en page (sélecteur de forçage de zone). */
  const zoneCandidates = (): { key: string; name: string; layouts: number }[] => {
    const byKey = new Map<string, { key: string; name: string; layouts: number }>();
    for (const l of rows) for (const z of l.zones) byKey.set(z.key, { key: z.key, name: z.name, layouts: (byKey.get(z.key)?.layouts ?? 0) + 1 });
    return [...byKey.values()];
  };

  function validate(body: Json): [number, string] | null {
    const name = typeof body.name === "string" ? body.name.trim() : "";
    const zones = Array.isArray(body.zones) ? (body.zones as Zone[]) : [];
    if (!name || (body.orientation !== "landscape" && body.orientation !== "portrait")) return [422, "errors.validation"];
    if (zones.length < 1 || zones.length > MAX_ZONES) return [422, "layout.too_many_zones"];
    const keys = new Set<string>();
    for (const z of zones) {
      if (!KEY_RE.test(String(z.key)) || !String(z.name ?? "").trim()) return [422, "errors.validation"];
      if (keys.has(z.key)) return [422, "layout.zone_key_duplicate"];
      keys.add(z.key);
      if (!(z.w > 0 && z.h > 0 && z.x >= 0 && z.y >= 0 && z.x + z.w <= 1.00001 && z.y + z.h <= 1.00001)) return [422, "layout.zone_out_of_bounds"];
    }
    return null;
  }

  const handle = async function handle(req: IncomingMessage, res: ServerResponse, route: string, query: URLSearchParams): Promise<boolean> {
    const method = req.method ?? "GET";
    const send = (status: number, payload: unknown) => {
      res.statusCode = status;
      res.setHeader("Content-Type", "application/json");
      res.setHeader("Cache-Control", "private, no-store");
      res.end(JSON.stringify(payload));
    };
    const fail = (status: number, code: string) => (send(status, { code, params: {} }), true);
    if (route === "/layouts" && method === "GET") {
      const q = (query.get("q") ?? "").toLowerCase();
      const o = query.get("orientation");
      const items = rows.filter((l) => (!q || l.name.toLowerCase().includes(q)) && (!o || l.orientation === o)).map(summary);
      return send(200, { items, total: items.length }), true;
    }
    if (route === "/layouts" && method === "POST") {
      const body = await readJson(req);
      const bad = validate(body);
      if (bad) return fail(...bad);
      if (rows.some((l) => l.name === String(body.name).trim())) return fail(409, "layout.name_conflict");
      const row: LayoutRow = {
        id: randomUUID(), name: String(body.name).trim(), orientation: body.orientation as LayoutRow["orientation"], background_color: (body.background_color as string | null) ?? null,
        background_media_id: null, tags: (body.tags as string[] | undefined) ?? [], zones: body.zones as Zone[], revision: 1, created_at: now(), updated_at: now(),
      };
      rows.push(row);
      return send(201, full(row)), true;
    }
    const m = /^\/layouts\/([^/]+)(?:\/(usage|duplicate|preview))?$/.exec(route);
    if (!m) return false;
    const row = rows.find((l) => l.id === m[1]);
    if (!row) return fail(404, "layout.not_found");
    if (!m[2]) {
      if (method === "GET") return send(200, full(row)), true;
      if (method === "PUT") {
        const header = req.headers["if-match"];
        if (header === undefined) return fail(428, "layout.if_match_required");
        if (!/^\d+$/.test(String(header))) return fail(422, "layout.if_match_invalid");
        if (Number(header) !== row.revision) return fail(412, "layout.revision_conflict");
        const body = await readJson(req);
        const bad = validate(body);
        if (bad) return fail(...bad);
        if (rows.some((l) => l !== row && l.name === String(body.name).trim())) return fail(409, "layout.name_conflict");
        Object.assign(row, { name: String(body.name).trim(), orientation: body.orientation, background_color: (body.background_color as string | null) ?? null, tags: (body.tags as string[] | undefined) ?? [], zones: body.zones as Zone[], revision: row.revision + 1, updated_at: now() });
        return send(200, full(row)), true;
      }
      if (method === "DELETE") {
        if (usage(row).length > 0) return fail(409, "layout.in_use");
        rows.splice(rows.indexOf(row), 1);
        res.statusCode = 204;
        return res.end(), true;
      }
    }
    if (m[2] === "usage" && method === "GET") {
      const mismatch = world.screens.filter((s) => (s.orientation ?? "landscape") !== row.orientation).length;
      return send(200, { schedules: usage(row).map((s) => ({ id: s.id, name: s.name, playlist_id: null, layout_id: row.id, target_type: s.target_type, target_id: s.target_id, target_name: "", active: true })), overrides: [], zone_overrides: [], screens_total: world.screens.length, screens_orientation_mismatch: usage(row).length ? mismatch : 0 }), true;
    }
    if (m[2] === "duplicate" && method === "POST") {
      const copy: LayoutRow = { ...row, id: randomUUID(), name: `${row.name} (copie)`, revision: 1, created_at: now(), updated_at: now(), zones: row.zones.map((z) => ({ ...z })) };
      rows.push(copy);
      return send(201, full(copy)), true;
    }
    return false;
  };
  return Object.assign(handle, { zoneCandidates, layouts: rows });
}
