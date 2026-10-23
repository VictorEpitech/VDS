import { useTranslation } from "react-i18next";
import { Icon, type IconName } from "./Icon";

export type ScreenState = "online" | "scheduled_off" | "offline" | "power_failed" | "overridden" | "pairing";

export type DevicePresence = "online" | "offline" | "unseen" | "revoked";

const PRESENCE_BADGE: Record<DevicePresence, { state: ScreenState | "revoked"; icon: IconName }> = {
  online: { state: "online", icon: "online" },
  offline: { state: "offline", icon: "offline" },
  unseen: { state: "pairing", icon: "pairing" },
  revoked: { state: "revoked", icon: "revoke" },
};

/** Présence d'un appareil appairé : couleur + icône + libellé. */
export function PresenceBadge({ presence }: { presence: DevicePresence }) {
  const { t } = useTranslation();
  const { state, icon } = PRESENCE_BADGE[presence];
  return <StatusBadge state={state} icon={icon} label={t(`screens.device.presence.${presence}`)} />;
}

/** État = couleur + icône + libellé, jamais la couleur seule. */
export function StatusBadge({ state, label, icon }: { state: ScreenState | "revoked"; label?: string; icon?: IconName }) {
  const { t } = useTranslation();
  const name: IconName = icon ?? (state === "revoked" ? "revoke" : state);
  return (
    <span className="status-badge" data-state={state}>
      <Icon name={name} />
      <span>{label ?? (state === "revoked" ? t("screens.device.presence.revoked") : t(`states.${state}`))}</span>
    </span>
  );
}
