/** Icône sémantique : le module de thème actif relie chaque nom à sa bibliothèque (voir theme.css). */
export const ICON_NAMES = [
  "online",
  "scheduled_off",
  "offline",
  "power_failed",
  "overridden",
  "pairing",
  "back",
  "logout",
  "user",
  "language",
  "screen",
  "warning",
  "add",
  "delete",
  "upload",
  "move_up",
  "move_down",
  "media",
  "playlist",
  "schedule",
  "revoke",
  "processing",
  "failed",
  "site",
  "agent",
  "power",
  "incident",
  "check",
  "rotate",
  "calendar",
  "mail",
  "inherit",
  "detect",
  "copy",
] as const;
export type IconName = (typeof ICON_NAMES)[number];

export function Icon({ name }: { name: IconName }) {
  return <span className="vds-icon" data-icon={name} aria-hidden="true" />;
}
