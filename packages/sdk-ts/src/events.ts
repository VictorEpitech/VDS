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

/** vds-core 1.11.0 (additif) : commandes player et captures. Payloads `version: 1`. */
export type PlayerCommandName = "reload" | "restart" | "identify" | "screenshot" | "cache.resync";

export interface PlayerCommandRequestedV1 {
  version: 1;
  command_id: string;
  screen_id: string;
  name: PlayerCommandName;
  actor_kind: "user" | "module" | "system";
  batch_id: string | null;
}
export interface PlayerCommandCompletedV1 {
  version: 1;
  command_id: string;
  screen_id: string | null;
  name: PlayerCommandName;
  status: "ok" | "failed" | "expired" | "rejected" | "cancelled";
  code: string | null;
}
export interface ScreenScreenshotCapturedV1 {
  version: 1;
  screenshot_id: string;
  screen_id: string;
  trigger: "on_demand" | "periodic";
  captured_at: string;
}
