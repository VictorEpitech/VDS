import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@vds/sdk-ts";
import { useApi } from "../../lib/api";
import { keys } from "../../lib/queries";
import { layoutKeys } from "../layouts/layouts";
import { useScope } from "../scope/scope";

type S = components["schemas"];
export type Publication = S["PublicationOut"];
export type PublicationType = Publication["entity_type"];
export type PublicationState = Publication["state"];
export type EntityRef = S["EntityRef"];

export const publicationKeys = {
  all: ["publications"] as const,
  list: (type: PublicationType | "all", state: PublicationState | "all") => ["publications", "list", type, state] as const,
  one: (type: PublicationType, id: string) => ["publications", type, id] as const,
};

/** Présentation d'un état : « jamais publié » et « modifications non publiées » sont deux brouillons. */
export type PublicationView = "never" | "changed" | "in_review" | "published";

export function viewOf(p: Pick<Publication, "state" | "published_rev">): PublicationView {
  if (p.state === "in_review") return "in_review";
  if (p.state === "published") return "published";
  return p.published_rev == null ? "never" : "changed";
}

export function usePublication(type: PublicationType, id: string) {
  const api = useApi();
  return useQuery({ queryKey: publicationKeys.one(type, id), enabled: id !== "", retry: false, queryFn: () => api.get<Publication>(`/publications/${type}/${id}`) });
}

/** Publications d'un type (ou en revue) : alimente les badges des listes en une seule requête. */
export function usePublicationList(opts: { type?: PublicationType; state?: PublicationState; enabled?: boolean } = {}) {
  const api = useApi();
  const qs = new URLSearchParams({ limit: "500" });
  if (opts.type) qs.set("entity_type", opts.type);
  if (opts.state) qs.set("state", opts.state);
  return useQuery({
    queryKey: publicationKeys.list(opts.type ?? "all", opts.state ?? "all"),
    enabled: opts.enabled ?? true,
    retry: false,
    queryFn: () => api.get<Publication[]>(`/publications?${qs.toString()}`),
  });
}

/** Index par identifiant, pour les listes. */
export function usePublicationIndex(type: PublicationType): Map<string, Publication> {
  const list = usePublicationList({ type });
  return new Map((list.data ?? []).map((p) => [p.entity_id, p]));
}

export function useIsAdmin(): boolean {
  return useScope().isAdmin;
}

/** Recharge tout ce qu'une transition de publication peut changer. */
export function useRefreshPublications() {
  const qc = useQueryClient();
  return () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: publicationKeys.all }),
      qc.invalidateQueries({ queryKey: keys.playlists }),
      qc.invalidateQueries({ queryKey: keys.schedules }),
      qc.invalidateQueries({ queryKey: layoutKeys.all }),
    ]);
}

export function usePublicationActions(type: PublicationType, id: string) {
  const api = useApi();
  const refresh = useRefreshPublications();
  const base = `/publications/${type}/${id}`;
  const submit = useMutation({ mutationFn: (comment?: string) => api.post<Publication>(`${base}/submit`, comment ? { comment } : {}), onSuccess: refresh });
  const withdraw = useMutation({ mutationFn: () => api.post<Publication>(`${base}/withdraw`, {}), onSuccess: refresh });
  const approve = useMutation({
    mutationFn: (v: { expected_rev: number; include_dependencies?: boolean }) => api.post<Publication>(`${base}/approve`, v),
    onSuccess: refresh,
  });
  const reject = useMutation({ mutationFn: (comment: string) => api.post<Publication>(`${base}/reject`, { comment }), onSuccess: refresh });
  return { submit, withdraw, approve, reject };
}
