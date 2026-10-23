/**
 * Données simulées du lot 2 pour le dev et les tests e2e (le vrai serveur est dans apps/server) :
 * parc, médias (envoi multipart, traitement simulé), playlists, programmations, appairage et révocation.
 * Mêmes chemins, codes d'erreur et formes que l'API réelle (types de packages/sdk-ts).
 */
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { IncomingMessage, ServerResponse } from "node:http";

type Json = Record<string, unknown>;
type Handled = boolean;

const ACCEPT: Record<string, "image" | "video" | "document"> = {
  "image/jpeg": "image",
  "image/png": "image",
  "image/webp": "image",
  "image/gif": "image",
  "video/mp4": "video",
  "video/webm": "video",
  "application/pdf": "document",
};
/** Délai de traitement simulé d'un média envoyé. */
const PROCESSING_MS = 600;
const ITEM_MEDIA_FIELD: Record<string, [string, string]> = {
  "content.image": ["media", "image"],
  "content.video": ["media", "video"],
  "content.pdf": ["media", "document"],
};

interface MediaRow {
  id: string;
  title: string;
  kind: "image" | "video" | "document";
  mime: string;
  bytes: number;
  original_filename: string;
  created: number;
  broken: boolean;
}
interface ItemRow {
  id: string;
  content_type: string;
  duration_ms: number | null;
  enabled: boolean;
  props: Json;
}
interface PlaylistRow {
  id: string;
  name: string;
  description: string | null;
  items: ItemRow[];
  created: string;
}
interface ScheduleRow extends Json {
  id: string;
}

interface DeviceRow {
  id: string;
  platform: string;
  model: string | null;
  app_version: string | null;
  paired_at: string;
  last_seen_at: string | null;
  revoked_at: string | null;
  /** Appareil simulé toujours joignable : sa dernière activité est « maintenant ». */
  live?: boolean;
}

const MODULES = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../modules");
const readJson = (file: string): Json | null => (fs.existsSync(file) ? (JSON.parse(fs.readFileSync(file, "utf8")) as Json) : null);

/** Même forme que GET /registry/content-types : lue dans modules/content.*, comme le fait le serveur. */
function loadContentTypes(): Json[] {
  return fs
    .readdirSync(MODULES)
    .filter((d) => d.startsWith("content."))
    .flatMap((d) => {
      const dir = path.join(MODULES, d);
      const manifest = readJson(path.join(dir, "module.json")) as (Json & { content?: Json }) | null;
      const item = readJson(path.join(dir, "item.schema.json"));
      if (!manifest || manifest.kind !== "content-type" || !item) return [];
      const content = (manifest.content ?? {}) as Json & { media?: { accepts?: string[] }; player?: { element?: string } };
      return [
        {
          id: manifest.id,
          version: manifest.version,
          name: manifest.name,
          description: manifest.description,
          item_schema: item,
          config_schema: readJson(path.join(dir, "config.schema.json")),
          i18n: Object.fromEntries(["fr", "en"].map((l) => [l, readJson(path.join(dir, "i18n", `${l}.json`)) ?? {}])),
          element: content.player?.element ?? "",
          accepts: content.media?.accepts ?? [],
          requires_network: content.requires_network === true,
        },
      ];
    })
    .sort((a, b) => String(a.id).localeCompare(String(b.id)));
}

const now = () => new Date().toISOString();

function readRaw(req: IncomingMessage): Promise<Buffer> {
  return new Promise((resolve) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
  });
}

function parseMultipart(body: Buffer, contentType: string): { file?: { name: string; mime: string; bytes: number }; fields: Record<string, string> } {
  const boundary = /boundary=(?:"([^"]+)"|([^;]+))/.exec(contentType);
  const marker = boundary?.[1] ?? boundary?.[2];
  const out: { file?: { name: string; mime: string; bytes: number }; fields: Record<string, string> } = { fields: {} };
  if (!marker) return out;
  for (const part of body.toString("latin1").split(`--${marker}`)) {
    const split = part.indexOf("\r\n\r\n");
    if (split < 0) continue;
    const head = part.slice(0, split);
    const content = part.slice(split + 4).replace(/\r\n$/, "");
    const name = /name="([^"]*)"/.exec(head)?.[1];
    const filename = /filename="([^"]*)"/.exec(head)?.[1];
    if (filename !== undefined) {
      out.file = { name: Buffer.from(filename, "latin1").toString("utf8"), mime: /Content-Type:\s*([^\r\n]+)/i.exec(head)?.[1]?.trim() ?? "", bytes: content.length };
    } else if (name) out.fields[name] = Buffer.from(content, "latin1").toString("utf8");
  }
  return out;
}

export function createDiffusionMock() {
  const site = { id: randomUUID(), name: "Moulins", slug: "moulins", archived_at: null as string | null };
  const sites = [site];
  const group = { id: randomUUID(), site_id: site.id, name: "Accueil" };
  const screens = [
    { id: randomUUID(), site_id: site.id, group_id: group.id, name: "Hall d'accueil", orientation: "landscape", archived_at: null },
    { id: randomUUID(), site_id: site.id, group_id: null, name: "Totem", orientation: "portrait", archived_at: null },
  ];
  const media: MediaRow[] = [];
  const playlists: PlaylistRow[] = [];
  const schedules: ScheduleRow[] = [];
  /** Un appareil par écran ; l'écran « Hall d'accueil » arrive déjà appairé et vu récemment. */
  const devices = new Map<string, DeviceRow>([
    [screens[0]!.id, { id: randomUUID(), platform: "android", model: "Tab A9", app_version: "1.0.0", paired_at: now(), last_seen_at: now(), revoked_at: null, live: true }],
  ]);
  const contentTypes = loadContentTypes();
  const deviceOut = ({ live, ...d }: DeviceRow) => ({
    ...d,
    last_seen_at: live && !d.revoked_at ? now() : d.last_seen_at,
    heartbeat: null,
    presence: d.revoked_at ? "revoked" : live ? "online" : !d.last_seen_at ? "unseen" : "offline",
  });
  const screenOut = (s: (typeof screens)[number]) => ({ ...s, device: devices.has(s.id) ? deviceOut(devices.get(s.id)!) : null });
  const claimed = new Set<string>();

  const mediaOut = (m: MediaRow) => {
    const age = Date.now() - m.created;
    const status = age < PROCESSING_MS ? "processing" : m.broken ? "failed" : "ready";
    return {
      id: m.id,
      title: m.title,
      kind: m.kind,
      mime: m.mime,
      bytes: m.bytes,
      status,
      error: status === "failed" ? "media.type_mismatch" : null,
      original_filename: m.original_filename,
      width: null,
      height: null,
      duration_ms: null,
      page_count: null,
      sha256: null,
      created_at: new Date(m.created).toISOString(),
      updated_at: new Date(m.created).toISOString(),
    };
  };
  const itemOut = (it: ItemRow, position: number) => {
    const field = ITEM_MEDIA_FIELD[it.content_type]?.[0];
    const mid = field ? it.props[field] : undefined;
    return { id: it.id, content_type: it.content_type, duration_ms: it.duration_ms, enabled: it.enabled, position, props: it.props, media_ids: typeof mid === "string" ? [mid] : [] };
  };
  const playlistOut = (p: PlaylistRow) => ({ id: p.id, name: p.name, description: p.description, item_count: p.items.length, created_at: p.created, updated_at: p.created });
  const playlistDetail = (p: PlaylistRow) => ({ ...playlistOut(p), items: p.items.map(itemOut) });
  const mediaUsedBy = (id: string) => playlists.filter((p) => p.items.some((i) => itemOut(i, 0).media_ids.includes(id))).map((p) => p.id);

  /** Retourne true si la route a été traitée ; `route` est relatif à /api/v1. */
  const handle = async function handle(req: IncomingMessage, res: ServerResponse, route: string, query: URLSearchParams): Promise<Handled> {
    const method = req.method ?? "GET";
    const send = (status: number, payload: unknown) => {
      res.statusCode = status;
      res.setHeader("Content-Type", "application/json");
      res.setHeader("Cache-Control", "private, no-store");
      res.end(JSON.stringify(payload));
    };
    const fail = (status: number, code: string, params: Json = {}) => send(status, { code, params });
    const empty = () => {
      res.statusCode = 204;
      res.end();
    };
    const m = (re: RegExp) => re.exec(route);
    const json = async (): Promise<Json> => {
      try {
        return JSON.parse((await readRaw(req)).toString("utf8") || "{}") as Json;
      } catch {
        return {};
      }
    };

    if (route === "/sites" && method === "GET") return send(200, sites), true;
    if (m(/^\/sites\/[^/]+\/groups$/) && method === "GET") return send(200, [group]), true;
    if (route === "/screens" && method === "GET") return send(200, screens.map(screenOut)), true;
    if (route === "/registry/content-types" && method === "GET") return send(200, contentTypes), true;

    // --- appairage -------------------------------------------------------
    if (route === "/pairings/claim" && method === "POST") {
      const body = await json();
      const code = String(body.code ?? "").toUpperCase();
      const screen = screens.find((s) => s.id === body.screen_id);
      if (!screen) return fail(404, "errors.screen_not_found"), true;
      if (code === "EXPIRE") return fail(410, "errors.pairing_code_expired"), true;
      if (!/^[A-Z2-9]{6}$/.test(code) || code.startsWith("X") || claimed.has(code)) return fail(404, "errors.pairing_code_invalid"), true;
      claimed.add(code);
      devices.set(screen.id, { id: randomUUID(), platform: "android", model: null, app_version: null, paired_at: now(), last_seen_at: null, revoked_at: null });
      return send(200, { screen_id: screen.id }), true;
    }
    const dev = m(/^\/screens\/([^/]+)\/device$/);
    if (dev && method === "DELETE") {
      if (!screens.some((s) => s.id === dev[1])) return fail(404, "errors.screen_not_found"), true;
      const device = devices.get(dev[1]!);
      if (!device || device.revoked_at) return fail(404, "errors.device_not_found"), true;
      device.revoked_at = now();
      return empty(), true;
    }

    // --- médias ----------------------------------------------------------
    if (route === "/media" && method === "GET") {
      const kind = query.get("kind");
      return send(200, media.filter((x) => !kind || x.kind === kind).map(mediaOut).reverse()), true;
    }
    if (route === "/media" && method === "POST") {
      const parsed = parseMultipart(await readRaw(req), String(req.headers["content-type"] ?? ""));
      if (!parsed.file || parsed.file.bytes === 0) return fail(422, "media.empty"), true;
      const kind = ACCEPT[parsed.file.mime];
      if (!kind) return fail(415, "media.type_invalid"), true;
      const row: MediaRow = {
        id: randomUUID(),
        title: parsed.fields.title?.trim() || parsed.file.name.replace(/\.[^.]+$/, ""),
        kind,
        mime: parsed.file.mime,
        bytes: parsed.file.bytes,
        original_filename: parsed.file.name,
        created: Date.now(),
        broken: /broken/i.test(parsed.file.name),
      };
      media.push(row);
      return send(201, mediaOut(row)), true;
    }
    const med = m(/^\/media\/([^/]+)$/);
    if (med) {
      const row = media.find((x) => x.id === med[1]);
      if (!row) return fail(404, "media.not_found"), true;
      if (method === "GET") return send(200, { ...mediaOut(row), variants: [], used_by: mediaUsedBy(row.id) }), true;
      if (method === "DELETE") {
        const used = mediaUsedBy(row.id);
        if (used.length > 0) return fail(409, "media.in_use", { playlists: used }), true;
        media.splice(media.indexOf(row), 1);
        return empty(), true;
      }
    }

    // --- playlists -------------------------------------------------------
    if (route === "/playlists" && method === "GET") return send(200, playlists.map(playlistOut)), true;
    if (route === "/playlists" && method === "POST") {
      const body = await json();
      const row: PlaylistRow = { id: randomUUID(), name: String(body.name ?? "").trim(), description: null, items: [], created: now() };
      if (!row.name) return fail(422, "errors.validation"), true;
      playlists.push(row);
      return send(201, playlistDetail(row)), true;
    }
    const pl = m(/^\/playlists\/([^/]+)(?:\/(items)(?:\/(order|[^/]+))?)?$/);
    if (pl) {
      const row = playlists.find((p) => p.id === pl[1]);
      if (!row) return fail(404, "playlist.not_found"), true;
      if (!pl[2]) {
        if (method === "GET") return send(200, playlistDetail(row)), true;
        if (method === "PATCH") {
          const body = await json();
          if (typeof body.name === "string" && body.name.trim()) row.name = body.name.trim();
          return send(200, playlistDetail(row)), true;
        }
        if (method === "DELETE") {
          const used = schedules.filter((s) => s.playlist_id === row.id).map((s) => s.id);
          if (used.length > 0) return fail(409, "playlist.in_use", { schedules: used }), true;
          playlists.splice(playlists.indexOf(row), 1);
          return empty(), true;
        }
      } else if (!pl[3] && method === "POST") {
        const body = await json();
        const type = String(body.content_type ?? "");
        const props = (body.props ?? {}) as Json;
        const err = validateProps(type, props);
        if (err) return fail(...err), true;
        const item: ItemRow = { id: randomUUID(), content_type: type, duration_ms: (body.duration_ms as number | null | undefined) ?? null, enabled: body.enabled !== false, props };
        row.items.push(item);
        return send(201, itemOut(item, row.items.length - 1)), true;
      } else if (pl[3] === "order" && method === "PUT") {
        const ids = ((await json()).item_ids ?? []) as string[];
        if (ids.length !== row.items.length || !ids.every((i) => row.items.some((x) => x.id === i))) return fail(422, "playlist.order_mismatch"), true;
        row.items = ids.map((i) => row.items.find((x) => x.id === i)!);
        return send(200, playlistDetail(row)), true;
      } else if (pl[3]) {
        const idx = row.items.findIndex((x) => x.id === pl[3]);
        if (idx < 0) return fail(404, "playlist.item_not_found"), true;
        const item = row.items[idx]!;
        if (method === "DELETE") {
          row.items.splice(idx, 1);
          return empty(), true;
        }
        if (method === "PATCH") {
          const body = await json();
          if (body.props) {
            const err = validateProps(item.content_type, body.props as Json);
            if (err) return fail(...err), true;
            item.props = body.props as Json;
          }
          if ("duration_ms" in body) item.duration_ms = (body.duration_ms as number | null) ?? null;
          if (typeof body.enabled === "boolean") item.enabled = body.enabled;
          return send(200, itemOut(item, idx)), true;
        }
      }
    }

    // --- programmations --------------------------------------------------
    if (route === "/schedules" && method === "GET") return send(200, schedules), true;
    if (route === "/schedules" && method === "POST") {
      const body = await json();
      const err = validateSchedule(body);
      if (err) return fail(...err), true;
      const row: ScheduleRow = { id: randomUUID(), enabled: true, priority: 0, rules: [], starts_on: null, ends_on: null, ...body, created_at: now(), updated_at: now() };
      schedules.push(row);
      return send(201, row), true;
    }
    const sc = m(/^\/schedules\/([^/]+)$/);
    if (sc) {
      const row = schedules.find((s) => s.id === sc[1]);
      if (!row) return fail(404, "schedule.not_found"), true;
      if (method === "GET") return send(200, row), true;
      if (method === "PATCH") {
        const body = await json();
        const err = validateSchedule({ ...row, ...body });
        if (err) return fail(...err), true;
        Object.assign(row, body, { updated_at: now() });
        return send(200, row), true;
      }
      if (method === "DELETE") {
        schedules.splice(schedules.indexOf(row), 1);
        return empty(), true;
      }
    }
    return false;
  };
  return Object.assign(handle, { world: { sites, group, screens } });

  function validateProps(type: string, props: Json): [number, string, Json?] | null {
    const field = ITEM_MEDIA_FIELD[type];
    if (!field && !["content.url"].includes(type)) return [422, "playlist.content_type_unknown", { content_type: type }];
    if (field) {
      const row = media.find((x) => x.id === props[field[0]]);
      if (!row) return [422, "playlist.media_not_found", { field: field[0] }];
      if (row.kind !== field[1]) return [422, "playlist.media_kind_mismatch", { field: field[0], expected: field[1] }];
    } else if (typeof props.url !== "string" || !/^https:\/\/\S+$/.test(props.url)) return [422, "playlist.item_props_invalid"];
    return null;
  }

  function validateSchedule(body: Json): [number, string, Json?] | null {
    if (!playlists.some((p) => p.id === body.playlist_id)) return [422, "schedule.playlist_not_found"];
    const pool = body.target_type === "screen" ? screens : body.target_type === "group" ? [group] : body.target_type === "site" ? [site] : [];
    if (!pool.some((x) => x.id === body.target_id)) return [422, "schedule.target_not_found"];
    if (body.starts_on && body.ends_on && String(body.ends_on) < String(body.starts_on)) return [422, "schedule.period_invalid"];
    return null;
  }
}
