import { useQuery } from "@tanstack/react-query";
import type { components } from "@vds/sdk-ts";
import { useApi } from "./api";

type S = components["schemas"];
export type Screen = S["ScreenOut"];
export type Site = S["SiteOut"];
export type Group = S["GroupOut"];
export type Media = S["MediaOut"];
export type Playlist = S["PlaylistOut"];
export type PlaylistDetail = S["PlaylistDetailOut"];
export type PlaylistItem = S["ItemOut"];
export type Schedule = S["ScheduleOut"];
export type Rule = S["RuleIn"];

export const keys = {
  screens: ["screens"] as const,
  sites: ["sites"] as const,
  groups: (siteId: string) => ["groups", siteId] as const,
  media: ["media"] as const,
  playlists: ["playlists"] as const,
  playlist: (id: string) => ["playlists", id] as const,
  schedules: ["schedules"] as const,
};

export function useScreens() {
  const api = useApi();
  return useQuery({ queryKey: keys.screens, queryFn: () => api.get<Screen[]>("/screens") });
}

export function useSites() {
  const api = useApi();
  return useQuery({ queryKey: keys.sites, queryFn: () => api.get<Site[]>("/sites") });
}

/** Groupes de tous les sites. */
export function useAllGroups() {
  const api = useApi();
  const sites = useSites();
  return useQuery({
    queryKey: ["groups", "all", sites.data?.map((s) => s.id)],
    enabled: sites.isSuccess,
    queryFn: async () => (await Promise.all((sites.data ?? []).map((s) => api.get<Group[]>(`/sites/${s.id}/groups`)))).flat(),
  });
}

/** Médiathèque ; interroge à intervalle tant qu'un média est en traitement. */
export function useMedia() {
  const api = useApi();
  return useQuery({
    queryKey: keys.media,
    queryFn: () => api.get<Media[]>("/media?limit=200"),
    refetchInterval: (q) => (q.state.data?.some((m) => m.status === "processing") ? 1500 : false),
  });
}

export function usePlaylists() {
  const api = useApi();
  return useQuery({ queryKey: keys.playlists, queryFn: () => api.get<Playlist[]>("/playlists") });
}

export function usePlaylist(id: string) {
  const api = useApi();
  return useQuery({ queryKey: keys.playlist(id), queryFn: () => api.get<PlaylistDetail>(`/playlists/${id}`) });
}

export function useSchedules() {
  const api = useApi();
  return useQuery({ queryKey: keys.schedules, queryFn: () => api.get<Schedule[]>("/schedules") });
}
