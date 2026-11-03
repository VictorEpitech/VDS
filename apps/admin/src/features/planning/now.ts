import { useQueries } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import { useSites, type ScreenPlayback } from "../../lib/queries";
import type { PlanningAt } from "./planning";

export type NowScreen = PlanningAt["screens"][number];

/** Présentation seule : la forme historique `ScreenPlayback` à partir de ce que le serveur résout (`/planning/at`). */
export function toPlayback(a: NowScreen): ScreenPlayback {
  const { segment } = a;
  const src = segment.source;
  const playing = segment.state === "play" || segment.state === "overridden";
  const source = !playing ? "none" : src.kind === "override" ? "override" : src.kind === "schedule" ? "schedule" : "none";
  return {
    screen_id: a.screen_id,
    source,
    override_id: source === "override" ? src.id : null,
    override_name: source === "override" ? src.name : null,
    schedule_id: source === "schedule" ? src.id : null,
    schedule_name: source === "schedule" ? src.name : null,
    playlist_id: segment.content?.id ?? null,
    playlist_name: segment.content?.name ?? null,
    until: source === "none" ? null : segment.end,
    next_change_at: a.next_change_at,
  };
}

/**
 * Ce qui joue maintenant sur chaque écran, un appel `/planning/at` par site (pagination suivie, 100 écrans par page).
 * Aucune résolution côté admin : le serveur répond, ces hooks regroupent seulement.
 */
export function useNowAll() {
  const api = useApi();
  const sites = useSites();
  const live = (sites.data ?? []).filter((s) => !s.archived_at);
  return useQueries({
    queries: live.map((s) => ({
      queryKey: ["planning", "now", s.id] as const,
      refetchInterval: 60_000,
      queryFn: async (): Promise<NowScreen[]> => {
        const out: NowScreen[] = [];
        let cursor: string | null = null;
        for (let page = 0; page < 20; page++) {
          const r: PlanningAt = await api.get<PlanningAt>(`/planning/at?target_type=site&target_id=${s.id}&limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
          out.push(...r.screens);
          if (!r.next_cursor) break;
          cursor = r.next_cursor;
        }
        return out;
      },
    })),
    combine: (results) => ({
      data: sites.isSuccess ? results.flatMap((r) => r.data ?? []) : undefined,
      isPending: sites.isPending || results.some((r) => r.isPending),
      isError: sites.isError || results.some((r) => r.isError),
      error: sites.error ?? results.find((r) => r.error)?.error ?? null,
    }),
  });
}

/** Programmation active de chaque écran (forme `ScreenPlayback`), alimentée par `/planning/at`. */
export function usePlayback() {
  const all = useNowAll();
  return { ...all, data: all.data?.map(toPlayback) };
}
