import { useTranslation } from "react-i18next";
import { EXPIRY_ICON, EXPIRY_TONE, expiryKind, formatDateTime } from "../lib/media-view";
import { Icon } from "./Icon";

/** « Expire le… » / « Expiré » : couleur + icône + libellé ; rien si aucune expiration. */
export function ExpiryBadge({ item }: { item: { expired: boolean; expires_at: string | null } }) {
  const { t, i18n } = useTranslation();
  const kind = expiryKind(item);
  if (kind === "none") return null;
  return (
    <span className="status-badge" data-state={EXPIRY_TONE[kind]}>
      <Icon name={EXPIRY_ICON[kind]} />
      <span>{kind === "expired" ? t("expiry.expired") : t("expiry.expires", { date: formatDateTime(item.expires_at!, i18n.language) })}</span>
    </span>
  );
}

/** « Inutilisé » : le média n'est dans aucune playlist. */
export function UnusedBadge() {
  const { t } = useTranslation();
  return (
    <span className="status-badge" data-state="revoked">
      <Icon name="media" />
      <span>{t("expiry.unused")}</span>
    </span>
  );
}
