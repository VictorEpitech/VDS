import { useTranslation } from "react-i18next";
import type { Media } from "../lib/queries";
import { Icon, type IconName } from "./Icon";

const SPEC: Record<Media["status"], { icon: IconName; tone: string }> = {
  ready: { icon: "online", tone: "online" },
  processing: { icon: "processing", tone: "pairing" },
  failed: { icon: "failed", tone: "offline" },
};

/** Statut d'un média : couleur + icône + libellé (réutilise les teintes d'état d'écran). */
export function MediaStatus({ status }: { status: Media["status"] }) {
  const { t } = useTranslation();
  const { icon, tone } = SPEC[status];
  return (
    <span className="status-badge" data-state={tone}>
      <Icon name={icon} />
      <span>{t(`media.status.${status}`)}</span>
    </span>
  );
}
