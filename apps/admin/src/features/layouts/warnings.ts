import { useQueries } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import { useConfig, useOrganization, keys, type PlaylistDetail } from "../../lib/queries";

export const MAX_EXCLUSIVE_KEY = "player.max_exclusive_elements";

/** Zones dont la playlist contient une vidéo active (un décodeur vidéo sur la plupart des TV). */
export function videoZoneKeys(zones: readonly { key: string; playlist_id: string | null }[], details: ReadonlyMap<string, PlaylistDetail>): string[] {
  return zones.filter((z) => z.playlist_id && details.get(z.playlist_id)?.items.some((i) => i.enabled && i.content_type === "content.video")).map((z) => z.key);
}

/** `player.max_exclusive_elements` résolu pour l'organisation (repli : 1, le défaut du noyau). */
export function useMaxExclusive(): number {
  const org = useOrganization();
  const cfg = useConfig("organization", org.data?.id ?? "", "core", !!org.data);
  const v = cfg.data?.values[MAX_EXCLUSIVE_KEY]?.value;
  return typeof v === "number" && v >= 1 ? v : 1;
}

/** Clés des zones à vidéo et limite : avertissement actionnable quand il y a plus de vidéos que la limite. */
export function useVideoWarning(zones: readonly { key: string; playlist_id: string | null }[]): { videoZones: string[]; max: number; exceeded: boolean } {
  const api = useApi();
  const ids = [...new Set(zones.map((z) => z.playlist_id).filter((x): x is string => !!x))];
  const results = useQueries({ queries: ids.map((id) => ({ queryKey: keys.playlist(id), queryFn: () => api.get<PlaylistDetail>(`/playlists/${id}`) })) });
  const details = new Map<string, PlaylistDetail>();
  results.forEach((r, i) => r.data && details.set(ids[i]!, r.data));
  const max = useMaxExclusive();
  const videoZones = videoZoneKeys(zones, details);
  return { videoZones, max, exceeded: videoZones.length > max };
}

/** `core.layout.max_zones` résolu (repli : 12, le défaut du noyau). */
export function useMaxZones(): number {
  const org = useOrganization();
  const cfg = useConfig("organization", org.data?.id ?? "", "core", !!org.data);
  const v = cfg.data?.values["layout.max_zones"]?.value;
  return typeof v === "number" && v >= 1 ? v : 12;
}
