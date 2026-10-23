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

export function useScreens(opts: { refetchInterval?: number } = {}) {
  const api = useApi();
  return useQuery({ queryKey: keys.screens, queryFn: () => api.get<Screen[]>("/screens"), refetchInterval: opts.refetchInterval });
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

export type Agent = S["AgentOut"];
export type AgentWithToken = S["AgentWithToken"];
export type Command = S["CommandOut"];
export type Plan = S["PlanOut"];
export type PowerException = S["ExceptionOut"];
export type Calendar = S["CalendarOut"];
export type CalendarDay = S["CalendarDayOut"];
export type ScreenPower = S["ScreenPowerOut"];
export type Incident = S["IncidentOut"];
export type ConfigOut = S["ConfigOut"];
export type ConfigValue = S["ConfigValueOut"];
export type Origin = S["OriginOut"];
export type Organization = S["OrganizationOut"];
export type ModuleSettings = S["ModuleSettingsOut"];

export const powerKeys = {
  plans: ["power", "plans"] as const,
  exceptions: ["power", "exceptions"] as const,
  calendars: ["power", "calendars"] as const,
  days: (key: string) => ["power", "calendars", key, "days"] as const,
  screen: (id: string) => ["power", "screen", id] as const,
};
export const agentKeys = {
  site: (siteId: string) => ["agents", siteId] as const,
  commands: (siteId: string, agentId: string) => ["agents", siteId, agentId, "commands"] as const,
};
export const configKey = (level: string, id: string, ns: string) => ["config", level, id, ns] as const;

export function useAgents(siteId: string) {
  const api = useApi();
  return useQuery({
    queryKey: agentKeys.site(siteId),
    queryFn: () => api.get<Agent[]>(`/sites/${siteId}/agents`),
    refetchInterval: 15_000,
  });
}

/** Agents de tous les sites (supervision), regroupés par site. */
export function useAllAgents() {
  const api = useApi();
  const sites = useSites();
  return useQuery({
    queryKey: ["agents", "all", sites.data?.map((s) => s.id)],
    enabled: sites.isSuccess,
    refetchInterval: 15_000,
    queryFn: async () => {
      const lists = await Promise.all((sites.data ?? []).map((s) => api.get<Agent[]>(`/sites/${s.id}/agents`)));
      return lists.flat();
    },
  });
}

export function usePlans() {
  const api = useApi();
  return useQuery({ queryKey: powerKeys.plans, queryFn: () => api.get<Plan[]>("/power/plans") });
}

export function useExceptions() {
  const api = useApi();
  return useQuery({ queryKey: powerKeys.exceptions, queryFn: () => api.get<PowerException[]>("/power/exceptions") });
}

export function useCalendars() {
  const api = useApi();
  return useQuery({ queryKey: powerKeys.calendars, queryFn: () => api.get<Calendar[]>("/power/calendars") });
}

export function useIncidents(state: "open" | "resolved" | "all") {
  const api = useApi();
  return useQuery({
    queryKey: ["incidents", state],
    queryFn: () => api.get<Incident[]>(`/incidents?state=${state}&limit=100`),
    refetchInterval: 20_000,
  });
}

export function useOrganization() {
  const api = useApi();
  return useQuery({ queryKey: ["organization"], queryFn: () => api.get<Organization>("/organization") });
}

export function useConfig(level: string, id: string, ns: string, enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: configKey(level, id, ns),
    enabled: enabled && id !== "",
    queryFn: () => api.get<ConfigOut>(`/config/${level}/${id}/${ns}`),
  });
}
