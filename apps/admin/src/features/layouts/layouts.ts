import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@vds/sdk-ts";
import { useApi } from "../../lib/api";

type S = components["schemas"];
export type Layout = S["LayoutOut"];
export type LayoutSummary = S["LayoutSummary"];
export type LayoutIn = S["LayoutIn"];
export type LayoutZone = S["LayoutZoneOut"];
export type LayoutZoneIn = S["LayoutZoneIn"];
export type LayoutUsage = S["LayoutUsageOut"];
export type Orientation = Layout["orientation"];

export const layoutKeys = {
  all: ["layouts"] as const,
  one: (id: string) => ["layouts", id] as const,
  usage: (id: string) => ["layouts", id, "usage"] as const,
};

/** Borne par défaut de `core.layout.max_zones` (le serveur reste l'autorité). */
export const DEFAULT_MAX_ZONES = 12;

export function useLayoutPage(params: { q?: string; orientation?: Orientation | "" }) {
  const api = useApi();
  const qs = new URLSearchParams({ limit: "100" });
  if (params.q?.trim()) qs.set("q", params.q.trim());
  if (params.orientation) qs.set("orientation", params.orientation);
  return useQuery({ queryKey: [...layoutKeys.all, "page", params] as const, queryFn: () => api.get<S["LayoutPage"]>(`/layouts?${qs.toString()}`), placeholderData: (p) => p });
}

/** Toutes les mises en page, pour les sélecteurs (programmation, forçage). */
export function useLayouts() {
  const api = useApi();
  return useQuery({ queryKey: [...layoutKeys.all, "all"] as const, queryFn: () => api.get<S["LayoutPage"]>("/layouts?limit=100") });
}

export function useLayout(id: string) {
  const api = useApi();
  return useQuery({ queryKey: layoutKeys.one(id), queryFn: () => api.get<Layout>(`/layouts/${id}`) });
}

export function useLayoutUsage(id: string) {
  const api = useApi();
  return useQuery({ queryKey: layoutKeys.usage(id), queryFn: () => api.get<LayoutUsage>(`/layouts/${id}/usage`) });
}

export function useCreateLayout() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LayoutIn) => api.post<Layout>("/layouts", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: layoutKeys.all }),
  });
}

/** Remplacement complet, conditionné par la révision lue (`If-Match`). */
export function useSaveLayout(id: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ body, revision }: { body: LayoutIn; revision: number }) => api.put<Layout>(`/layouts/${id}`, body, { "If-Match": String(revision) }),
    onSuccess: (saved) => {
      qc.setQueryData(layoutKeys.one(id), saved);
      return qc.invalidateQueries({ queryKey: layoutKeys.all });
    },
  });
}

export function useDuplicateLayout() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<Layout>(`/layouts/${id}/duplicate`),
    onSuccess: () => qc.invalidateQueries({ queryKey: layoutKeys.all }),
  });
}

export function useDeleteLayout() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/layouts/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: layoutKeys.all }),
  });
}

/** Zone d'édition : le serveur renvoie `LayoutZoneOut`, la mise à jour envoie `LayoutZoneIn` (mêmes champs). */
export const toIn = (z: LayoutZone): LayoutZoneIn => ({ ...z });

export function toLayoutIn(l: Pick<Layout, "name" | "orientation" | "background_color" | "background_media_id" | "tags"> & { owner?: Layout["owner"] }, zones: readonly LayoutZone[]): LayoutIn {
  // PUT remplace tout : le propriétaire est renvoyé tel quel, sinon il retomberait sur le défaut du serveur.
  return { name: l.name, orientation: l.orientation, background_color: l.background_color, background_media_id: l.background_media_id, tags: l.tags, zones: zones.map(toIn), ...(l.owner === undefined ? {} : { owner: l.owner }) };
}

// --- Modèles : constantes de l'admin, simples préremplissages (le serveur ne connaît que des mises en page) ---

export const TEMPLATE_IDS = ["full", "two_columns", "two_rows", "bottom_banner", "l_shape", "corner_inset"] as const;
export type TemplateId = (typeof TEMPLATE_IDS)[number];

type Rect = { key: string; x: number; y: number; w: number; h: number; z: number };
/** Géométrie en fractions 0..1 ; les noms viennent de `layouts.templates.<id>.<key>`. */
export const TEMPLATES: Record<TemplateId, readonly Rect[]> = {
  full: [{ key: "main", x: 0, y: 0, w: 1, h: 1, z: 0 }],
  two_columns: [
    { key: "left", x: 0, y: 0, w: 0.5, h: 1, z: 0 },
    { key: "right", x: 0.5, y: 0, w: 0.5, h: 1, z: 0 },
  ],
  two_rows: [
    { key: "top", x: 0, y: 0, w: 1, h: 0.5, z: 0 },
    { key: "bottom", x: 0, y: 0.5, w: 1, h: 0.5, z: 0 },
  ],
  bottom_banner: [
    { key: "main", x: 0, y: 0, w: 1, h: 0.85, z: 0 },
    { key: "banner", x: 0, y: 0.85, w: 1, h: 0.15, z: 1 },
  ],
  l_shape: [
    { key: "main", x: 0, y: 0, w: 0.75, h: 0.85, z: 0 },
    { key: "side", x: 0.75, y: 0, w: 0.25, h: 0.85, z: 0 },
    { key: "banner", x: 0, y: 0.85, w: 1, h: 0.15, z: 0 },
  ],
  corner_inset: [
    { key: "main", x: 0, y: 0, w: 1, h: 1, z: 0 },
    { key: "inset", x: 0.7, y: 0.7, w: 0.27, h: 0.27, z: 1 },
  ],
};

export function templateZones(id: TemplateId, name: (key: string) => string): LayoutZoneIn[] {
  return TEMPLATES[id].map((r) => ({ ...r, name: name(r.key), fit: "contain" as const, playlist_id: null, default_item_duration_ms: null, active_from: null, active_until: null }));
}

/** Clé de zone libre (`zone-2`…) au format `^[a-z0-9_-]{1,32}$`. */
export function freeKey(zones: readonly { key: string }[]): string {
  const used = new Set(zones.map((z) => z.key));
  for (let i = zones.length + 1; ; i++) if (!used.has(`zone-${i}`)) return `zone-${i}`;
}
/** Fond de repli du player (noir), pour le sélecteur de couleur tant qu'aucun fond n'est choisi. */
export const DEFAULT_BACKGROUND = `#${"0".repeat(6)}`;
export const KEY_RE = /^[a-z0-9_-]{1,32}$/;
