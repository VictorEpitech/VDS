/**
 * Données simulées du lot 3 pour le dev et les tests e2e : sites, agents (jeton affiché une fois), plans
 * d'alimentation, exceptions, calendriers, rattachement hérité (config), incidents, commandes à distance
 * et réglages du module notify.mail. Mêmes chemins, codes d'erreur et formes que l'API réelle.
 */
import { randomBytes, randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { IncomingMessage, ServerResponse } from "node:http";

type Json = Record<string, unknown>;

interface World {
  sites: { id: string; name: string; slug: string; archived_at: string | null }[];
  group: { id: string; site_id: string; name: string };
  screens: { id: string; site_id: string; group_id: string | null; name: string }[];
}

const MODULES = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../modules");
const now = () => new Date().toISOString();
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();
const COMMAND_DELAY_MS = 400;

interface AgentRow {
  id: string;
  site_id: string;
  name: string;
  token: string;
  created_at: string;
  rotated_at: string | null;
  revoked_at: string | null;
  last_seen_at: string | null;
}
interface CommandRow extends Json {
  id: string;
  created: number;
}

/** Valeurs par défaut des clés de config simulées (core et notify.mail). */
const DEFAULTS: Record<string, Record<string, unknown>> = {
  core: { "power.plan_id": null, "power.calendars": [] },
  "notify.mail": {
    smtp_host: "",
    smtp_port: 587,
    smtp_security: "starttls",
    smtp_username: "",
    smtp_password: "",
    from: "",
    notify_admins: true,
    extra_recipients: [],
    kinds: ["screen.offline", "agent.offline", "power.failed", "module.quarantined"],
    send_resolved: true,
  },
};
/** Pilotes d'alimentation simulés : schémas lus dans modules/ (les mêmes que ceux servis par le registre). */
const DRIVERS = ["power.wol", "power.samsung-ws", "power.adb"];
const SCOPES: Record<string, Record<string, string[] | undefined>> = {};
for (const id of DRIVERS) {
  const schema = JSON.parse(fs.readFileSync(path.join(MODULES, id, "config.schema.json"), "utf8")) as { properties: Record<string, { default?: unknown; "x-vds-scopes"?: string[] }> };
  DEFAULTS[id] = Object.fromEntries(Object.entries(schema.properties).map(([k, v]) => [k, v.default]));
  SCOPES[id] = Object.fromEntries(Object.entries(schema.properties).map(([k, v]) => [k, v["x-vds-scopes"]]));
}
DEFAULTS.core!["power.cascade"] = DRIVERS;
const ENV_KEYS = new Set(["smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_password", "from"]);
const SECRET_KEYS = new Set(["smtp_password"]);

/** Commande telle que l'API la renvoie (sans les champs internes du simulateur). */
const publicCommand = (c: CommandRow): Json => Object.fromEntries(Object.entries(c).filter(([k]) => k !== "created" && k !== "agent_id"));

export function createPowerMock(world: World) {
  const org = { id: randomUUID(), name: "Collectivité de Moulins" };
  const moulins = world.sites[0]!;
  const agents: AgentRow[] = [
    { id: randomUUID(), site_id: moulins.id, name: "Serveur technique", token: "vds_agt_seed", created_at: ago(86_400_000), rotated_at: null, revoked_at: null, last_seen_at: now() },
  ];
  const plans: { id: string; name: string; description: string | null; rules: Json[]; created_at: string; updated_at: string }[] = [];
  const exceptions: Json[] = [];
  const calendars: { key: string; module_id: string; name: Json; refreshed_at: string | null; days: Map<string, string> }[] = [];
  const commands: CommandRow[] = [];
  const overrides = new Map<string, unknown>();
  const hall = world.screens[0]!;
  const incidents: Json[] = [
    {
      id: randomUUID(), kind: "agent.offline", severity: "critical", subject_type: "agent", subject_id: agents[0]!.id, subject_name: "Serveur technique",
      site_id: moulins.id, site_name: moulins.name, details: {}, opened_at: ago(3_600_000), resolved_at: null, acknowledged_at: null, acknowledged_by: null,
    },
    {
      id: randomUUID(), kind: "screen.offline", severity: "warning", subject_type: "screen", subject_id: hall.id, subject_name: hall.name,
      site_id: moulins.id, site_name: moulins.name, details: {}, opened_at: ago(7_200_000), resolved_at: ago(6_000_000), acknowledged_at: null, acknowledged_by: null,
    },
  ];

  const agentStatus = (a: AgentRow) => (a.revoked_at ? "revoked" : !a.last_seen_at ? "unseen" : "online");
  const agentOut = (a: AgentRow) => ({
    id: a.id, site_id: a.site_id, name: a.name, token_prefix: a.token.slice(0, 12), status: agentStatus(a), version: a.last_seen_at ? "1.0.0" : null,
    last_seen_at: a.last_seen_at && !a.revoked_at ? now() : a.last_seen_at, last_heartbeat: null, created_at: a.created_at, rotated_at: a.rotated_at, revoked_at: a.revoked_at,
  });
  const newToken = () => `vds_agt_${randomBytes(32).toString("base64url")}`;

  const read = (file: string): Json | null => (fs.existsSync(file) ? (JSON.parse(fs.readFileSync(file, "utf8")) as Json) : null);

  /** Chaîne d'héritage d'une cible : de la plus précise à l'organisation. */
  function chain(level: string, id: string): { level: string; id: string; name: string | null }[] {
    const out: { level: string; id: string; name: string | null }[] = [];
    let screen = level === "screen" ? world.screens.find((s) => s.id === id) : undefined;
    if (screen) out.push({ level: "screen", id, name: screen.name });
    const group = level === "group" ? world.group : screen?.group_id ? world.group : undefined;
    if (group && (level === "group" ? group.id === id : true)) out.push({ level: "group", id: group.id, name: group.name });
    const siteId = level === "site" ? id : level === "group" ? group?.site_id : screen?.site_id;
    const site = world.sites.find((s) => s.id === siteId);
    if (site) out.push({ level: "site", id: site.id, name: site.name });
    out.push({ level: "organization", id: org.id, name: org.name });
    screen = undefined;
    return out;
  }
  function resolveKey(level: string, id: string, ns: string, key: string) {
    const levels = chain(level, id);
    const def = DEFAULTS[ns]![key];
    const own = (from: number) => {
      for (let i = from; i < levels.length; i++) {
        const l = levels[i]!;
        const k = `${l.level}:${l.id}:${ns}:${key}`;
        if (overrides.has(k)) return { value: overrides.get(k), origin: { level: l.level, id: l.id, name: l.name } };
      }
      return { value: def, origin: { level: ns === "notify.mail" && ENV_KEYS.has(key) ? "env" : "default", id: null, name: null } };
    };
    const cur = own(0);
    const inh = own(1);
    return {
      value: SECRET_KEYS.has(key) ? "" : cur.value,
      origin: cur.origin,
      inherited: SECRET_KEYS.has(key) ? "" : inh.value,
      inherited_origin: inh.origin,
      overridable: !ENV_KEYS.has(key) && (SCOPES[ns]?.[key]?.includes(level) ?? true),
      secret: SECRET_KEYS.has(key),
      is_set: SECRET_KEYS.has(key) ? false : null,
    };
  }

  function slotsFor(screenId: string) {
    const r = resolveKey("screen", screenId, "core", "power.plan_id");
    const plan = plans.find((p) => p.id === r.value);
    if (!plan) return [];
    // Créneaux simulés : allumé de -1 h à +8 h autour de maintenant, puis éteint 8 h.
    return [
      { from: ago(3_600_000), to: new Date(Date.now() + 8 * 3_600_000).toISOString(), state: "on" },
      { from: new Date(Date.now() + 8 * 3_600_000).toISOString(), to: new Date(Date.now() + 16 * 3_600_000).toISOString(), state: "off" },
    ];
  }

  function advance(c: CommandRow) {
    if (c.status !== "pending" || Date.now() - c.created < COMMAND_DELAY_MS) return;
    const screen = world.screens.find((s) => s.id === c.screen_id);
    c.done_at = now();
    if (c.name === "power.set" && screen?.name === "Totem") {
      c.status = "failed";
      c.code = "power.adb.unreachable";
      c.result = {};
    } else if (c.name === "power.detect") {
      c.status = "ok";
      c.result = { results: [{ driver: "power.wol", confidence: 0.9, capabilities: ["power.on"], proposed_config: { mac: "aa:bb:cc:dd:ee:ff" }, requires_user_action: null }] };
    } else {
      c.status = "ok";
      c.result = { state: (c.args as { state?: string }).state ?? "on" };
    }
  }

  return async function handle(req: IncomingMessage, res: ServerResponse, route: string, _query: URLSearchParams): Promise<boolean> {
    void _query;
    const method = req.method ?? "GET";
    const send = (status: number, payload: unknown) => {
      res.statusCode = status;
      res.setHeader("Content-Type", "application/json");
      res.setHeader("Cache-Control", "private, no-store");
      res.end(JSON.stringify(payload));
    };
    const fail = (status: number, code: string) => send(status, { code, params: {} });
    const empty = () => {
      res.statusCode = 204;
      res.end();
    };
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
    const m = (re: RegExp) => re.exec(route);

    if (route === "/organization" && method === "GET") return send(200, org), true;

    // --- sites -----------------------------------------------------------
    if (route === "/sites" && method === "POST") {
      const b = await json();
      if (world.sites.some((s) => s.slug === b.slug)) return fail(409, "errors.validation"), true;
      const row = { id: randomUUID(), name: String(b.name), slug: String(b.slug), archived_at: null };
      world.sites.push(row);
      return send(201, row), true;
    }
    const siteM = m(/^\/sites\/([^/]+)$/);
    if (siteM && method === "PATCH") {
      const row = world.sites.find((s) => s.id === siteM[1]);
      if (!row) return fail(404, "errors.not_found"), true;
      const b = await json();
      if (b.archived === true) row.archived_at = now();
      if (typeof b.name === "string") row.name = b.name;
      return send(200, row), true;
    }

    // --- agents ----------------------------------------------------------
    const ag = m(/^\/sites\/([^/]+)\/agents(?:\/([^/]+)(?:\/(rotate|revoke|commands))?)?$/);
    if (ag) {
      const siteId = ag[1]!;
      if (!ag[2]) {
        if (method === "GET") return send(200, agents.filter((a) => a.site_id === siteId).map(agentOut)), true;
        if (method === "POST") {
          if (agents.some((a) => a.site_id === siteId && !a.revoked_at)) return fail(409, "agents.already_active"), true;
          const b = await json();
          const row: AgentRow = { id: randomUUID(), site_id: siteId, name: String(b.name), token: newToken(), created_at: now(), rotated_at: null, revoked_at: null, last_seen_at: null };
          agents.push(row);
          return send(201, { agent: agentOut(row), token: row.token }), true;
        }
      } else {
        const row = agents.find((a) => a.id === ag[2] && a.site_id === siteId);
        if (!row) return fail(404, "agents.not_found"), true;
        if (ag[3] === "rotate" && method === "POST") {
          if (row.revoked_at) return fail(409, "agents.revoked"), true;
          row.token = newToken();
          row.rotated_at = now();
          return send(200, { agent: agentOut(row), token: row.token }), true;
        }
        if (ag[3] === "revoke" && method === "POST") {
          row.revoked_at ??= now();
          return send(200, agentOut(row)), true;
        }
        if (ag[3] === "commands" && method === "GET") {
          commands.forEach(advance);
          return send(200, commands.filter((c) => c.agent_id === row.id).map(publicCommand)), true;
        }
        if (ag[3] === "commands" && method === "POST") {
          if (row.revoked_at) return fail(409, "agents.revoked"), true;
          const b = await json();
          const cmd: CommandRow = {
            id: randomUUID(), created: Date.now(), agent_id: row.id, seq: commands.length + 1, name: String(b.name), screen_id: (b.screen_id as string | null) ?? null,
            args: (b.args as Json) ?? {}, status: "pending", code: null, result: null, created_at: now(), sent_at: null, done_at: null, expires_at: new Date(Date.now() + 120_000).toISOString(),
          };
          commands.push(cmd);
          return send(202, publicCommand(cmd)), true;
        }
      }
    }

    // --- planning d'alimentation ----------------------------------------
    if (route === "/power/plans" && method === "GET") return send(200, plans), true;
    if (route === "/power/plans" && method === "POST") {
      const b = await json();
      const row = { id: randomUUID(), name: String(b.name), description: (b.description as string | null) ?? null, rules: (b.rules as Json[]) ?? [], created_at: now(), updated_at: now() };
      plans.push(row);
      return send(201, row), true;
    }
    const pl = m(/^\/power\/plans\/([^/]+)$/);
    if (pl) {
      const row = plans.find((p) => p.id === pl[1]);
      if (!row) return fail(404, "power.plan_not_found"), true;
      if (method === "GET") return send(200, row), true;
      if (method === "PATCH") {
        const b = await json();
        if (typeof b.name === "string") row.name = b.name;
        if ("description" in b) row.description = (b.description as string | null) ?? null;
        if (Array.isArray(b.rules)) row.rules = b.rules as Json[];
        row.updated_at = now();
        return send(200, row), true;
      }
      if (method === "DELETE") {
        if ([...overrides.entries()].some(([k, v]) => k.endsWith(":core:power.plan_id") && v === row.id)) return fail(409, "power.plan_in_use"), true;
        plans.splice(plans.indexOf(row), 1);
        return empty(), true;
      }
    }
    if (route === "/power/exceptions" && method === "GET") return send(200, exceptions), true;
    if (route === "/power/exceptions" && method === "POST") {
      const row = { ...(await json()), id: randomUUID(), created_at: now(), updated_at: now() };
      exceptions.push(row);
      return send(201, row), true;
    }
    const ex = m(/^\/power\/exceptions\/([^/]+)$/);
    if (ex) {
      const idx = exceptions.findIndex((x) => x.id === ex[1]);
      if (idx < 0) return fail(404, "power.exception_not_found"), true;
      if (method === "PUT") {
        exceptions[idx] = { ...(await json()), id: ex[1], created_at: exceptions[idx]!.created_at, updated_at: now() };
        return send(200, exceptions[idx]), true;
      }
      if (method === "DELETE") {
        exceptions.splice(idx, 1);
        return empty(), true;
      }
      if (method === "GET") return send(200, exceptions[idx]), true;
    }
    if (route === "/power/calendars" && method === "GET") {
      return send(200, calendars.map((c) => ({ key: c.key, module_id: c.module_id, name: c.name, day_count: c.days.size, refreshed_at: c.refreshed_at }))), true;
    }
    if (route === "/power/calendars" && method === "POST") {
      const b = await json();
      if (calendars.some((c) => c.key === b.key)) return fail(409, "power.calendar_exists"), true;
      const row = { key: String(b.key), module_id: "core", name: (b.name as Json) ?? {}, refreshed_at: null, days: new Map<string, string>() };
      calendars.push(row);
      return send(201, { key: row.key, module_id: row.module_id, name: row.name, day_count: 0, refreshed_at: null }), true;
    }
    const cal = m(/^\/power\/calendars\/([^/]+)(?:\/days(?:\/([^/]+))?)?$/);
    if (cal) {
      const row = calendars.find((c) => c.key === cal[1]);
      if (!row) return fail(404, "power.calendar_not_found"), true;
      const isDays = route.includes("/days");
      if (!isDays && method === "DELETE") {
        calendars.splice(calendars.indexOf(row), 1);
        return empty(), true;
      }
      if (isDays && !cal[2] && method === "GET") {
        return send(200, [...row.days.entries()].sort().map(([day, label]) => ({ day, label }))), true;
      }
      if (isDays && cal[2] && method === "PUT") {
        row.days.set(cal[2], String((await json()).label));
        return send(200, { day: cal[2], label: row.days.get(cal[2]) }), true;
      }
      if (isDays && cal[2] && method === "DELETE") {
        if (!row.days.delete(cal[2])) return fail(404, "power.calendar_day_not_found"), true;
        return empty(), true;
      }
    }
    const sp = m(/^\/power\/screens\/([^/]+)$/);
    if (sp && method === "GET") {
      const plan = resolveKey("screen", sp[1]!, "core", "power.plan_id");
      const cals = resolveKey("screen", sp[1]!, "core", "power.calendars");
      return send(200, { screen_id: sp[1], timezone: "Europe/Paris", plan_id: plan.value, plan_id_origin: plan.origin, calendars: cals.value, calendars_origin: cals.origin, slots: slotsFor(sp[1]!) }), true;
    }

    // --- config héritée --------------------------------------------------
    const cfg = m(/^\/config\/(organization|site|group|screen)\/([^/]+)\/([^/]+)(?:\/([^/]+))?$/);
    if (cfg) {
      const [, level, id, ns, key] = cfg as unknown as [string, string, string, string, string | undefined];
      if (!DEFAULTS[ns]) return fail(404, "config.namespace_unknown"), true;
      if (!key && method === "GET") {
        return send(200, { values: Object.fromEntries(Object.keys(DEFAULTS[ns]!).map((k) => [k, resolveKey(level, id, ns, k)])), warnings: [] }), true;
      }
      if (key && !(key in DEFAULTS[ns]!)) return fail(404, "config.key_unknown"), true;
      if (key && ns === "notify.mail" && ENV_KEYS.has(key)) return fail(409, "config.key_from_env"), true;
      if (key && method === "PUT" && SCOPES[ns]?.[key] && !SCOPES[ns]![key]!.includes(level)) return fail(422, "config.scope_not_allowed"), true;
      if (key && method === "PUT") {
        overrides.set(`${level}:${id}:${ns}:${key}`, (await json()).value);
        return empty(), true;
      }
      if (key && method === "DELETE") {
        overrides.delete(`${level}:${id}:${ns}:${key}`);
        return empty(), true;
      }
    }

    if (route === "/registry/modules" && method === "GET" && _query.get("kind") === "power-driver") {
      const ids = fs.readdirSync(MODULES).filter((d) => read(path.join(MODULES, d, "module.json"))?.kind === "power-driver").sort();
      return send(200, ids.map((id) => ({
        id, name: read(path.join(MODULES, id, "module.json"))?.name, enabled: true, has_settings: !!read(path.join(MODULES, id, "config.schema.json")),
      }))), true;
    }

    // --- réglages de module (formulaire généré) --------------------------
    const ms = m(/^\/registry\/modules\/([^/]+)\/settings$/);
    if (ms && method === "GET") {
      const dir = path.join(MODULES, ms[1]!);
      const manifest = read(path.join(dir, "module.json"));
      const schema = read(path.join(dir, "config.schema.json"));
      if (!manifest || !schema || manifest.kind === "content-type") return fail(404, "registry.module_not_found"), true;
      return send(200, {
        id: manifest.id, version: manifest.version, name: manifest.name, description: manifest.description, config_schema: schema,
        i18n: Object.fromEntries(["fr", "en"].map((l) => [l, read(path.join(dir, "i18n", `${l}.json`)) ?? {}])),
      }), true;
    }

    // --- incidents -------------------------------------------------------
    if (route === "/incidents" && method === "GET") {
      const state = _query.get("state") ?? "open";
      return send(200, incidents.filter((i) => (state === "all" ? true : state === "open" ? !i.resolved_at : !!i.resolved_at))), true;
    }
    const ack = m(/^\/incidents\/([^/]+)\/acknowledge$/);
    if (ack && method === "POST") {
      const row = incidents.find((i) => i.id === ack[1]);
      if (!row) return fail(404, "incidents.not_found"), true;
      if (row.resolved_at) return fail(409, "incidents.already_resolved"), true;
      row.acknowledged_at ??= now();
      row.acknowledged_by = randomUUID();
      return send(200, row), true;
    }
    return false;
  };
}
