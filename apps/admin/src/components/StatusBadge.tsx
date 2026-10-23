import { useTranslation } from "react-i18next";
import { Icon, type IconName } from "./Icon";

export type ScreenState = "online" | "scheduled_off" | "offline" | "power_failed" | "overridden" | "pairing";

/** État = couleur + icône + libellé, jamais la couleur seule. */
export function StatusBadge({ state, label }: { state: ScreenState; label?: string }) {
  const { t } = useTranslation();
  const icon: IconName = state;
  return (
    <span className="status-badge" data-state={state}>
      <Icon name={icon} />
      <span>{label ?? t(`states.${state}`)}</span>
    </span>
  );
}
