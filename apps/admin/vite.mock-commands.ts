/**
 * Simule les commandes player et les captures (lot I) pour les e2e : cycle queued -> sent -> ok piloté par l'horloge,
 * capture JPEG simulée (SVG, servie no-store), droits (restart réservé à l'admin). Aucune logique propre à un mode.
 */
import { randomUUID } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";
import { whoOf } from "./vite.mock-users";

type Shot = { id: string; screen_id: string; trigger: "on_demand" | "periodic"; captured_at: string; width: number; height: number; bytes: number; image_url: string };
interface World {
  screens: { id: string; name: string }[];
  shots: Map<string, Shot[]>;
  devices: Map<string, { platform: string; revoked_at: string | null }>;
}
interface Cmd {
  id: string;
  screen_id: string;
  name: string;
  args: Record<string, unknown>;
  key: string;
  created: number;
  cancelled: boolean;
  by: string;
}

const QUEUED_MS = 1_200;
const SENT_MS = 2_400;
const caps = (platform: string) => (platform === "android" ? ["cmd.reload", "cmd.identify", "cmd.screenshot", "cmd.restart.app", "cmd.restart.device", "cmd.cache.resync"] : ["cmd.reload", "cmd.identify"]);

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

export function createCommandsMock(world: World) {
  const cmds: Cmd[] = [];
  const statusOf = (c: Cmd) => {
    if (c.cancelled) return "cancelled";
    const age = Date.now() - c.created;
    return age < QUEUED_MS ? "queued" : age < SENT_MS ? "sent" : "ok";
  };
  const out = (c: Cmd) => {
    const status = statusOf(c);
    if (status === "ok" && c.name === "screenshot" && !shotOf(c)) addShot(c);
    return {
      id: c.id,
      screen_id: c.screen_id,
      name: c.name,
      args: c.args,
      status,
      code: null,
      requested_by: c.by,
      created_at: new Date(c.created).toISOString(),
      expires_at: new Date(c.created + 600_000).toISOString(),
      done_at: status === "ok" || status === "cancelled" ? new Date(c.created + SENT_MS).toISOString() : null,
      screenshot_id: shotOf(c)?.id ?? null,
    };
  };
  const shotIds = new Map<string, Shot>();
  const shotOf = (c: Cmd) => shotIds.get(c.id);
  const addShot = (c: Cmd) => {
    const id = randomUUID();
    const shot: Shot = { id, screen_id: c.screen_id, trigger: "on_demand", captured_at: new Date().toISOString(), width: 640, height: 360, bytes: 4200, image_url: `/api/v1/screenshots/${id}/image` };
    shotIds.set(c.id, shot);
    world.shots.set(c.screen_id, [shot, ...(world.shots.get(c.screen_id) ?? [])].slice(0, 10));
  };
  return async function handle(req: IncomingMessage, res: ServerResponse, route: string, query: URLSearchParams): Promise<boolean> {
    const method = req.method ?? "GET";
    const send = (status: number, payload: unknown) => {
      res.statusCode = status;
      res.setHeader("Content-Type", "application/json");
      res.setHeader("Cache-Control", "private, no-store");
      res.end(JSON.stringify(payload));
      return true;
    };
    const fail = (status: number, code: string) => send(status, { code, params: {} });
    const who = whoOf(req);
    const img = /^\/screenshots\/([^/]+)\/image$/.exec(route);
    if (img && method === "GET") {
      res.statusCode = 200;
      res.setHeader("Content-Type", "image/svg+xml");
      res.setHeader("Cache-Control", "private, no-store");
      res.setHeader("X-Content-Type-Options", "nosniff");
      res.end('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#ccc"/></svg>');
      return true;
    }
    if (route === "/player-commands" && method === "GET") {
      const ids = (query.get("ids") ?? "").split(",").filter(Boolean);
      return send(200, cmds.filter((c) => ids.includes(c.id)).map(out));
    }
    const m = /^\/screens\/([^/]+)\/(commands|screenshots)(?:\/([^/]+)\/cancel)?$/.exec(route);
    if (!m) return false;
    const screenId = m[1]!;
    const screen = world.screens.find((s) => s.id === screenId);
    if (!screen) return fail(404, "errors.not_found");
    if (m[2] === "screenshots" && method === "GET") return send(200, world.shots.get(screenId) ?? []);
    if (m[2] === "commands" && m[3] && method === "POST") {
      const c = cmds.find((x) => x.id === m[3] && x.screen_id === screenId);
      if (!c) return fail(404, "errors.cmd.not_found");
      if (statusOf(c) !== "queued") return fail(409, "cmd.not_cancellable");
      c.cancelled = true;
      return send(200, out(c));
    }
    if (m[2] === "commands" && method === "GET") return send(200, cmds.filter((c) => c.screen_id === screenId).map(out).reverse());
    if (m[2] === "commands" && method === "POST") {
      const b = await readJson(req);
      const name = String(b.name);
      const args = (b.args ?? {}) as Record<string, unknown>;
      const key = String(b.request_key ?? "");
      const prior = cmds.find((c) => c.key === key);
      if (prior) return send(200, out(prior));
      const device = world.devices.get(screenId);
      if (!device || device.revoked_at) return fail(409, "cmd.no_device");
      if ((name === "restart" || name === "cache.resync") && who !== "admin") return fail(403, "cmd.role_required");
      const cap = name === "restart" ? `cmd.restart.${String(args.scope)}` : `cmd.${name}`;
      if (!caps(device.platform).includes(cap)) return fail(409, "cmd.unsupported");
      const c: Cmd = { id: randomUUID(), screen_id: screenId, name, args, key, created: Date.now(), cancelled: false, by: who === "admin" ? "Ada" : "Marie" };
      cmds.push(c);
      return send(201, out(c));
    }
    return false;
  };
}
