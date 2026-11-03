import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Icon } from "../../components/Icon";
import type { Marker } from "./planning";

/** Repère d'expiration sur l'axe (icône + libellé) ; lien vers le média ou la playlist concernée. */
export function ExpiryMarker({ marker, left, time }: { marker: Marker; left: number; time: string }) {
  const { t } = useTranslation();
  const to = marker.kind === "playlist_expires" ? `/content/playlists/${marker.ref_id}` : "/content/media";
  const name = marker.name ?? "";
  return (
    <Link
      to={to}
      className="tl-expiry"
      data-impact={marker.impact}
      style={{ insetInlineStart: `${left}%` }}
      aria-label={t("planning.expiry.aria", { kind: marker.kind, name, time, impact: marker.impact })}
    >
      <Icon name="expiry" />
      <span className="tl-expiry-text">{t("planning.expiry.label", { kind: marker.kind, name })}</span>
    </Link>
  );
}
