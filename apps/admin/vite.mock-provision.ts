/**
 * Assistant « Ajouter une TV Android » simulé (dev et e2e) : mêmes chemins et formes que
 * apps/server/src/vds_server/api/v1/provisioning.py. Le scénario dépend de l'adresse saisie :
 * 192.168.1.50 réussit (invite RSA puis « En ligne »), .51 échoue en tls_required,
 * .52 finit en kiosque doux (comptes présents), toute autre adresse privée réussit comme .50.
 */
import { randomUUID } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";

type Json = Record<string, unknown>;
const ORDER = ["connect", "authorize", "inspect", "download", "verify", "install", "device_owner", "soft_kiosk", "configure", "inject", "launch"] as const;
type Step = (typeof ORDER)[number];
const TICK_MS = 350;
const RSA_WAIT_MS = 3600;
const ONLINE_WAIT_MS = 1200;

interface Row {
  id: string;
  screen_id: string;
  site_id: string;
  address: string;
  port: number;
  created: number;
  /** Instant de la reprise (retry) et première étape rejouée. */
  restart: number;
  from: Step | null;
  cancelled: boolean;
  ack: boolean;
  ackAt: string | null;
}

const isPrivate = (a: string) => /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(a) || /^f[cd][0-9a-f]{2}:/i.test(a);

export function createProvisionMock(world: { sites: { id: string }[]; screens: { id: string; site_id: string }[] }) {
  const rows = new Map<string, Row>();

  /** Étapes jouées par le scénario ; chaque entrée occupe un tick, `authorize` attend l'invite. */
  function view(r: Row): Json {
    const scenario = r.address.endsWith(".51") ? "tls" : r.address.endsWith(".52") ? "soft" : "ok";
    const from = r.from ? ORDER.indexOf(r.from) : 0;
    const elapsed = Date.now() - r.restart;
    let t = 0;
    let status = "running";
    let code: string | null = null;
    let kiosk: "device_owner" | "soft" | "none" = "device_owner";
    const steps: Json[] = [];
    const at = () => new Date(r.restart + t).toISOString();
    for (const [i, id] of ORDER.entries()) {
      if (i < from) {
        steps.push({ step: id, status: "ok", code: null, at: at() });
        continue;
      }
      let duration = TICK_MS;
      if (id === "authorize") duration = RSA_WAIT_MS;
      if (scenario === "tls" && id === "connect") {
        steps.push({ step: id, status: elapsed >= t + TICK_MS ? "failed" : "running", code: elapsed >= t + TICK_MS ? "provision.android.tls_required" : null, at: at() });
        if (elapsed >= t + TICK_MS) {
          status = "failed";
          code = "provision.android.tls_required";
        }
        return finish(r, status, code, steps, kiosk);
      }
      const soft = scenario === "soft" && id === "device_owner";
      if (elapsed < t) steps.push({ step: id, status: "pending", code: null, at: null });
      else if (elapsed < t + duration) steps.push({ step: id, status: id === "authorize" ? "waiting_user" : "running", code: id === "authorize" ? "provision.android.accept_on_tv" : null, at: at() });
      else steps.push({ step: id, status: soft ? "skipped" : "ok", code: soft ? "provision.android.accounts_present" : null, at: at() });
      if (soft) kiosk = "soft";
      t += duration;
    }
    if (r.cancelled) status = "cancelled";
    else if (elapsed >= t + ONLINE_WAIT_MS) status = "done";
    else if (elapsed >= t) status = "waiting_online";
    else status = steps.some((s) => s.status === "waiting_user") ? "waiting_user" : "running";
    return finish(r, status, code, steps, kiosk);
  }

  function finish(r: Row, status: string, code: string | null, steps: Json[], kiosk: "device_owner" | "soft" | "none"): Json {
    const ended = status === "done" || status === "failed";
    return {
      id: r.id,
      screen_id: r.screen_id,
      site_id: r.site_id,
      platform: "android",
      address: r.address,
      port: r.port,
      status,
      code,
      steps: status === "failed" ? steps.filter((s) => s.status !== "pending") : steps,
      outcome: ended ? { ok: status === "done", kiosk, installed_version_code: 7, code: status === "done" && kiosk === "soft" ? "provision.android.adb_open" : code } : null,
      non_public_ack: r.ack,
      non_public_ack_at: r.ackAt,
      apk_version_code: 7,
      pairing_code_fallback: status === "failed",
      ticket_expires_at: new Date(r.created + 30 * 60_000).toISOString(),
      created_at: new Date(r.created).toISOString(),
      finished_at: ended ? new Date().toISOString() : null,
    };
  }

  return async function handle(req: IncomingMessage, res: ServerResponse, route: string, query: URLSearchParams): Promise<boolean> {
    const method = req.method ?? "GET";
    const send = (status: number, payload: unknown) => {
      res.statusCode = status;
      res.setHeader("Content-Type", "application/json");
      res.setHeader("Cache-Control", "private, no-store");
      res.end(JSON.stringify(payload));
    };
    const fail = (status: number, code: string) => send(status, { code, params: {} });
    const json = (): Promise<Json> =>
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

    if (route === "/provisioning/capabilities" && method === "GET") {
      const site = query.get("site_id");
      if (!world.sites.some((s) => s.id === site)) return fail(404, "errors.not_found"), true;
      const first = world.sites[0]!.id === site;
      return send(200, first ? { available: true, reason: null, platforms: ["android"], apk: { version_name: "1.3.0", version_code: 7 } } : { available: false, reason: "agent_outdated", platforms: [], apk: null }), true;
    }
    const create = /^\/screens\/([^/]+)\/provision$/.exec(route);
    if (create && method === "POST") {
      const screen = world.screens.find((s) => s.id === create[1]);
      if (!screen) return fail(404, "errors.screen_not_found"), true;
      const body = await json();
      const address = String(body.address ?? "");
      if (!isPrivate(address)) return fail(422, "provision.address_not_lan"), true;
      for (const r of rows.values()) if (r.screen_id === screen.id && !r.cancelled && !["done", "failed"].includes(String(view(r).status))) return fail(409, "provision.session_active"), true;
      const now = Date.now();
      const row: Row = { id: randomUUID(), screen_id: screen.id, site_id: screen.site_id, address, port: Number(body.port ?? 5555), created: now, restart: now, from: null, cancelled: false, ack: body.non_public_ack === true, ackAt: body.non_public_ack === true ? new Date(now).toISOString() : null };
      rows.set(row.id, row);
      return send(201, view(row)), true;
    }
    const one = /^\/provisioning\/([^/]+)(\/retry|\/cancel|\/non-public-ack)?$/.exec(route);
    if (one) {
      const row = rows.get(one[1]!);
      if (!row) return fail(404, "provision.not_found"), true;
      if (one[2] === undefined && method === "GET") return send(200, view(row)), true;
      if (one[2] === "/non-public-ack" && method === "POST") {
        const body = await json();
        if (body.confirmed !== true) return fail(422, "provision.ack_required"), true;
        row.ack = true;
        row.ackAt = new Date().toISOString();
        return send(200, view(row)), true;
      }
      if (one[2] === "/retry" && method === "POST") {
        if (view(row).status !== "failed") return fail(409, "provision.not_retryable"), true;
        const body = await json();
        // Le scénario « tls » réussit après correction du réglage sur la TV.
        row.address = row.address.replace(/\.51$/, ".50");
        row.from = (body.from_step as Step | null) ?? null;
        row.restart = Date.now();
        return send(200, view(row)), true;
      }
      if (one[2] === "/cancel" && method === "POST") {
        if (!["queued", "running", "waiting_user", "waiting_online"].includes(String(view(row).status))) return fail(409, "provision.not_cancellable"), true;
        row.cancelled = true;
        return send(200, view(row)), true;
      }
    }
    return false;
  };
}
