/**
 * Simule /publications (validation de publication) pour le dev et les e2e ; le vrai service est dans apps/server.
 * Même règle que le serveur : un admin qui enregistre publie, un éditeur laisse l'objet en brouillon.
 * L'identité vient du cookie de test (voir vite.mock-users.ts). Les lignes sont créées à la demande (« publié »)
 * ou à la création d'un objet (observée sur la réponse), sans toucher aux autres simulations.
 */
import type { IncomingMessage, ServerResponse } from "node:http";
import { whoOf } from "./vite.mock-users";

type Json = Record<string, unknown>;
type EType = "playlist" | "layout" | "schedule";
interface Row {
  entity_type: EType;
  entity_id: string;
  state: "draft" | "in_review" | "published";
  working_rev: number;
  published_rev: number | null;
  submitted_by: { id: string; display_name: string } | null;
  submitted_at: string | null;
  published_by: { id: string; display_name: string } | null;
  published_at: string | null;
  review_comment: string | null;
  name: string | null;
  updated: number;
}
interface World {
  playlists: { id: string; name?: unknown }[];
  schedules: { id: string; name?: unknown; playlist_id?: unknown; layout_id?: unknown }[];
}

const PLURAL: Record<string, EType> = { playlists: "playlist", layouts: "layout", schedules: "schedule" };
const ME = { admin: { id: "u-1", display_name: "Ada" }, editor: { id: "u-2", display_name: "Marie Dupont" }, empty: { id: "u-3", display_name: "Paul Martin" } } as const;

function readBody(req: IncomingMessage): Promise<Json> {
  return new Promise((resolve) => {
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
}

export function createPublicationsMock(world: World, layouts: { id: string; name: string }[]) {
  const rows = new Map<string, Row>();
  const key = (t: EType, id: string) => `${t}:${id}`;
  let tick = 0;
  const fresh = (t: EType, id: string, name: string | null, published: boolean): Row => ({
    entity_type: t,
    entity_id: id,
    state: published ? "published" : "draft",
    working_rev: 1,
    published_rev: published ? 1 : null,
    submitted_by: null,
    submitted_at: null,
    published_by: null,
    published_at: null,
    review_comment: null,
    name,
    updated: ++tick,
  });
  const known = (t: EType): { id: string; name: string | null }[] =>
    t === "playlist" ? world.playlists.map((p) => ({ id: p.id, name: String(p.name ?? "") })) : t === "schedule" ? world.schedules.map((s) => ({ id: s.id, name: String(s.name ?? "") })) : layouts.map((l) => ({ id: l.id, name: l.name }));
  const row = (t: EType, id: string): Row | null => {
    const found = rows.get(key(t, id));
    if (found) return found;
    const k = known(t).find((x) => x.id === id);
    if (!k) return null;
    const r = fresh(t, id, k.name, true);
    rows.set(key(t, id), r);
    return r;
  };
  const sync = () => {
    for (const t of ["playlist", "layout", "schedule"] as const) for (const k of known(t)) row(t, k.id);
    for (const r of rows.values()) {
      const k = known(r.entity_type).find((x) => x.id === r.entity_id);
      if (k && k.name) r.name = k.name;
    }
  };
  const out = (r: Row) => {
    const deps: { entity_type: EType; entity_id: string }[] = [];
    if (r.entity_type === "schedule") {
      const s = world.schedules.find((x) => x.id === r.entity_id);
      if (s && typeof s.playlist_id === "string") deps.push({ entity_type: "playlist", entity_id: s.playlist_id });
      if (s && typeof s.layout_id === "string") deps.push({ entity_type: "layout", entity_id: s.layout_id });
    }
    const blocked_by = deps.filter((d) => {
      const dr = row(d.entity_type, d.entity_id);
      return dr !== null && dr.published_rev === null;
    });
    return { ...r, updated: undefined, blocked_by };
  };
  const publish = (r: Row, who: keyof typeof ME) => {
    r.state = "published";
    r.published_rev = r.working_rev;
    r.published_by = ME[who];
    r.published_at = new Date().toISOString();
    r.review_comment = null;
    r.updated = ++tick;
  };

  /** Observe l'écriture d'un objet par les autres simulations : création, modification, suppression. */
  function observe(req: IncomingMessage, res: ServerResponse, route: string): void {
    const method = req.method ?? "GET";
    if (method === "GET") return;
    const m = /^\/(playlists|layouts|schedules)(?:\/([^/]+))?/.exec(route);
    if (!m) return;
    const t = PLURAL[m[1]!]!;
    const who = whoOf(req);
    const id = m[2];
    if (method === "DELETE" && id) {
      rows.delete(key(t, id));
      return;
    }
    const end = res.end.bind(res) as (...a: unknown[]) => ServerResponse;
    (res as { end: unknown }).end = (...a: unknown[]) => {
      if (res.statusCode >= 200 && res.statusCode < 300) {
        let body: Json = {};
        try {
          body = JSON.parse(String(a[0] ?? "{}")) as Json;
        } catch {
          body = {};
        }
        const eid = typeof body.id === "string" ? body.id : id;
        if (eid) {
          const isCreate = method === "POST" && !id;
          if (isCreate && !rows.has(key(t, eid))) rows.set(key(t, eid), fresh(t, eid, null, false));
          const r = row(t, eid) ?? (rows.set(key(t, eid), fresh(t, eid, null, false)), rows.get(key(t, eid))!);
          if (typeof body.name === "string") r.name = body.name;
          if (!isCreate || who === "admin") r.working_rev += isCreate ? 0 : 1;
          if (who === "admin") publish(r, who);
          else {
            r.state = "draft";
            r.updated = ++tick;
          }
        }
      }
      return end(...a);
    };
  }

  async function handle(req: IncomingMessage, res: ServerResponse, route: string, query: URLSearchParams): Promise<boolean> {
    const method = req.method ?? "GET";
    const send = (status: number, payload: unknown) => {
      res.statusCode = status;
      res.setHeader("Content-Type", "application/json");
      res.setHeader("Cache-Control", "private, no-store");
      res.end(JSON.stringify(payload));
      return true;
    };
    const fail = (status: number, code: string, params: Json = {}) => send(status, { code, params });
    const who = whoOf(req);
    if (who === "empty" && route.startsWith("/publications")) return fail(403, "scope.forbidden");
    if (route === "/publications" && method === "GET") {
      sync();
      const state = query.get("state");
      const type = query.get("entity_type");
      const limit = Number(query.get("limit") ?? 200);
      const list = [...rows.values()].filter((r) => (!state || r.state === state) && (!type || r.entity_type === type)).sort((a, b) => b.updated - a.updated);
      return send(200, list.slice(0, limit).map(out));
    }
    const m = /^\/publications\/(playlist|layout|schedule)\/([^/]+)(?:\/(submit|withdraw|approve|reject))?$/.exec(route);
    if (!m) {
      observe(req, res, route);
      return false;
    }
    const t = m[1] as EType;
    const r = row(t, m[2]!);
    if (!r) return fail(404, "errors.not_found");
    const action = m[3];
    if (!action && method === "GET") return send(200, out(r));
    if (method !== "POST" || !action) return fail(405, "errors.not_found");
    const body = await readBody(req);
    if (action === "submit") {
      if (r.state !== "draft") return fail(409, "publication.invalid_transition");
      r.state = "in_review";
      r.submitted_by = ME[who];
      r.submitted_at = new Date().toISOString();
      r.review_comment = null;
    } else if (action === "withdraw") {
      if (r.state !== "in_review") return fail(409, "publication.invalid_transition");
      r.state = "draft";
    } else {
      if (who !== "admin") return fail(403, "auth.forbidden");
      if (r.state !== "in_review") return fail(409, "publication.invalid_transition");
      if (action === "reject") {
        const c = typeof body.comment === "string" ? body.comment.trim() : "";
        if (c === "" || c.length > 500) return fail(422, "errors.validation");
        r.state = "draft";
        r.review_comment = c;
      } else {
        if (body.expected_rev !== r.working_rev) return fail(409, "publication.stale", { working_rev: r.working_rev });
        const blocked = out(r).blocked_by;
        if (blocked.length > 0 && body.include_dependencies !== true) return fail(409, "publication.dependency_unpublished", { deps: blocked });
        for (const d of blocked) {
          const dr = row(d.entity_type, d.entity_id);
          if (dr) publish(dr, who);
        }
        publish(r, who);
      }
    }
    r.updated = ++tick;
    return send(200, out(r));
  }
  return handle;
}
