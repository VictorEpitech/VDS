import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import type { components } from "@vds/sdk-ts";
import { useEffect, useState } from "react";
import { useApi } from "../../lib/api";
import type { Group, Media, Screen } from "../../lib/queries";

type S = components["schemas"];
export type Override = S["OverrideOut"];
export type OverrideCreate = S["OverrideCreate"];
export type OverridePreview = S["OverridePreviewOut"];
export type OverrideTargetType = OverrideCreate["target_type"];

export const overrideKeys = { active: ["overrides", "active"] as const };

/** Durée d'une prolongation rapide. */
export const EXTEND_MINUTES = 60;
/** Valeurs de repli si la configuration résolue (`core.override.*`) n'est pas lisible par l'utilisateur. */
export const FALLBACK_PRESETS: readonly (number | "end_of_day")[] = [15, 60, 240, "end_of_day"];
export const FALLBACK_DEFAULT_MINUTES = 60;

/** Seuls l'administrateur et l'éditeur peuvent forcer ; l'extinction forcée est réservée à l'administrateur. */
export const canForce = (role: string | undefined): boolean => role === "admin" || role === "editor";

/** Forçages à venir ou en cours (le serveur est la seule source : aucune résolution dans le front). */
export function useOverrides(enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: overrideKeys.active,
    queryFn: () => api.get<Override[]>("/overrides?active=true"),
    enabled,
    refetchInterval: 30_000,
  });
}

export function refreshAfterOverride(qc: QueryClient): Promise<unknown> {
  return Promise.all([
    qc.invalidateQueries({ queryKey: ["overrides"] }),
    qc.invalidateQueries({ queryKey: ["fleet"] }),
    qc.invalidateQueries({ queryKey: ["planning"] }),
    qc.invalidateQueries({ queryKey: ["power", "screen"] }),
  ]);
}

export function useStopOverride() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (o: Override) => api.post<Override>(`/overrides/${o.id}/stop`),
    onSuccess: () => refreshAfterOverride(qc),
  });
}

export function useExtendOverride() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (o: Override) => api.patch<Override>(`/overrides/${o.id}`, { ends_at: new Date(new Date(o.ends_at).getTime() + EXTEND_MINUTES * 60_000).toISOString() }),
    onSuccess: () => refreshAfterOverride(qc),
  });
}

/** Écrans touchés par un forçage, d'après les listes déjà chargées. */
export function screensOf(o: Pick<Override, "target_type" | "target_id">, screens: readonly Screen[]): Screen[] {
  const live = screens.filter((s) => !s.archived_at);
  switch (o.target_type) {
    case "screen":
      return live.filter((s) => s.id === o.target_id);
    case "group":
      return live.filter((s) => s.group_id === o.target_id);
    case "site":
      return live.filter((s) => s.site_id === o.target_id);
    default:
      return live;
  }
}

/** Écrans hors ligne : un arrêt anticipé ne les atteint pas, ils affichent jusqu'à la fin prévue. */
export const offlineOf = (screens: readonly Screen[]): Screen[] => screens.filter((s) => s.device?.presence === "offline");

/** Contenu d'un média pour « forcer un média » (playlist éphémère d'un élément côté serveur). */
export function mediaContent(m: Pick<Media, "id" | "kind">): NonNullable<OverrideCreate["content"]> {
  const type = m.kind === "image" ? "content.image" : m.kind === "video" ? "content.video" : "content.pdf";
  return { content_type: type, props: { media: m.id } };
}

/** Groupe dont la sélection couvre exactement tous les écrans (sinon null). */
export function wholeGroup(selected: readonly Screen[], all: readonly Screen[], groups: readonly Group[]): Group | null {
  const gid = selected[0]?.group_id;
  if (!gid || selected.some((s) => s.group_id !== gid)) return null;
  const members = all.filter((s) => !s.archived_at && s.group_id === gid);
  return members.length === selected.length ? (groups.find((g) => g.id === gid) ?? null) : null;
}

/** Heure courante rafraîchie à la minute (fin de forçage affichée sans bruit pour les lecteurs d'écran). */
export function useMinuteNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(id);
  }, []);
  return now;
}

/** Minutes restantes (arrondi supérieur, jamais négatif). */
export const minutesLeft = (endsAt: string, now: Date): number => Math.max(0, Math.ceil((new Date(endsAt).getTime() - now.getTime()) / 60_000));
