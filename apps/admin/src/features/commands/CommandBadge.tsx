import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import { STATUS_VIEW, type PlayerCommandStatus } from "./commands";

/** Statut d'une commande : couleur + icône + libellé (réutilise les teintes d'état d'écran). */
export function CommandBadge({ status }: { status: PlayerCommandStatus }) {
  const { t } = useTranslation();
  const v = STATUS_VIEW[status];
  return (
    <span className="status-badge" data-state={v.state} data-command-status={status}>
      <Icon name={v.icon} />
      <span>{t(`screens.commands.status.${status}`)}</span>
    </span>
  );
}
