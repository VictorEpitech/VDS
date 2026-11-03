/** vds-core 1.10.0 (additif) : droits par périmètre. Nœud de périmètre / propriétaire de contenu. */
export type NodeType = "organization" | "site" | "group";
export interface NodeRef { type: NodeType; id: string }

/** Payloads `version: 1` du domaine `user` (bus du noyau). */
export interface UserScopeChangedV1 {
  version: 1;
  user_id: string;
  scopes: NodeRef[];
  actor_id?: string | null;
}
export interface UserRoleChangedV1 {
  version: 1;
  user_id: string;
  from_role: string;
  to_role: string;
  source: "vds" | "provider";
}
