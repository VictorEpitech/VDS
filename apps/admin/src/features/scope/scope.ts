import { useQuery } from "@tanstack/react-query";
import type { components, Me } from "@vds/sdk-ts";
import { useTranslation } from "react-i18next";
import { useSession } from "../../auth/session";
import { useApi } from "../../lib/api";
import { useAllGroups, useSites, type Group, type Screen, type Site } from "../../lib/queries";

type S = components["schemas"];
export type ScopeTree = S["ScopeTree"];
export type NodeRef = S["NodeRef"];
export type ScopeNode = S["ScopeNodeOut"];
export type UserOut = S["UserOut"];
export type UserPage = S["UserPage"];

export const SCOPE_TREE_KEY = ["scope-tree"] as const;
export const usersKey = ["users"] as const;

/** Arbre sites -> groupes visibles pour l'utilisateur courant, avec `writable` par nœud (alimente propriétaire et cibles). */
export function useScopeTree() {
  const api = useApi();
  const session = useSession();
  return useQuery({
    queryKey: SCOPE_TREE_KEY,
    enabled: session.isSuccess,
    retry: false,
    staleTime: 60_000,
    queryFn: () => api.get<ScopeTree>("/scope-tree"),
    // Réponse inattendue (serveur ancien, proxy) : arbre inconnu, le serveur reste l'autorité.
    select: (tree) => (Array.isArray(tree.sites) ? tree : (undefined as unknown as ScopeTree)),
  });
}

export interface ScopeInfo {
  ready: boolean;
  isAdmin: boolean;
  /** Éditeur dont le périmètre n'est pas toute l'organisation. */
  restricted: boolean;
  /** Éditeur sans aucun nœud : rien n'est visible tant qu'un admin n'a pas attribué de périmètre. */
  empty: boolean;
  canShare: boolean;
  tree: ScopeTree | undefined;
}

export function useScope(): ScopeInfo {
  const session = useSession();
  const tree = useScopeTree();
  const me = session.data;
  const isAdmin = me?.role === "admin";
  // Défensif : une réponse sans périmètre (serveur ancien) est lue comme « toute l'organisation ».
  const perimeter = me?.perimeter as Me["perimeter"] | undefined;
  const unrestricted = isAdmin || (perimeter?.unrestricted ?? true);
  return {
    ready: session.isSuccess,
    isAdmin,
    restricted: !unrestricted,
    empty: !unrestricted && (perimeter?.nodes.length ?? 1) === 0,
    canShare: tree.data?.can_share ?? perimeter?.can_share ?? unrestricted,
    tree: tree.data,
  };
}

/** `null` = contenu partagé (organisation). */
export type Owner = NodeRef | null | undefined;
/** Propriétaire absent ou nœud organisation : contenu partagé. */
export const isShared = (o: Owner): o is null | undefined => !o || o.type === "organization";
export const ownerKey = (o: Owner): string => (isShared(o) ? "shared" : `${o!.type}:${o!.id}`);

export interface OwnerOption {
  key: string;
  owner: NodeRef | null;
  label: string;
}

/** Choix de propriétaire : « Partagé » seulement si `can_share`, puis sites et groupes modifiables. */
export function ownerOptions(tree: ScopeTree | undefined, sharedLabel: string): OwnerOption[] {
  if (!tree) return [];
  const out: OwnerOption[] = [];
  if (tree.can_share) out.push({ key: "shared", owner: null, label: sharedLabel });
  for (const s of tree.sites) {
    if (s.writable) out.push({ key: `site:${s.id}`, owner: { type: "site", id: s.id }, label: s.name });
    for (const g of s.groups) if (g.writable) out.push({ key: `group:${g.id}`, owner: { type: "group", id: g.id }, label: `${s.name} / ${g.name}` });
  }
  return out;
}

/** Nom du propriétaire d'après l'arbre ; `null` si le nœud n'y figure pas. */
export function ownerName(tree: ScopeTree | undefined, o: NodeRef): string | null {
  for (const s of tree?.sites ?? []) {
    if (o.type === "site" && s.id === o.id) return s.name;
    for (const g of s.groups) if (o.type === "group" && g.id === o.id) return `${s.name} / ${g.name}`;
  }
  return null;
}

/** Libellé du propriétaire (« Partagé » ou nom du site/groupe). */
export function useOwnerLabel(): (o: Owner) => string {
  const { t } = useTranslation();
  const { tree } = useScope();
  return (o) => (isShared(o) ? t("scope.owner.shared") : (ownerName(tree, o) ?? t(`scope.owner.generic.${o.type}`)));
}

/** Droit d'écriture sur un objet d'après son propriétaire ; permissif tant que l'arbre est inconnu (le serveur tranche). */
export function useCanWrite(): (o: Owner) => boolean {
  const { tree } = useScope();
  return (o) => {
    if (!tree) return true;
    if (isShared(o)) return tree.can_share;
    for (const s of tree.sites) {
      if (o.type === "site" && s.id === o.id) return s.writable;
      for (const g of s.groups) if (o.type === "group" && g.id === o.id) return g.writable;
    }
    return false;
  };
}

/** Sites, groupes et écrans proposés comme cibles : seuls les nœuds modifiables (tous si l'arbre est indisponible). */
export function useWritableTargets() {
  const sites = useSites();
  const groups = useAllGroups();
  const { tree } = useScope();
  if (!tree) return { sites: sites.data ?? [], groups: groups.data ?? [], screens: (s: readonly Screen[]): Screen[] => [...s] };
  const siteOk = new Set(tree.sites.filter((s) => s.writable).map((s) => s.id));
  const groupOk = new Set(tree.sites.flatMap((s) => s.groups.filter((g) => g.writable).map((g) => g.id)));
  return {
    sites: (sites.data ?? []).filter((s: Site) => siteOk.has(s.id)),
    groups: (groups.data ?? []).filter((g: Group) => groupOk.has(g.id)),
    screens: (list: readonly Screen[]): Screen[] => list.filter((s) => (s.group_id ? groupOk.has(s.group_id) : siteOk.has(s.site_id))),
  };
}
