/**
 * Simule /me (périmètre), /scope-tree et /users pour le dev et les e2e (le vrai serveur est dans apps/server).
 * L'identité se lit dans le cookie de session de test : `e2e-admin` (défaut), `e2e-editor` (éditeur limité au
 * premier site) ou `e2e-editor-empty` (éditeur sans périmètre). Aucune logique propre à un mode.
 */
import type { IncomingMessage, ServerResponse } from "node:http";

type Json = Record<string, unknown>;
interface World {
  sites: { id: string; name: string }[];
  groups: { id: string; site_id: string; name: string }[];
}
type Node = { type: "organization" | "site" | "group"; id: string };
interface UserRow {
  id: string;
  display_name: string;
  email: string;
  role: "admin" | "editor";
  role_source: "vds" | "provider";
  is_active: boolean;
  last_seen_at: string | null;
  scopes: Node[];
  version: number;
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

export const ORG_ID = "00000000-0000-4000-8000-0000000000a0";
export type Who = "admin" | "editor" | "empty";

export function whoOf(req: IncomingMessage): Who {
  const m = /(?:^|;\s*)vds_session=([^;]+)/.exec(req.headers.cookie ?? "");
  return m?.[1] === "e2e-editor" ? "editor" : m?.[1] === "e2e-editor-empty" ? "empty" : "admin";
}

export function createUsersMock(world: World) {
  const rows: UserRow[] = [
    { id: "u-1", display_name: "Ada", email: "ada@vds.test", role: "admin", role_source: "vds", is_active: true, last_seen_at: "2026-11-02T08:00:00Z", scopes: [{ type: "organization", id: ORG_ID }], version: 1 },
    { id: "u-2", display_name: "Marie Dupont", email: "marie@vds.test", role: "editor", role_source: "vds", is_active: true, last_seen_at: null, scopes: [], version: 1 },
    { id: "u-3", display_name: "Paul Martin", email: "paul@vds.test", role: "editor", role_source: "provider", is_active: true, last_seen_at: null, scopes: [], version: 1 },
  ];
  const nameOf = (n: Node): string =>
    n.type === "organization" ? "Collectivité" : n.type === "site" ? (world.sites.find((s) => s.id === n.id)?.name ?? "") : (world.groups.find((g) => g.id === n.id)?.name ?? "");
  const out = (u: UserRow) => ({
    id: u.id,
    display_name: u.display_name,
    email: u.email,
    role: u.role,
    role_source: u.role_source,
    is_active: u.is_active,
    last_seen_at: u.last_seen_at,
    scopes: u.scopes.map((n) => ({ ...n, name: nameOf(n) })),
    scopes_version: `v${u.version}`,
  });

  const perimeter = (who: Who) => {
    if (who === "admin") return { unrestricted: true, can_share: true, nodes: [{ type: "organization", id: ORG_ID, name: "Collectivité" }] };
    const first = world.sites[0];
    return { unrestricted: false, can_share: false, nodes: who === "empty" || !first ? [] : [{ type: "site", id: first.id, name: first.name }] };
  };

  /** Corps de GET /me (champs ajoutés par le lot J). */
  const me = (who: Who) => ({ role: who === "admin" ? "admin" : "editor", role_source: "vds", perimeter: perimeter(who) });

  const tree = (who: Who) => {
    const visible = who === "admin" ? world.sites : who === "empty" ? [] : world.sites.slice(0, 1);
    return {
      can_share: who === "admin",
      sites: visible.map((s) => ({ id: s.id, name: s.name, writable: true, groups: world.groups.filter((g) => g.site_id === s.id).map((g) => ({ id: g.id, name: g.name, writable: true })) })),
    };
  };

  async function handle(req: IncomingMessage, res: ServerResponse, route: string, query: URLSearchParams): Promise<boolean> {
    const method = req.method ?? "GET";
    const send = (status: number, body: unknown) => {
      res.statusCode = status;
      res.setHeader("Content-Type", "application/json");
      res.setHeader("Cache-Control", "private, no-store");
      res.end(JSON.stringify(body));
      return true;
    };
    const fail = (status: number, code: string) => send(status, { code, params: {} });
    const who = whoOf(req);
    if (route === "/scope-tree" && method === "GET") return send(200, tree(who));
    if (!route.startsWith("/users")) return false;
    if (who !== "admin") return fail(403, "auth.forbidden");
    if (route === "/users" && method === "GET") {
      const q = (query.get("q") ?? "").toLowerCase();
      const role = query.get("role");
      const site = query.get("site_id");
      const list = rows.filter(
        (u) =>
          (!q || `${u.display_name} ${u.email}`.toLowerCase().includes(q)) &&
          (!role || u.role === role) &&
          (!site || u.scopes.some((n) => n.type === "organization" || n.id === site || world.groups.some((g) => g.id === n.id && g.site_id === site))),
      );
      const limit = Number(query.get("limit") ?? 50);
      const offset = Number(query.get("offset") ?? 0);
      return send(200, { items: list.slice(offset, offset + limit).map(out), total: list.length });
    }
    const one = /^\/users\/([^/]+)(\/scopes)?$/.exec(route);
    const row = one ? rows.find((u) => u.id === one[1]) : undefined;
    if (!one || !row) return fail(404, "errors.not_found");
    if (!one[2] && method === "GET") return send(200, out(row));
    if (!one[2] && method === "PATCH") {
      const body = await readJson(req);
      if (body.role !== undefined && row.role_source === "provider") return fail(409, "user.role_managed_by_provider");
      if (body.role !== undefined && row.id === "u-1") return fail(422, "user.self_demotion");
      if (body.role === "admin" || body.role === "editor") row.role = body.role;
      if (typeof body.is_active === "boolean") row.is_active = body.is_active;
      return send(200, out(row));
    }
    if (one[2] && method === "PUT") {
      if (req.headers["if-match"] !== `v${row.version}`) return fail(412, "errors.precondition_failed");
      const body = await readJson(req);
      const nodes = (Array.isArray(body.scopes) ? body.scopes : []) as Node[];
      if (nodes.length > 100) return fail(422, "errors.validation");
      row.scopes = nodes.map((n) => ({ type: n.type, id: n.id }));
      row.version += 1;
      return send(200, out(row));
    }
    return fail(404, "errors.not_found");
  }
  return Object.assign(handle, { me, tree });
}
