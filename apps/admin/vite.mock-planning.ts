/**
 * Simule GET /planning/timeline et GET /planning/at pour le dev et les e2e (le vrai résolveur est dans apps/server).
 * Données de démonstration déterministes dérivées des programmations existantes : ce n'est pas un résolveur,
 * il pose simplement un jeu de segments plausible (une source de fond, une source prioritaire de 12 h à 14 h).
 */
import { randomUUID } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";
import { addDays, dayBounds, todayIn, wallClock, dateKey, zonedToUtc } from "./src/features/planning/time";

type Row = Record<string, unknown> & { id: string };
interface World {
  sites: { id: string; name: string }[];
  screens: { id: string; site_id: string; group_id: string | null; name: string; archived_at: unknown }[];
  schedules: Row[];
  playlists: { id: string; name: string }[];
}

/** Fuseau du site simulé : volontairement différent de celui des navigateurs de test (mention testée en e2e). */
export const MOCK_SITE_TZ = "Europe/Paris";
const level = (p: number): "background" | "normal" | "high" => (p < 0 ? "background" : p >= 100 ? "high" : "normal");
const iso = (ms: number) => new Date(ms).toISOString();
const HOUR = 3_600_000;

export function createPlanningMock(world: World, forcedOn: (screenId: string) => { id: string; name: string; playlist_id: string | null; ends_at: string } | undefined = () => undefined) {
  const demoPlaylist = randomUUID();
  const sources = () => {
    const mk = (row: Row | undefined, fallbackName: string, priority: number, tid: string) => {
      const name = String(row?.name ?? fallbackName);
      const pl = world.playlists.find((p) => p.id === row?.playlist_id);
      const pr = typeof row?.priority === "number" ? (row.priority as number) : priority;
      const targetType = (row?.target_type as string | undefined) ?? "site";
      return {
        source: {
          kind: "schedule",
          id: row?.id ?? randomUUID(),
          name,
          target_type: targetType,
          target_id: (row?.target_id as string | undefined) ?? tid,
          target_name: targetType === "site" ? "Moulins" : null,
          inherited: targetType !== "screen",
          priority: pr,
          priority_level: level(pr),
        },
        content: { kind: "playlist", id: (pl?.id ?? demoPlaylist) as string, name: pl?.name ?? `${name} (playlist)` },
      };
    };
    const site = world.sites[0]!.id;
    const ordered = [...world.schedules];
    return [mk(ordered[0], "Accueil", 0, site), mk(ordered[1], "Promo", 100, site)];
  };

  const none = { kind: "none", id: null, name: null, target_type: null, target_id: null, target_name: null, inherited: false, priority: null, priority_level: null };

  /** Segments d'une journée murale du site. */
  const day = (date: string) => {
    const [lo] = dayBounds(date, MOCK_SITE_TZ);
    const at = (h: number) => lo + h * HOUR;
    const [a, b] = sources() as [ReturnType<typeof sources>[number], ReturnType<typeof sources>[number]];
    const seg = (s: number, e: number, w: (typeof a) | null, reason: string, losers: unknown[] = []) => ({
      start: iso(at(s)),
      end: iso(at(e)),
      state: w ? "play" : "off",
      content: w?.content ?? null,
      source: w?.source ?? none,
      reason,
      losers,
    });
    return [
      seg(0, 8, null, "no_candidate"),
      seg(8, 12, a, "only_candidate"),
      seg(12, 14, b, "higher_priority", [{ source: a.source, content: a.content, reason: "higher_priority" }]),
      seg(14, 18, a, "only_candidate"),
      seg(18, 24, null, "no_candidate"),
    ];
  };

  const scopeScreens = (type: string, id: string) =>
    world.screens.filter((s) => !s.archived_at && (type === "screen" ? s.id === id : type === "group" ? s.group_id === id : type === "site" ? s.site_id === id : true));

  /** Timeline d'une cible, paginée par curseur (décalage) comme le serveur. */
  const timelineFor = (type: string, id: string, from: string, to: string, list: World["screens"], offset: number, limit: number) => {
    const days: string[] = [];
    for (let d = from, n = 0; d <= to && n < 42; d = addDays(d, 1), n++) days.push(d);
    const [a, b] = sources() as [ReturnType<typeof sources>[number], ReturnType<typeof sources>[number]];
    const occ = (h0: number, h1: number) => days.map((d) => [iso(dayBounds(d, MOCK_SITE_TZ)[0] + h0 * HOUR), iso(dayBounds(d, MOCK_SITE_TZ)[0] + h1 * HOUR)]);
    const lanes = [
      { source: b.source, content: b.content, occurrences: occ(12, 14), effective_ratio: 1 },
      { source: a.source, content: a.content, occurrences: occ(8, 18), effective_ratio: 0.8 },
    ];
    const expiryDay = days[1] ?? days[0]!;
    const markers = [{ at: iso(dayBounds(expiryDay, MOCK_SITE_TZ)[0] + 16 * HOUR), kind: "playlist_expires", ref_id: a.content.id, name: a.content.name, impact: "gap" }];
    const conflicts = [
      { kind: "content_expires", severity: "warning", source: a.source, other: null, first_at: markers[0]!.at, occurrences: 1 },
      { kind: "partially_masked", severity: "info", source: a.source, other: b.source, first_at: iso(dayBounds(days[0]!, MOCK_SITE_TZ)[0] + 12 * HOUR), occurrences: days.length },
    ];
    const page = list.slice(offset, offset + limit);
    return {
      body: {
        format: 1,
        target_type: type,
        target_id: id,
        window: [iso(dayBounds(days[0]!, MOCK_SITE_TZ)[0]), iso(dayBounds(days.at(-1)!, MOCK_SITE_TZ)[1])],
        timezone: MOCK_SITE_TZ,
        mixed_timezones: false,
        lanes,
        screens: page.map((sc) => ({ screen_id: sc.id, screen_name: sc.name, timezone: MOCK_SITE_TZ, segments: days.flatMap(day), markers, conflicts })),
        next_cursor: offset + limit < list.length ? String(offset + limit) : null,
        computed_at: new Date().toISOString(),
      },
    };
  };

  const handle = async (req: IncomingMessage, res: ServerResponse, route: string, query: URLSearchParams): Promise<boolean> => {
    const simulate = req.method === "POST" && route === "/planning/simulate";
    if (!simulate && (req.method !== "GET" || (route !== "/planning/timeline" && route !== "/planning/at"))) return false;
    const send = (status: number, payload: unknown) => {
      res.statusCode = status;
      res.setHeader("Content-Type", "application/json");
      res.setHeader("Cache-Control", "private, no-store");
      res.end(JSON.stringify(payload));
    };
    if (simulate) {
      const chunks: Buffer[] = [];
      for await (const c of req) chunks.push(c as Buffer);
      interface Draft {
        name?: string;
        playlist_id?: string;
        priority?: number;
        rules?: { weekdays: number; start: string; end: string }[];
        starts_on?: string | null;
        ends_on?: string | null;
      }
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}") as { draft?: Draft; edit?: [string, Draft]; target_type?: string; target_id?: string; from?: string; to?: string };
      const draft = body.draft ?? body.edit?.[1] ?? null;
      if (!draft || !body.target_type || !body.target_id || !body.from || !body.to) return send(422, { code: "errors.validation", params: {} }), true;
      if (draft.playlist_id !== undefined && !world.playlists.some((p) => p.id === draft.playlist_id)) return send(422, { code: "errors.not_found", params: {} }), true;
      const scope = scopeScreens(body.target_type, body.target_id);
      const pl = world.playlists.find((p) => p.id === draft.playlist_id);
      const content = { kind: "playlist", id: pl?.id ?? demoPlaylist, name: pl?.name ?? "Playlist" };
      const draftSource = { kind: "draft", id: null, name: String(draft.name ?? ""), target_type: body.target_type, target_id: body.target_id, target_name: null, inherited: false, priority: Number(draft.priority ?? 0), priority_level: level(Number(draft.priority ?? 0)) };
      const rules = (draft.rules as { weekdays: number; start: string; end: string }[] | undefined)?.length ? (draft.rules as { weekdays: number; start: string; end: string }[]) : [{ weekdays: 127, start: "00:00", end: "23:59" }];
      const changes: unknown[] = [];
      const afterDays: string[] = [];
      for (let d = String(body.from), n = 0; d <= String(body.to) && n < 14; d = addDays(d, 1), n++) {
        afterDays.push(d);
        const dow = (new Date(`${d}T12:00:00Z`).getUTCDay() + 6) % 7;
        for (const r of rules) {
          if (!(r.weekdays & (1 << dow))) continue;
          if ((draft.starts_on && d < draft.starts_on) || (draft.ends_on && d > draft.ends_on)) continue;
          const start = zonedToUtc(d, r.start, MOCK_SITE_TZ);
          const end = zonedToUtc(d, r.end, MOCK_SITE_TZ);
          const before = day(d).find((sg) => Date.parse(sg.start) <= start && start < Date.parse(sg.end)) ?? day(d)[0]!;
          const after = { start: iso(start), end: iso(end), state: "play", content, source: draftSource, reason: "higher_priority", losers: [] };
          for (const sc of scope) changes.push({ screen_id: sc.id, start: iso(start), end: iso(end), before, after });
        }
      }
      const first = timelineFor(body.target_type, body.target_id, body.from, body.to, scope, 0, 100);
      return send(200, { after: first.body, changes, new_conflicts: draft.priority === 100 ? [] : [{ kind: "partially_masked", severity: "info", source: draftSource, other: sources()[0]!.source, first_at: iso(Date.now()), occurrences: 1 }], resolved_conflicts: [], screens_touched: changes.length ? scope.length : 0 }), true;
    }
    const type = query.get("target_type") ?? "";
    const id = query.get("target_id") ?? "";
    const list = scopeScreens(type, id);
    if (!type || !id || (type !== "organization" && list.length === 0)) return send(404, { code: "errors.not_found", params: {} }), true;

    if (route === "/planning/at") {
      const atMs = Date.parse(query.get("at") ?? "") || Date.now();
      const date = dateKey(wallClock(atMs, MOCK_SITE_TZ));
      const segs = day(date);
      const next = day(addDays(date, 1));
      const cur = segs.find((s) => Date.parse(s.start) <= atMs && atMs < Date.parse(s.end)) ?? segs[0]!;
      const following = [...segs, ...next].find((s) => Date.parse(s.start) >= Date.parse(cur.end));
      return send(200, {
        at: iso(atMs),
        next_cursor: null,
        screens: list.map((s) => {
          const forced = forcedOn(s.id);
          if (!forced) return { screen_id: s.id, screen_name: s.name, segment: cur, next_change_at: following ? following.start : null, presence: "online" };
          const src = { kind: "override", id: forced.id, name: forced.name, target_type: "screen", target_id: s.id, target_name: s.name, inherited: false, priority: 0, priority_level: "normal" };
          const content = { kind: "playlist", id: forced.playlist_id ?? demoPlaylist, name: forced.name };
          return { screen_id: s.id, screen_name: s.name, segment: { start: iso(atMs), end: forced.ends_at, state: "overridden", content, source: src, reason: "override_active", losers: [] }, next_change_at: forced.ends_at, presence: "online" };
        }),
      }), true;
    }

    const offset = Number(query.get("cursor") ?? 0) || 0;
    const limit = Math.min(Number(query.get("limit") ?? 50) || 50, 100);
    return send(200, timelineFor(type, id, query.get("from") ?? todayIn(MOCK_SITE_TZ), query.get("to") ?? query.get("from") ?? todayIn(MOCK_SITE_TZ), list, offset, limit).body), true;
  };
  return handle;
}
