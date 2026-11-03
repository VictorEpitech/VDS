/**
 * Simule GET /planning/timeline et GET /planning/at pour le dev et les e2e (le vrai résolveur est dans apps/server).
 * Données de démonstration déterministes dérivées des programmations existantes : ce n'est pas un résolveur,
 * il pose simplement un jeu de segments plausible (une source de fond, une source prioritaire de 12 h à 14 h).
 */
import { randomUUID } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";
import { addDays, dayBounds, todayIn, wallClock, dateKey } from "./src/features/planning/time";

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

export function createPlanningMock(world: World) {
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

  const handle = async (req: IncomingMessage, res: ServerResponse, route: string, query: URLSearchParams): Promise<boolean> => {
    if (req.method !== "GET" || (route !== "/planning/timeline" && route !== "/planning/at")) return false;
    const send = (status: number, payload: unknown) => {
      res.statusCode = status;
      res.setHeader("Content-Type", "application/json");
      res.setHeader("Cache-Control", "private, no-store");
      res.end(JSON.stringify(payload));
    };
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
        screens: list.map((s) => ({ screen_id: s.id, screen_name: s.name, segment: cur, next_change_at: following ? following.start : null, presence: "online" })),
      }), true;
    }

    const from = query.get("from") ?? todayIn(MOCK_SITE_TZ);
    const to = query.get("to") ?? from;
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
    return send(200, {
      format: 1,
      target_type: type,
      target_id: id,
      window: [iso(dayBounds(days[0]!, MOCK_SITE_TZ)[0]), iso(dayBounds(days.at(-1)!, MOCK_SITE_TZ)[1])],
      timezone: MOCK_SITE_TZ,
      mixed_timezones: false,
      lanes,
      screens: list.map((s) => ({ screen_id: s.id, screen_name: s.name, timezone: MOCK_SITE_TZ, segments: days.flatMap(day), markers, conflicts })),
      next_cursor: null,
      computed_at: new Date().toISOString(),
    }), true;
  };
  return handle;
}
