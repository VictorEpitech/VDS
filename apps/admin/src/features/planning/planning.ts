import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { components } from "@vds/sdk-ts";
import { useEffect, useState } from "react";
import { useApi } from "../../lib/api";
import { dayBounds } from "./time";

type S = components["schemas"];
export type Timeline = S["TimelineOut"];
export type ScreenTimeline = S["ScreenTimeline"];
export type Segment = S["Segment"];
export type Lane = S["Lane"];
export type Marker = S["Marker"];
export type Conflict = S["Conflict"];
export type SourceRef = S["SourceRef"];
export type ContentRef = S["ContentRef"];
export type Loser = S["Loser"];
export type PlanningAt = S["AtOut"];
export type PriorityLevel = NonNullable<SourceRef["priority_level"]>;
export type ReasonCode = Segment["reason"] extends infer R ? Exclude<R, null> : never;
export type TargetType = "screen" | "group" | "site";

export interface Target {
  type: TargetType;
  id: string;
}

export const parseTarget = (raw: string | null): Target | null => {
  const m = /^(screen|group|site):([0-9a-f-]{36})$/.exec(raw ?? "");
  return m ? { type: m[1] as TargetType, id: m[2]! } : null;
};
export const targetParam = (t: Target): string => `${t.type}:${t.id}`;

export type PlanningView = "day" | "week" | "list";
export const VIEWS: readonly PlanningView[] = ["day", "week", "list"];

/** Valeurs créées par défaut pour chaque niveau (docs/architecture-planning.md §3) ; la bande reste calculée par le serveur. */
export const LEVEL_DEFAULT: Record<PriorityLevel, number> = { background: -100, normal: 0, high: 100 };
export const LEVELS: readonly PriorityLevel[] = ["background", "normal", "high"];

export const planningKeys = {
  all: ["planning"] as const,
  timeline: (t: Target, from: string, to: string) => ["planning", "timeline", targetParam(t), from, to] as const,
  at: (t: Target, at: string) => ["planning", "at", targetParam(t), at] as const,
};

export function usePlanningTimeline(target: Target | null, from: string, to: string) {
  const api = useApi();
  return useQuery({
    queryKey: target ? planningKeys.timeline(target, from, to) : ["planning", "timeline", "none"],
    queryFn: () => api.get<Timeline>(`/planning/timeline?target_type=${target!.type}&target_id=${target!.id}&from=${from}&to=${to}&explain=true`),
    enabled: target !== null,
    placeholderData: keepPreviousData,
  });
}

export function usePlanningAt(target: Target | null, atIso: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: target && atIso ? planningKeys.at(target, atIso) : ["planning", "at", "none"],
    queryFn: () => api.get<PlanningAt>(`/planning/at?target_type=${target!.type}&target_id=${target!.id}&at=${encodeURIComponent(atIso!)}`),
    enabled: target !== null && atIso !== null,
  });
}

/** Sous 600 px la vue Liste est la vue par défaut (alternative textuelle de la grille). */
export function useNarrow(): boolean {
  const q = "(max-width: 599.98px)";
  const get = () => (typeof window.matchMedia === "function" ? window.matchMedia(q).matches : false);
  const [narrow, setNarrow] = useState(get);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(q);
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return narrow;
}

/** Teinte de contenu : rotation indexée par l'identifiant (jamais seule, le nom est toujours affiché). */
export function toneOf(id: string | undefined | null): 0 | 1 | 2 {
  let h = 0;
  for (const c of id ?? "") h = (h * 31 + c.charCodeAt(0)) % 3;
  return h as 0 | 1 | 2;
}

export interface Block {
  key: string;
  startMs: number;
  endMs: number;
  /** Position dans la journée affichée, en % (présentation). */
  left: number;
  width: number;
  segment?: Segment;
  /** Occurrence d'un candidat (piste de source). */
  lane?: Lane;
  maskedBy?: string | null;
}

export interface Track {
  key: string;
  kind: "effective" | "lane";
  lane?: Lane;
  blocks: Block[];
}

export interface DayModel {
  date: string;
  startMs: number;
  endMs: number;
  tracks: Track[];
  markers: Marker[];
}

const clip = (a: number, b: number, lo: number, hi: number): [number, number] | null => (b <= lo || a >= hi ? null : [Math.max(a, lo), Math.min(b, hi)]);

/** Découpe les données du serveur par journée et par piste (regroupement et positions seulement). */
export function buildDay(date: string, tz: string, screen: ScreenTimeline | undefined, lanes: Lane[]): DayModel {
  const [lo, hi] = dayBounds(date, tz);
  const span = hi - lo;
  const pos = (a: number, b: number) => ({ left: ((a - lo) / span) * 100, width: Math.max(((b - a) / span) * 100, 0.4) });
  const segments = screen?.segments ?? [];
  const effective: Block[] = [];
  segments.forEach((s, i) => {
    const c = clip(Date.parse(s.start), Date.parse(s.end), lo, hi);
    if (c) effective.push({ key: `${date}:e-${i}`, startMs: c[0], endMs: c[1], ...pos(c[0], c[1]), segment: s });
  });
  const tracks: Track[] = [{ key: "effective", kind: "effective", blocks: effective }];
  lanes.forEach((lane, li) => {
    const blocks: Block[] = [];
    lane.occurrences.forEach(([a, b], oi) => {
      const c = clip(Date.parse(a), Date.parse(b), lo, hi);
      if (!c) return;
      // Information de présentation : qui occupe la piste « Effectif » pendant cette occurrence (donnée du serveur).
      const same = segments.some((s) => s.source.id === lane.source.id && s.source.kind === lane.source.kind && Date.parse(s.start) < c[1] && Date.parse(s.end) > c[0]);
      const at = segments.find((s) => Date.parse(s.start) <= c[0] && Date.parse(s.end) > c[0]);
      const maskedBy = same ? null : (at?.source.name ?? at?.content?.name ?? null);
      blocks.push({ key: `${date}:l${li}-${oi}`, startMs: c[0], endMs: c[1], ...pos(c[0], c[1]), lane, maskedBy });
    });
    tracks.push({ key: `lane-${lane.source.kind}-${lane.source.id ?? li}`, kind: "lane", lane, blocks });
  });
  const markers = (screen?.markers ?? []).filter((m) => (m.kind === "playlist_expires" || m.kind === "media_expires") && Date.parse(m.at) >= lo && Date.parse(m.at) < hi);
  return { date, startMs: lo, endMs: hi, tracks, markers };
}
